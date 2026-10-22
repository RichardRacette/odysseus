"""Exercise the real urllib client against a local synthetic Ollama server."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from homelab.embeddings import OllamaEmbedder, EmbeddingError


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.digest = "digest-one"
        self.vectors = None
        self.redirect = False
        self.tags_fail = False
        self.remote_model = False
        self.remote_details = False
        self.model_size = 1024
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append((self.path, None))
                if outer.tags_fail:
                    self.send_error(503)
                    return
                model = {"name": "embeddinggemma:latest", "digest": outer.digest, "size": outer.model_size}
                if outer.remote_model:
                    model["remote_host"] = "https://example.invalid"
                data = json.dumps({"models": [model]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, data))
                if self.path == "/api/show":
                    details = {"details": {"family": "gemma3"}}
                    if outer.remote_details:
                        details["details"]["remote_host"] = "https://example.invalid"
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(details).encode())
                    return
                if outer.redirect:
                    self.send_response(302)
                    self.send_header("Location", "/should-not-follow")
                    self.end_headers()
                    return
                vectors = outer.vectors if outer.vectors is not None else [[3, 4]] * len(data["input"])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"model": "embeddinggemma", "embeddings": vectors}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def test_construction_performs_no_network(self):
        OllamaEmbedder(self.url)
        self.assertEqual(self.requests, [])

    def test_native_api_uses_task_prefix_and_disables_truncation(self):
        encoder = OllamaEmbedder(self.url)
        result = encoder.embed(["backup evidence"])
        self.assertAlmostEqual(result[0][0], 0.6)
        self.assertAlmostEqual(result[0][1], 0.8)
        request = self.requests[-1]
        self.assertEqual(request[0], "/api/embed")
        self.assertFalse(request[1]["truncate"])
        self.assertTrue(request[1]["input"][0].startswith("title: none | text: "))
        encoder.embed(["backup"], purpose="query")
        self.assertTrue(self.requests[-1][1]["input"][0].startswith("task: search result | query: "))

    def test_model_digest_changes_fingerprint(self):
        encoder = OllamaEmbedder(self.url)
        first = encoder.fingerprint()
        self.digest = "digest-two"
        self.assertNotEqual(encoder.fingerprint(), first)

    def test_unavailable_digest_is_visible(self):
        self.tags_fail = True
        encoder = OllamaEmbedder(self.url)
        self.assertTrue(encoder.fingerprint())
        self.assertTrue(encoder.warnings)
        with self.assertRaises(EmbeddingError):
            encoder.embed(["private document"])
        self.assertFalse(any(path == "/api/embed" for path, _ in self.requests))

    def test_local_inventory_without_digest_warns_but_can_embed(self):
        self.digest = None
        encoder = OllamaEmbedder(self.url)
        self.assertEqual(len(encoder.embed(["text"])), 1)
        self.assertTrue(encoder.warnings)

    def test_non_loopback_credentials_and_cloud_models_are_rejected(self):
        for url in ("http://example.com:11434", "http://192.168.1.2:11434",
                    "http://user:pass@127.0.0.1:11434", "http://127.0.0.1:11434/other",
                    "http://127.0.0.1:11434?redirect=x", "http://127.0.0.1:0", None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaEmbedder(url)
        with self.assertRaises(ValueError):
            OllamaEmbedder(self.url, model="embedding:cloud")
        self.remote_model = True
        with self.assertRaises(EmbeddingError):
            OllamaEmbedder(self.url).embed(["text"])

    def test_redirects_are_not_followed(self):
        self.redirect = True
        with self.assertRaises(EmbeddingError):
            OllamaEmbedder(self.url).embed(["text"])
        self.assertNotIn("/should-not-follow", [path for path, _ in self.requests])

    def test_environment_proxy_is_ignored(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertEqual(len(OllamaEmbedder(self.url).embed(["text"])), 1)

    def test_malformed_zero_nonfinite_and_wrong_count_vectors_fail(self):
        for vectors in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]],
                        [[True, 2]], [["1", 2]], [], [[1, 2], [3, 4]]):
            self.vectors = vectors
            with self.subTest(vectors=vectors), self.assertRaises(EmbeddingError):
                OllamaEmbedder(self.url).embed(["text"])

    def test_dimension_change_fails(self):
        encoder = OllamaEmbedder(self.url)
        encoder.embed(["first"])
        self.vectors = [[1, 2, 3]]
        with self.assertRaises(EmbeddingError):
            encoder.embed(["second"])

    def test_extreme_numeric_input_is_a_validation_error(self):
        self.vectors = [[10 ** 1000, 1]]
        with self.assertRaises(EmbeddingError):
            OllamaEmbedder(self.url).embed(["text"])

    def test_show_remote_metadata_blocks_document_submission(self):
        self.remote_details = True
        with self.assertRaises(EmbeddingError):
            OllamaEmbedder(self.url).embed(["private document"])
        self.assertFalse(any(path == "/api/embed" for path, _ in self.requests))

    def test_zero_size_inventory_does_not_verify_local_weights(self):
        self.model_size = 0
        with self.assertRaises(EmbeddingError):
            OllamaEmbedder(self.url).embed(["private document"])
        self.assertFalse(any(path == "/api/embed" for path, _ in self.requests))


if __name__ == "__main__":
    unittest.main()
