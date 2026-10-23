"""Project Factory v0.1 regressions: real queue, typed workers, crash and recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from homelab.config import add_project, init_state, load_config
from homelab.factory import Factory, FactoryError, _hash
from homelab.operations import create_snapshot, restore_snapshot, verify_snapshot
from homelab.queue import LeaseError
from homelab.worker import _execute


HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE / "examples" / "project-factory-release-audit.json"


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config_path = init_state(self.root / "state", port=0)
        add_project(self.config_path, "lab", self.project)
        self.config = load_config(self.config_path)
        self.factory = Factory(self.config)
        self.mission = json.loads(SOURCE.read_text(encoding="utf-8"))

    def submit(self, mission=None):
        return self.factory.submit(mission if mission is not None else self.mission)

    def advance(self):
        return self.factory.advance(self.mission["mission_id"])

    def finish(self):
        """Use the existing typed executor, not a fake worker or shell."""
        for _ in range(5):
            current = self.advance()
            if current["status"] in ("review_required", "failed"):
                return current
            claimed = self.factory.queue.claim("hub")
            if claimed is not None:
                outcome = _execute(claimed, "http://127.0.0.1:11434", [], threading.Event())
                self.factory.queue.complete("hub", claimed["id"], claimed["lease_token"], outcome)
        return self.advance()

    def test_mission_lifecycle_verification_approval_and_immutability(self):
        initial = self.submit()
        self.assertEqual(initial["status"], "queued")
        self.assertEqual(initial["steps"][0]["job_id"], None)
        done = self.finish()
        self.assertEqual(done["status"], "review_required")
        self.assertEqual([s["verification"] for s in done["steps"]], ["verified", "verified"])
        digest = self.factory.seal(self.mission["mission_id"])
        with self.assertRaises(FactoryError):
            self.factory.approve(self.mission["mission_id"], "operator", "0" * 64)
        approved = self.factory.approve(self.mission["mission_id"], "operator", digest)
        self.assertEqual(approved["status"], "approved")
        self.assertFalse(approved["grants_execution_authority"])
        with self.assertRaises(FactoryError):
            self.factory.retry(self.mission["mission_id"], "schema")
        self.assertEqual(self.factory.inspect(self.mission["mission_id"])["status"], "approved")

    def test_manifest_idempotency_and_same_id_conflict(self):
        self.submit()
        self.assertEqual(self.submit()["mission_id"], self.mission["mission_id"])
        altered = copy.deepcopy(self.mission)
        altered["objective"] += " changed"
        with self.assertRaisesRegex(FactoryError, "different manifest"):
            self.submit(altered)

    def test_unsafe_jobs_and_permissions_are_rejected(self):
        for mutation in ("shell", "spend", "review", "network", "extra"):
            changed = copy.deepcopy(self.mission)
            if mutation == "shell":
                changed["steps"][0]["kind"] = "shell.execute"
            elif mutation == "spend":
                changed["restrictions"]["cloud_spend_allowed"] = True
            elif mutation == "review":
                changed["human_approval"] = False
            elif mutation == "network":
                changed["restrictions"]["network"] = "internet"
            else:
                changed["steps"][0]["command"] = "rm -rf /"
            with self.subTest(mutation=mutation), self.assertRaises(FactoryError):
                self.submit(changed)
        self.assertEqual(self.factory.queue.status()["jobs"]["queued"], 0)

    def test_cycles_unknown_dependencies_and_duplicate_ids(self):
        for mutation in ("cycle", "missing", "duplicate"):
            changed = copy.deepcopy(self.mission)
            if mutation == "cycle":
                changed["steps"][0]["depends_on"] = ["schema"]
            elif mutation == "missing":
                changed["steps"][1]["depends_on"] = ["ghost"]
            else:
                changed["steps"][1]["id"] = "integrity"
            with self.subTest(mutation=mutation), self.assertRaises(FactoryError):
                self.submit(changed)

    def test_offline_worker_and_restart_preserve_queue(self):
        self.submit()
        first = self.advance()
        jid = first["steps"][0]["job_id"]
        self.assertIsNotNone(jid)
        self.assertEqual(self.factory.queue.get(jid)["status"], "queued")
        reopened = Factory(load_config(self.config_path))
        self.assertEqual(reopened.inspect(self.mission["mission_id"])["steps"][0]["job_id"], jid)
        self.assertEqual(reopened.advance(self.mission["mission_id"])["steps"][0]["job_id"], jid)
        self.assertEqual(len(reopened.queue.list_jobs("lab")), 1)

    def test_gap_between_job_admission_and_factory_record_is_repaired(self):
        self.submit()
        _, row, _, _ = self.factory._mission(self.mission["mission_id"])
        step = self.mission["steps"][0]
        key = "pf-" + _hash(row[1] + ":" + step["id"] + ":0")[:40]
        orphan = self.factory.queue.submit(step["kind"], step["payload"], project="lab",
                                           idempotency_key=key, max_attempts=step["max_attempts"])
        resumed = Factory(load_config(self.config_path))
        state = resumed.advance(self.mission["mission_id"])
        self.assertEqual(state["steps"][0]["job_id"], orphan["id"])
        self.assertEqual(len(resumed.queue.list_jobs("lab")), 1)

    def test_stale_lease_is_fenced_and_expires_to_requeue(self):
        self.submit()
        jid = self.advance()["steps"][0]["job_id"]
        now = [1000.0]
        self.factory.queue.clock = lambda: now[0]
        # Re-anchor the real initial row's next_attempt_at to simulated time,
        # keeping the existing queue retry logic under test.
        with self.factory._db(write=True) as db:
            db.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", (1000.0, jid))
        first = self.factory.queue.claim("hub", lease_seconds=2)
        self.assertEqual(first["id"], jid)
        now[0] += 3
        self.advance()
        self.assertEqual(self.factory.queue.get(jid)["status"], "queued")
        now[0] += 2
        second = self.factory.queue.claim("mac", lease_seconds=30)
        self.assertEqual(second["id"], jid)
        with self.assertRaises(LeaseError):
            self.factory.queue.complete("hub", jid, first["lease_token"], {"sha256": "fake"})
        result = _execute(second, "http://127.0.0.1:11434", [], threading.Event())
        self.factory.queue.complete("mac", jid, second["lease_token"], result)
        self.assertEqual(self.factory.queue.get(jid)["status"], "succeeded")

    def test_terminal_failure_retry_keeps_prior_attempt_history(self):
        self.submit()
        jid = self.advance()["steps"][0]["job_id"]
        leased = self.factory.queue.claim("hub")
        self.factory.queue.fail("hub", jid, leased["lease_token"], "Simulated policy failure", retryable=False)
        failed = self.advance()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["steps"][0]["state"], "failed")
        restarted = self.factory.retry(self.mission["mission_id"], "integrity")
        self.assertEqual(restarted["steps"][0]["generation"], 1)
        self.assertNotEqual(restarted["steps"][0]["job_id"], jid)
        self.assertEqual(self.finish()["status"], "review_required")
        packet = self.factory._latest(self.mission["mission_id"])["packet"]
        attempts = [x for x in packet["dispatch_history"] if x["step_id"] == "integrity"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["job_id"], jid)

    def test_result_mismatch_fails_closed_and_is_retryable(self):
        self.submit()
        jid = self.advance()["steps"][0]["job_id"]
        first = self.factory.queue.claim("hub")
        self.factory.queue.complete("hub", jid, first["lease_token"], {"sha256": "0" * 64, "bytes": 5})
        state = self.advance()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["steps"][0]["state"], "verification_failed")
        self.assertIsNone(state["steps"][1]["job_id"])
        self.factory.retry(self.mission["mission_id"], "integrity")
        self.assertEqual(self.finish()["status"], "review_required")

    def test_gpu_routes_only_to_explicitly_labelled_node(self):
        changed = copy.deepcopy(self.mission)
        changed["steps"] = [{
            "id": "model", "kind": "ollama.generate", "payload": {
                "model": "gemma3:4b", "prompt": "Summarize the fixture.", "max_tokens": 16
            }, "depends_on": [], "resources": {"gpu": True}
        }]
        self.submit(changed)
        with self.assertRaisesRegex(FactoryError, "No configured node"):
            self.advance()
        # A policy label, not an unverified guess based on node name.
        self.config["nodes"]["gpu"]["resources"] = {"gpu": True}
        labelled = Factory(self.config)
        result = labelled.advance(self.mission["mission_id"])
        job = labelled.queue.get(result["steps"][0]["job_id"])
        self.assertEqual(job["target_node"], "gpu")
        self.assertIsNone(labelled.queue.claim("hub"))
        self.assertEqual(labelled.queue.claim("gpu")["id"], job["id"])

    def test_model_outputs_are_reported_not_verified(self):
        changed = copy.deepcopy(self.mission)
        changed["steps"] = [{
            "id": "model", "kind": "ollama.generate", "payload": {
                "model": "gemma3:4b", "prompt": "Summarize a short public checklist.", "max_tokens": 16
            }, "depends_on": []
        }]
        self.submit(changed)
        job_id = self.advance()["steps"][0]["job_id"]
        claim = self.factory.queue.claim("gpu")
        self.assertEqual(claim["id"], job_id)
        # A deliberately synthetic worker response; no claim of live inference.
        self.factory.queue.complete("gpu", job_id, claim["lease_token"],
                                    {"model": "gemma3:4b", "text": "Reported output", "usage": {}})
        state = self.advance()
        self.assertEqual(state["status"], "review_required")
        self.assertEqual(state["steps"][0]["verification"], "reported_only")
        digest = self.factory.seal(self.mission["mission_id"])
        with self.assertRaisesRegex(FactoryError, "accept-reported"):
            self.factory.approve(self.mission["mission_id"], "operator", digest)
        self.assertEqual(self.factory.approve(self.mission["mission_id"], "operator", digest,
                                              accept_reported=True)["status"], "approved")

    def test_snapshot_contains_factory_tables_and_recovery(self):
        self.submit()
        jid = self.advance()["steps"][0]["job_id"]
        destination = self.root / "snapshot"
        create_snapshot(self.config["state_dir"], destination)
        verify_snapshot(destination)
        restore_snapshot(destination, self.root / "restored")
        restored = Factory(load_config(self.root / "restored" / "config.json"))
        self.assertEqual(restored.inspect(self.mission["mission_id"])["steps"][0]["job_id"], jid)
        self.assertEqual(restored.queue.get(jid)["status"], "queued")

    def test_export_detects_tampering_and_refuses_overwrite(self):
        self.submit()
        self.assertEqual(self.finish()["status"], "review_required")
        destination = self.root / "receipt.json"
        done = self.factory.export(self.mission["mission_id"], destination)
        self.assertEqual(Factory.verify_export(destination)["sha256"], done["packet_sha256"])
        with self.assertRaises(FileExistsError):
            self.factory.export(self.mission["mission_id"], destination)
        value = json.loads(destination.read_text())
        value["packet"]["status"] = "approved"
        destination.write_text(json.dumps(value))
        with self.assertRaises(FactoryError):
            Factory.verify_export(destination)

    def test_reconciliation_is_concurrency_idempotent(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=6) as threads:
            results = list(threads.map(lambda _: self.advance(), range(12)))
        ids = {r["steps"][0]["job_id"] for r in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(self.factory.queue.list_jobs("lab")), 1)

    def test_independent_work_continues_when_another_branch_fails(self):
        changed = copy.deepcopy(self.mission)
        changed["steps"].append({
            "id": "independent", "kind": "text.hash",
            "payload": {"text": "This check can finish without the first branch."},
            "depends_on": [], "expected_sha256": hashlib.sha256(
                b"This check can finish without the first branch.").hexdigest()
        })
        self.submit(changed)
        state = self.advance()
        self.assertEqual(state["steps"][2]["state"], "queued")
        original = self.factory.queue.claim("hub")
        self.assertEqual(original["id"], state["steps"][0]["job_id"])
        self.factory.queue.fail("hub", original["id"], original["lease_token"],
                                "Simulated terminal failure", retryable=False)
        degraded = self.advance()
        self.assertEqual(degraded["status"], "running_with_failures")
        self.assertEqual(degraded["steps"][1]["state"], "blocked")
        independent = self.factory.queue.claim("mac")
        self.assertEqual(independent["id"], degraded["steps"][2]["job_id"])
        result = _execute(independent, "http://127.0.0.1:11434", [], threading.Event())
        self.factory.queue.complete("mac", independent["id"], independent["lease_token"], result)
        final = self.advance()
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["steps"][2]["verification"], "verified")
        self.assertEqual(final["steps"][1]["state"], "blocked")

    def test_real_http_worker_demo_and_restored_approval(self):
        from factory import demo
        report = demo(self.root / "demonstration")
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["worker_executions"], 2)
        self.assertFalse(report["physical_nodes_verified"])
        self.assertEqual(report["restored_approved_state"], "passed")


if __name__ == "__main__":
    unittest.main()
