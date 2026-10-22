import json
import tempfile
import unittest
from pathlib import Path

from homelab.config import (
    ConfigError, add_project, export_client, export_worker, init_state,
    load_config, read_token, validate_server_url,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_init_has_no_approved_sources_and_distinct_credentials(self):
        path = init_state(self.root / 'state')
        config = load_config(path)
        self.assertEqual(config['projects'], {})
        self.assertEqual(config['bind'], '127.0.0.1')
        tokens = [read_token(config['admin_token_file'])]
        tokens += [read_token(n['token_file']) for n in config['nodes'].values()]
        self.assertEqual(len(tokens), len(set(tokens)))
        self.assertTrue(all(len(t) >= 32 for t in tokens))
        self.assertFalse((self.root / 'state' / 'queue.sqlite3').exists())

    def test_init_never_overwrites_existing_state(self):
        path = init_state(self.root / 'state')
        before = path.read_bytes()
        with self.assertRaises(ConfigError):
            init_state(self.root / 'state')
        self.assertEqual(path.read_bytes(), before)

    def test_project_registration_is_explicit_and_scoped(self):
        path = init_state(self.root / 'state')
        source = self.root / 'project'; source.mkdir()
        add_project(path, 'alpha', source)
        c = load_config(path)
        self.assertEqual(c['projects']['alpha'], str(source.resolve()))
        self.assertEqual(set(c['readers']), {'alpha'})
        with self.assertRaises(ConfigError):
            add_project(path, '../escape', source)
        with self.assertRaises(ConfigError):
            add_project(path, 'wide', Path.home())

    def test_worker_export_contains_only_its_credential(self):
        path = init_state(self.root / 'state')
        out = export_worker(path, 'gpu', self.root / 'export', 'https://hub.example.ts.net:8443')
        worker = load_config(out)
        hub = load_config(path)
        self.assertEqual(read_token(worker['token_file']), read_token(hub['nodes']['gpu']['token_file']))
        self.assertNotIn(read_token(hub['admin_token_file']), (self.root / 'export' / 'node.token').read_text())
        self.assertNotIn('projects', json.loads(out.read_text()))
        self.assertEqual(worker['role'], 'worker')

    def test_project_policy_rejection_leaves_config_and_credentials_unchanged(self):
        path = init_state(self.root / 'state')
        source = self.root / 'project'; source.mkdir()
        child = source / 'child'; child.mkdir()
        hidden = self.root / '.private'; hidden.mkdir()
        add_project(path, 'alpha', source)
        before = path.read_bytes()
        secrets_before = set((path.parent / 'secrets').iterdir())
        for name, candidate in [('overlap', child), ('duplicate', source), ('hidden', hidden)]:
            with self.subTest(name=name), self.assertRaises(ConfigError):
                add_project(path, name, candidate)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(set((path.parent / 'secrets').iterdir()), secrets_before)

    def test_hand_edited_overlapping_project_config_is_refused_before_start(self):
        path = init_state(self.root / 'state')
        source = self.root / 'project'; source.mkdir()
        add_project(path, 'alpha', source)
        raw = json.loads(path.read_text())
        raw['projects']['beta'] = str(source / 'child')
        path.write_text(json.dumps(raw))
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_reader_export_is_bound_to_one_project(self):
        path = init_state(self.root / 'state')
        source = self.root / 'project'; source.mkdir()
        add_project(path, 'alpha', source)
        out = export_client(path, 'alpha', self.root / 'reader', 'http://127.0.0.1:8765')
        reader = load_config(out)
        self.assertEqual(reader['project'], 'alpha')
        self.assertEqual(reader['role'], 'reader')
        self.assertNotIn('nodes', reader)

    def test_cleartext_remote_credentials_and_ambiguous_urls_rejected(self):
        for value in ['http://hub.ts.net', 'https://user:pass@host', 'https://host/path',
                      'https://host?q=x', 'file:///tmp/no', 'http://127.0.0.1.evil']:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_server_url(value)
        self.assertEqual(validate_server_url('http://127.0.0.1:8765/'), 'http://127.0.0.1:8765')

    def test_public_listener_and_duplicate_tokens_refused(self):
        path = init_state(self.root / 'state')
        raw = json.loads(path.read_text())
        raw['bind'] = '0.0.0.0'
        path.write_text(json.dumps(raw))
        with self.assertRaises(ConfigError): load_config(path)
        raw['bind'] = '127.0.0.1'
        raw['nodes']['hub']['token_file'] = raw['admin_token_file']
        path.write_text(json.dumps(raw))
        with self.assertRaises(ConfigError): load_config(path)

    def test_symlink_secret_refused(self):
        target = self.root / 'real.token'; target.write_text('x' * 48)
        link = self.root / 'link.token'
        try: link.symlink_to(target)
        except OSError: self.skipTest('symlinks unavailable')
        with self.assertRaises(ConfigError): read_token(link)
