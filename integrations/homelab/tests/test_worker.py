"""Real queue and loopback HTTP tests for constrained worker execution."""

import hashlib
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from homelab.queue import QueueStore
from homelab.worker import run_once, run_worker


class QueueClient:
    """In-process transport adapter; queue semantics remain production code."""
    def __init__(self, store, node):
        self.store = store
        self.node = node
        self.calls = []
        self.on_heartbeat = None
        self.reject_heartbeat = False
        self.on_claim = None

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path.endswith("/claim"):
            if self.on_claim:
                self.on_claim()
            return {"job": self.store.claim(self.node)}
        if path.endswith("/heartbeat"):
            if self.on_heartbeat:
                self.on_heartbeat()
            if self.reject_heartbeat:
                raise RuntimeError("private-credential must not appear in results")
            return {"job": self.store.heartbeat(self.node, data["job_id"], data["lease_token"])}
        if path.endswith("/complete"):
            return {"job": self.store.complete(self.node, data["job_id"], data["lease_token"], data["result"])}
        if path.endswith("/fail"):
            return {"job": self.store.fail(self.node, data["job_id"], data["lease_token"], data["error"], data["retryable"])}
        raise AssertionError("Unexpected API path")


class FakeOllama:
    def __init__(self):
        self.routes = {
            "/api/tags": {"models": [{"name": "qwen3.5:4b", "model": "qwen3.5:4b", "size": 1000}]},
            "/api/show": {"details": {"family": "qwen"}, "model_info": {"general.architecture": "qwen"}},
            "/api/generate": {"model": "qwen3.5:4b", "response": "synthetic answer", "done": True,
                              "prompt_eval_count": 6, "eval_count": 3},
        }
        self.calls = []
        self.statuses = {}
        self.redirect = None
        self.generate_started = threading.Event()
        self.generate_wait = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw) if raw else None
                fake.calls.append((self.command, self.path, body))
                if self.path == "/api/generate":
                    fake.generate_started.set()
                    if fake.generate_wait:
                        fake.generate_wait.wait(2)
                    if fake.redirect:
                        self.send_response(302)
                        self.send_header("Location", fake.redirect)
                        self.end_headers()
                        return
                value = fake.routes.get(self.path, {})
                data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
                self.send_response(fake.statuses.get(self.path, 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        if self.generate_wait:
            self.generate_wait.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QueueStore(Path(self.temp.name) / "queue.sqlite3", {
            "hub": {"capabilities": ["text.hash", "json.validate"]},
            "gpu": {"capabilities": ["ollama.generate"], "allowed_models": ["qwen3.5:4b"]},
        })

    def submit(self, kind, payload, key="job"):
        return self.store.submit(kind, payload, project="demo", idempotency_key=key)

    def generate(self, **kwargs):
        return self.submit("ollama.generate", {"model": "qwen3.5:4b", "prompt": "synthetic", **kwargs})

    def test_hash_executes_real_job_and_never_sends_node_identity(self):
        job = self.submit("text.hash", {"text": "Unicode evidence: café"})
        client = QueueClient(self.store, "hub")
        self.assertTrue(run_once(client, "untrusted-label"))
        saved = self.store.get(job["id"])
        encoded = "Unicode evidence: café".encode("utf-8")
        self.assertEqual(saved["result"], {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)})
        self.assertEqual(saved["status"], "succeeded")
        self.assertTrue(all("node_id" not in (body or {}) for _, _, body in client.calls))
        self.assertFalse(run_once(client, "hub"))

    def test_json_object_validation_reports_keys(self):
        job = self.submit("json.validate", {"text": '{"b": 2, "a": 1}', "required_keys": ["a"]})
        run_once(QueueClient(self.store, "hub"), "hub")
        self.assertEqual(self.store.get(job["id"])["result"], {"valid": True, "keys": ["a", "b"]})

    def test_invalid_json_and_missing_keys_fail_without_echoing_input(self):
        for index, payload in enumerate([
            {"text": "private-credential"},
            {"text": "[]"},
            {"text": '{"value":NaN}'},
            {"text": "{}", "required_keys": ["private-credential"]},
        ]):
            with self.subTest(payload=payload):
                job = self.submit("json.validate", payload, str(index))
                run_once(QueueClient(self.store, "hub"), "hub")
                saved = self.store.get(job["id"])
                self.assertEqual(saved["status"], "failed")
                self.assertNotIn("private-credential", saved["error"])

    def test_local_generation_checks_inventory_and_shapes_bounded_request(self):
        job = self.generate(format="json", max_tokens=123)
        with FakeOllama() as server:
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
        saved = self.store.get(job["id"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["result"], {"model": "qwen3.5:4b", "text": "synthetic answer",
                                          "usage": {"input_tokens": 6, "output_tokens": 3}})
        self.assertEqual([path for _, path, _ in server.calls], ["/api/tags", "/api/show", "/api/generate"])
        request = server.calls[-1][2]
        self.assertIs(request["stream"], False)
        self.assertEqual(request["options"]["num_predict"], 123)
        self.assertLessEqual(request["options"]["num_ctx"], 4096)
        self.assertEqual(request["format"], "json")

    def test_worker_allowlist_is_required_independently_of_hub(self):
        job = self.generate()
        with FakeOllama() as server:
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url)
        self.assertEqual(self.store.get(job["id"])["status"], "failed")
        self.assertEqual(server.calls, [])

    def test_remote_ollama_and_url_overrides_are_rejected_before_network(self):
        for index, url in enumerate([
            "https://example.com", "http://127.0.0.1@evil.example", "http://127.0.0.1/api",
            "http://127.0.0.1?forward=https://example.com", "file:///etc/passwd",
        ]):
            with self.subTest(url=url):
                job = self.submit("ollama.generate", {"model": "qwen3.5:4b", "prompt": "synthetic"}, str(index))
                run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=url, allowed_models=["qwen3.5:4b"])
                self.assertEqual(self.store.get(job["id"])["status"], "failed")

    def test_inventory_rejects_missing_remote_and_zero_size_models(self):
        for index, entry in enumerate([
            {"name": "different", "size": 1000},
            {"name": "qwen3.5:4b", "size": 0},
            {"name": "qwen3.5:4b", "size": 1000, "remote_host": "cloud.example"},
            {"name": "qwen3.5:4b", "size": 1000, "details": {"remote_model": "hidden"}},
        ]):
            with self.subTest(entry=entry):
                job = self.submit("ollama.generate", {"model": "qwen3.5:4b", "prompt": "synthetic"}, str(index))
                with FakeOllama() as server:
                    server.routes["/api/tags"] = {"models": [entry]}
                    run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
                self.assertEqual(self.store.get(job["id"])["status"], "failed")
                self.assertFalse(any(path == "/api/generate" for _, path, _ in server.calls))

    def test_show_remote_metadata_is_rejected(self):
        job = self.generate()
        with FakeOllama() as server:
            server.routes["/api/show"]["remote_model"] = "hidden-cloud-alias"
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
        self.assertEqual(self.store.get(job["id"])["status"], "failed")
        self.assertFalse(any(path == "/api/generate" for _, path, _ in server.calls))

    def test_redirects_are_not_followed(self):
        job = self.generate()
        with FakeOllama() as server:
            server.redirect = server.url + "/leak"
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
        self.assertEqual(self.store.get(job["id"])["status"], "failed")
        self.assertFalse(any(path == "/leak" for _, path, _ in server.calls))

    def test_environment_proxies_are_not_used(self):
        job = self.generate()
        proxy_env = {key: "http://127.0.0.1:1" for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")}
        proxy_env.update({"NO_PROXY": "", "no_proxy": ""})
        with FakeOllama() as server, patch.dict(os.environ, proxy_env):
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
        self.assertEqual(self.store.get(job["id"])["status"], "succeeded")

    def test_heartbeat_renews_while_generation_http_request_is_in_flight(self):
        job = self.generate()
        client = QueueClient(self.store, "gpu")
        observed = threading.Event()
        with FakeOllama() as server:
            server.generate_wait = observed
            client.on_heartbeat = lambda: observed.set() if server.generate_started.is_set() else None
            run_once(client, "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"], heartbeat_interval=0.01)
        self.assertTrue(observed.is_set())
        self.assertEqual(self.store.get(job["id"])["status"], "succeeded")

    def test_lost_heartbeat_discards_result_instead_of_committing_stale_work(self):
        job = self.generate()
        client = QueueClient(self.store, "gpu")
        client.reject_heartbeat = True
        observed = threading.Event()
        client.on_heartbeat = observed.set
        with FakeOllama() as server:
            server.generate_wait = observed
            run_once(client, "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"], heartbeat_interval=0.01)
        self.assertFalse(any(path.endswith(("/complete", "/fail")) for _, path, _ in client.calls))
        self.assertEqual(self.store.get(job["id"])["status"], "running")

    def test_ollama_overload_is_retryable_and_response_body_is_not_exposed(self):
        job = self.generate()
        with FakeOllama() as server:
            server.statuses["/api/generate"] = 503
            server.routes["/api/generate"] = {"error": "private-credential"}
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
        saved = self.store.get(job["id"])
        self.assertEqual(saved["status"], "queued")
        self.assertNotIn("private-credential", saved["error"])

    def test_generation_socket_timeout_requeues_bounded_attempt(self):
        job = self.generate()
        with FakeOllama() as server, patch("homelab.worker.OLLAMA_TIMEOUT_SECONDS", 0.05):
            server.generate_wait = threading.Event()
            run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url,
                     allowed_models=["qwen3.5:4b"], heartbeat_interval=0.01)
        saved = self.store.get(job["id"])
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(saved["attempts"], 1)

    def test_worker_rechecks_job_kind_and_local_only_policy(self):
        class AlteredClaim(QueueClient):
            def request(self, method, path, data=None):
                response = super().request(method, path, data)
                if path.endswith("/claim") and response["job"]:
                    response["job"].update(self.alteration)
                return response

        for index, alteration in enumerate([
            {"kind": "shell.run", "payload": {"command": "never execute"}},
            {"local_only": False},
        ]):
            with self.subTest(alteration=alteration):
                job = self.submit("text.hash", {"text": "synthetic"}, str(index))
                client = AlteredClaim(self.store, "hub")
                client.alteration = alteration
                run_once(client, "hub")
                self.assertEqual(self.store.get(job["id"])["status"], "failed")

    def test_oversize_and_invalid_model_responses_fail_closed(self):
        for index, response in enumerate([
            b"x" * (128 * 1024 + 1),
            {"model": "different", "response": "ok", "done": True},
            {"model": "qwen3.5:4b", "response": "unfinished", "done": False},
        ]):
            with self.subTest(index=index):
                job = self.submit("ollama.generate", {"model": "qwen3.5:4b", "prompt": "synthetic"}, str(index))
                with FakeOllama() as server:
                    server.routes["/api/generate"] = response
                    run_once(QueueClient(self.store, "gpu"), "gpu", ollama_url=server.url, allowed_models=["qwen3.5:4b"])
                self.assertEqual(self.store.get(job["id"])["status"], "failed")

    def test_worker_loop_honors_stop_event_without_polling_again(self):
        stop = threading.Event()
        client = QueueClient(self.store, "hub")
        client.on_claim = stop.set
        run_worker(client, "hub", stop_event=stop, poll_interval=0.01)
        self.assertEqual(sum(path.endswith("/claim") for _, path, _ in client.calls), 1)


if __name__ == "__main__":
    unittest.main()
