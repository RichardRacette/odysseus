"""Offline request preparation: no execution or caller-controlled capabilities."""
from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from homelab.config import add_project, init_state, load_config
from homelab.factory import Factory, FactoryError, validate_mission
from homelab.worker import _execute

try:
    from homelab import agent_requests as api
except ImportError:
    api = None

HERE = Path(__file__).resolve().parents[1]


def request(**changes):
    value = {"schema_version": 1, "request_id": "pilot-001", "requested_by": "fae",
             "capability": "fixture.audit.v1"}
    value.update(changes)
    return value


class AgentRequestTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(api, "Offline assistant request preparation is not implemented")

    def test_prepares_fixed_mission_without_granting_authority(self):
        result = api.prepare_request(request())
        self.assertEqual(result["state"], "prepared_not_submitted")
        self.assertIs(result["execution_authorized"], False)
        self.assertIs(result["requested_by_verified"], False)
        self.assertEqual(result["mission"]["mission_id"], "agent-pilot-001")
        self.assertEqual(result["mission"]["repository"]["revision"],
                         "5581cdd624b9df49efcd4683e60c29d2da80830f")
        self.assertEqual([s["kind"] for s in result["mission"]["steps"]],
                         ["text.hash", "json.validate"])
        self.assertTrue(result["mission"]["human_approval"])
        canonical = validate_mission(result["mission"])
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), result["mission_sha256"])

    def test_reordering_input_keys_is_deterministic(self):
        first = request()
        self.assertEqual(api.prepare_request(first), api.prepare_request(dict(reversed(list(first.items())))))

    def test_request_digest_is_bound_to_manifest(self):
        first, second = api.prepare_request(request()), api.prepare_request(request(requested_by="grok"))
        self.assertEqual(first["mission"]["mission_id"], second["mission"]["mission_id"])
        self.assertNotEqual(first["request_sha256"], second["request_sha256"])
        self.assertNotEqual(first["mission_sha256"], second["mission_sha256"])
        self.assertIn({"reference": "agent-request:sha256:" + first["request_sha256"],
                       "sha256": first["request_sha256"]}, first["mission"]["inputs"])

    def test_extra_authority_and_execution_fields_rejected(self):
        for field in ("approved", "command", "argv", "environment", "config", "cwd", "source_url", "path", "project"):
            with self.subTest(field=field), self.assertRaises(api.RequestError):
                api.prepare_request(request(**{field: "untrusted"}))

    def test_missing_fields_rejected(self):
        for field in request():
            value = request()
            del value[field]
            with self.subTest(field=field), self.assertRaises(api.RequestError):
                api.prepare_request(value)

    def test_wrong_schema_types_and_values_rejected(self):
        for value in (True, False, 0, 2, 1.0, "1", None, [], {}):
            with self.subTest(value=value), self.assertRaises(api.RequestError):
                api.prepare_request(request(schema_version=value))

    def test_id_paths_unicode_and_metacharacters_rejected(self):
        for value in ("", "a" * 49, "../x", "/tmp/x", "a/b", "a\\b", "a;echo", "é", "a\n", [], None):
            with self.subTest(value=value), self.assertRaises(api.RequestError):
                api.prepare_request(request(request_id=value))
        self.assertEqual(api.prepare_request(request(request_id="a" * 48))["mission"]["mission_id"], "agent-" + "a" * 48)

    def test_actor_and_capability_allowlists(self):
        for actor in ("fae", "duet", "grok", "operator"):
            self.assertFalse(api.prepare_request(request(requested_by=actor))["requested_by_verified"])
        for value in ("Richie", "admin", None, [], {}):
            with self.subTest(value=value), self.assertRaises(api.RequestError):
                api.prepare_request(request(requested_by=value))
        for value in ("shell.execute", "ollama.generate", None, [], {}):
            with self.subTest(value=value), self.assertRaises(api.RequestError):
                api.prepare_request(request(capability=value))

    def test_non_objects_rejected(self):
        for value in (None, [], "hello", 1, True):
            with self.subTest(value=value), self.assertRaises(api.RequestError):
                api.prepare_request(value)

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_invalid_utf8(self):
        for value in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":Infinity}',
                      b'{"x":-Infinity}', b'\xff', b'{broken', b'{} trailing', b'[' * 2000):
            with self.subTest(value=value[:40]), self.assertRaises(api.RequestError):
                api.load_request(value)

    def test_json_size_cap_and_valid_parse(self):
        good = json.dumps(request()).encode()
        self.assertEqual(api.load_request(good), request())
        self.assertEqual(api.load_request(good + b' ' * (4096 - len(good))), request())
        with self.assertRaises(api.RequestError):
            api.load_request(good + b' ' * (4097 - len(good)))

    def test_template_and_fixture_pins_match_tracked_bytes(self):
        self.assertEqual(api.TEMPLATE_SHA256, hashlib.sha256((HERE / "examples/project-factory-release-audit.json").read_bytes()).hexdigest())
        self.assertEqual(api.FIXTURE_SHA256, hashlib.sha256((HERE / "examples/validate.json").read_bytes()).hexdigest())

    def test_git_autocrlf_checkout_preserves_exact_pinned_bytes(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("Git unavailable for checkout normalization regression")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            examples = root / "integrations/homelab/examples"
            examples.mkdir(parents=True)
            (root / ".gitattributes").write_bytes((HERE.parents[1] / ".gitattributes").read_bytes())
            attributes = HERE / "examples/.gitattributes"
            if attributes.exists():
                (examples / ".gitattributes").write_bytes(attributes.read_bytes())
            names = {"project-factory-release-audit.json": api.TEMPLATE_SHA256,
                     "validate.json": api.FIXTURE_SHA256}
            for name in names:
                (examples / name).write_bytes((HERE / "examples" / name).read_bytes())
            for args in (["init", "--quiet"], ["config", "core.autocrlf", "true"], ["add", "."]):
                subprocess.run([git, *args], cwd=root, capture_output=True, check=True, timeout=10)
            for name in names:
                (examples / name).unlink()
            subprocess.run([git, "checkout-index", "--all", "--force"], cwd=root,
                           capture_output=True, check=True, timeout=10)
            for name, expected in names.items():
                raw = (examples / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
                self.assertNotIn(b"\r\n", raw, name)

    def test_template_and_fixture_drift_fail_closed(self):
        for filename in ("project-factory-release-audit.json", "validate.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in ("project-factory-release-audit.json", "validate.json"):
                    (root / name).write_bytes((HERE / "examples" / name).read_bytes())
                with (root / filename).open("ab") as stream:
                    stream.write(b" ")
                with patch.object(api, "EXAMPLES", root), self.assertRaises(api.RequestError):
                    api.prepare_request(request())

    def test_cli_outputs_new_file_and_refuses_overwrite(self):
        cli = importlib.import_module("agent_request")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, destination = root / "request.json", root / "prepared.json"
            source.write_text(json.dumps(request()), encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--request", str(source), "--output", str(destination)]), 0)
                first = destination.read_bytes()
                self.assertEqual(cli.main(["--request", str(source), "--output", str(destination)]), 2)
            self.assertEqual(first, destination.read_bytes())
            self.assertEqual(json.loads(first), api.prepare_request(request()))

    def test_cli_rejects_symlink_destination_and_missing_parent(self):
        cli = importlib.import_module("agent_request")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "request.json"
            source.write_text(json.dumps(request()), encoding="utf-8")
            link = root / "link.json"
            try:
                link.symlink_to(root / "absent.json")
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation unavailable")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--request", str(source), "--output", str(link)]), 2)
                self.assertEqual(cli.main(["--request", str(source), "--output", str(root / "absent/out.json")]), 2)
            self.assertFalse((root / "absent.json").exists())
            self.assertFalse((root / "absent").exists())

    def test_cli_does_not_echo_untrusted_input_or_create_output_on_rejection(self):
        cli = importlib.import_module("agent_request")
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "request.json", Path(tmp) / "prepared.json"
            source.write_text(json.dumps(request(command="PRIVATE_SENTINEL")), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cli.main(["--request", str(source), "--output", str(destination)]), 2)
            self.assertNotIn("PRIVATE_SENTINEL", out.getvalue() + err.getvalue())
            self.assertLess(len(err.getvalue()), 200)
            self.assertFalse(destination.exists())

    def test_real_factory_reuses_duplicate_rejects_conflict_and_executes_fixed_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            config_path = init_state(root / "state", port=0)
            add_project(config_path, "lab", project)
            factory = Factory(load_config(config_path))
            prepared = api.prepare_request(request())
            mission = prepared["mission"]
            first = factory.submit(mission)
            second = factory.submit(copy.deepcopy(mission))
            self.assertEqual(first["mission_id"], second["mission_id"])
            with self.assertRaisesRegex(FactoryError, "different manifest"):
                factory.submit(api.prepare_request(request(requested_by="duet"))["mission"])
            executions = 0
            for _ in range(5):
                current = factory.advance(mission["mission_id"])
                if current["status"] in ("review_required", "failed"):
                    break
                job = factory.queue.claim("hub")
                if job is not None:
                    outcome = _execute(job, "http://127.0.0.1:11434", [], threading.Event())
                    factory.queue.complete("hub", job["id"], job["lease_token"], outcome)
                    executions += 1
            self.assertEqual(current["status"], "review_required")
            self.assertEqual(executions, 2)
            self.assertEqual([s["verification"] for s in current["steps"]], ["verified", "verified"])
            exported = root / "evidence.json"
            factory.export(mission["mission_id"], exported)
            self.assertEqual(Factory.verify_export(exported)["integrity"], "passed")


if __name__ == "__main__":
    unittest.main()
