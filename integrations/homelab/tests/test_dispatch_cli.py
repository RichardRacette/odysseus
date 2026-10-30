"""Actual operator processes, including a real HTTP worker interruption drill."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from homelab.config import add_project, init_state, load_config
from homelab.factory import Factory

HERE = Path(__file__).resolve().parents[1]


class DispatchCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = init_state(self.root / "state", port=0)
        add_project(self.config, "lab", self.project)
        self.manifest = HERE / "examples/project-factory-release-audit.json"
        self.base = ["--config", str(self.config), "--receipts", str(self.root / "receipts")]

    def command(self, *args):
        return subprocess.run([sys.executable, "-B", str(HERE / "dispatch.py"), *args],
                              cwd=HERE, text=True, capture_output=True, timeout=20)

    def test_cli_admits_explicit_mission_and_advances_once(self):
        admitted = self.command("admit", *self.base, "--mission", str(self.manifest), "--authorization-ref", "owner:cli")
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        result = self.command("run", *self.base, "--once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(Factory(load_config(self.config)).queue.list_jobs()), 1)

    def test_invalid_run_interval_fails_without_admitting_work(self):
        result = self.command("run", *self.base, "--interval", "nan", "--max-seconds", "0.1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("interval", result.stderr.lower())
        self.assertEqual(Factory(load_config(self.config)).queue.list_jobs(), [])

    def test_pause_and_status_are_persistent_operator_actions(self):
        first = self.command("admit", *self.base, "--mission", str(self.manifest), "--authorization-ref", "owner:cli")
        self.assertEqual(first.returncode, 0, first.stderr)
        mid = json.loads(self.manifest.read_text())["mission_id"]
        paused = self.command("pause", *self.base, "--mission-id", mid)
        self.assertEqual(paused.returncode, 0, paused.stderr)
        status = self.command("status", *self.base)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["missions"][0]["enabled"], 0)

    def test_proof_script_refuses_an_existing_destination(self):
        target = self.root / "existing"
        target.mkdir()
        original = target / "keep.txt"
        original.write_text("preserve")
        run = subprocess.run([sys.executable, "-B", str(HERE / "scripts/verify-durable-dispatch.py"),
                              "--output", str(target)], text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 2)
        self.assertIn("exist", run.stderr.lower())
        self.assertEqual(original.read_text(), "preserve")

    def test_real_process_interruption_recovers_and_verifies_receipt(self):
        target = self.root / "proof"
        run = subprocess.run([sys.executable, "-B", str(HERE / "scripts/verify-durable-dispatch.py"),
                              "--output", str(target)], text=True, capture_output=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        proof = json.loads((target / "PROOF.json").read_text())
        self.assertEqual(proof["result"], "VERIFIED")
        self.assertEqual(proof["logical_jobs"], 2)
        self.assertEqual(proof["integrity_attempts"], 2)
        self.assertTrue(proof["worker_interrupted_after_authenticated_claim"])
        self.assertTrue(proof["coordinator_interrupted_and_restarted"])
        self.assertTrue(proof["stale_result_rejected"])
        self.assertTrue(proof["receipt_stable_after_restart"])
        self.assertTrue(proof["restore_disarmed"])
        self.assertFalse(proof["physical_home_hosts_verified"])
        receipt = next((target / "receipts").glob("*.receipt.json"))
        evidence = next((target / "receipts").glob("*.evidence.json"))
        verified = self.command("verify", "--receipt", str(receipt), "--evidence", str(evidence),
                                "--mission", str(target / "mission.json"))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["steps_verified"], 2)


if __name__ == "__main__":
    unittest.main()
