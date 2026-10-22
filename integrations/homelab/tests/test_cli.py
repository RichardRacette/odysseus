import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'lab.py'), *map(str, args)],
                              text=True, capture_output=True, timeout=20)

    def test_help_and_no_args_are_actionable(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in ['init', 'serve', 'worker', 'demo', 'snapshot']:
            self.assertIn(word, result.stdout)
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)

    def test_init_scan_ingest_search_does_not_need_a_server_or_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            result = self.run_cli('init', '--state', root / 'state')
            self.assertEqual(result.returncode, 0, result.stderr)
            config = root / 'state' / 'config.json'
            corpus = root / 'docs'; corpus.mkdir()
            (corpus / 'design.md').write_text('A quokka stores project evidence.\n', encoding='utf-8')
            for args in [('project-add', '--project', 'demo', '--root', corpus),
                         ('scan', '--project', 'demo'), ('ingest', '--project', 'demo')]:
                result = self.run_cli(*args, '--config', config)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli('search', '--project', 'demo', '--query', 'quokka', '--config', config)
            self.assertEqual(result.returncode, 0, result.stderr)
            results = json.loads(result.stdout)['results']
            self.assertEqual(len(results), 1)
            self.assertIn('quokka', json.dumps(results))

    def test_missing_config_error_does_not_traceback(self):
        result = self.run_cli('status', '--config', '/missing/not-there.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('error', result.stderr.lower())

    def test_synthetic_demo_runs_http_workers_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_cli('demo', '--output', Path(temp).resolve() / 'demo')
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['physical_nodes_verified'], False)
            self.assertTrue(report['checks']['http_worker_round_trip'])
            self.assertTrue(report['checks']['source_citation'])
            self.assertTrue(report['checks']['restore_integrity'])
            self.assertTrue((Path(temp) / 'demo' / 'DEMO-RESULT.json').is_file())
