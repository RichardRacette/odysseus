"""Behavioral tests for durable admission, leases, and execution policy."""

import concurrent.futures
import copy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from homelab.queue import LeaseError, PolicyError, QueueError, QueueStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.sqlite3"
        self.clock = Clock()
        self.nodes = {
            "hub": {"capabilities": ["text.hash", "json.validate"]},
            "mac": {"capabilities": ["text.hash"], "max_concurrency": 1},
            "gpu": {
                "capabilities": ["ollama.generate"],
                "max_concurrency": 1,
                "allowed_models": ["qwen3.5:4b"],
            },
        }
        self.store = QueueStore(self.path, self.nodes, clock=self.clock)

    def submit(self, key="one", **kwargs):
        return self.store.submit(
            "text.hash", {"text": "synthetic evidence"},
            project="demo", idempotency_key=key, **kwargs,
        )

    def test_submission_survives_reopening_and_does_not_alias_input(self):
        payload = {"text": "original"}
        job = self.store.submit("text.hash", payload, project="demo", idempotency_key="p")
        payload["text"] = "changed"
        reopened = QueueStore(self.path, self.nodes, clock=self.clock)
        saved = reopened.get(job["id"])
        self.assertEqual(saved["payload"], {"text": "original"})
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(saved["attempts"], 0)
        self.assertTrue(saved["local_only"])
        self.assertIsNone(saved.get("lease_token"))

    def test_idempotency_is_project_scoped_and_rejects_changed_request(self):
        first = self.submit()
        self.assertEqual(self.submit()["id"], first["id"])
        with self.assertRaises(PolicyError):
            self.store.submit("text.hash", {"text": "different"}, project="demo", idempotency_key="one")
        with self.assertRaises(PolicyError):
            self.submit(target_node="mac")
        other = self.store.submit("text.hash", {"text": "different"}, project="other", idempotency_key="one")
        self.assertNotEqual(first["id"], other["id"])

    def test_claim_enforces_capabilities_target_and_per_node_capacity(self):
        first = self.submit("first", target_node="hub")
        second = self.submit("second", target_node="hub")
        self.assertIsNone(self.store.claim("gpu"))
        self.assertIsNone(self.store.claim("mac"))
        claimed = self.store.claim("hub")
        self.assertEqual(claimed["id"], first["id"])
        self.assertEqual(claimed["assigned_node"], "hub")
        self.assertEqual(claimed["attempts"], 1)
        self.assertIsNone(self.store.claim("hub"))
        self.store.complete("hub", claimed["id"], claimed["lease_token"], {"ok": True})
        self.assertEqual(self.store.claim("hub")["id"], second["id"])

    def test_independent_instances_cannot_claim_same_job_or_exceed_capacity(self):
        self.submit("a", target_node="hub")
        self.submit("b", target_node="hub")
        peer = QueueStore(self.path, self.nodes, clock=self.clock)
        barrier = threading.Barrier(2)

        def claim(store):
            barrier.wait(timeout=2)
            return store.claim("hub")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [self.store, peer]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.status()["jobs"]["running"], 1)

    def test_different_nodes_have_independent_capacity(self):
        self.submit("hub", target_node="hub")
        self.submit("mac", target_node="mac")
        self.assertIsNotNone(self.store.claim("hub"))
        self.assertIsNotNone(self.store.claim("mac"))
        self.assertEqual(self.store.status()["jobs"]["running"], 2)

    def test_expiration_fences_completion_even_before_sweep(self):
        self.submit()
        first = self.store.claim("hub", lease_seconds=5)
        self.clock.advance(5)
        with self.assertRaises(LeaseError):
            self.store.complete("hub", first["id"], first["lease_token"], {"late": True})
        with self.assertRaises(LeaseError):
            self.store.heartbeat("hub", first["id"], first["lease_token"])
        self.assertEqual(self.store.sweep_expired(), 1)
        self.assertIsNone(self.store.claim("hub"))
        self.clock.advance(1)
        second = self.store.claim("mac")
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(LeaseError):
            self.store.complete("hub", first["id"], first["lease_token"], {"late": True})
        done = self.store.complete("mac", second["id"], second["lease_token"], {"fresh": True})
        self.assertEqual(done["status"], "succeeded")

    def test_heartbeat_renews_only_current_node_lease(self):
        self.submit()
        job = self.store.claim("hub", lease_seconds=5)
        with self.assertRaises(LeaseError):
            self.store.heartbeat("mac", job["id"], job["lease_token"])
        with self.assertRaises(LeaseError):
            self.store.heartbeat("hub", job["id"], "wrong-token")
        self.clock.advance(4)
        updated = self.store.heartbeat("hub", job["id"], job["lease_token"], lease_seconds=5)
        self.clock.advance(2)
        self.assertEqual(updated["lease_expires_at"], 1009)
        self.assertEqual(self.store.sweep_expired(), 0)

    def test_lease_time_is_checked_after_waiting_for_database_lock(self):
        self.submit()
        job = self.store.claim("hub", lease_seconds=1)
        clock_read = threading.Event()

        def observed_clock():
            now = self.clock()
            clock_read.set()
            return now

        peer = QueueStore(self.path, self.nodes, clock=observed_clock)
        blocker = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(peer.complete, "hub", job["id"], job["lease_token"], {"late": True})
            # A pre-transaction timestamp will be observed here; a correct
            # implementation cannot read the clock until the lock is released.
            clock_read.wait(0.1)
            self.clock.advance(2)
            blocker.commit()
            with self.assertRaises(LeaseError):
                result.result(timeout=2)

    def test_lease_operation_ids_are_bounded_before_database_access(self):
        for operation, args in [
            (self.store.heartbeat, ("hub", "x" * 129, "token")),
            (self.store.complete, ("hub", {"invalid": "id"}, "token", {})),
            (self.store.fail, ("hub", "", "token", "failed")),
        ]:
            with self.subTest(operation=operation.__name__), self.assertRaises(PolicyError):
                operation(*args)

    def test_retries_have_backoff_and_stop_at_attempt_limit(self):
        self.submit(max_attempts=2)
        first = self.store.claim("hub")
        queued = self.store.fail("hub", first["id"], first["lease_token"], "temporary", retryable=True)
        self.assertEqual(queued["status"], "queued")
        self.assertIsNone(queued.get("lease_token"))
        self.assertIsNone(self.store.claim("hub"))
        self.clock.advance(1)
        second = self.store.claim("hub")
        failed = self.store.fail("hub", second["id"], second["lease_token"], "still unavailable", retryable=True)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 2)
        self.clock.advance(3600)
        self.assertIsNone(self.store.claim("hub"))

    def test_expired_last_attempt_is_failed(self):
        self.submit(max_attempts=1)
        job = self.store.claim("hub", lease_seconds=1)
        self.clock.advance(1)
        self.assertEqual(self.store.sweep_expired(), 1)
        self.assertEqual(self.store.get(job["id"])["status"], "failed")

    def test_terminal_replay_is_idempotent_but_cannot_change_result(self):
        self.submit()
        job = self.store.claim("hub")
        done = self.store.complete("hub", job["id"], job["lease_token"], {"value": 1})
        replay = self.store.complete("hub", job["id"], job["lease_token"], {"value": 1})
        self.assertEqual(done, replay)
        self.assertIsNone(done.get("lease_token"))
        self.assertNotIn("last_lease_token", done)
        with self.assertRaises(LeaseError):
            self.store.complete("hub", job["id"], job["lease_token"], {"value": 2})

    def test_fail_replay_does_not_schedule_another_attempt(self):
        self.submit()
        job = self.store.claim("hub")
        first = self.store.fail("hub", job["id"], job["lease_token"], "temporary", retryable=True)
        self.clock.advance(0.5)
        replay = self.store.fail("hub", job["id"], job["lease_token"], "temporary", retryable=True)
        self.assertEqual(first["next_attempt_at"], replay["next_attempt_at"])
        self.assertEqual(first["attempts"], replay["attempts"])

    def test_cancel_revokes_lease_and_is_idempotent(self):
        self.submit()
        job = self.store.claim("hub")
        cancelled = self.store.cancel(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.store.cancel(job["id"]), cancelled)
        with self.assertRaises(LeaseError):
            self.store.complete("hub", job["id"], job["lease_token"], {"late": True})
        with self.assertRaises(QueueError):
            self.store.cancel("missing")

    def test_unknown_node_and_unfulfillable_job_fail_closed(self):
        with self.assertRaises(PolicyError):
            self.store.claim("unknown")
        with self.assertRaises(PolicyError):
            self.submit(target_node="gpu")
        with self.assertRaises(PolicyError):
            self.submit(target_node="unknown")

    def test_local_only_and_exact_model_allowlists(self):
        with self.assertRaises(PolicyError):
            self.submit(local_only=False)
        for model in ["qwen3.5", "qwen3.5:4b-cloud", "gpt-oss:120b-cloud", "https://remote/model"]:
            with self.subTest(model=model), self.assertRaises(PolicyError):
                self.store.submit("ollama.generate", {"model": model, "prompt": "synthetic"}, project="demo", idempotency_key=model)
        job = self.store.submit("ollama.generate", {"model": "qwen3.5:4b", "prompt": "synthetic"}, project="demo", idempotency_key="generation")
        self.assertEqual(self.store.claim("gpu")["id"], job["id"])

    def test_payload_schema_and_limits(self):
        cases = [
            ("shell.run", {"command": "anything"}),
            ("text.hash", {"text": "ok", "command": "anything"}),
            ("text.hash", {"text": 123}),
            ("text.hash", {"text": "x" * 32768}),
            ("json.validate", {"text": "{}", "required_keys": [1]}),
            ("ollama.generate", {"model": "qwen3.5:4b", "prompt": "ok", "max_tokens": 2049}),
            ("ollama.generate", {"model": "qwen3.5:4b", "prompt": "ok", "max_tokens": True}),
            ("ollama.generate", {"model": "qwen3.5:4b", "prompt": "ok", "format": "xml"}),
        ]
        for index, (kind, payload) in enumerate(cases):
            with self.subTest(kind=kind, payload=list(payload)), self.assertRaises(PolicyError):
                self.store.submit(kind, payload, project="demo", idempotency_key=str(index))
        for attempts in [0, 11, True]:
            with self.subTest(attempts=attempts), self.assertRaises(PolicyError):
                self.submit(max_attempts=attempts)

    def test_result_limits_do_not_finish_the_job(self):
        self.submit()
        job = self.store.claim("hub")
        for result in [{"text": "x" * 131072}, {"number": float("nan")}]:
            with self.assertRaises(PolicyError):
                self.store.complete("hub", job["id"], job["lease_token"], result)
        self.assertEqual(self.store.get(job["id"])["status"], "running")

    def test_status_and_listing_keep_project_filter_and_node_observation(self):
        job = self.submit()
        self.store.submit("text.hash", {"text": "elsewhere"}, project="other", idempotency_key="other")
        self.assertEqual([j["id"] for j in self.store.list_jobs(project="demo")], [job["id"]])
        self.assertIsNone(self.store.get("missing"))
        self.store.node_seen("hub")
        status = self.store.status()
        self.assertEqual(status["jobs"]["queued"], 2)
        self.assertEqual(status["nodes"]["hub"]["last_seen_at"], 1000)
        self.assertEqual(status["nodes"]["hub"]["max_concurrency"], 1)
        self.assertIsNone(status["nodes"]["gpu"]["last_seen_at"])

    def test_node_configuration_is_validated_and_copied(self):
        self.nodes["hub"]["capabilities"].clear()
        self.submit(target_node="hub")
        invalid = copy.deepcopy(self.nodes)
        invalid["gpu"]["max_concurrency"] = 0
        with self.assertRaises(PolicyError):
            QueueStore(self.path, invalid)


if __name__ == "__main__":
    unittest.main()
