"""Private loopback hub; tokens carry roles rather than client-supplied identities."""
from __future__ import annotations

import hmac
import json
import re
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import ConfigError, read_token
from .knowledge import KnowledgeStore
from .queue import LeaseError, QueueError, QueueStore


def public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if 'token' not in k and not k.startswith('_')}


class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True
    request_queue_size = 16

    def __init__(self, config: dict):
        if config.get('role') != 'hub' or config.get('bind') != '127.0.0.1':
            raise ConfigError('A loopback hub configuration is required.')
        self.config = config
        self.slots = threading.BoundedSemaphore(16)
        self.identities = [(read_token(config['admin_token_file']), 'admin', None)]
        self.identities += [(read_token(n['token_file']), 'worker', name) for name, n in config['nodes'].items()]
        self.identities += [(read_token(n['token_file']), 'reader', project) for project, n in config['readers'].items()]
        state = Path(config['state_dir'])
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.queue = QueueStore(state / 'queue.sqlite3', config['nodes'])
        embedder = None
        embedding_config = config.get('embeddings', {})
        if embedding_config.get('enabled', False):
            from .embeddings import OllamaEmbedder
            embedder = OllamaEmbedder(embedding_config['base_url'], model=embedding_config['model'])
        self.knowledge = KnowledgeStore(state / 'knowledge.sqlite3', config['projects'], embedder=embedder)
        super().__init__(('127.0.0.1', config.get('port', 8765)), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        super().server_close()
        close = getattr(self.knowledge, 'close', None)
        if close:
            close()


class RequestError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'HomeLab/0.1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Request paths can contain private searches; operational output is status-only.
        pass

    def _send(self, status: int, value: dict):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _identity(self) -> tuple[str, str | None]:
        if self.headers.get('Origin') is not None:
            raise RequestError(403, 'browser_origin_refused')
        auth = self.headers.get_all('Authorization', [])
        if len(auth) != 1 or not auth[0].startswith('Bearer '):
            raise RequestError(401, 'authentication_required')
        supplied = auth[0][7:]
        if len(supplied) > 256 or not supplied.isascii():
            raise RequestError(401, 'invalid_credential')
        for token, role, subject in self.server.identities:
            if hmac.compare_digest(supplied, token):
                return role, subject
        raise RequestError(401, 'invalid_credential')

    def _body(self) -> dict:
        if self.headers.get('Transfer-Encoding'):
            raise RequestError(400, 'unsupported_encoding')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise RequestError(411, 'length_required')
        try:
            length = int(lengths[0])
        except ValueError:
            raise RequestError(400, 'invalid_length') from None
        if length < 0 or length > 262144:
            raise RequestError(413, 'request_too_large')
        if self.headers.get_content_type() != 'application/json':
            raise RequestError(415, 'json_required')
        def invalid_constant(value):
            raise ValueError('Nonfinite JSON')
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, 'incomplete_body')
        try:
            value = json.loads(data, parse_constant=invalid_constant)
        except (ValueError, UnicodeError):
            raise RequestError(400, 'invalid_json') from None
        if not isinstance(value, dict):
            raise RequestError(400, 'object_required')
        return value

    @staticmethod
    def _fields(body: dict, allowed: set, required: set = frozenset()):
        if set(body) - allowed or required - set(body):
            raise RequestError(400, 'invalid_fields')

    def _project(self, query: dict, role: str, subject: str | None, required: bool = False):
        selected = query.get('project', [subject if role == 'reader' else None])[0]
        if role == 'reader' and selected != subject:
            raise RequestError(403, 'project_scope')
        if selected is not None and selected not in self.server.config['projects']:
            raise RequestError(400, 'unknown_project')
        if required and not selected:
            raise RequestError(400, 'project_required')
        return selected

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method: str):
        try:
            if len(self.path) > 8192:
                raise RequestError(414, 'url_too_long')
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc:
                raise RequestError(400, 'relative_path_required')
            path = parsed.path
            query = parse_qs(parsed.query, max_num_fields=12, keep_blank_values=True)
            if any(len(v) != 1 for v in query.values()):
                raise RequestError(400, 'duplicate_query')
            if method == 'GET' and path == '/health':
                return self._send(200, {'status': 'alive'})
            role, subject = self._identity()
            if method == 'GET':
                if role not in {'reader', 'admin'}:
                    raise RequestError(403, 'role_forbidden')
                return self._get(path, query, role, subject)
            body = self._body()
            if path.startswith('/v1/worker/'):
                if role != 'worker':
                    raise RequestError(403, 'role_forbidden')
                return self._worker(path, body, subject)
            if role != 'admin':
                raise RequestError(403, 'role_forbidden')
            return self._post(path, body)
        except RequestError as exc:
            self._send(exc.status, {'error': exc.code})
        except LeaseError:
            self._send(409, {'error': 'lease_conflict'})
        except (QueueError, ValueError, KeyError, TypeError):
            self._send(400, {'error': 'invalid_request'})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self._send(503, {'error': 'service_unavailable'})

    def _get(self, path, query, role, subject):
        queue = self.server.queue
        project = self._project(query, role, subject)
        if path == '/v1/status':
            queue.sweep_expired()
            if role == 'reader':
                jobs = queue.list_jobs(project=project, limit=500)
                value = {'project': project, 'jobs': dict(Counter(j['status'] for j in jobs)),
                         'counts_scope': 'latest_500', 'knowledge': self.server.knowledge.status(project)}
            else:
                value = {'queue': queue.status(), 'knowledge': self.server.knowledge.status(),
                         'cloud_execution': 'disabled', 'version': '0.1.0'}
            return self._send(200, value)
        if path == '/v1/jobs':
            limit = int(query.get('limit', ['50'])[0])
            if not 1 <= limit <= 100:
                raise RequestError(400, 'invalid_limit')
            summaries = [{k: v for k, v in public_job(j).items() if k not in {'payload', 'result', 'error'}}
                         for j in queue.list_jobs(project=project, limit=limit)]
            return self._send(200, {'jobs': summaries})
        if re.fullmatch(r'/v1/jobs/[A-Za-z0-9_-]+', path):
            job = queue.get(path.rsplit('/', 1)[1])
            if not job or (role == 'reader' and job['project'] != subject):
                raise RequestError(404, 'not_found')
            return self._send(200, {'job': public_job(job)})
        if path == '/v1/knowledge/search':
            project = self._project(query, role, subject, required=True)
            text = query.get('query', [''])[0]
            if not text or len(text) > 2000:
                raise RequestError(400, 'invalid_query')
            limit = int(query.get('limit', ['5'])[0])
            if not 1 <= limit <= 20:
                raise RequestError(400, 'invalid_limit')
            mode = query.get('mode', ['lexical'])[0]
            result = self.server.knowledge.search(project, text, limit=limit, mode=mode)
            result['trust'] = 'untrusted_source_evidence'
            return self._send(200, result)
        raise RequestError(404, 'not_found')

    def _post(self, path, body):
        if path == '/v1/jobs':
            self._fields(body, {'kind', 'payload', 'project', 'idempotency_key', 'target_node', 'max_attempts', 'local_only'},
                         {'kind', 'payload', 'project', 'idempotency_key'})
            if body['project'] not in self.server.config['projects']:
                raise RequestError(400, 'unknown_project')
            result = self.server.queue.submit(**body)
            return self._send(200, {'job': public_job(result)})
        if re.fullmatch(r'/v1/jobs/[A-Za-z0-9_-]+/cancel', path):
            self._fields(body, set())
            result = self.server.queue.cancel(path.split('/')[3])
            return self._send(200, {'job': public_job(result)})
        raise RequestError(404, 'not_found')

    def _worker(self, path, body, node):
        queue = self.server.queue
        if path == '/v1/worker/claim':
            self._fields(body, set())
            queue.node_seen(node)
            return self._send(200, {'job': queue.claim(node)})
        allowed = {'job_id', 'lease_token'}
        if path == '/v1/worker/complete':
            self._fields(body, allowed | {'result'}, allowed | {'result'})
            result = queue.complete(node, body['job_id'], body['lease_token'], body['result'])
        elif path == '/v1/worker/fail':
            self._fields(body, allowed | {'error', 'retryable'}, allowed | {'error'})
            result = queue.fail(node, body['job_id'], body['lease_token'], body['error'], retryable=body.get('retryable', False))
        elif path == '/v1/worker/heartbeat':
            self._fields(body, allowed, allowed)
            result = queue.heartbeat(node, body['job_id'], body['lease_token'])
        else:
            raise RequestError(404, 'not_found')
        return self._send(200, {'job': public_job(result)})


def make_server(config: dict) -> HubServer:
    return HubServer(config)
