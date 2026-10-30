"""Queue-side enforcement for opt-in dispatcher permits, including crash gaps.

This module has no Factory/Queue imports. Non-dispatcher jobs retain their
existing policy. Registered dispatcher missions are fenced at claim and result
acceptance, even when the coordinator is not running after a restore.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def _rows(db, *, project=None, mission_id=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_dispatch_permits'").fetchone():
        return None
    where, args = [], []
    if project is not None:
        where.append("m.project=?")
        args.append(project)
    if mission_id is not None:
        where.append("m.mission_id=?")
        args.append(mission_id)
    sql = ("SELECT m.mission_id,m.project,m.spec_hash,m.spec_json,s.step_id,s.generation,"
           "p.mission_id AS permit_id,p.enabled,p.manifest_sha256,p.root_sha256 "
           "FROM factory_missions m JOIN factory_steps s ON s.mission_id=m.mission_id "
           "LEFT JOIN durable_dispatch_permits p ON p.mission_id=m.mission_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.execute(sql, args).fetchall()


def _keys(row):
    generation = row["generation"]
    if type(generation) is not int or not 0 <= generation <= 100:
        return ()
    return tuple("pf-" + hashlib.sha256(
        (row["spec_hash"] + ":" + row["step_id"] + ":" + str(g)).encode()).hexdigest()[:40]
                 for g in range(generation + 1))


def job_allowed(db, job, projects):
    """Match deterministic admission keys even before Factory records job_id."""
    if job is None:
        return False
    key = job["idempotency_key"]
    if not key.startswith("pf-"):
        return True
    rows = _rows(db, project=job["project"])
    if rows is None:
        return True  # Existing Factory installations without the opt-in extension.
    for row in rows:
        if key not in _keys(row):
            continue
        if row["permit_id"] is None:
            return True  # Explicit/manual Factory missions retain their original behavior.
        root = projects.get(row["project"]) if isinstance(projects, dict) else None
        if not row["enabled"] or root is None:
            return False
        try:
            spec = json.loads(row["spec_json"])
            return (row["spec_hash"] == row["manifest_sha256"] == _digest(spec)
                    and spec["project"] == row["project"]
                    and _digest(str(Path(root).resolve())) == row["root_sha256"])
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            return False
    return False  # Unknown Factory identity is not made executable by recovery.


def mission_job_ids(db, mission_id):
    """Find linked and committed-but-unlinked jobs without trusting mutable spec text."""
    rows = _rows(db, mission_id=mission_id) or []
    result = set()
    for row in rows:
        for key in _keys(row):
            match = db.execute("SELECT id FROM jobs WHERE project=? AND idempotency_key=?",
                               (row["project"], key)).fetchone()
            if match:
                result.add(match[0])
    return sorted(result)
