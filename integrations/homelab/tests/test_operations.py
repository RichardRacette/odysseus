"""Recovery tests use real SQLite/WAL files and isolated local Git repositories."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from homelab import operations
except ImportError:
    operations = None


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(operations, "operations module must implement recovery")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "secrets").mkdir()
        (self.state / "secrets/admin.token").write_text("private-admin-token\n")
        (self.state / "secrets/reader-project.token").write_text("private-reader-token\n")
        self.config = {
            "state_dir": str(self.state),
            "projects": {"project": str(self.root / "approved-source")},
            "readers": {"project": {"token_file": "secrets/reader-project.token"}},
            "admin_token_file": "secrets/admin.token",
            "port": 8765,
        }
        (self.state / "config.json").write_text(json.dumps(self.config))
        self.writer = sqlite3.connect(self.state / "knowledge.sqlite3")
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY, content TEXT)")
        self.writer.execute("INSERT INTO evidence VALUES ('known', 'recover this evidence')")
        self.writer.commit()
        self.addCleanup(self.writer.close)

    def snapshot(self):
        dest = self.root / "snapshot"
        manifest = operations.create_snapshot(self.state, dest)
        return dest, manifest

    def test_snapshot_captures_committed_wal_without_copying_live_sidecars(self):
        dest, manifest = self.snapshot()
        with closing(sqlite3.connect(dest / "knowledge.sqlite3")) as conn, conn:
            self.assertEqual(conn.execute("SELECT content FROM evidence").fetchone()[0],
                             "recover this evidence")
        self.assertEqual(operations.verify_snapshot(dest)["ok"], True)
        self.assertTrue(manifest["contains_secrets"])
        self.assertNotIn("private-admin-token", json.dumps(manifest))
        self.assertFalse((dest / "knowledge.sqlite3-wal").exists())

    def test_snapshot_flush_uses_writable_descriptor(self):
        real_fsync = os.fsync
        def checked_fsync(fd):
            # A zero-byte write is harmless but fails on read-only descriptors.
            os.write(fd, b"")
            real_fsync(fd)
        with patch("homelab.operations.os.fsync", side_effect=checked_fsync):
            destination, _ = self.snapshot()
        self.assertTrue(operations.verify_snapshot(destination)["ok"])

    def test_snapshot_only_copies_declared_state_files(self):
        (self.state / "household-private.txt").write_text("do not copy")
        (self.state / "artifacts").mkdir()
        (self.state / "artifacts/result.txt").write_text("separate artifact backup")
        (self.state / "workers").mkdir()
        (self.state / "workers/gpu.json").write_text('{"node_id":"gpu"}')
        (self.state / "workers/unrelated.txt").write_text("do not copy")
        dest, manifest = self.snapshot()
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("workers/gpu.json", paths)
        self.assertNotIn("household-private.txt", paths)
        self.assertFalse((dest / "artifacts").exists())
        self.assertFalse((dest / "workers/unrelated.txt").exists())
        self.assertFalse(manifest["source_corpus_included"])

    def test_snapshot_rejects_nested_destination_and_existing_destination(self):
        with self.assertRaises(ValueError):
            operations.create_snapshot(self.state, self.state / "backup")
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_text("original")
        with self.assertRaises(ValueError):
            operations.create_snapshot(self.state, existing)
        self.assertEqual((existing / "keep").read_text(), "original")

    def test_snapshot_rejects_linked_config_without_following_it(self):
        (self.state / "config.json").unlink()
        outside = self.root / "private.json"
        outside.write_text('{"secret":"outside"}')
        try:
            (self.state / "config.json").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse((self.root / "snapshot").exists())

    def test_bad_database_leaves_no_published_partial_snapshot(self):
        (self.state / "queue.sqlite3").write_bytes(b"not a database")
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse((self.root / "snapshot").exists())
        self.assertEqual(list(self.root.glob(".snapshot.partial-*")), [])

    def test_tampered_file_is_rejected_before_restore_creates_destination(self):
        dest, _ = self.snapshot()
        (dest / "config.json").write_text('{}')
        restored = self.root / "restored"
        with self.assertRaises(ValueError):
            operations.restore_snapshot(dest, restored)
        self.assertFalse(restored.exists())

    def test_manifest_cannot_escape_snapshot(self):
        dest, _ = self.snapshot()
        manifest_path = dest / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][0]["path"] = "../private.txt"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            operations.verify_snapshot(dest)

    def test_manifest_rejects_unlisted_file(self):
        dest, _ = self.snapshot()
        (dest / "surprise.token").write_text("not listed")
        with self.assertRaises(ValueError):
            operations.verify_snapshot(dest)

    def test_restore_preserves_data_disarms_roots_and_rotates_credentials(self):
        snapshot, _ = self.snapshot()
        restored = self.root / "restored"
        result = operations.restore_snapshot(snapshot, restored)
        with closing(sqlite3.connect(restored / "knowledge.sqlite3")) as conn, conn:
            self.assertEqual(conn.execute("SELECT id FROM evidence").fetchone()[0], "known")
        config = json.loads((restored / "config.json").read_text())
        self.assertEqual(config["state_dir"], ".")
        self.assertEqual(config["projects"], {})
        self.assertEqual(config["readers"], {})
        self.assertEqual(result["projects_requiring_reapproval"], self.config["projects"])
        self.assertNotEqual((restored / "secrets/admin.token").read_text(), "private-admin-token\n")
        self.assertFalse((restored / "secrets/reader-project.token").exists())
        self.assertEqual((snapshot / "secrets/admin.token").read_text(), "private-admin-token\n")
        self.assertNotIn("private-admin-token", json.dumps(result))
        self.assertNotIn("private-reader-token", json.dumps(result))
        if os.name != "nt":
            self.assertEqual((restored / "secrets/admin.token").stat().st_mode & 0o777, 0o600)

    def test_restore_allows_empty_destination_but_never_overwrites_contents(self):
        snapshot, _ = self.snapshot()
        restored = self.root / "restored"
        restored.mkdir()
        operations.restore_snapshot(snapshot, restored)
        with self.assertRaises(ValueError):
            operations.restore_snapshot(snapshot, restored)
        self.assertTrue((restored / "knowledge.sqlite3").exists())

    def test_restore_rebinds_absolute_credentials_to_recovered_state(self):
        self.config["admin_token_file"] = str(self.state / "secrets/admin.token")
        self.config["readers"]["project"]["token_file"] = str(self.state / "secrets/reader-project.token")
        (self.state / "config.json").write_text(json.dumps(self.config))
        snapshot, _ = self.snapshot()
        restored = self.root / "restored"
        operations.restore_snapshot(snapshot, restored)
        config = json.loads((restored / "config.json").read_text())
        self.assertEqual(config["admin_token_file"], "secrets/admin.token")
        self.assertFalse((restored / "secrets/reader-project.token").exists())

    def test_snapshot_rejects_credentials_outside_recognized_state(self):
        outside = self.root / "outside.token"
        outside.write_text("outside-private-credential")
        self.config["admin_token_file"] = str(outside)
        (self.state / "config.json").write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse((self.root / "snapshot").exists())

    def test_restore_rejects_destination_symlink(self):
        snapshot, _ = self.snapshot()
        target = self.root / "target"
        target.mkdir()
        link = self.root / "restore-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaises(ValueError):
            operations.restore_snapshot(snapshot, link)
        self.assertEqual(list(target.iterdir()), [])

    def test_restore_invalidates_leases_without_resetting_attempt_limit(self):
        with closing(sqlite3.connect(self.state / "queue.sqlite3")) as conn, conn:
            conn.executescript("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, status TEXT, attempts INTEGER, max_attempts INTEGER,
                    assigned_node TEXT, lease_token TEXT, lease_expires_at REAL,
                    next_attempt_at REAL, updated_at REAL, error TEXT,
                    last_lease_token TEXT, last_node TEXT, last_action TEXT,
                    last_action_hash TEXT, result_json TEXT
                );
                INSERT INTO jobs VALUES ('retry','running',1,3,'gpu','old',9,0,0,NULL,'old','gpu','claim','x',NULL);
                INSERT INTO jobs VALUES ('exhausted','running',3,3,'gpu','old2',9,0,0,NULL,'old2','gpu','claim','y',NULL);
                INSERT INTO jobs VALUES ('done','succeeded',1,3,NULL,NULL,NULL,0,0,NULL,'done-token','gpu','complete','z','{"ok":true}');
            """)
        snapshot, _ = self.snapshot()
        restored = self.root / "restored"
        result = operations.restore_snapshot(snapshot, restored)
        self.assertEqual(result["leases_reset"], 2)
        with closing(sqlite3.connect(restored / "queue.sqlite3")) as conn, conn:
            self.assertEqual(conn.execute("SELECT status,attempts,lease_token,last_lease_token FROM jobs WHERE id='retry'").fetchone(),
                             ("queued", 1, None, None))
            self.assertEqual(conn.execute("SELECT status FROM jobs WHERE id='exhausted'").fetchone()[0], "failed")
            self.assertEqual(conn.execute("SELECT status,result_json FROM jobs WHERE id='done'").fetchone(),
                             ("succeeded", '{"ok":true}'))

    def test_doctor_uses_no_network_processes_and_redacts_config(self):
        with patch("homelab.operations.subprocess.run", side_effect=AssertionError("doctor must not run tools")):
            result = operations.doctor({"state_dir": str(self.state), "port": 8765,
                                        "admin_token": "never-show-this"})
        serialized = json.dumps(result)
        self.assertNotIn("never-show-this", serialized)
        for key in ("python", "sqlite", "disk", "commands", "ports"):
            self.assertIn(key, result)
        self.assertIn("git", result["commands"])

    def test_doctor_reports_busy_loopback_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = operations.doctor({"state_dir": str(self.state), "port": port})
        self.assertEqual(result["ports"][str(port)]["status"], "in_use")


