import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from homelab.config import add_project, init_state, load_config, read_token
from homelab.http_client import APIError, Client
from homelab.server import make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        path = init_state(root / 'state', port=0)
        for project in ['alpha', 'beta']:
            folder = root / project; folder.mkdir()
            (folder / 'note.md').write_text(f'{project} unique knowledge\n', encoding='utf-8')
            add_project(path, project, folder)
        self.config = load_config(path)
        self.server = make_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.admin = Client(self.url, self.config['admin_token_file'])
        self.reader = Client(self.url, self.config['readers']['alpha']['token_file'])
        self.hub = Client(self.url, self.config['nodes']['hub']['token_file'])
        self.gpu = Client(self.url, self.config['nodes']['gpu']['token_file'])

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def submit(self, project='alpha', key='test', target='hub'):
        return self.admin.request('POST', '/v1/jobs', {
            'kind': 'text.hash', 'payload': {'text': 'hello'}, 'project': project,
            'idempotency_key': key, 'target_node': target,
        })['job']

    def test_worker_round_trip_is_scoped_and_reader_cannot_mutate(self):
        job = self.submit()
        leased = self.hub.request('POST', '/v1/worker/claim', {})['job']
        self.assertEqual(job['id'], leased['id'])
        body = {'job_id': job['id'], 'lease_token': leased['lease_token'],
                'result': {'sha256': hashlib.sha256(b'hello').hexdigest(), 'bytes': 5}}
        with self.assertRaises(APIError) as ctx:
            self.gpu.request('POST', '/v1/worker/complete', body)
        self.assertEqual(ctx.exception.status, 409)
        self.hub.request('POST', '/v1/worker/complete', body)
        result = self.reader.request('GET', f'/v1/jobs/{job["id"]}')['job']
        self.assertEqual(result['status'], 'succeeded')
        self.assertNotIn('lease_token', result)
        with self.assertRaises(APIError) as ctx:
            self.reader.request('POST', '/v1/jobs', {})
        self.assertEqual(ctx.exception.status, 403)

    def test_reader_project_scope_applies_to_get_list_search_and_status(self):
        self.submit('alpha', 'alpha-key')
        other = self.submit('beta', 'beta-key')
        with self.assertRaises(APIError) as ctx:
            self.reader.request('GET', f'/v1/jobs/{other["id"]}')
        self.assertEqual(ctx.exception.status, 404)
        for path in ['/v1/jobs?project=beta', '/v1/knowledge/search?project=beta&query=unique']:
            with self.assertRaises(APIError) as ctx:
                self.reader.request('GET', path)
            self.assertEqual(ctx.exception.status, 403)
        jobs = self.reader.request('GET', '/v1/jobs')['jobs']
        self.assertEqual({j['project'] for j in jobs}, {'alpha'})
        text = json.dumps(self.reader.request('GET', '/v1/status'))
        self.assertNotIn('beta', text)
        self.assertNotIn(read_token(self.config['admin_token_file']), text)

    def test_workers_cannot_access_knowledge_or_operator_status(self):
        for path in ['/v1/status', '/v1/jobs', '/v1/knowledge/search?project=alpha&query=x']:
            with self.assertRaises(APIError) as ctx:
                self.hub.request('GET', path)
            self.assertEqual(ctx.exception.status, 403)

    def test_mcp_mismatched_project_and_credential_never_mislabels_data(self):
        from homelab.mcp_server import ToolBridge
        job = self.submit()
        bridge = ToolBridge(self.reader, 'beta')
        for name, arguments in [('lab_status', {}), ('lab_jobs', {}),
                                ('lab_job', {'job_id': job['id']}),
                                ('project_search', {'query': 'unique'})]:
            with self.subTest(name=name), self.assertRaises(APIError) as ctx:
                bridge.dispatch(name, arguments)
            self.assertEqual(ctx.exception.status, 403)

    def test_health_is_minimal_and_every_other_get_requires_auth(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1])
        conn.request('GET', '/health')
        response = conn.getresponse(); value = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(value, {'status': 'alive'})
        conn.request('GET', '/v1/status')
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 401)
        conn.close()

    def test_claim_cannot_change_credential_bound_identity(self):
        self.submit(target='gpu')
        with self.assertRaises(APIError) as ctx:
            self.hub.request('POST', '/v1/worker/claim', {'node_id': 'gpu'})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIsNone(self.hub.request('POST', '/v1/worker/claim', {})['job'])

    def test_unknown_job_fields_and_unknown_project_fail_closed(self):
        base = {'kind': 'text.hash', 'payload': {'text': 'safe'}, 'project': 'alpha', 'idempotency_key': 'x'}
        for body in [{**base, 'command': 'anything'}, {**base, 'project': 'unknown'},
                     {**base, 'local_only': False}]:
            with self.assertRaises(APIError) as ctx:
                self.admin.request('POST', '/v1/jobs', body)
            self.assertEqual(ctx.exception.status, 400)

    def test_browser_origin_and_large_body_refused(self):
        for extra, data, status in [({'Origin': 'https://evil.test'}, b'{}', 403),
                                    ({}, b'x' * 300000, 413)]:
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1])
            headers = {'Authorization': 'Bearer ' + read_token(self.config['admin_token_file']),
                       'Content-Type': 'application/json', **extra}
            conn.request('POST', '/v1/jobs', data, headers)
            response = conn.getresponse(); response.read()
            self.assertEqual(response.status, status)
            conn.close()

    def test_idempotent_http_submission_survives_second_store_instance(self):
        first = self.submit()
        second = self.submit()
        self.assertEqual(first['id'], second['id'])
        from homelab.queue import QueueStore
        queue = QueueStore(Path(self.config['state_dir']) / 'queue.sqlite3', self.config['nodes'])
        self.assertEqual(queue.get(first['id'])['status'], 'queued')


class ClientTests(unittest.TestCase):
    def test_redirect_never_receives_bearer_credential(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302); self.send_header('Location', '/stolen'); self.end_headers()
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp).resolve() / 'secret'; token.write_text('A' * 40)
            try:
                with self.assertRaises(APIError):
                    Client(f'http://127.0.0.1:{server.server_port}', token).request('GET', '/v1/status')
                self.assertEqual(seen, ['/v1/status'])
            finally:
                server.shutdown(); server.server_close(); thread.join(2)
