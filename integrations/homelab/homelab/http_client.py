"""Bounded authenticated client. Redirects and environment proxies are disabled."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError, read_token, validate_server_url


class APIError(RuntimeError):
    def __init__(self, status: int, code: str = 'request_failed'):
        self.status = status
        self.code = code
        super().__init__(f'Hub request failed ({status}: {code}).')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, server_url: str, token_file: str | Path, timeout: float = 10):
        self.server_url = validate_server_url(server_url)
        self._token = read_token(token_file)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method: str, path: str, data: dict | None = None) -> dict:
        if (method not in {'GET', 'POST'} or not path.startswith('/') or path.startswith('//')
                or any(ord(c) < 32 for c in path) or '#' in path):
            raise ConfigError('Invalid API method or relative path.')
        body = None if data is None else json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if body is not None and len(body) > 262144:
            raise APIError(413, 'request_too_large')
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.server_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read(1048577)
                if len(content) > 1048576:
                    raise APIError(502, 'response_too_large')
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise APIError(502, 'invalid_response')
                return value
        except urllib.error.HTTPError as exc:
            code = 'http_error'
            try:
                details = json.loads(exc.read(8192))
                candidate = details.get('error', '') if isinstance(details, dict) else ''
                if isinstance(candidate, str) and candidate.replace('_', '').isalnum() and len(candidate) < 80:
                    code = candidate
            except (ValueError, OSError):
                pass
            finally:
                exc.close()
            raise APIError(exc.code, code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError(0, 'unreachable') from None
        except (ValueError, UnicodeError):
            raise APIError(502, 'invalid_response') from None
