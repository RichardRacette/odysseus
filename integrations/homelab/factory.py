#!/usr/bin/env python3
"""Private operator CLI for Project Factory v0.1; no new service or shell executor."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from homelab.config import ConfigError, add_project, init_state, load_config
from homelab.factory import Factory, FactoryError
from homelab.queue import PolicyError, QueueError


def _parser():
    parser = argparse.ArgumentParser(description="Project Factory: durable mission DAGs atop the existing Home Lab queue")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("submit", "advance", "inspect", "retry", "seal", "approve", "export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True, help="Private hub config.json (execute on the authoritative hub)")
        if name == "submit":
            cmd.add_argument("--mission", required=True, help="A bounded, trusted JSON mission definition")
        else:
            cmd.add_argument("--mission-id", required=True)
        if name == "retry":
            cmd.add_argument("--step-id", required=True)
        if name == "approve":
            cmd.add_argument("--reviewer", required=True)
            cmd.add_argument("--evidence-sha256", required=True)
            cmd.add_argument("--accept-reported", action="store_true")
        if name == "export":
            cmd.add_argument("--destination", required=True, help="New private JSON file; never overwrites")
    verify = sub.add_parser("verify-evidence")
    verify.add_argument("--file", required=True)
    demo = sub.add_parser("demo", help="Existing worker/HTTP paths, real source fixture, restart and snapshot drill")
    demo.add_argument("--output", required=True, help="New private folder, outside the source checkout")
    return parser


def demo(destination):
    """Single-machine integration, not a claim of real three-host deployment."""
    from homelab.http_client import Client
    from homelab.operations import create_snapshot, restore_snapshot, verify_snapshot
    from homelab.server import make_server
    from homelab.worker import run_once

    out = Path(destination).expanduser().absolute()
    if out.exists():
        raise FactoryError("Demo destination already exists; refuse to overwrite old evidence")
    if not out.parent.is_dir() or out.is_symlink():
        raise FactoryError("Demo parent must exist and be a real directory")
    out.mkdir(mode=0o700)
    examples = Path(__file__).resolve().parent / "examples"
    manifest = json.loads((examples / "project-factory-release-audit.json").read_text(encoding="utf-8"))
    original = (examples / "validate.json").read_text(encoding="utf-8")
    if manifest["steps"][0]["payload"]["text"] != original:
        raise FactoryError("Pinned repository example differs from mission input; audit stopped")
    config_path = init_state(out / "state", port=0)
    add_project(config_path, manifest["project"], examples)
    config = load_config(config_path)
    server = make_server(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    factory = Factory(config)
    completed = 0
    try:
        factory.submit(manifest)
        for iteration in range(12):
            state = factory.advance(manifest["mission_id"])
            for node in ("hub", "gpu", "mac"):
                client = Client("http://127.0.0.1:" + str(server.server_address[1]),
                                config["nodes"][node]["token_file"])
                if run_once(client, node, allowed_models=config["nodes"][node]["allowed_models"]):
                    completed += 1
            state = factory.advance(manifest["mission_id"])
            if state["status"] in ("review_required", "failed"):
                break
        if state["status"] != "review_required" or completed != 2:
            raise FactoryError("Existing HTTP workers did not complete and verify both steps")
        # A new factory handle proves mission recovery from the same durable DB.
        factory = Factory(load_config(config_path))
        resumed = factory.inspect(manifest["mission_id"])
        if resumed["status"] != "review_required":
            raise FactoryError("Mission state was lost across operator restart")
        receipt = factory.export(manifest["mission_id"], out / "EVIDENCE.json")
        verification = Factory.verify_export(out / "EVIDENCE.json")
        if verification["integrity"] != "passed":
            raise FactoryError("Evidence integrity verification failed")
        approval = factory.approve(manifest["mission_id"], "operator-demo",
                                   receipt["packet_sha256"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    snapshot = create_snapshot(config["state_dir"], out / "snapshot")
    verify_snapshot(out / "snapshot")
    restore_snapshot(out / "snapshot", out / "restored")
    restored = Factory(load_config(out / "restored" / "config.json"))
    if restored.inspect(manifest["mission_id"])["status"] != "approved":
        raise FactoryError("Restored queue snapshot lost factory state")
    report = {
        "result": "passed", "mission_id": manifest["mission_id"],
        "scope": "One host: genuine hub HTTP, existing worker implementations and SQLite snapshot APIs",
        "worker_executions": completed, "steps_verified": 2,
        "operator_reopen": "passed", "evidence_hash": receipt["packet_sha256"],
        "final_evidence_hash": approval["final_evidence_sha256"],
        "export_verification": verification, "restored_approved_state": "passed",
        "snapshot_file_count": len(snapshot["files"]),
        "physical_nodes_verified": False, "gpu_inference_verified": False,
        "github_security_checks_verified": False, "human_approval": "operator-demo-only",
        "evidence_path": str(out / "EVIDENCE.json"),
    }
    (out / "DEMO-RESULT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    return report


def run(args):
    if args.action == "verify-evidence":
        return Factory.verify_export(args.file)
    if args.action == "demo":
        return demo(args.output)
    factory = Factory(load_config(args.config))
    if args.action == "submit":
        path = Path(args.mission)
        if path.stat().st_size > 65536:
            raise FactoryError("Mission file exceeds 64 KiB")
        return factory.submit(json.loads(path.read_text(encoding="utf-8")))
    if args.action == "advance":
        return factory.advance(args.mission_id)
    if args.action == "inspect":
        return factory.inspect(args.mission_id)
    if args.action == "retry":
        return factory.retry(args.mission_id, args.step_id)
    if args.action == "seal":
        return {"evidence_sha256": factory.seal(args.mission_id)}
    if args.action == "approve":
        return factory.approve(args.mission_id, args.reviewer, args.evidence_sha256,
                               accept_reported=args.accept_reported)
    if args.action == "export":
        return factory.export(args.mission_id, args.destination)
    raise FactoryError("Unknown action")


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        print(json.dumps(run(arguments), indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except (FactoryError, ConfigError, QueueError, PolicyError, OSError,
            KeyError, ValueError, TypeError, RuntimeError) as exc:
        # Avoid reflecting exception strings from external transports or payloads.
        message = str(exc) if isinstance(exc, (FactoryError, ConfigError, QueueError, PolicyError)) else "Operation failed"
        print(json.dumps({"error": type(exc).__name__, "message": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
