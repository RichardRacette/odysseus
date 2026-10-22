"""Real local HTTP integration exercise with synthetic data and no model requests."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .config import ConfigError, add_project, init_state, load_config
from .http_client import Client


def run_demo(output: str | Path) -> dict:
    from .knowledge import KnowledgeStore
    from .operations import create_snapshot, restore_snapshot, verify_snapshot
    from .queue import QueueStore
    from .server import make_server
    from .worker import run_once

    root = Path(output).expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        raise ConfigError('Demo output must be a new or empty directory.')
    root.mkdir(parents=True, exist_ok=True)
    corpus = root / 'synthetic-project'; corpus.mkdir()
    source = '# Synthetic lab project\nThe hub stores attributable evidence.\nThe GPU handles generation.\n'
    (corpus / 'design.md').write_text(source, encoding='utf-8', newline='\n')
    path = init_state(root / 'state', port=0)
    add_project(path, 'demo', corpus)
    config = load_config(path)
    server = make_server(config)
    server.knowledge.ingest('demo')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    client = Client(url, config['admin_token_file'])
    checks = {}
    completed = []
    try:
        for node, kind, payload in [
            ('gpu', 'text.hash', {'text': 'synthetic evidence'}),
            ('hub', 'json.validate', {'text': '{"ready": true}', 'required_keys': ['ready']}),
            ('mac', 'text.hash', {'text': 'independent verifier'}),
        ]:
            body = {'kind': kind, 'payload': payload, 'project': 'demo',
                    'idempotency_key': f'demo-{node}', 'target_node': node}
            job = client.request('POST', '/v1/jobs', body)['job']
            worker_client = Client(url, config['nodes'][node]['token_file'])
            if not run_once(worker_client, node, allowed_models=[]):
                raise RuntimeError('Synthetic worker did not claim its job.')
            result = client.request('GET', f'/v1/jobs/{job["id"]}')['job']
            if result['status'] != 'succeeded':
                raise RuntimeError('Synthetic worker job did not succeed.')
            if kind == 'text.hash' and result['result']['sha256'] != hashlib.sha256(payload['text'].encode()).hexdigest():
                raise RuntimeError('Worker produced incorrect hash.')
            again = client.request('POST', '/v1/jobs', body)['job']
            if again['id'] != job['id']: raise RuntimeError('Idempotency check failed.')
            completed.append(job['id'])
        checks['http_worker_round_trip'] = True
        checks['three_distinct_worker_credentials'] = True
        checks['idempotent_submission'] = True
        reader = Client(url, config['readers']['demo']['token_file'])
        hit = reader.request('GET', '/v1/knowledge/search?' + urlencode({'query': 'evidence', 'project': 'demo'}))
        if not hit.get('results') or hashlib.sha256(source.encode()).hexdigest() not in json.dumps(hit):
            raise RuntimeError('Source citation/hash check failed.')
        checks['source_citation'] = True
        pending = client.request('POST', '/v1/jobs', {'kind': 'text.hash', 'payload': {'text': 'wait for a sleeping worker'},
                                'project': 'demo', 'idempotency_key': 'demo-offline', 'target_node': 'gpu'})['job']
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
    reopened = QueueStore(Path(config['state_dir']) / 'queue.sqlite3', config['nodes'])
    if reopened.get(pending['id'])['status'] != 'queued': raise RuntimeError('Pending job did not survive reopening.')
    checks['pending_job_survives_restart'] = True
    create_snapshot(config['state_dir'], root / 'snapshot')
    verify_snapshot(root / 'snapshot')
    restore_snapshot(root / 'snapshot', root / 'restored')
    restored_config = root / 'restored' / 'config.json'
    restored_raw = json.loads(restored_config.read_text(encoding='utf-8'))
    if 'demo' not in restored_raw.get('projects', {}):
        add_project(restored_config, 'demo', corpus)
    restored = load_config(restored_config)
    restored_store = KnowledgeStore(root / 'restored' / 'knowledge.sqlite3', restored['projects'])
    try:
        result = restored_store.search('demo', 'evidence')
        if not result.get('results'): raise RuntimeError('Restored knowledge could not be retrieved.')
    finally:
        close = getattr(restored_store, 'close', None)
        if close: close()
    restored_queue = QueueStore(root / 'restored' / 'queue.sqlite3', restored['nodes'])
    if restored_queue.get(pending['id'])['status'] != 'queued': raise RuntimeError('Restored pending job missing.')
    checks['restore_integrity'] = True
    report = {'status': 'passed', 'version': '0.1.0', 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'checks': checks, 'completed_jobs': len(completed), 'pending_jobs': 1,
              'physical_nodes_verified': False, 'model_inference_verified': False,
              'scope': 'Synthetic jobs through real loopback HTTP and worker code on one host.',
              'report': str(root / 'DEMO-RESULT.json')}
    (root / 'DEMO-RESULT.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
