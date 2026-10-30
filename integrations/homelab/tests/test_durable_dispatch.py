"""Behavioral regressions for explicitly authorized unattended Factory work."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from homelab.config import add_project, init_state, load_config
from homelab.factory import Factory
from homelab.operations import create_snapshot, restore_snapshot
from homelab.worker import _execute

try:
    from homelab.durable_dispatch import Dispatcher, DispatchError, LocalLock
    from homelab.dispatch_receipts import ReceiptError, verify_receipt
except ImportError:
    Dispatcher = None

HERE = Path(__file__).resolve().parents[1]


class DurableDispatchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Dispatcher, "Durable dispatcher is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config_path = init_state(self.root / "state", port=0)
        add_project(self.config_path, "lab", self.project)
        self.config = load_config(self.config_path)
        self.receipts = self.root / "receipts"
        self.dispatcher = Dispatcher(self.config, self.receipts)
        self.manifest = json.loads((HERE / "examples/project-factory-release-audit.json").read_text())
        self.mid = self.manifest["mission_id"]

    def finish(self):
        for _ in range(8):
            result = self.dispatcher.tick()
            job = self.dispatcher.factory.queue.claim("hub")
            if job:
                value = _execute(job, "http://127.0.0.1:11434", [], threading.Event())
                self.dispatcher.factory.queue.complete("hub", job["id"], job["lease_token"], value)
            if result["missions"] and result["missions"][0]["status"] == "execution_verified":
                return result
        self.fail("Mission did not complete")

    def test_unapproved_factory_mission_is_not_dispatched(self):
        self.dispatcher.factory.submit(self.manifest)
        self.assertEqual(self.dispatcher.tick()["missions"], [])
        self.assertEqual(self.dispatcher.factory.queue.list_jobs(), [])

    def test_authorization_survives_reopen_and_duplicate_admission(self):
        self.dispatcher.admit(self.manifest, "owner:20261030:synthetic")
        self.dispatcher.admit(self.manifest, "owner:20261030:synthetic")
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.finish()
        self.assertEqual(len(self.dispatcher.factory.queue.list_jobs()), 2)
        paths = list(self.receipts.glob("*.receipt.json"))
        self.assertEqual(len(paths), 1)
        before = {p.name: p.read_bytes() for p in self.receipts.iterdir()}
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.dispatcher.tick()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.receipts.iterdir()})
        report = json.loads(paths[0].read_text())["receipt"]
        self.assertEqual(report["verification"], "VERIFIED")
        self.assertEqual(report["human_acceptance"], "PENDING_HUMAN")
        self.assertEqual(self.dispatcher.factory.inspect(self.mid)["status"], "review_required")

    def test_same_id_with_changed_manifest_or_authorization_is_rejected(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        changed = copy.deepcopy(self.manifest)
        changed["objective"] = "different scope"
        with self.assertRaises((DispatchError, ValueError)):
            self.dispatcher.admit(changed, "owner:one")
        with self.assertRaises(DispatchError):
            self.dispatcher.admit(self.manifest, "owner:two")
        with self.assertRaises(DispatchError):
            self.dispatcher.admit(self.manifest, "owner\nforged-report")

    def test_pause_revokes_running_claim_and_never_rearms_on_reopen(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.dispatcher.tick()
        queue = self.dispatcher.factory.queue
        claimed = queue.claim("hub")
        self.assertIsNotNone(claimed)
        self.dispatcher.pause(self.mid)
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.assertEqual(self.dispatcher.tick()["missions"][0]["status"], "paused")
        self.dispatcher.admit(self.manifest, "owner:one")
        self.assertEqual(self.dispatcher.tick()["missions"][0]["status"], "paused")
        self.assertEqual(queue.get(claimed["id"])["status"], "cancelled")
        with self.assertRaises(ValueError):
            queue.complete("hub", claimed["id"], claimed["lease_token"], {"sha256": "0" * 64, "bytes": 1})
        self.assertEqual(list(self.receipts.glob("*.receipt.json")), [])

    def test_disarmed_restore_does_not_resume_or_export(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        create_snapshot(self.config["state_dir"], self.root / "snapshot")
        restore_snapshot(self.root / "snapshot", self.root / "restored")
        restored = Dispatcher(load_config(self.root / "restored/config.json"), self.root / "restored-receipts")
        self.assertEqual(restored.tick()["missions"][0]["status"], "blocked")
        self.assertEqual(restored.factory.queue.list_jobs(), [])

    def test_restored_hub_fences_queued_work_before_coordinator_start(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.dispatcher.tick()
        create_snapshot(self.config["state_dir"], self.root / "snapshot")
        restore_snapshot(self.root / "snapshot", self.root / "restored")
        # The worker-facing queue must enforce disarming before any tick.
        restored = Factory(load_config(self.root / "restored/config.json"))
        self.assertIsNone(restored.queue.claim("hub"))
        self.assertEqual(restored.queue.list_jobs()[0]["status"], "cancelled")

    def test_disarmed_running_claim_cannot_commit_before_coordinator_start(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.dispatcher.tick()
        job = self.dispatcher.factory.queue.claim("hub")
        changed = copy.deepcopy(self.config)
        changed["projects"] = {}
        disarmed = Factory(changed)
        result = _execute(job, "http://127.0.0.1:11434", [], threading.Event())
        with self.assertRaises(ValueError):
            disarmed.queue.complete("hub", job["id"], job["lease_token"], result)

    def test_pause_finds_committed_job_before_factory_linkage(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        queue = self.dispatcher.factory.queue
        original = queue.submit

        def interrupted_submit(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated termination after real queue commit")

        with patch.object(queue, "submit", interrupted_submit), self.assertRaises(RuntimeError):
            self.dispatcher.tick()
        self.assertEqual(len(queue.list_jobs()), 1)
        self.assertIsNone(self.dispatcher.factory.inspect(self.mid)["steps"][0]["job_id"])
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.dispatcher.pause(self.mid)
        self.assertIsNone(self.dispatcher.factory.queue.claim("hub"))
        self.assertEqual(self.dispatcher.factory.queue.list_jobs()[0]["status"], "cancelled")

    def test_changed_project_root_is_not_implicitly_authorized(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        other = self.root / "other-project"
        other.mkdir()
        config = copy.deepcopy(self.config)
        config["projects"]["lab"] = str(other)
        reopened = Dispatcher(config, self.receipts)
        self.assertEqual(reopened.tick()["missions"][0]["status"], "blocked")
        self.assertEqual(reopened.factory.queue.list_jobs(), [])

    def test_changed_stored_manifest_cannot_use_an_old_execution_permit(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        changed = copy.deepcopy(self.manifest)
        changed["objective"] = "unapproved changed scope"
        with self.dispatcher.factory._db(write=True) as db:
            db.execute("UPDATE factory_missions SET spec_json=? WHERE mission_id=?",
                       (json.dumps(changed), self.mid))
        self.assertEqual(self.dispatcher.tick()["missions"][0]["status"], "blocked")
        self.assertEqual(self.dispatcher.factory.queue.list_jobs(), [])

    def test_pause_interruption_finishes_revocation_on_next_tick(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.dispatcher.tick()
        job = self.dispatcher.factory.queue.claim("hub")
        with self.dispatcher._db(write=True) as db:
            db.execute("UPDATE durable_dispatch_permits SET enabled=0 WHERE mission_id=?", (self.mid,))
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.dispatcher.tick()
        self.assertEqual(self.dispatcher.factory.queue.get(job["id"])["status"], "cancelled")

    def test_later_human_approval_does_not_replace_execution_receipt(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.finish()
        before = {p.name: p.read_bytes() for p in self.receipts.iterdir()}
        pin = self.dispatcher.factory.seal(self.mid)
        self.dispatcher.factory.approve(self.mid, "test-only-human", pin)
        self.assertEqual(self.dispatcher.tick()["missions"][0]["status"], "execution_verified")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.receipts.iterdir()})

    def test_partial_publication_recovers_after_independent_human_approval(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        real_publish = __import__("homelab.durable_dispatch", fromlist=["_publish"])._publish

        def interrupted_publish(path, data):
            if str(path).endswith(".receipt.json"):
                raise OSError("simulated interruption after evidence publication")
            return real_publish(path, data)

        with patch("homelab.durable_dispatch._publish", interrupted_publish), self.assertRaises(OSError):
            self.finish()
        evidence = next(self.receipts.glob("*.evidence.json"))
        original = evidence.read_bytes()
        self.dispatcher.factory.approve(self.mid, "test-only-human", self.dispatcher.factory.seal(self.mid))
        self.dispatcher = Dispatcher(load_config(self.config_path), self.receipts)
        self.assertEqual(self.dispatcher.tick()["missions"][0]["status"], "execution_verified")
        self.assertEqual(original, evidence.read_bytes())

    def test_reported_model_jobs_are_not_automatically_admitted(self):
        changed = copy.deepcopy(self.manifest)
        changed["steps"] = [{"id": "language", "kind": "ollama.generate", "depends_on": [],
                             "payload": {"model": "gemma3:4b", "prompt": "Say hello"}}]
        with self.assertRaises(DispatchError):
            self.dispatcher.admit(changed, "owner:one")
        self.assertEqual(self.dispatcher.tick()["missions"], [])

    def test_failed_mission_blocks_dependents_without_manual_retry(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.dispatcher.tick()
        queue = self.dispatcher.factory.queue
        job = queue.claim("hub")
        queue.fail("hub", job["id"], job["lease_token"], "controlled terminal failure", retryable=False)
        state = self.dispatcher.tick()["missions"][0]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(len(queue.list_jobs()), 1)
        self.assertEqual(list(self.receipts.glob("*.receipt.json")), [])

    def test_semantic_verifier_rejects_hash_consistent_forged_result(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.finish()
        receipt = next(self.receipts.glob("*.receipt.json"))
        evidence = next(self.receipts.glob("*.evidence.json"))
        self.assertEqual(verify_receipt(receipt, evidence, self.manifest)["verification"], "VERIFIED")
        obj = json.loads(evidence.read_text())
        obj["packet"]["steps"][0]["result"] = {"sha256": "0" * 64, "bytes": 1}
        canonical = lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        obj["packet"]["steps"][0]["result_sha256"] = hashlib.sha256(canonical(obj["packet"]["steps"][0]["result"]).encode()).hexdigest()
        obj["sha256"] = hashlib.sha256(canonical(obj["packet"]).encode()).hexdigest()
        evidence.write_text(json.dumps(obj))
        with self.assertRaises(ReceiptError):
            verify_receipt(receipt, evidence, self.manifest)

    def test_receipt_cannot_claim_a_different_project_with_recomputed_hash(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.finish()
        receipt = next(self.receipts.glob("*.receipt.json"))
        evidence = next(self.receipts.glob("*.evidence.json"))
        obj = json.loads(receipt.read_text())
        obj["receipt"]["project"] = "other"
        obj["sha256"] = hashlib.sha256(json.dumps(obj["receipt"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        receipt.write_text(json.dumps(obj))
        with self.assertRaises(ReceiptError):
            verify_receipt(receipt, evidence, self.manifest)

    def test_conflicting_receipt_is_never_overwritten(self):
        self.dispatcher.admit(self.manifest, "owner:one")
        self.finish()
        path = next(self.receipts.glob("*.receipt.json"))
        path.write_text("unrelated operator evidence")
        state = self.dispatcher.tick()["missions"][0]
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(path.read_text(), "unrelated operator evidence")

    def test_lock_rejects_second_owner_and_releases_on_close(self):
        path = self.root / "singleton.lock"
        with LocalLock(path, wait=0):
            with self.assertRaises(DispatchError):
                with LocalLock(path, wait=0):
                    self.fail("second owner entered")
        with LocalLock(path, wait=0):
            pass

    def test_run_rejects_invalid_timings_before_any_dispatch(self):
        for interval in [0, -1, float("nan"), float("inf"), True]:
            with self.subTest(interval=interval), self.assertRaises(DispatchError):
                self.dispatcher.run(interval=interval, stop_event=threading.Event(), max_seconds=0.1)
        self.assertEqual(self.dispatcher.factory.queue.list_jobs(), [])


if __name__ == "__main__":
    unittest.main()
