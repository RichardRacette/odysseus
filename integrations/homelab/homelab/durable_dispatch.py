"""Opt-in, hub-local progression of explicitly authorized Project Factory missions."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import _no_symlink, identifier
from .dispatch_receipts import (AUTH_REF, FIXED_KINDS, ReceiptError, build_receipt,
                                canonical, digest, read_json, render_report, verify_receipt)
from .factory import Factory, FactoryError, validate_mission
from .dispatch_policy import mission_job_ids

DISPATCH_SCHEMA = 1
MAX_AUTHORIZED_MISSIONS = 128


class DispatchError(ValueError):
    pass


class LocalLock:
    """Kernel-owned lock: process termination releases it without PID-file guesses."""
    def __init__(self, path, *, wait=5):
        self.path, self.wait, self.stream = Path(path).absolute(), wait, None

    def __enter__(self):
        _no_symlink(self.path)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.stream = os.fdopen(fd, "r+b", buffering=0)
        if os.fstat(fd).st_size == 0:
            self.stream.write(b"0")
        deadline = time.monotonic() + self.wait
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise DispatchError("Another coordinator owns the local lock") from None
                time.sleep(0.02)

    def __exit__(self, *_args):
        if self.stream is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None


def _publish(path, data):
    """Publish complete bytes exclusively; restart adopts identical existing files."""
    path = Path(path).absolute()
    _no_symlink(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise DispatchError("Conflicting receipt exists; nothing was overwritten")
        return
    fd, tmp = tempfile.mkstemp(prefix=".dispatch-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise DispatchError("Conflicting receipt exists; nothing was overwritten") from None
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        os.unlink(tmp)


class Dispatcher:
    def __init__(self, config, receipt_dir):
        self.config = config
        self.factory = Factory(config)
        self.path = self.factory.path
        self.receipt_dir = Path(receipt_dir).expanduser().absolute()
        _no_symlink(self.receipt_dir)
        self.receipt_dir.mkdir(mode=0o700, exist_ok=True)
        if not self.receipt_dir.is_dir():
            raise DispatchError("Receipt destination must be a private directory")
        self.state_lock = self.path.parent / "durable-dispatch-state.lock"
        self.run_lock = self.path.parent / "durable-dispatch-run.lock"
        with LocalLock(self.state_lock), self._db(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS durable_dispatch_meta (version INTEGER NOT NULL)")
            db.execute("INSERT INTO durable_dispatch_meta SELECT ? WHERE NOT EXISTS "
                       "(SELECT 1 FROM durable_dispatch_meta)", (DISPATCH_SCHEMA,))
            if [r[0] for r in db.execute("SELECT version FROM durable_dispatch_meta")] != [DISPATCH_SCHEMA]:
                raise DispatchError("Unknown durable dispatcher database schema")
            db.execute("""CREATE TABLE IF NOT EXISTS durable_dispatch_permits (
                mission_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
                authorization_ref TEXT NOT NULL, recorded_at TEXT NOT NULL,
                root_sha256 TEXT NOT NULL, enabled INTEGER NOT NULL,
                last_status TEXT NOT NULL, receipt_sha256 TEXT)""")

    @contextmanager
    def _db(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _root_hash(self, project):
        root = self.config.get("projects", {}).get(project)
        return digest(str(Path(root).resolve())) if root else None

    def admit(self, manifest, authorization_ref):
        validate_mission(manifest)
        if not isinstance(authorization_ref, str) or not AUTH_REF.fullmatch(authorization_ref):
            raise DispatchError("Use a bounded execution authorization reference without whitespace")
        if any(s["kind"] not in FIXED_KINDS for s in manifest["steps"]):
            raise DispatchError("This coordinator admits only independently verifiable fixed jobs")
        root_hash = self._root_hash(manifest["project"])
        if root_hash is None:
            raise DispatchError("Project is not registered for execution")
        with LocalLock(self.state_lock):
            self.factory.submit(manifest)
            mid, pin = manifest["mission_id"], digest(manifest)
            with self._db(write=True) as db:
                old = db.execute("SELECT * FROM durable_dispatch_permits WHERE mission_id=?", (mid,)).fetchone()
                if old:
                    if (old["manifest_sha256"] != pin or old["authorization_ref"] != authorization_ref
                            or old["root_sha256"] != root_hash):
                        raise DispatchError("Execution authorization differs; use a new mission revision")
                else:
                    if db.execute("SELECT COUNT(*) FROM durable_dispatch_permits").fetchone()[0] >= MAX_AUTHORIZED_MISSIONS:
                        raise DispatchError("Authorized mission limit reached")
                    db.execute("INSERT INTO durable_dispatch_permits VALUES(?,?,?,?,?,1,'authorized',NULL)",
                               (mid, pin, authorization_ref, datetime.now(timezone.utc).isoformat(), root_hash))
            return {"mission_id": mid, "manifest_sha256": pin, "execution_authorized": True,
                    "enabled": bool(old["enabled"]) if old else True, "human_acceptance": "PENDING_HUMAN"}

    def pause(self, mission_id):
        identifier(mission_id)
        with LocalLock(self.state_lock):
            with self._db(write=True) as db:
                if not db.execute("UPDATE durable_dispatch_permits SET enabled=0,last_status='paused' "
                                  "WHERE mission_id=?", (mission_id,)).rowcount:
                    raise DispatchError("Mission has no execution authorization")
            self._cancel_active(mission_id)
        return {"mission_id": mission_id, "status": "paused"}

    def _cancel_active(self, mission_id):
        with self._db() as db:
            ids = mission_job_ids(db, mission_id)
        for job_id in ids:
            self.factory.queue.cancel(job_id)

    def status(self):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT mission_id,manifest_sha256,enabled,last_status,receipt_sha256 "
                "FROM durable_dispatch_permits ORDER BY mission_id")]

    def _export(self, permit, manifest):
        stem = permit["mission_id"] + "-" + permit["manifest_sha256"][:16]
        evidence_path = self.receipt_dir / (stem + ".evidence.json")
        if evidence_path.exists():
            # This is the immutable selected export transaction, even if a later
            # human approval produced a newer Factory packet before restart.
            evidence = read_json(evidence_path)
            evidence_bytes = evidence_path.read_bytes()
        else:
            temporary = self.receipt_dir / (".factory-" + uuid.uuid4().hex + ".tmp")
            try:
                self.factory.export(permit["mission_id"], temporary)
                evidence_bytes = temporary.read_bytes()
                evidence = read_json(temporary)
            finally:
                temporary.unlink(missing_ok=True)
        receipt = build_receipt(evidence, manifest,
                                {"reference": permit["authorization_ref"], "recorded_at": permit["recorded_at"]})
        pin = digest(receipt)
        receipt_path = self.receipt_dir / (stem + ".receipt.json")
        report_path = self.receipt_dir / (stem + ".report.md")
        _publish(evidence_path, evidence_bytes)
        _publish(receipt_path, (json.dumps({"receipt": receipt, "sha256": pin}, indent=2,
                                         sort_keys=True, ensure_ascii=False) + "\n").encode())
        verify_receipt(receipt_path, evidence_path, manifest)
        _publish(report_path, render_report(receipt, pin).encode())
        return pin

    def _existing_receipt(self, permit, manifest):
        stem = permit["mission_id"] + "-" + permit["manifest_sha256"][:16]
        receipt_path = self.receipt_dir / (stem + ".receipt.json")
        evidence_path = self.receipt_dir / (stem + ".evidence.json")
        verified = verify_receipt(receipt_path, evidence_path, manifest)
        if verified["receipt_sha256"] != permit["receipt_sha256"]:
            raise DispatchError("Receipt does not match the previously accepted digest")
        receipt = read_json(receipt_path)["receipt"]
        _publish(self.receipt_dir / (stem + ".report.md"),
                 render_report(receipt, permit["receipt_sha256"]).encode())
        return permit["receipt_sha256"]

    def tick(self):
        results = []
        with LocalLock(self.state_lock):
            with self._db() as db:
                permits = [dict(r) for r in db.execute("SELECT * FROM durable_dispatch_permits ORDER BY mission_id")]
            for permit in permits:
                mid, pin = permit["mission_id"], None
                status = "paused"
                reason = None
                if permit["enabled"]:
                    try:
                        with self._db() as db:
                            row = db.execute("SELECT spec_json FROM factory_missions WHERE mission_id=?", (mid,)).fetchone()
                        if row is None:
                            raise DispatchError("Authorized mission is missing")
                        spec = json.loads(row[0])
                        if digest(spec) != permit["manifest_sha256"]:
                            raise DispatchError("Stored manifest differs from the execution authorization")
                        view = self.factory.inspect(mid)
                        if (view["manifest_sha256"] != permit["manifest_sha256"]
                                or self._root_hash(view["project"]) != permit["root_sha256"]):
                            raise DispatchError("Execution scope changed or project is disarmed")
                        if permit["receipt_sha256"]:
                            pin = self._existing_receipt(permit, spec)
                            status = "execution_verified"
                        else:
                            view = self.factory.advance(mid)
                            status = view["status"]
                            if status in {"review_required", "approved"}:
                                pin = self._export(permit, spec)
                                status = "execution_verified"
                    except (FactoryError, ReceiptError, DispatchError) as exc:
                        status = "blocked"
                        reason = str(exc)
                else:
                    self._cancel_active(mid)
                with self._db(write=True) as db:
                    db.execute("UPDATE durable_dispatch_permits SET last_status=?,receipt_sha256=COALESCE(?,receipt_sha256) "
                               "WHERE mission_id=?", (status, pin, mid))
                result = {"mission_id": mid, "status": status}
                if pin:
                    result["receipt_sha256"] = pin
                if reason:
                    result["reason"] = reason
                results.append(result)
        return {"missions": results, "human_acceptance": "PENDING_HUMAN"}

    def run(self, *, interval=2, stop_event=None, max_seconds=None, on_tick=None):
        if (type(interval) not in {int, float} or not math.isfinite(interval) or not 0 < interval <= 60
                or (max_seconds is not None and (type(max_seconds) not in {int, float}
                    or not math.isfinite(max_seconds) or max_seconds <= 0))):
            raise DispatchError("Intervals must be finite and positive; polling maximum is 60 seconds")
        stop = stop_event if stop_event is not None else threading.Event()
        deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        result = {"missions": []}
        with LocalLock(self.run_lock, wait=0):
            while not stop.is_set():
                result = self.tick()
                if on_tick is not None:
                    on_tick(result)
                remaining = deadline - time.monotonic() if deadline is not None else interval
                if remaining <= 0:
                    break
                stop.wait(min(interval, remaining))
        return result
