#!/usr/bin/env python3
"""Disposable acceptance drill with real HTTP, processes, crashes and receipts.

Only a new directory and loopback-bound synthetic services are used. A gated
client pauses the REAL worker immediately after authenticated job claim, before
execution, making abrupt process termination repeatable without adding a slow
or privileged production job kind. This does not prove physical home hosts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from homelab.config import _no_symlink, add_project, export_worker, init_state, load_config
from homelab.dispatch_receipts import read_json, verify_receipt
from homelab.durable_dispatch import Dispatcher
from homelab.http_client import APIError, Client
from homelab.operations import create_snapshot, restore_snapshot, verify_snapshot
from homelab.worker import _execute, run_once, run_worker


def _write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _wait(predicate, children, *, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        if any(p.poll() is not None for p in children):
            raise RuntimeError("Acceptance child exited unexpectedly; retain private logs")
        time.sleep(0.05)
    raise RuntimeError("Acceptance condition timed out; retain private logs")


def _stop(process, *, abrupt=False):
    if process.poll() is None:
        (process.kill if abrupt else process.terminate)()
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=5)


def worker_child(config_path, marker=None):
    config = load_config(config_path)

    class GateClient(Client):
        def request(self, method, path, data=None):
            response = super().request(method, path, data)
            if marker and path == "/v1/worker/claim" and response.get("job"):
                _write(Path(marker), response["job"])
                threading.Event().wait(150)
            return response

    client = GateClient(config["server_url"], config["token_file"])
    run_worker(client, config["node_id"], allowed_models=[], poll_interval=0.05, heartbeat_interval=0.1)


def prove(destination):
    out = Path(destination).expanduser().absolute()
    _no_symlink(out)
    if out.exists():
        raise ValueError("Acceptance destination already exists")
    checkout = HERE.parents[1]
    if out == checkout or checkout in out.parents or not out.parent.is_dir():
        raise ValueError("Choose a new private destination outside the checkout, with an existing parent")
    out.mkdir(mode=0o700)
    manifest = read_json(HERE / "examples/project-factory-release-audit.json")
    original = (HERE / "examples/validate.json").read_text(encoding="utf-8")
    if (original != manifest["steps"][0]["payload"]["text"]
            or hashlib.sha256(original.encode()).hexdigest() != manifest["steps"][0]["expected_sha256"]):
        raise ValueError("Pinned source fixture mismatch")
    manifest["mission_id"] = "durable-dispatch-proof-v1"
    _write(out / "mission.json", manifest)
    config_path = init_state(out / "state", port=0)
    add_project(config_path, manifest["project"], HERE / "examples")
    config = load_config(config_path)
    dispatcher = Dispatcher(config, out / "receipts")
    dispatcher.admit(manifest, "owner:20261030:isolated-synthetic")
    dispatcher.admit(manifest, "owner:20261030:isolated-synthetic")
    children, logs = [], []

    def launch(name, args):
        log = open(out / (name + ".log"), "wb")
        logs.append(log)
        child = subprocess.Popen([sys.executable, "-B", *map(str, args)], cwd=HERE,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        children.append(child)
        return child

    try:
        hub = launch("hub", [HERE / "lab.py", "serve", "--config", config_path])

        def address():
            content = (out / "hub.log").read_text(encoding="utf-8")
            for line in content.splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if value.get("listening"):
                    return value["listening"]
            return None

        url = _wait(address, [hub])
        worker_path = export_worker(config_path, "hub", out / "worker", url)
        marker = out / "claimed-private.json"
        worker = launch("interrupted-worker", [Path(__file__), "--worker-child", "--config", worker_path,
                                                "--marker", marker])
        run_args = [HERE / "dispatch.py", "run", "--config", config_path,
                    "--receipts", out / "receipts", "--interval", "0.1", "--max-seconds", "150"]
        coordinator = launch("interrupted-coordinator", run_args)
        _wait(marker.is_file, [hub, worker, coordinator])
        abandoned = read_json(marker)
        worker_exit = _stop(worker, abrupt=True)
        coordinator_exit = _stop(coordinator, abrupt=True)
        if worker_exit == 0 or coordinator_exit == 0:
            raise RuntimeError("Interruption was not demonstrated")
        # Queue authority, leases and logical mission identity survive fresh processes.
        coordinator = launch("restarted-coordinator", run_args)
        worker = launch("recovery-worker", [Path(__file__), "--worker-child", "--config", worker_path])
        _wait(lambda: list((out / "receipts").glob("*.report.md")), [hub, worker, coordinator], seconds=95)
        receipt = next((out / "receipts").glob("*.receipt.json"))
        evidence = next((out / "receipts").glob("*.evidence.json"))
        verified = verify_receipt(receipt, evidence, manifest)
        state = dispatcher.factory.inspect(manifest["mission_id"])
        if state["status"] != "review_required" or [s["attempts"] for s in state["steps"]] != [2, 1]:
            raise RuntimeError("Expected interrupted-worker retry and untouched human gate")
        worker_config = load_config(worker_path)
        stale_client = Client(url, worker_config["token_file"])
        stale = _execute(abandoned, "http://127.0.0.1:11434", [], threading.Event())
        try:
            stale_client.request("POST", "/v1/worker/complete", {"job_id": abandoned["id"],
                                 "lease_token": abandoned["lease_token"], "result": stale})
        except APIError as exc:
            if exc.status != 409:
                raise
        else:
            raise RuntimeError("Stale worker result was accepted")
        before = {p.name: p.read_bytes() for p in (out / "receipts").iterdir()}
        _stop(coordinator)
        _stop(worker)
        final = subprocess.run([sys.executable, "-B", str(HERE / "dispatch.py"), "run", "--once",
                                "--config", str(config_path), "--receipts", str(out / "receipts")],
                               cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if final.returncode or before != {p.name: p.read_bytes() for p in (out / "receipts").iterdir()}:
            raise RuntimeError("Receipt did not remain stable after coordinator restart")
        _stop(hub)
        jobs = dispatcher.factory.queue.list_jobs(project=manifest["project"])
        if len(jobs) != 2:
            raise RuntimeError("Duplicate logical jobs were created")
        create_snapshot(config["state_dir"], out / "snapshot")
        verify_snapshot(out / "snapshot")
        restore_snapshot(out / "snapshot", out / "restored")
        restored = Dispatcher(load_config(out / "restored/config.json"), out / "restored-receipts")
        if restored.tick()["missions"][0]["status"] != "blocked":
            raise RuntimeError("Restored execution was not disarmed")
        proof = {"format": "richieos-durable-dispatch-proof", "version": 1, "result": "VERIFIED",
                 "mission_id": manifest["mission_id"], "scope": "Disposable single-host real-HTTP process drill",
                 "platform": sys.platform, "python_version": platform.python_version(),
                 "logical_jobs": 2, "steps_verified": verified["steps_verified"], "integrity_attempts": 2,
                 "schema_attempts": 1, "worker_interrupted_after_authenticated_claim": True,
                 "coordinator_interrupted_and_restarted": True, "stale_result_rejected": True,
                 "receipt_stable_after_restart": True, "restore_disarmed": True,
                 "receipt_sha256": verified["receipt_sha256"], "evidence_sha256": verified["evidence_sha256"],
                 "human_acceptance": "PENDING_HUMAN", "physical_home_hosts_verified": False,
                 "windows_native_verified": False, "production_deployed": False}
        _write(out / "PROOF.json", proof)
        return proof
    finally:
        for process in reversed(children):
            _stop(process)
        for log in logs:
            log.close()


def main():
    p = argparse.ArgumentParser(description="Prove durable dispatch using disposable synthetic state only")
    p.add_argument("--output")
    p.add_argument("--worker-child", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--config", help=argparse.SUPPRESS)
    p.add_argument("--marker", help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        if args.worker_child:
            worker_child(args.config, args.marker)
            return 0
        if not args.output:
            p.error("--output is required")
        print(json.dumps(prove(args.output), sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
        print("Acceptance failed: destination must not exist; verify the source, child process logs and expected recovery conditions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
