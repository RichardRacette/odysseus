"""A small SQLite job queue with atomic capacity admission and fenced leases.

Each operation opens its own connection. Independent hub threads and processes
therefore coordinate through SQLite transactions, not an in-memory semaphore.
The database must live on local storage, and all instances must use the same
administrator-owned node configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MAX_PAYLOAD_BYTES = 32 * 1024
MAX_RESULT_BYTES = 128 * 1024
JOB_KINDS = frozenset({"text.hash", "json.validate", "ollama.generate"})
STATES = ("queued", "running", "succeeded", "failed", "cancelled")


class QueueError(ValueError):
    """An invalid queue operation."""


class LeaseError(QueueError):
    """The caller no longer owns the current execution lease."""


class PolicyError(QueueError):
    """A request exceeds a configured capability or resource boundary."""


def _bounded_string(value, name, maximum=128, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise PolicyError(f"Invalid {name}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PolicyError(f"Invalid {name}")
    return value


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyError(f"Invalid {name}")
    return value


def _json(value, maximum, name):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PolicyError(f"{name} must be finite UTF-8 JSON") from None
    if size > maximum:
        raise PolicyError(f"{name} exceeds {maximum} bytes")
    return encoded


def validate_model(model):
    """Validate a model identifier; callers must also apply an exact allowlist."""
    _bounded_string(model, "model")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", model) or "://" in model:
        raise PolicyError("Invalid local model identifier")
    if "cloud" in model.lower():
        raise PolicyError("Cloud models are disabled")
    return model


def validate_payload(kind, payload):
    """Validate the fixed job schemas without executing a job or doing I/O."""
    if not isinstance(kind, str) or kind not in JOB_KINDS:
        raise PolicyError("Unsupported job kind")
    if not isinstance(payload, dict):
        raise PolicyError("Job payload must be an object")
    encoded = _json(payload, MAX_PAYLOAD_BYTES, "Payload")
    allowed = {
        "text.hash": {"text"},
        "json.validate": {"text", "required_keys"},
        "ollama.generate": {"model", "prompt", "format", "max_tokens"},
    }[kind]
    if set(payload) - allowed:
        raise PolicyError("Unknown job payload field")
    text_field = "prompt" if kind == "ollama.generate" else "text"
    if not isinstance(payload.get(text_field), str):
        raise PolicyError(f"{text_field} must be a string")
    if kind == "json.validate":
        keys = payload.get("required_keys", [])
        if not isinstance(keys, list) or len(keys) > 100:
            raise PolicyError("required_keys must be a bounded string list")
        for key in keys:
            _bounded_string(key, "required key", maximum=256, empty=True)
    elif kind == "ollama.generate":
        validate_model(payload.get("model"))
        if payload.get("format", "text") not in ("text", "json"):
            raise PolicyError("format must be text or json")
        _integer(payload.get("max_tokens", 512), "max_tokens", 1, 2048)
    return encoded


class QueueStore:
    def __init__(self, db_path, nodes: dict, clock=time.time, *, projects=None):
        if str(db_path) == ":memory:":
            raise PolicyError("The durable queue requires a database file")
        if not isinstance(nodes, dict) or not nodes:
            raise PolicyError("At least one configured node is required")
        self.db_path = Path(db_path).expanduser().resolve()
        self.clock = clock
        self._projects = projects
        self._nodes = {}
        for node_id, config in nodes.items():
            _bounded_string(node_id, "node id")
            if not isinstance(config, dict):
                raise PolicyError("Invalid node configuration")
            capabilities = config.get("capabilities", [])
            models = config.get("allowed_models", [])
            if not isinstance(capabilities, list) or any(not isinstance(c, str) or c not in JOB_KINDS for c in capabilities):
                raise PolicyError("Unsupported node capability")
            if not isinstance(models, list):
                raise PolicyError("allowed_models must be a list")
            for model in models:
                validate_model(model)
            self._nodes[node_id] = {
                "capabilities": sorted(set(capabilities)),
                "allowed_models": sorted(set(models)),
                "max_concurrency": _integer(config.get("max_concurrency", 1), "max_concurrency", 1, 64),
            }
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise QueueError("Unsupported queue schema version")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                        project TEXT NOT NULL, status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                        target_node TEXT, assigned_node TEXT, lease_token TEXT,
                        lease_expires_at REAL, next_attempt_at REAL NOT NULL,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        result_json TEXT, error TEXT, idempotency_key TEXT NOT NULL,
                        request_hash TEXT NOT NULL, local_only INTEGER NOT NULL DEFAULT 1,
                        last_lease_token TEXT, last_node TEXT, last_action TEXT, last_action_hash TEXT,
                        UNIQUE(project, idempotency_key)
                    )
                """)
                connection.execute("CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status, next_attempt_at, created_at)")
                connection.execute("CREATE INDEX IF NOT EXISTS jobs_node ON jobs(assigned_node, status)")
                connection.execute("CREATE TABLE IF NOT EXISTS node_state (node_id TEXT PRIMARY KEY, last_seen_at REAL NOT NULL)")
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _node(self, node_id):
        if not isinstance(node_id, str) or node_id not in self._nodes:
            raise PolicyError("Unknown worker node")
        return self._nodes[node_id]

    @staticmethod
    def _job(row):
        if row is None:
            return None
        public = {key: row[key] for key in (
            "id", "kind", "project", "status", "attempts", "max_attempts", "target_node",
            "assigned_node", "lease_expires_at", "next_attempt_at", "created_at", "updated_at", "error",
        )}
        public["payload"] = json.loads(row["payload_json"])
        public["result"] = json.loads(row["result_json"]) if row["result_json"] is not None else None
        public["local_only"] = bool(row["local_only"])
        public["lease_token"] = row["lease_token"] if row["status"] == "running" else None
        return public

    @staticmethod
    def _read(connection, job_id):
        return connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    @staticmethod
    def _seen(connection, node_id, now):
        connection.execute(
            "INSERT INTO node_state(node_id,last_seen_at) VALUES (?,?) "
            "ON CONFLICT(node_id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (node_id, now),
        )

    @staticmethod
    def _backoff(attempts):
        return min(60, 2 ** max(0, attempts - 1))

    def _eligible(self, node, kind, payload):
        return kind in node["capabilities"] and (
            kind != "ollama.generate" or payload["model"] in node["allowed_models"]
        )

    def submit(self, kind, payload, *, project, idempotency_key, target_node=None, max_attempts=3, local_only=True):
        if local_only is not True:
            raise PolicyError("Only local-only jobs are supported")
        _bounded_string(project, "project")
        _bounded_string(idempotency_key, "idempotency key")
        _integer(max_attempts, "max_attempts", 1, 10)
        encoded = validate_payload(kind, payload)
        candidates = [self._node(target_node)] if target_node is not None else self._nodes.values()
        if not any(self._eligible(node, kind, payload) for node in candidates):
            raise PolicyError("No configured node allows this job and model")
        request = _json({"kind": kind, "payload": payload, "project": project, "target_node": target_node,
                         "max_attempts": max_attempts, "local_only": True}, MAX_PAYLOAD_BYTES + 2048, "Request")
        digest = hashlib.sha256(request.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            now = self.clock()
            existing = connection.execute("SELECT * FROM jobs WHERE project=? AND idempotency_key=?", (project, idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise PolicyError("Idempotency key already belongs to a different request")
                return self._job(existing)
            job_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO jobs(id,kind,payload_json,project,status,max_attempts,target_node,next_attempt_at,"
                "created_at,updated_at,idempotency_key,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, kind, encoded, project, "queued", max_attempts, target_node, now, now, now, idempotency_key, digest),
            )
            return self._job(self._read(connection, job_id))

    def _sweep(self, connection, now):
        from .dispatch_policy import job_allowed
        for job in connection.execute("SELECT * FROM jobs WHERE status IN ('queued','running') "
                                      "AND idempotency_key LIKE 'pf-%'").fetchall():
            if not job_allowed(connection, job, self._projects):
                self._cancel_row(connection, job["id"], now)
        expired = connection.execute(
            "SELECT id,attempts,max_attempts FROM jobs WHERE status='running' AND (lease_expires_at<=? OR lease_expires_at IS NULL)",
            (now,),
        ).fetchall()
        for row in expired:
            retry = row["attempts"] < row["max_attempts"]
            connection.execute(
                "UPDATE jobs SET status=?,assigned_node=NULL,lease_token=NULL,lease_expires_at=NULL,"
                "next_attempt_at=?,updated_at=?,error='Worker lease expired',last_lease_token=NULL,"
                "last_node=NULL,last_action=NULL,last_action_hash=NULL WHERE id=?",
                ("queued" if retry else "failed", now + self._backoff(row["attempts"]) if retry else now, now, row["id"]),
            )
        return len(expired)

    def sweep_expired(self):
        with self._transaction() as connection:
            return self._sweep(connection, self.clock())

    def claim(self, node_id, lease_seconds=60):
        node = self._node(node_id)
        _integer(lease_seconds, "lease_seconds", 1, 300)
        with self._transaction() as connection:
            now = self.clock()
            self._seen(connection, node_id, now)
            self._sweep(connection, now)
            active = connection.execute("SELECT COUNT(*) FROM jobs WHERE status='running' AND assigned_node=?", (node_id,)).fetchone()[0]
            if active >= node["max_concurrency"] or not node["capabilities"]:
                return None
            placeholders = ",".join("?" for _ in node["capabilities"])
            candidates = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' AND next_attempt_at<=? AND attempts<max_attempts "
                f"AND (target_node IS NULL OR target_node=?) AND kind IN ({placeholders}) ORDER BY created_at,rowid",
                (now, node_id, *node["capabilities"]),
            )
            for row in candidates:
                if not self._eligible(node, row["kind"], json.loads(row["payload_json"])):
                    continue
                token = secrets.token_urlsafe(32)
                connection.execute(
                    "UPDATE jobs SET status='running',assigned_node=?,lease_token=?,lease_expires_at=?,"
                    "attempts=attempts+1,updated_at=?,error=NULL,last_lease_token=NULL,last_node=NULL,"
                    "last_action=NULL,last_action_hash=NULL WHERE id=?",
                    (node_id, token, now + lease_seconds, now, row["id"]),
                )
                return self._job(self._read(connection, row["id"]))
            return None

    @staticmethod
    def _same_token(expected, token):
        return bool(isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,128}", token)
                    and isinstance(expected, str) and secrets.compare_digest(expected, token))

    def _lease(self, row, node_id, token, now):
        if (row is None or row["status"] != "running" or row["assigned_node"] != node_id
                or not self._same_token(row["lease_token"], token)
                or row["lease_expires_at"] is None or row["lease_expires_at"] <= now):
            raise LeaseError("Worker lease is missing, expired, or no longer current")

    def _replay(self, row, node_id, token, action, digest):
        return bool(row is not None and row["last_node"] == node_id
                    and self._same_token(row["last_lease_token"], token)
                    and row["last_action"] == action and row["last_action_hash"] == digest)

    def heartbeat(self, node_id, job_id, lease_token, lease_seconds=60):
        self._node(node_id)
        _bounded_string(job_id, "job id")
        _integer(lease_seconds, "lease_seconds", 1, 300)
        with self._transaction() as connection:
            now = self.clock()
            row = self._read(connection, job_id)
            self._lease(row, node_id, lease_token, now)
            self._require_dispatch_scope(connection, row)
            connection.execute("UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE id=?", (now + lease_seconds, now, job_id))
            self._seen(connection, node_id, now)
            return self._job(self._read(connection, job_id))

    def complete(self, node_id, job_id, lease_token, result):
        self._node(node_id)
        _bounded_string(job_id, "job id")
        encoded = _json(result, MAX_RESULT_BYTES, "Result")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            now = self.clock()
            row = self._read(connection, job_id)
            self._require_dispatch_scope(connection, row)
            if self._replay(row, node_id, lease_token, "complete", digest):
                return self._job(row)
            self._lease(row, node_id, lease_token, now)
            connection.execute(
                "UPDATE jobs SET status='succeeded',result_json=?,error=NULL,lease_token=NULL,lease_expires_at=NULL,"
                "updated_at=?,last_lease_token=?,last_node=?,last_action='complete',last_action_hash=? WHERE id=?",
                (encoded, now, lease_token, node_id, digest, job_id),
            )
            self._seen(connection, node_id, now)
            return self._job(self._read(connection, job_id))

    def fail(self, node_id, job_id, lease_token, error, retryable=False):
        self._node(node_id)
        _bounded_string(job_id, "job id")
        _bounded_string(error, "error", maximum=1024)
        if not isinstance(retryable, bool):
            raise PolicyError("retryable must be a boolean")
        digest = hashlib.sha256(_json({"error": error, "retryable": retryable}, 8192, "Failure").encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            now = self.clock()
            row = self._read(connection, job_id)
            self._require_dispatch_scope(connection, row)
            if self._replay(row, node_id, lease_token, "fail", digest):
                return self._job(row)
            self._lease(row, node_id, lease_token, now)
            retry = retryable and row["attempts"] < row["max_attempts"]
            connection.execute(
                "UPDATE jobs SET status=?,assigned_node=?,error=?,lease_token=NULL,lease_expires_at=NULL,"
                "next_attempt_at=?,updated_at=?,last_lease_token=?,last_node=?,last_action='fail',last_action_hash=? WHERE id=?",
                ("queued" if retry else "failed", None if retry else node_id, error,
                 now + self._backoff(row["attempts"]) if retry else now, now, lease_token, node_id, digest, job_id),
            )
            self._seen(connection, node_id, now)
            return self._job(self._read(connection, job_id))

    def get(self, job_id):
        _bounded_string(job_id, "job id")
        with self._connection() as connection:
            return self._job(self._read(connection, job_id))

    def list_jobs(self, project=None, limit=50):
        _integer(limit, "limit", 1, 500)
        if project is not None:
            _bounded_string(project, "project")
        with self._connection() as connection:
            if project is None:
                rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,))
            else:
                rows = connection.execute("SELECT * FROM jobs WHERE project=? ORDER BY created_at DESC,rowid DESC LIMIT ?", (project, limit))
            return [self._job(row) for row in rows]

    def status(self):
        with self._transaction() as connection:
            now = self.clock()
            self._sweep(connection, now)
            counts = dict.fromkeys(STATES, 0)
            counts.update({row["status"]: row["n"] for row in connection.execute("SELECT status,COUNT(*) AS n FROM jobs GROUP BY status")})
            seen = {row["node_id"]: row["last_seen_at"] for row in connection.execute("SELECT * FROM node_state")}
            active = {row["assigned_node"]: row["n"] for row in connection.execute("SELECT assigned_node,COUNT(*) AS n FROM jobs WHERE status='running' GROUP BY assigned_node")}
            nodes = {}
            for node_id, config in self._nodes.items():
                last_seen = seen.get(node_id)
                nodes[node_id] = {**config, "capabilities": list(config["capabilities"]),
                                  "allowed_models": list(config["allowed_models"]), "active_jobs": active.get(node_id, 0),
                                  "last_seen_at": last_seen, "online": last_seen is not None and now - last_seen < 90}
            return {"nodes": nodes, "jobs": counts}

    def _require_dispatch_scope(self, connection, row):
        from .dispatch_policy import job_allowed
        if row is not None and not job_allowed(connection, row, self._projects):
            raise LeaseError("Dispatcher execution scope is disarmed or revoked")

    @staticmethod
    def _cancel_row(connection, job_id, now):
        connection.execute(
            "UPDATE jobs SET status='cancelled',assigned_node=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,"
            "last_lease_token=NULL,last_node=NULL,last_action=NULL,last_action_hash=NULL WHERE id=? "
            "AND status IN ('queued','running')", (now, job_id))

    def cancel(self, job_id):
        _bounded_string(job_id, "job id")
        with self._transaction() as connection:
            row = self._read(connection, job_id)
            if row is None:
                raise QueueError("Job not found")
            if row["status"] not in ("queued", "running"):
                return self._job(row)
            self._cancel_row(connection, job_id, self.clock())
            return self._job(self._read(connection, job_id))

    def node_seen(self, node_id):
        self._node(node_id)
        with self._transaction() as connection:
            self._seen(connection, node_id, self.clock())
