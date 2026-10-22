"""Optional, loopback-only Ollama embeddings. No downloads or startup requests."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class EmbeddingError(RuntimeError):
    """A visible embedding failure; callers may retain lexical retrieval."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalized_vector(vector, dimension=None):
    """Validate an untrusted vector and return finite unit-length floats."""
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 8192:
        raise EmbeddingError("Embedding has an invalid dimension")
    if dimension is not None and len(vector) != dimension:
        raise EmbeddingError("Embedding dimension changed; rebuild with one model")
    try:
        invalid = any(isinstance(value, bool) or not isinstance(value, (int, float))
                      or not math.isfinite(value) for value in vector)
    except OverflowError:
        invalid = True
    if invalid:
        raise EmbeddingError("Embedding contains non-finite or non-numeric values")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("Embedding has zero or invalid magnitude")
    return [float(value / norm) for value in vector]


def _model_name(name):
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def _has_remote_metadata(value):
    if isinstance(value, dict):
        return any(str(key).lower() in ("remote_host", "remote_model") or _has_remote_metadata(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_has_remote_metadata(item) for item in value)
    return False


class OllamaEmbedder:
    """Use a separately installed local model through Ollama's native API.

    ``fingerprint()`` refreshes the model digest. Call once before each indexing
    or search operation; ``embed()`` also initializes it on first use. Unknown
    digests have a distinct fingerprint and a visible warning, so verified and
    unverified indexes are never silently mixed.
    """

    def __init__(self, base_url, model="embeddinggemma", timeout=30):
        if not isinstance(base_url, str):
            raise ValueError("Invalid Ollama base URL")
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid Ollama base URL") from exc
        if (parsed.scheme not in ("http", "https") or not hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError("Ollama must use an uncredentialed loopback base URL")
        if hostname.lower() == "localhost":
            hostname = "127.0.0.1"  # Do not resolve a hostname to send document text.
        try:
            if "%" in hostname or not ipaddress.ip_address(hostname).is_loopback:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("Only literal loopback Ollama endpoints are allowed") from exc
        host = f"[{hostname}]" if ":" in hostname else hostname
        self.base_url = urlunsplit((parsed.scheme, host + (f":{port}" if port else ""), "", "", ""))
        if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model)
                or "cloud" in model.lower() or "://" in model or ".." in model):
            raise ValueError("Use an explicit local Ollama embedding model name")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("Embedding timeout must be between zero and 300 seconds")
        self.model = model
        self.timeout = timeout
        self.dimension = None
        self.warnings = []
        self._fingerprint = None
        self._local_confirmed = False
        # ProxyHandler({}) deliberately ignores HTTP(S)_PROXY and system proxies.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path, body=None):
        payload = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        request = Request(self.base_url + path, data=payload,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise EmbeddingError("Ollama response exceeds the size limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise EmbeddingError("Ollama returned an invalid JSON object")
            return data
        except HTTPError as exc:
            raise EmbeddingError(f"Ollama HTTP {exc.code}; redirects are disabled") from None
        except (URLError, TimeoutError, OSError, ValueError, RecursionError):
            raise EmbeddingError("Ollama is unavailable or returned invalid JSON") from None

    def fingerprint(self):
        self.warnings = []
        self._local_confirmed = False
        digest = "unverified"
        try:
            models = self._request("/api/tags").get("models")
            if not isinstance(models, list):
                raise EmbeddingError("Ollama model inventory is invalid")
        except EmbeddingError:
            self.warnings.append("Model digest could not be verified; vectors use an unverified fingerprint.")
        else:
            matches = [item for item in models if isinstance(item, dict)
                       and _model_name(str(item.get("name", item.get("model", "")))) == _model_name(self.model)]
            if len(matches) != 1:
                raise EmbeddingError("Configured embedding model is not uniquely installed locally")
            model = matches[0]
            size = model.get("size")
            if (isinstance(size, bool) or not isinstance(size, int) or size <= 0
                    or _has_remote_metadata(model)
                    or any(_model_name(str(model[key])) != _model_name(self.model)
                           for key in ("name", "model") if key in model)):
                raise EmbeddingError("Embedding inventory does not verify locally stored model weights")
            details = self._request("/api/show", {"model": self.model})
            if _has_remote_metadata(details) or "error" in details or not isinstance(details.get("details"), dict):
                raise EmbeddingError("Remote/cloud or unverifiable embedding models are not allowed")
            self._local_confirmed = True
            if isinstance(model.get("digest"), str) and model["digest"]:
                digest = model["digest"]
            else:
                self.warnings.append("Model inventory has no digest; vectors use an unverified fingerprint.")
        raw = json.dumps({"model": _model_name(self.model), "digest": digest,
                          "preprocessing": self._preprocessing()}, sort_keys=True)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        if self._fingerprint != fingerprint:
            self.dimension = None
        self._fingerprint = fingerprint
        return fingerprint

    def _preprocessing(self):
        return "embeddinggemma-retrieval-v1" if self.model.split(":")[0].rsplit("/", 1)[-1] == "embeddinggemma" else "raw-v1"

    def embed(self, texts, *, purpose="document"):
        if purpose not in ("document", "query"):
            raise ValueError("Embedding purpose must be document or query")
        if not isinstance(texts, (list, tuple)) or not 1 <= len(texts) <= 64:
            raise ValueError("Embed between one and 64 text strings at a time")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 16000 for text in texts):
            raise ValueError("Embedding inputs must be nonempty text of at most 16000 characters")
        if self._fingerprint is None:
            self.fingerprint()
        if not self._local_confirmed:
            raise EmbeddingError("Local model inventory is unavailable; no document text was sent")
        prefix = ""
        if self._preprocessing() == "embeddinggemma-retrieval-v1":
            prefix = "task: search result | query: " if purpose == "query" else "title: none | text: "
        data = self._request("/api/embed", {"model": self.model, "input": [prefix + text for text in texts],
                                           "truncate": False, "keep_alive": "1m"})
        response_model = data.get("model")
        if response_model is not None and _model_name(str(response_model)) != _model_name(self.model):
            raise EmbeddingError("Ollama returned embeddings from a different model")
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError("Ollama returned the wrong number of embeddings")
        result = []
        for vector in vectors:
            normalized = normalized_vector(vector, self.dimension)
            if self.dimension is None:
                self.dimension = len(normalized)
            result.append(normalized)
        return result
