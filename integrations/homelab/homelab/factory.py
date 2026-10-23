"""Project Factory v0.1: hub-local mission journal atop the existing fenced queue.

No executor, remote shell, cloud provider or new network listener is added.
Use through the hub operator CLI; workers retain their existing scoped tokens.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .queue import JOB_KINDS, PolicyError, QueueStore, validate_payload

VERSION = "0.1.0"
FACTORY_SCHEMA = 1
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_MISSION_BYTES = 65536
MAX_STEPS = 64
MAX_RETRIES = 3
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


class FactoryError(ValueError):
    """Fail-closed operator error; never contains payload or credentials."""


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise FactoryError("Mission must be finite JSON") from None


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _name(value, label):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise FactoryError("Invalid " + label)
    return value


def _text(value, label, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise FactoryError("Invalid " + label)
    return value


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise FactoryError("Invalid " + label + " fields")


def validate_mission(value):
    """Validate the declarative DAG, resource requirements and hard safety policy."""
    _keys(value,
          {"schema_version", "mission_id", "project", "objective", "repository", "inputs",
           "expected_outputs", "acceptance_criteria", "restrictions", "human_approval", "steps"},
          {"schema_version", "mission_id", "project", "objective", "repository", "inputs",
           "expected_outputs", "acceptance_criteria", "restrictions", "human_approval", "steps"},
          "mission")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise FactoryError("Unsupported mission schema")
    _name(value["mission_id"], "mission id")
    _name(value["project"], "project")
    _text(value["objective"], "objective", 2000)
    repo = value["repository"]
    _keys(repo, {"url", "revision"}, {"url", "revision"}, "repository")
    url = _text(repo["url"], "repository URL", 512)
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", url):
        raise FactoryError("Repository must be an explicit GitHub HTTPS origin path")
    if not isinstance(repo["revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", repo["revision"]):
        raise FactoryError("Repository revision must be a 40-character commit SHA")
    if (not isinstance(value["inputs"], list) or len(value["inputs"]) > 64):
        raise FactoryError("Invalid input references")
    for item in value["inputs"]:
        _keys(item, {"reference", "sha256"}, {"reference", "sha256"}, "input")
        _text(item["reference"], "input reference", 512)
        if not isinstance(item["sha256"], str) or not HEX64.fullmatch(item["sha256"]):
            raise FactoryError("Input reference must carry a SHA-256")
    for label in ("expected_outputs", "acceptance_criteria"):
        items = value[label]
        if not isinstance(items, list) or not 1 <= len(items) <= 64:
            raise FactoryError("Invalid " + label)
        for item in items:
            _text(item, label, 512)
    restrictions = value["restrictions"]
    _keys(restrictions, {"local_only", "cloud_spend_allowed", "network", "actions"},
          {"local_only", "cloud_spend_allowed", "network", "actions"}, "restrictions")
    if (restrictions["local_only"] is not True or restrictions["cloud_spend_allowed"] is not False
            or restrictions["network"] != "loopback_only" or restrictions["actions"] != []
            or value["human_approval"] is not True):
        raise FactoryError("Factory v0.1 permits only local typed jobs and mandatory human approval")
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise FactoryError("Mission requires 1 to 64 bounded steps")
    index = {}
    for step in steps:
        _keys(step, {"id", "kind", "payload", "depends_on", "resources", "target_node",
                     "max_attempts", "expected_sha256"},
              {"id", "kind", "payload", "depends_on"}, "step")
        sid = _name(step["id"], "step id")
        if sid in index:
            raise FactoryError("Duplicate step identifier")
        index[sid] = step
        if step["kind"] not in JOB_KINDS:
            raise FactoryError("Unsupported job kind; arbitrary execution is forbidden")
        try:
            validate_payload(step["kind"], step["payload"])
        except PolicyError as exc:
            raise FactoryError("Step payload violates existing queue policy") from exc
        deps = step["depends_on"]
        if (not isinstance(deps, list) or len(deps) > MAX_STEPS
                or any(not isinstance(d, str) or not ID.fullmatch(d) for d in deps)
                or len(set(deps)) != len(deps) or sid in deps):
            raise FactoryError("Invalid step dependencies")
        resources = step.get("resources", {})
        _keys(resources, {"gpu", "cpu_threads"}, set(), "resources")
        if (type(resources.get("gpu", False)) is not bool
                or type(resources.get("cpu_threads", 1)) is not int
                or resources.get("cpu_threads", 1) != 1):
            raise FactoryError("Only declared GPU and one CPU thread are supported in v0.1")
        node = step.get("target_node")
        if node is not None:
            _name(node, "target node")
        attempts = step.get("max_attempts", 3)
        if type(attempts) is not int or not 1 <= attempts <= 10:
            raise FactoryError("Invalid attempt limit")
        expected = step.get("expected_sha256")
        if step["kind"] == "text.hash":
            if not isinstance(expected, str) or not HEX64.fullmatch(expected):
                raise FactoryError("Hash steps require an independently pinned expected SHA-256")
        elif expected is not None:
            raise FactoryError("Expected SHA-256 is only supported for text.hash")
    for sid, step in index.items():
        if any(d not in index for d in step["depends_on"]):
            raise FactoryError("Step depends on a nonexistent step")
    visiting, visited = set(), set()
    def visit(sid):
        if sid in visiting:
            raise FactoryError("Mission dependency cycle")
        if sid in visited:
            return
        visiting.add(sid)
        for dep in index[sid]["depends_on"]:
            visit(dep)
        visiting.remove(sid)
        visited.add(sid)
    for sid in index:
        visit(sid)
    canonical = _canonical(value)
    if len(canonical.encode("utf-8")) > MAX_MISSION_BYTES:
        raise FactoryError("Mission exceeds 64 KiB")
    return canonical


class Factory:
    """Single-hub mission controller. Reconcile is safe to repeat after crashes."""

    def __init__(self, config):
        if config.get("role") != "hub":
            raise FactoryError("Factory commands require the private hub configuration")
        self.config = config
        self.path = Path(config["state_dir"]) / "queue.sqlite3"
        if self.path.is_symlink():
            raise FactoryError("Refusing a symbolic-link queue database")
        self.queue = QueueStore(self.path, config["nodes"])
        with self._db(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS factory_meta (version INTEGER NOT NULL)")
            db.execute("INSERT INTO factory_meta(version) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM factory_meta)",
                       (FACTORY_SCHEMA,))
            if db.execute("SELECT version FROM factory_meta").fetchall() != [(FACTORY_SCHEMA,)]:
                raise FactoryError("Unknown Project Factory database schema")
            db.execute("""CREATE TABLE IF NOT EXISTS factory_missions (
                mission_id TEXT PRIMARY KEY, project TEXT NOT NULL, spec_json TEXT NOT NULL,
                spec_hash TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT,
                approved_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS factory_steps (
                mission_id TEXT NOT NULL, step_id TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
                job_id TEXT, PRIMARY KEY(mission_id,step_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS factory_dispatches (
                mission_id TEXT NOT NULL, step_id TEXT NOT NULL, generation INTEGER NOT NULL,
                job_id TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(mission_id,step_id,generation))""")
            db.execute("""CREATE TABLE IF NOT EXISTS factory_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                step_id TEXT, kind TEXT NOT NULL, recorded_at TEXT NOT NULL, reference TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS factory_packets (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                revision INTEGER NOT NULL, stage TEXT NOT NULL, sealed_at TEXT NOT NULL,
                sha256 TEXT NOT NULL, packet_json TEXT NOT NULL,
                UNIQUE(mission_id,revision,stage))""")

    @contextmanager
    def _db(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = lambda cursor, row: tuple(row)
        db.execute("PRAGMA busy_timeout=10000")
        if write:
            db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _mission(self, mission_id):
        _name(mission_id, "mission id")
        with self._db() as db:
            row = db.execute("SELECT spec_json,spec_hash,created_at,approved_by,approved_at "
                             "FROM factory_missions WHERE mission_id=?", (mission_id,)).fetchone()
            if row is None:
                raise FactoryError("Mission does not exist")
            steps = db.execute("SELECT step_id,generation,job_id FROM factory_steps WHERE mission_id=?",
                               (mission_id,)).fetchall()
            history = db.execute("SELECT step_id,generation,job_id,created_at FROM factory_dispatches "
                                 "WHERE mission_id=? ORDER BY step_id,generation", (mission_id,)).fetchall()
        return (json.loads(row[0]), row, {s[0]: {"generation": s[1], "job_id": s[2]} for s in steps}, history)

    def plan(self, manifest):
        """Dry-run routing preview. No mission or job is admitted."""
        canonical = validate_mission(manifest)
        if manifest["project"] not in self.config["projects"]:
            raise FactoryError("Project must first be registered with lab.py project-add")
        health = self.queue.status()["nodes"]
        steps = []
        for step in manifest["steps"]:
            try:
                target, eligible = self._route(step)
                routing_blocker = None
            except FactoryError as exc:
                target, eligible, routing_blocker = None, [], str(exc)
            steps.append({"step_id": step["id"], "kind": step["kind"],
                          "depends_on": step["depends_on"], "declared_resources": step.get("resources", {}),
                          "target_node": target, "eligible_nodes": eligible,
                          "online_eligible_nodes": [n for n in eligible if health[n]["online"]],
                          "routing_blocker": routing_blocker})
        return {"mission_id": manifest["mission_id"], "project": manifest["project"],
                "manifest_sha256": _hash(canonical), "admitted": False,
                "executes_commands": False, "steps": steps}

    def submit(self, manifest):
        canonical = validate_mission(manifest)
        if manifest["project"] not in self.config["projects"]:
            raise FactoryError("Project must first be registered with lab.py project-add")
        mid = manifest["mission_id"]
        digest = _hash(canonical)
        # Admission is durable before any queue mutation. Reconciliation repairs
        # a crash after queue.submit but before recording factory_dispatches.
        with self._db(write=True) as db:
            old = db.execute("SELECT spec_hash FROM factory_missions WHERE mission_id=?", (mid,)).fetchone()
            if old:
                if old[0] != digest:
                    raise FactoryError("Mission id already belongs to a different manifest")
                return self.inspect(mid)
            db.execute("INSERT INTO factory_missions(mission_id,project,spec_json,spec_hash,created_at) "
                       "VALUES(?,?,?,?,?)", (mid, manifest["project"], canonical, digest, _utc()))
            for step in manifest["steps"]:
                db.execute("INSERT INTO factory_steps(mission_id,step_id,generation) VALUES(?,?,0)",
                           (mid, step["id"]))
            db.execute("INSERT INTO factory_events(mission_id,kind,recorded_at,reference) VALUES(?,?,?,?)",
                       (mid, "mission_submitted", _utc(), digest))
        return self.inspect(mid)

    def _route(self, step):
        model = step["payload"].get("model") if step["kind"] == "ollama.generate" else None
        gpu = step.get("resources", {}).get("gpu", False)
        pinned = step.get("target_node")
        status = self.queue.status()["nodes"]
        eligible = []
        for name, node in self.config["nodes"].items():
            if pinned is not None and name != pinned:
                continue
            if step["kind"] not in node.get("capabilities", []):
                continue
            if model is not None and model not in node.get("allowed_models", []):
                continue
            if gpu and node.get("resources", {}).get("gpu") is not True:
                continue
            eligible.append(name)
        if not eligible:
            raise FactoryError("No configured node meets the declared capability and resources")
        if gpu:
            # The queue cannot filter generic resource labels, so pin GPU tasks
            # only to an explicitly administrator-labelled GPU node.
            eligible.sort(key=lambda name: (not status[name]["online"],
                          status[name]["active_jobs"] / status[name]["max_concurrency"], name))
            return eligible[0], eligible
        # Do not pin general work: the existing pull queue can dispatch it to
        # any eligible online worker as connectivity changes.
        return pinned, eligible

    @staticmethod
    def _verification(step, job):
        if not job or job["status"] != "succeeded":
            return "pending"
        result = job["result"]
        if not isinstance(result, dict):
            return "mismatch"
        if step["kind"] == "text.hash":
            expected = step["expected_sha256"]
            original = step["payload"]["text"].encode("utf-8")
            if (expected != hashlib.sha256(original).hexdigest()
                    or result != {"sha256": expected, "bytes": len(original)}):
                return "mismatch"
            return "verified"
        if step["kind"] == "json.validate":
            try:
                obj = json.loads(step["payload"]["text"],
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                expected = {"valid": True, "keys": sorted(obj)}
            except (ValueError, TypeError, RecursionError):
                return "mismatch"
            return "verified" if isinstance(obj, dict) and result == expected else "mismatch"
        return "reported_only"   # A model's answer is never automatically factual truth.

    def _view(self, mission_id):
        spec, row, entries, history = self._mission(mission_id)
        jobs = {}
        summaries = []
        for step in spec["steps"]:
            sid = step["id"]
            entry = entries[sid]
            job = self.queue.get(entry["job_id"]) if entry["job_id"] else None
            if entry["job_id"] and job is None:
                raise FactoryError("Mission references a missing queued job; inspect backup before recovery")
            jobs[sid] = job
            verify = self._verification(step, job)
            state = job["status"] if job else "waiting"
            if state == "succeeded" and verify == "mismatch":
                state = "verification_failed"
            summaries.append({"step_id": sid, "kind": step["kind"], "depends_on": step["depends_on"],
                              "generation": entry["generation"], "job_id": entry["job_id"],
                              "state": state, "verification": verify,
                              "attempts": job["attempts"] if job else 0,
                              "assigned_node": job["assigned_node"] if job else None,
                              "error": job["error"] if job else None})
        bad = {"failed", "cancelled", "verification_failed", "blocked"}
        # Failure is terminal only once independent work finishes. A downstream
        # step whose prerequisite has failed can never be dispatched as-is.
        for _ in summaries:
            current = {s["step_id"]: s for s in summaries}
            for item in summaries:
                if item["state"] == "waiting" and any(
                        current[d]["state"] in bad for d in item["depends_on"]):
                    item["state"] = "blocked"
                    item["verification"] = "blocked"
        if any(x["state"] in bad for x in summaries):
            state = ("failed" if all(x["state"] not in ("waiting", "queued", "running")
                                 for x in summaries) else "running_with_failures")
        elif all(x["state"] == "succeeded" for x in summaries):
            state = "approved" if row[3] else "review_required"
        elif any(x["state"] == "running" for x in summaries):
            state = "running"
        else:
            state = "queued"
        if row[3] and state != "approved":
            raise FactoryError("Approved mission unexpectedly changed; manual integrity review required")
        return spec, row, entries, history, jobs, summaries, state

    def inspect(self, mission_id):
        spec, row, _, _, _, steps, state = self._view(mission_id)
        return {"mission_id": mission_id, "project": spec["project"], "objective": spec["objective"],
                "manifest_sha256": row[1], "status": state,
                "approved_by": row[3], "approved_at": row[4], "steps": steps}

    def advance(self, mission_id):
        self.queue.sweep_expired()
        spec, row, entries, _, jobs, summaries, state = self._view(mission_id)
        # The existing restore workflow deliberately disarms project roots.
        # Do not bypass its registration gate through the local factory API.
        if spec["project"] not in self.config["projects"]:
            raise FactoryError("Restored project requires explicit reapproval before advancing")
        if state in ("approved", "failed", "review_required"):
            if state != "approved":
                self.seal(mission_id)
            return self.inspect(mission_id)
        current = {item["step_id"]: item for item in summaries}
        for step in spec["steps"]:
            sid = step["id"]
            if entries[sid]["job_id"] is not None:
                continue
            if not all(current[d]["verification"] == "verified" for d in step["depends_on"]):
                continue
            target, _ = self._route(step)
            generation = entries[sid]["generation"]
            key = "pf-" + _hash(row[1] + ":" + sid + ":" + str(generation))[:40]
            queued = self.queue.submit(step["kind"], step["payload"], project=spec["project"],
                                       idempotency_key=key, target_node=target,
                                       max_attempts=step.get("max_attempts", 3), local_only=True)
            with self._db(write=True) as db:
                updated = db.execute("UPDATE factory_steps SET job_id=? WHERE mission_id=? "
                                     "AND step_id=? AND generation=? AND job_id IS NULL",
                                     (queued["id"], mission_id, sid, generation)).rowcount
                if updated:
                    db.execute("INSERT OR IGNORE INTO factory_dispatches VALUES(?,?,?,?,?)",
                               (mission_id, sid, generation, queued["id"], _utc()))
                    db.execute("INSERT INTO factory_events(mission_id,step_id,kind,recorded_at,reference) "
                               "VALUES(?,?,?,?,?)", (mission_id, sid, "job_dispatched", _utc(), queued["id"]))
        final = self.inspect(mission_id)
        if final["status"] in ("review_required", "failed"):
            self.seal(mission_id)
        return final

    def retry(self, mission_id, step_id):
        _name(step_id, "step id")
        spec, row, entries, _, jobs, summaries, state = self._view(mission_id)
        if spec["project"] not in self.config["projects"]:
            raise FactoryError("Restored project requires explicit reapproval before retry")
        if state == "approved":
            raise FactoryError("Approved missions are immutable")
        match = next((x for x in summaries if x["step_id"] == step_id), None)
        if not match or match["state"] not in ("failed", "cancelled", "verification_failed"):
            raise FactoryError("Only a terminal failed step can be manually retried")
        generation = entries[step_id]["generation"]
        if generation >= MAX_RETRIES:
            raise FactoryError("Manual retry limit reached; create a new mission revision")
        with self._db(write=True) as db:
            updated = db.execute("UPDATE factory_steps SET generation=generation+1,job_id=NULL "
                                 "WHERE mission_id=? AND step_id=? AND generation=? AND job_id=?",
                                 (mission_id, step_id, generation, entries[step_id]["job_id"])).rowcount
            if not updated:
                raise FactoryError("Concurrent mission change; inspect and retry")
            db.execute("INSERT INTO factory_events(mission_id,step_id,kind,recorded_at,reference) "
                       "VALUES(?,?,?,?,?)", (mission_id, step_id, "operator_retry", _utc(),
                                             entries[step_id]["job_id"]))
        return self.advance(mission_id)

    def _packet(self, mission_id):
        spec, row, entries, history, jobs, steps, state = self._view(mission_id)
        if state not in ("failed", "review_required", "approved"):
            raise FactoryError("Mission is still active; evidence cannot be sealed")
        past = []
        for sid, generation, jid, created in history:
            j = self.queue.get(jid)
            past.append({"step_id": sid, "generation": generation, "job_id": jid,
                         "dispatch_recorded_at": created, "status": j["status"] if j else "missing",
                         "result_sha256": _hash(_canonical(j["result"])) if j and j["result"] is not None else None})
        records = []
        for definition, summary in zip(spec["steps"], steps):
            j = jobs[definition["id"]]
            records.append({"step_id": definition["id"], "job_id": summary["job_id"],
                            "kind": definition["kind"],
                            "input_sha256": _hash(_canonical(definition["payload"])),
                            "worker": summary["assigned_node"], "worker_version": "not_attested",
                            "status": summary["state"], "attempts": summary["attempts"],
                            "created_at": j["created_at"] if j else None,
                            "updated_at": j["updated_at"] if j else None,
                            "error": summary["error"], "verification": summary["verification"],
                            "result": j["result"] if j else None,
                            "result_sha256": _hash(_canonical(j["result"])) if j and j["result"] is not None else None})
        return {"format": "odysseus-project-factory-evidence", "version": 1,
                "factory_version": VERSION, "operator_python_version": platform.python_version(),
                "mission_id": mission_id, "mission_sha256": row[1],
                "mission_created_at": row[2], "sealed_at": _utc(), "status": state,
                "objective": spec["objective"], "repository": spec["repository"],
                "declared_inputs_unverified": spec["inputs"],
                "expected_outputs": spec["expected_outputs"],
                "acceptance_criteria": spec["acceptance_criteria"],
                "steps": records, "dispatch_history": past,
                "approval": {"reviewer": row[3], "recorded_at": row[4]},
                "authority": "Queue worker-reported outputs; verification is per-step and does not attest physical hardware",
                "physical_nodes_verified": False,
                "production_deployed": False}

    def seal(self, mission_id):
        packet = self._packet(mission_id)
        spec, row, entries, _, _, _, state = self._view(mission_id)
        revision = sum(entry["generation"] for entry in entries.values())
        with self._db(write=True) as db:
            existing = db.execute("SELECT sha256 FROM factory_packets WHERE mission_id=? "
                                  "AND revision=? AND stage=?", (mission_id, revision, state)).fetchone()
            if existing:
                return existing[0]
            data = _canonical(packet)
            if len(data.encode("utf-8")) > MAX_EVIDENCE_BYTES:
                raise FactoryError("Evidence packet exceeds the configured size limit")
            digest = _hash(data)
            db.execute("INSERT INTO factory_packets(mission_id,revision,stage,sealed_at,sha256,packet_json) "
                       "VALUES(?,?,?,?,?,?)", (mission_id, revision, state, _utc(), digest, data))
            db.execute("INSERT INTO factory_events(mission_id,kind,recorded_at,reference) VALUES(?,?,?,?)",
                       (mission_id, "evidence_sealed", _utc(), digest))
        return digest

    def _latest(self, mission_id):
        with self._db() as db:
            row = db.execute("SELECT stage,sha256,packet_json FROM factory_packets "
                             "WHERE mission_id=? ORDER BY seq DESC LIMIT 1", (mission_id,)).fetchone()
        if row is None:
            raise FactoryError("No sealed evidence; reconcile the mission first")
        if _hash(row[2]) != row[1]:
            raise FactoryError("Stored evidence digest is inconsistent")
        return {"stage": row[0], "sha256": row[1], "packet": json.loads(row[2])}

    def approve(self, mission_id, reviewer, evidence_sha256, *, accept_reported=False):
        _text(reviewer, "reviewer", 128)
        if not isinstance(evidence_sha256, str) or not HEX64.fullmatch(evidence_sha256):
            raise FactoryError("Approval requires the exact reviewed evidence SHA-256")
        spec, row, entries, _, _, summaries, state = self._view(mission_id)
        if spec["project"] not in self.config["projects"]:
            raise FactoryError("Restored project requires explicit reapproval before approval")
        packet = self._latest(mission_id)
        if state != "review_required" or packet["stage"] != state or packet["sha256"] != evidence_sha256:
            raise FactoryError("Approval requires current review-ready evidence with matching hash")
        if any(x["verification"] == "reported_only" for x in summaries) and not accept_reported:
            raise FactoryError("Model output requires explicit --accept-reported acknowledgement")
        with self._db(write=True) as db:
            updated = db.execute("UPDATE factory_missions SET approved_by=?,approved_at=? "
                                 "WHERE mission_id=? AND approved_at IS NULL",
                                 (reviewer, _utc(), mission_id)).rowcount
            if not updated:
                raise FactoryError("Mission was already approved")
            db.execute("INSERT INTO factory_events(mission_id,kind,recorded_at,reference) VALUES(?,?,?,?)",
                       (mission_id, "human_approval", _utc(), evidence_sha256))
        final_hash = self.seal(mission_id)
        return {"mission_id": mission_id, "status": "approved", "approved_by": reviewer,
                "approved_evidence_sha256": evidence_sha256, "final_evidence_sha256": final_hash,
                "grants_execution_authority": False}

    def export(self, mission_id, destination):
        self._mission(mission_id)
        latest = self._latest(mission_id)
        path = Path(destination).expanduser().absolute()
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise FactoryError("Evidence destination must not traverse symbolic links")
        if not path.parent.is_dir():
            raise FactoryError("Evidence parent folder must exist")
        packet = {"packet": latest["packet"], "sha256": latest["sha256"]}
        text = json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        import os
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        return {"mission_id": mission_id, "stage": latest["stage"],
                "path": str(path), "packet_sha256": latest["sha256"],
                "contains_private_results": True}

    @staticmethod
    def verify_export(path):
        target = Path(path).expanduser().absolute()
        if target.is_symlink() or target.stat().st_size > MAX_EVIDENCE_BYTES + 1024 * 1024:
            raise FactoryError("Invalid or oversized evidence file")
        data = json.loads(target.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"packet", "sha256"}
                or not isinstance(data["sha256"], str) or not HEX64.fullmatch(data["sha256"])
                or _hash(_canonical(data["packet"])) != data["sha256"]):
            raise FactoryError("Evidence hash mismatch")
        return {"sha256": data["sha256"], "integrity": "passed",
                "authenticity": "not_proven_without_trusted_external_anchor",
                "mission_id": data["packet"].get("mission_id")}
