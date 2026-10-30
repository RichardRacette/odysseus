"""Independent fixed-output verification and minimal RichieOS execution receipts.

Hashes detect alteration against a trusted anchor; they do not attest authorship,
physical hardware, authorization identity, or human acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .factory import validate_mission

FIXED_KINDS = frozenset({"text.hash", "json.validate"})
MAX_FILE_BYTES = 18 * 1024 * 1024
AUTH_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:./-]{0,199}\Z")


class ReceiptError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ReceiptError("Receipt inputs must not traverse symbolic links")
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ReceiptError("Missing or oversized receipt input")
    try:
        def reject(_value):
            raise ValueError("Non-finite JSON")
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReceiptError("Receipt input is not finite JSON") from exc


def _expected_result(step):
    text = step["payload"]["text"]
    if step["kind"] == "text.hash":
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != step["expected_sha256"]:
            raise ReceiptError("Trusted manifest hash does not match its input")
        return {"sha256": actual, "bytes": len(text.encode("utf-8"))}
    if step["kind"] == "json.validate":
        def reject(_value):
            raise ValueError("Non-finite JSON")
        try:
            value = json.loads(text, parse_constant=reject)
        except (ValueError, RecursionError) as exc:
            raise ReceiptError("Trusted JSON input is invalid") from exc
        if not isinstance(value, dict) or any(
                k not in value for k in step["payload"].get("required_keys", [])):
            raise ReceiptError("Trusted JSON input is missing required fields")
        return {"valid": True, "keys": sorted(value)}
    raise ReceiptError("Only independently checkable fixed jobs earn verified receipts")


def build_receipt(evidence, manifest, authorization):
    """Recompute results from the trusted manifest, independently of worker verdicts."""
    validate_mission(manifest)
    if (not isinstance(evidence, dict) or set(evidence) != {"packet", "sha256"}
            or not isinstance(evidence["packet"], dict)
            or digest(evidence["packet"]) != evidence["sha256"]):
        raise ReceiptError("Factory evidence hash mismatch")
    packet = evidence["packet"]
    if (packet.get("format") != "odysseus-project-factory-evidence"
            or type(packet.get("version")) is not int or packet["version"] != 1
            or packet.get("mission_id") != manifest["mission_id"]
            or packet.get("mission_sha256") != digest(manifest)
            or packet.get("repository") != manifest["repository"]
            or packet.get("status") not in {"review_required", "approved"}):
        raise ReceiptError("Evidence does not match the trusted completed mission")
    if (not isinstance(authorization, dict) or set(authorization) != {"reference", "recorded_at"}
            or not isinstance(authorization.get("reference"), str)
            or not AUTH_REF.fullmatch(authorization["reference"])
            or not isinstance(authorization.get("recorded_at"), str)
            or not re.fullmatch(r"[0-9T:.+Z-]{10,40}", authorization["recorded_at"])):
        raise ReceiptError("Invalid execution authorization reference")
    records = packet.get("steps")
    if not isinstance(records, list) or len(records) != len(manifest["steps"]):
        raise ReceiptError("Evidence step set mismatch")
    steps, job_ids = [], set()
    for spec, result in zip(manifest["steps"], records):
        if not isinstance(result, dict):
            raise ReceiptError("Invalid step evidence")
        expected = _expected_result(spec)
        job_id = result.get("job_id")
        if (result.get("step_id") != spec["id"] or result.get("kind") != spec["kind"]
                or result.get("status") != "succeeded" or result.get("verification") != "verified"
                or canonical(result.get("result")) != canonical(expected)
                or result.get("result_sha256") != digest(expected)
                or result.get("input_sha256") != digest(spec["payload"])
                or not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id)
                or job_id in job_ids or type(result.get("attempts")) is not int
                or not 1 <= result["attempts"] <= spec.get("max_attempts", 3)
                or not isinstance(result.get("worker"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", result["worker"])
                or (spec.get("target_node") is not None and spec["target_node"] != result["worker"])):
            raise ReceiptError("Independent step-result verification failed")
        job_ids.add(job_id)
        steps.append({"step_id": spec["id"], "kind": spec["kind"], "job_id": job_id,
                      "worker": result["worker"], "attempts": result["attempts"],
                      "input_sha256": digest(spec["payload"]),
                      "result_sha256": digest(expected), "verification": "VERIFIED"})
    return {"format": "richieos-execution-receipt", "schema_version": 1,
            "mission_id": manifest["mission_id"], "project": manifest["project"],
            "repository": manifest["repository"], "manifest_sha256": digest(manifest),
            "evidence_sha256": evidence["sha256"], "authorization": authorization,
            "verification": "VERIFIED", "factory_status": packet["status"], "steps": steps,
            "human_acceptance": "PENDING_HUMAN", "production_deployed": False,
            "physical_nodes_verified": False,
            "authority": "Verified fixed outputs; authorship, authorization identity and hardware are not attested"}


def verify_receipt(receipt_path, evidence_path, manifest):
    envelope = read_json(receipt_path)
    if (not isinstance(envelope, dict) or set(envelope) != {"receipt", "sha256"}
            or not isinstance(envelope["receipt"], dict)
            or digest(envelope["receipt"]) != envelope["sha256"]):
        raise ReceiptError("Execution receipt hash mismatch")
    expected = build_receipt(read_json(evidence_path), manifest,
                             envelope["receipt"].get("authorization"))
    if canonical(envelope["receipt"]) != canonical(expected):
        raise ReceiptError("Execution receipt does not match independently verified evidence")
    return {"mission_id": expected["mission_id"], "project": expected["project"],
            "verification": "VERIFIED", "receipt_sha256": envelope["sha256"],
            "evidence_sha256": expected["evidence_sha256"], "steps_verified": len(expected["steps"]),
            "human_acceptance": "PENDING_HUMAN", "authenticity": "requires_trusted_external_anchor"}


def render_report(receipt, receipt_sha256):
    """Only identifiers, pins, counts and hashes; no payloads, outputs, paths or tokens."""
    lines = ["# Durable dispatcher execution receipt", "", "Evidence state: VERIFIED", "",
             "- Mission: `" + receipt["mission_id"] + "`",
             "- Project: `" + receipt["project"] + "`",
             "- Source revision: `" + receipt["repository"]["revision"] + "`",
             "- Execution authorization reference: `" + receipt["authorization"]["reference"] + "`",
             "- Independently verified fixed-output steps: " + str(len(receipt["steps"])),
             "- Receipt SHA-256: `" + receipt_sha256 + "`",
             "- Private evidence SHA-256: `" + receipt["evidence_sha256"] + "`", "",
             "Human acceptance: PENDING_HUMAN. Production deployment and physical host acceptance are not asserted.",
             "This receipt checks declared fixed outputs against a trusted manifest. Its hashes require a trusted external anchor; they do not attest authorship or hardware.", ""]
    return "\n".join(lines)
