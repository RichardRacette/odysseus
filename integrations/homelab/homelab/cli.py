"""Explicit operator commands; all configuration mutations remain local."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

from .config import ConfigError, add_project, export_client, export_worker, init_state, load_config
from .http_client import APIError, Client


def _parser():
    parser = argparse.ArgumentParser(description='Home Lab: durable local jobs, attributable knowledge and recovery.')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create new private state; no sources are approved automatically.')
    init.add_argument('--state', required=True); init.add_argument('--port', type=int, default=8765)
    demo = sub.add_parser('demo', help='Exercise synthetic HTTP jobs, citations and restore without a model.')
    demo.add_argument('--output', required=True)
    restore = sub.add_parser('restore', help='Restore a verified snapshot into a new directory, disarmed.')
    restore.add_argument('--snapshot', required=True); restore.add_argument('--destination', required=True)
    verify = sub.add_parser('verify-snapshot', help='Verify snapshot hashes and SQLite integrity.')
    verify.add_argument('--snapshot', required=True)
    mirror = sub.add_parser('mirror', help='Create/update a pull-only Git mirror; never pushes.')
    mirror.add_argument('--source', required=True); mirror.add_argument('--destination', required=True)
    mirror.add_argument('--update', action='store_true')
    for name, helptext in [
        ('project-add', 'Register one specific project folder; restart hub afterward.'),
        ('scan', 'Preview eligible files without ingesting them.'),
        ('ingest', 'Index an approved project on the hub.'),
        ('search', 'Retrieve project evidence with source citations.'),
        ('remember', 'Record an explicit note, inference or decision.'),
        ('forget', 'Remove one authored record from retrieval.'),
        ('serve', 'Run the private loopback hub in the foreground.'),
        ('worker', 'Run a constrained pull worker.'),
        ('status', 'Read live hub status.'),
        ('submit', 'Submit a typed local-only job from a JSON file.'),
        ('jobs', 'List current project jobs.'),
        ('job', 'Read one job result.'),
        ('cancel', 'Cancel admission/result acceptance for a job.'),
        ('export-worker', 'Export one node credential/config to a new private folder.'),
        ('export-client', 'Export a reader restricted to one project.'),
        ('doctor', 'Check local prerequisites without changing services.'),
        ('snapshot', 'Create a consistent private state snapshot.'),
        ('mcp', 'Serve read-only tools for one configured project via MCP stdio.'),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument('--config', required=True)
        if name in {'project-add', 'scan', 'ingest', 'search', 'remember', 'forget', 'export-client'}:
            p.add_argument('--project', required=True)
        if name == 'project-add': p.add_argument('--root', required=True)
        if name == 'search':
            p.add_argument('--query', required=True); p.add_argument('--limit', type=int, default=5)
            p.add_argument('--mode', choices=['lexical', 'hybrid'], default='lexical')
        if name == 'remember':
            p.add_argument('--text', required=True)
            p.add_argument('--kind', choices=['note', 'inference', 'decision'], default='note')
            p.add_argument('--source-ref', action='append', default=[])
        if name == 'forget': p.add_argument('--record-id', required=True)
        if name == 'worker': p.add_argument('--once', action='store_true')
        if name == 'submit': p.add_argument('--job-file', required=True)
        if name == 'jobs': p.add_argument('--project')
        if name in {'job', 'cancel'}: p.add_argument('--job-id', required=True)
        if name in {'export-worker', 'export-client'}:
            p.add_argument('--destination', required=True); p.add_argument('--server-url', required=True)
        if name == 'export-worker': p.add_argument('--node', required=True)
        if name == 'snapshot': p.add_argument('--destination', required=True)
    return parser


def client_for(config):
    if config['role'] == 'hub':
        return Client(f'http://127.0.0.1:{config["port"]}', config['admin_token_file'])
    return Client(config['server_url'], config['token_file'])


def knowledge_for(config):
    if config['role'] != 'hub':
        raise ConfigError('This local operation requires the hub configuration.')
    from .knowledge import KnowledgeStore
    embedder = None
    embedding_config = config.get('embeddings', {})
    if embedding_config.get('enabled', False):
        from .embeddings import OllamaEmbedder
        embedder = OllamaEmbedder(embedding_config['base_url'], model=embedding_config['model'])
    return KnowledgeStore(Path(config['state_dir']) / 'knowledge.sqlite3', config['projects'], embedder=embedder)


def execute(args):
    command = args.command
    if command == 'init':
        return {'config': str(init_state(args.state, args.port)), 'approved_projects': 0,
                'next': 'Run doctor; register a specific project with project-add, then scan.'}
    if command == 'demo':
        from .demo import run_demo
        return run_demo(args.output)
    if command in {'restore', 'verify-snapshot', 'mirror'}:
        from .operations import mirror_repository, restore_snapshot, verify_snapshot
        if command == 'restore': return restore_snapshot(args.snapshot, args.destination)
        if command == 'verify-snapshot': return verify_snapshot(args.snapshot)
        return mirror_repository(args.source, args.destination, update=args.update)
    config = load_config(args.config)
    if command == 'project-add':
        add_project(args.config, args.project, args.root)
        return {'project': args.project, 'registered': True, 'ingested': False, 'hub_restart_required': True}
    if command == 'export-worker':
        return {'worker_config': str(export_worker(args.config, args.node, args.destination, args.server_url)),
                'contains_private_node_credential': True}
    if command == 'export-client':
        return {'client_config': str(export_client(args.config, args.project, args.destination, args.server_url)),
                'project': args.project, 'contains_private_reader_credential': True}
    if command in {'doctor', 'snapshot'}:
        from .operations import create_snapshot, doctor
        if command == 'doctor': return doctor(config)
        if config['role'] != 'hub': raise ConfigError('Snapshot requires hub configuration.')
        return create_snapshot(config['state_dir'], args.destination)
    if command == 'serve':
        from .server import make_server
        server = make_server(config)
        print(json.dumps({'listening': f'http://127.0.0.1:{server.server_address[1]}',
                          'authentication': 'required', 'cloud_execution': 'disabled'}), flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
        return None
    if command == 'worker':
        if config['role'] != 'worker': raise ConfigError('Worker requires an exported worker configuration.')
        from .worker import run_once, run_worker
        client = client_for(config)
        if args.once:
            return {'processed': run_once(client, config['node_id'], ollama_url=config['ollama_url'],
                                          allowed_models=config['allowed_models']), 'node': config['node_id']}
        print(json.dumps({'worker': config['node_id'], 'status': 'polling', 'cloud_execution': 'disabled'}), flush=True)
        run_worker(client, config['node_id'], ollama_url=config['ollama_url'], allowed_models=config['allowed_models'])
        return None
    if command == 'mcp':
        if config['role'] != 'reader': raise ConfigError('MCP requires a project-scoped reader configuration.')
        from .mcp_server import run
        run(config)
        return None
    if command in {'scan', 'ingest', 'remember', 'forget'} or (command == 'search' and config['role'] == 'hub'):
        store = knowledge_for(config)
        try:
            if command == 'scan': return store.scan(args.project)
            if command == 'ingest': return store.ingest(args.project)
            if command == 'search': return store.search(args.project, args.query, limit=args.limit, mode=args.mode)
            if command == 'remember': return store.remember(args.project, args.text, kind=args.kind, source_refs=args.source_ref)
            return {'removed': store.forget(args.project, args.record_id)}
        finally:
            close = getattr(store, 'close', None)
            if close: close()
    client = client_for(config)
    if command == 'status': return client.request('GET', '/v1/status')
    if command == 'search':
        return client.request('GET', '/v1/knowledge/search?' + urlencode(
            {'project': args.project, 'query': args.query, 'limit': args.limit, 'mode': args.mode}))
    if command == 'jobs':
        return client.request('GET', '/v1/jobs' + ('?' + urlencode({'project': args.project}) if args.project else ''))
    if command in {'job', 'cancel'}:
        from .config import identifier
        identifier(args.job_id)
        path = '/v1/jobs/' + args.job_id
        return client.request('GET', path) if command == 'job' else client.request('POST', path + '/cancel', {})
    if command == 'submit':
        path = Path(args.job_file)
        if path.stat().st_size > 65536: raise ConfigError('Job file exceeds 64 KiB.')
        body = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(body, dict): raise ConfigError('Job file must contain one JSON object.')
        return client.request('POST', '/v1/jobs', body)
    raise ConfigError('Unknown command.')


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = execute(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except (ConfigError, APIError, ValueError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
