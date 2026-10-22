"""Authenticated pull worker for three fixed, local-only capabilities.

There is no command, script, executable path, or arbitrary URL job type. Ollama
must also be started in local-only mode by the operator; inventory checks here
are an additional guard against cloud-backed model aliases.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from .queue import MAX_RESULT_BYTES, PolicyError, _json, validate_model, validate_payload


OLLAMA_TIMEOUT_SECONDS = 120
OLLAMA_PROBE_TIMEOUT_SECONDS = 10


class WorkerError(RuntimeError):
    """A sanitized hub/transport failure suitable for operator diagnostics."""


class _JobFailure(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _loopback_url(value):
    if not isinstance(value, str) or value != value.strip() or any(ord(char) < 32 for char in value):
        raise _JobFailure("Ollama URL must be an explicit loopback origin")
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or not parsed.hostname):
            raise ValueError
        host = parsed.hostname
        # Pin localhost to a literal loopback address, avoiding hostname/proxy
        # interpretation differences and any second DNS resolution.
        if host == "localhost":
            host = "127.0.0.1"
        address = ipaddress.ip_address(host)
        if not address.is_loopback or "%" in host:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = f"[{host}]" if address.version == 6 else host
        if port is not None:
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, TypeError):
        raise _JobFailure("Ollama URL must be an explicit loopback origin") from None


def _strict_json(text):
    def reject_constant(_value):
        raise ValueError("Non-finite JSON value")
    return json.loads(text, parse_constant=reject_constant)


def _http_json(origin, path, payload=None, *, timeout):
    data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(origin + path, data=data, headers=headers)
    # An environment proxy could otherwise send even a loopback prompt to a
    # remote service. Redirect rejection applies to discovery and generation.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESULT_BYTES + 1)
        if len(raw) > MAX_RESULT_BYTES:
            raise _JobFailure("Local Ollama response exceeded the size limit")
        try:
            decoded = _strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise _JobFailure("Local Ollama returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise _JobFailure("Local Ollama returned an invalid response object")
        return decoded
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if 300 <= code <= 399:
            raise _JobFailure("Local Ollama redirects are not allowed") from None
        raise _JobFailure(f"Local Ollama returned HTTP {code}", retryable=code in (408, 425, 429, 500, 502, 503, 504)) from None
    except (TimeoutError, socket.timeout):
        raise _JobFailure("Local Ollama request timed out", retryable=True) from None
    except (urllib.error.URLError, OSError):
        raise _JobFailure("Local Ollama is unavailable", retryable=True) from None


def _has_remote_metadata(value):
    if isinstance(value, dict):
        return any(str(key).lower() in ("remote_host", "remote_model") or _has_remote_metadata(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_has_remote_metadata(item) for item in value)
    return False


def _check_lease(lost):
    if lost.is_set():
        raise _JobFailure("Worker lease could not be renewed")


def _local_model_inventory(origin, model, lost):
    tags = _http_json(origin, "/api/tags", timeout=OLLAMA_PROBE_TIMEOUT_SECONDS)
    models = tags.get("models")
    if not isinstance(models, list):
        raise _JobFailure("Local model inventory is unavailable")
    matches = [item for item in models if isinstance(item, dict) and model in (item.get("name"), item.get("model"))]
    if len(matches) != 1:
        raise _JobFailure("Requested model is not uniquely installed locally")
    entry = matches[0]
    size = entry.get("size")
    if (isinstance(size, bool) or not isinstance(size, int) or size <= 0
            or _has_remote_metadata(entry)
            or any(entry.get(key, model) != model for key in ("name", "model"))):
        raise _JobFailure("Requested model is not verified as a local model")
    _check_lease(lost)
    details = _http_json(origin, "/api/show", {"model": model}, timeout=OLLAMA_PROBE_TIMEOUT_SECONDS)
    if _has_remote_metadata(details) or "error" in details or not isinstance(details.get("details"), dict):
        raise _JobFailure("Requested model metadata does not verify local execution")
    _check_lease(lost)


def _usage(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2 ** 63:
        raise _JobFailure("Local Ollama returned invalid usage counts")
    return value


def _execute(job, ollama_url, allowed_models, lost):
    if job.get("local_only") is not True:
        raise _JobFailure("Only local-only jobs can execute")
    kind, payload = job.get("kind"), job.get("payload")
    validate_payload(kind, payload)
    _check_lease(lost)
    if kind == "text.hash":
        data = payload["text"].encode("utf-8")
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if kind == "json.validate":
        try:
            value = _strict_json(payload["text"])
        except (ValueError, RecursionError):
            raise _JobFailure("Input is not valid finite JSON") from None
        if not isinstance(value, dict):
            raise _JobFailure("JSON input must be an object")
        if any(key not in value for key in payload.get("required_keys", [])):
            raise _JobFailure("JSON object is missing required keys")
        return {"valid": True, "keys": sorted(value)}

    model = payload["model"]
    if not isinstance(allowed_models, (list, tuple, set, frozenset)):
        raise _JobFailure("A worker model allowlist is required")
    for allowed in allowed_models:
        validate_model(allowed)
    if model not in allowed_models:
        raise _JobFailure("Requested model is not allowed by this worker")
    origin = _loopback_url(ollama_url)
    _local_model_inventory(origin, model, lost)
    request = {
        "model": model,
        "prompt": payload["prompt"],
        "stream": False,
        "options": {"num_predict": payload.get("max_tokens", 512), "num_ctx": 4096},
    }
    if payload.get("format") == "json":
        request["format"] = "json"
    response = _http_json(origin, "/api/generate", request, timeout=OLLAMA_TIMEOUT_SECONDS)
    if ("error" in response or response.get("done") is not True or response.get("model") != model
            or not isinstance(response.get("response"), str)):
        raise _JobFailure("Local Ollama did not return a complete result for the requested model")
    return {"model": model, "text": response["response"],
            "usage": {"input_tokens": _usage(response.get("prompt_eval_count")),
                      "output_tokens": _usage(response.get("eval_count"))}}


def _hub_request(client, path, data):
    try:
        return client.request("POST", path, data)
    except Exception:
        # Transport exceptions can contain URLs, credentials, or server bodies.
        raise WorkerError("Hub request failed; inspect hub availability and worker authorization") from None


def _interval(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise PolicyError(f"Invalid {name}")
    return value


def run_once(client, node_id, *, ollama_url="http://127.0.0.1:11434", allowed_models=None, heartbeat_interval=15):
    """Claim and process at most one job; return False when no job is available.

    node_id is a local label only. The server derives identity from the client's
    scoped token; no node identity is sent in request bodies. A heartbeat runs
    in a separate thread during blocking inference. If renewal fails, the
    result is discarded and the durable lease/retry policy handles recovery.
    """
    _interval(heartbeat_interval, "heartbeat_interval", 20)
    response = _hub_request(client, "/v1/worker/claim", {})
    if not isinstance(response, dict) or "job" not in response:
        raise WorkerError("Hub returned an invalid claim response")
    job = response["job"]
    if job is None:
        return False
    if (not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]
            or not isinstance(job.get("lease_token"), str) or not job["lease_token"]):
        raise WorkerError("Hub returned an invalid leased job")
    identity = {"job_id": job["id"], "lease_token": job["lease_token"]}
    stopped = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stopped.wait(heartbeat_interval):
            try:
                _hub_request(client, "/v1/worker/heartbeat", identity)
            except WorkerError:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, name="homelab-lease-heartbeat", daemon=True)
    thread.start()
    result = None
    failure = None
    try:
        result = _execute(job, ollama_url, allowed_models, lost)
        _json(result, MAX_RESULT_BYTES, "Result")
    except PolicyError:
        failure = ("Local execution policy rejected the job or result", False)
    except _JobFailure as exc:
        failure = (str(exc), exc.retryable)
    except Exception:
        failure = ("Local job execution failed", False)
    finally:
        stopped.set()
        thread.join(timeout=1)
    if lost.is_set():
        return True
    if failure:
        _hub_request(client, "/v1/worker/fail", {**identity, "error": failure[0], "retryable": failure[1]})
    else:
        _hub_request(client, "/v1/worker/complete", {**identity, "result": result})
    return True


def run_worker(client, node_id, *, ollama_url="http://127.0.0.1:11434", allowed_models=None,
               heartbeat_interval=15, poll_interval=2, stop_event=None):
    """Poll until stopped, retaining work in the hub through transient outages."""
    _interval(poll_interval, "poll_interval", 60)
    _interval(heartbeat_interval, "heartbeat_interval", 20)
    stop_event = stop_event if stop_event is not None else threading.Event()
    while not stop_event.is_set():
        try:
            worked = run_once(client, node_id, ollama_url=ollama_url, allowed_models=allowed_models,
                              heartbeat_interval=heartbeat_interval)
        except WorkerError:
            worked = False
        if not worked:
            stop_event.wait(poll_interval)
