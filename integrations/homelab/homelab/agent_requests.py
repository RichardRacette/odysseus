"""Offline assistant requests mapped to one reviewed Factory fixture capability.

This module prepares data only. It neither authenticates the named requester nor
submits, approves, dispatches, or executes a mission. It adds no queue or service.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .factory import FactoryError, validate_mission

MAX_REQUEST_BYTES = 4096
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
TEMPLATE_SHA256 = "00c64645aafb7a9c6fe03b986b6119095c52f39d9d1e6eff6ef15968cdd04b8d"
FIXTURE_SHA256 = "4d00dbf9a26892bb5a95bfe1f5a3426733ae1623a7e636b7d3f353039d4669de"
FIELDS = frozenset({"schema_version", "request_id", "requested_by", "capability"})
ACTORS = frozenset({"fae", "duet", "grok", "operator"})
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")


class RequestError(ValueError):
    """Bounded error that never includes untrusted payloads or filesystem paths."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RequestError("Duplicate JSON field")
        result[key] = value
    return result


def _nonfinite(_value):
    raise RequestError("Nonfinite JSON is forbidden")


def load_request(raw: bytes) -> dict:
    """Decode bounded, strict UTF-8 JSON and validate the complete request."""
    if not isinstance(raw, bytes) or len(raw) > MAX_REQUEST_BYTES:
        raise RequestError("Request exceeds byte limit or is not bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_nonfinite)
    except (UnicodeError, ValueError, RecursionError):
        raise RequestError("Request is not valid strict JSON") from None
    _validate_request(value)
    return value


def _validate_request(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise RequestError("Unexpected or missing request fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise RequestError("Unsupported request schema")
    if not isinstance(value["request_id"], str) or not REQUEST_ID.fullmatch(value["request_id"]):
        raise RequestError("Invalid request identifier")
    actor = value["requested_by"]
    if not isinstance(actor, str) or actor not in ACTORS:
        raise RequestError("Unknown requester label")
    if value["capability"] != "fixture.audit.v1":
        raise RequestError("Unsupported capability")


def _read_pinned(path, expected_hash, limit):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise RequestError("Reviewed capability source unavailable") from None
    if len(raw) > limit or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise RequestError("Reviewed capability source changed")
    return raw


def prepare_request(value: dict) -> dict:
    """Return deterministic preparation; caller attribution grants no authority."""
    _validate_request(value)
    # Copy the four validated primitives; no caller object survives by reference.
    request = {key: value[key] for key in sorted(FIELDS)}
    request_sha256 = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
    raw_template = _read_pinned(EXAMPLES / "project-factory-release-audit.json", TEMPLATE_SHA256, 65536)
    raw_fixture = _read_pinned(EXAMPLES / "validate.json", FIXTURE_SHA256, 65536)
    try:
        mission = json.loads(raw_template.decode("utf-8"))
        # Pin exact fixture bytes, including newlines, independently of the template.
        fixture = raw_fixture.decode("utf-8")
        if any(step["payload"]["text"] != fixture for step in mission["steps"]):
            raise RequestError("Reviewed capability input mismatch")
        mission["mission_id"] = "agent-" + request["request_id"]
        mission["inputs"].append({"reference": "agent-request:sha256:" + request_sha256,
                                  "sha256": request_sha256})
        canonical = validate_mission(mission)
    except (FactoryError, UnicodeError, KeyError, TypeError, ValueError):
        raise RequestError("Reviewed capability is invalid") from None
    return {
        "schema_version": 1,
        "request": request,
        "request_sha256": request_sha256,
        "mission": mission,
        "mission_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "state": "prepared_not_submitted",
        "requested_by_verified": False,
        "execution_authorized": False,
    }