@unittest.skipUnless(shutil.which("git"), "Git is optional")
class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(operations, "operations module must implement mirrors")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", str(self.source))
        (self.source / "README.md").write_text("first\n")
        self.git("-C", str(self.source), "add", "README.md")
        self.git("-C", str(self.source), "-c", "user.name=Recovery Test", "-c",
                 "user.email=recovery@example.invalid", "commit", "-m", "initial")

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_mirror_is_pull_only_and_update_retains_new_commits(self):
        dest = self.root / "mirror.git"
        result = operations.mirror_repository(str(self.source), dest)
        self.assertTrue(result["ok"])
        self.assertEqual(self.git("-C", str(dest), "rev-parse", "--is-bare-repository"), "true")
        self.assertEqual(self.git("-C", str(dest), "config", "--get", "remote.origin.pushurl"), "disabled://push-blocked")
        (self.source / "README.md").write_text("second\n")
        self.git("-C", str(self.source), "add", "README.md")
        self.git("-C", str(self.source), "-c", "user.name=Recovery Test", "-c",
                 "user.email=recovery@example.invalid", "commit", "-m", "second")
        operations.mirror_repository(str(self.source), dest, update=True)
        self.assertEqual(self.git("-C", str(self.source), "rev-parse", "HEAD"),
                         self.git("-C", str(dest), "rev-parse", "HEAD"))

    def test_mirror_never_overwrites_an_unmanaged_repository(self):
        with self.assertRaises(ValueError):
            operations.mirror_repository(str(self.source), self.source, update=True)
        self.assertEqual((self.source / "README.md").read_text(), "first\n")

    def test_mirror_rejects_shell_transport_and_credential_urls(self):
        for source in ("ext::sh -c arbitrary", "--upload-pack=bad", "https://token@github.com/a/b.git"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                operations.mirror_repository(source, self.root / "rejected.git")


@unittest.skipUnless(shutil.which("sh"), "POSIX shell is optional on Windows")
class LauncherTests(unittest.TestCase):
    def test_shell_help_does_not_start_a_service(self):
        script = Path(__file__).resolve().parents[1] / "scripts/start-homelab.sh"
        self.assertTrue(script.is_file(), "native launcher must exist")
        result = subprocess.run(["sh", str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--install-startup", result.stdout)

    def test_shell_rejects_unknown_options_before_starting(self):
        script = Path(__file__).resolve().parents[1] / "scripts/start-homelab.sh"
        self.assertTrue(script.is_file(), "native launcher must exist")
        result = subprocess.run(["sh", str(script), "--unknown"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_shell_preserves_supplied_config_path_for_child_policy_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "launcher with spaces"
            (root / "scripts").mkdir(parents=True)
            source = Path(__file__).resolve().parents[1] / "scripts/start-homelab.sh"
            self.assertTrue(source.is_file(), "native launcher must exist")
            shutil.copyfile(source, root / "scripts/start-homelab.sh")
            (root / "lab.py").write_text("import json, sys; print(json.dumps(sys.argv[1:]))\n")
            config = root / "original.json"
            config.write_text("{}")
            alias = root / "linked config.json"
            try:
                alias.symlink_to(config)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links unavailable")
            result = subprocess.run(["sh", str(root / "scripts/start-homelab.sh"),
                                     "--config", str(alias), "--python", sys.executable,
                                     "--role", "worker"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ["worker", "--config", str(alias)])


if __name__ == "__main__":
    unittest.main()
