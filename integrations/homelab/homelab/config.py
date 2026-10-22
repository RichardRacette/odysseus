"""Local configuration and scoped credentials. No discovery or network on import."""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from .knowledge import validate_projects


class ConfigError(ValueError):
    pass


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', value):
        raise ConfigError('Use a 1–64 character identifier containing letters, numbers, _ or -.')
    return value


def validate_server_url(value: str) -> str:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ConfigError('Invalid hub URL.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError('Invalid hub URL.') from exc
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ConfigError('Hub URL must be an HTTP(S) origin without credentials, path, query or fragment.')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ConfigError('Remote hub connections require HTTPS; HTTP is allowed only through loopback.')
    return value.rstrip('/')


def _no_symlink(path: Path) -> None:
    for candidate in [path, *path.parents]:
        if candidate.is_symlink():
            raise ConfigError('Configuration and credential paths must not traverse symbolic links.')


def read_token(path: str | Path) -> str:
    target = Path(path).absolute()
    _no_symlink(target)
    try:
        if target.stat().st_size > 4096:
            raise ConfigError('Credential file is too large.')
        token = target.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise ConfigError('Credential file is missing or unreadable.') from exc
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ConfigError('Credential must be a generated URL-safe token of at least 32 characters.')
    return token


def _private_write(path: Path, text: str) -> None:
    _no_symlink(path.absolute())
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def _json_write_new(path: Path, value: dict) -> None:
    _private_write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def _raw(path: Path) -> dict:
    _no_symlink(path.absolute())
    if path.stat().st_size > 262144:
        raise ConfigError('Configuration is too large.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ConfigError('Configuration is not readable JSON.') from exc
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ConfigError('Expected a configuration object with schema_version 1.')
    return value


def _path(parent: Path, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError('Expected a nonempty local path.')
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = parent / candidate
    _no_symlink(candidate.absolute())
    return str(candidate.resolve())


def load_config(path: str | Path) -> dict:
    path = Path(path).expanduser().absolute()
    raw = _raw(path)
    config = dict(raw)
    config['_config_path'] = str(path)
    role = config.get('role')
    if role == 'hub':
        if config.get('bind', '127.0.0.1') != '127.0.0.1':
            raise ConfigError('The hub binds only to 127.0.0.1. Use private HTTPS or SSH forwarding.')
        port = config.get('port', 8765)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ConfigError('Invalid hub port.')
        config['state_dir'] = _path(path.parent, config.get('state_dir', '.'))
        config['admin_token_file'] = _path(path.parent, config['admin_token_file'])
        tokens = [read_token(config['admin_token_file'])]
        config['nodes'] = {}
        nodes = raw.get('nodes')
        if not isinstance(nodes, dict) or not nodes or len(nodes) > 16:
            raise ConfigError('Configure between 1 and 16 nodes.')
        for name, node in nodes.items():
            identifier(name)
            if not isinstance(node, dict):
                raise ConfigError('Node configuration must be an object.')
            n = dict(node)
            n['token_file'] = _path(path.parent, n['token_file'])
            tokens.append(read_token(n['token_file']))
            caps = n.get('capabilities', [])
            if not isinstance(caps, list) or not all(c in {'text.hash', 'json.validate', 'ollama.generate'} for c in caps):
                raise ConfigError('Unsupported node capability.')
            if type(n.get('max_concurrency', 1)) is not int or not 1 <= n.get('max_concurrency', 1) <= 4:
                raise ConfigError('Node concurrency must be between 1 and 4.')
            models = n.get('allowed_models', [])
            if not isinstance(models, list) or not all(isinstance(m, str) and len(m) <= 128 for m in models):
                raise ConfigError('Model allowlist must be a list of exact names.')
            config['nodes'][name] = n
        projects = raw.get('projects', {})
        readers = raw.get('readers', {})
        if not isinstance(projects, dict) or not isinstance(readers, dict):
            raise ConfigError('Projects and readers must be objects.')
        config['projects'] = {identifier(k): _path(path.parent, v) for k, v in projects.items()}
        try:
            validate_projects(config['projects'])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        config['readers'] = {}
        for project, reader in readers.items():
            if project not in projects or not isinstance(reader, dict):
                raise ConfigError('Each reader must be assigned to a configured project.')
            token_file = _path(path.parent, reader['token_file'])
            tokens.append(read_token(token_file))
            config['readers'][project] = {'token_file': token_file}
        if len(set(tokens)) != len(tokens):
            raise ConfigError('Every administrator, reader and node credential must be different.')
        if config.get('cloud_enabled', False):
            raise ConfigError('Cloud execution is not implemented; cloud_enabled must be false.')
        return config
    if role not in {'worker', 'reader', 'admin'}:
        raise ConfigError('Configuration role must be hub, worker, reader or admin.')
    config['server_url'] = validate_server_url(config['server_url'])
    config['token_file'] = _path(path.parent, config['token_file'])
    read_token(config['token_file'])
    if role == 'worker':
        identifier(config['node_id'])
        config.setdefault('ollama_url', 'http://127.0.0.1:11434')
        config.setdefault('allowed_models', [])
    if role == 'reader':
        identifier(config['project'])
    return config


def init_state(state_dir: str | Path, port: int = 8765) -> Path:
    target = Path(state_dir).expanduser().absolute()
    _no_symlink(target)
    if target.exists() and any(target.iterdir()):
        raise ConfigError('State directory is not empty; refusing to replace it.')
    if type(port) is not int or not 0 <= port <= 65535:
        raise ConfigError('Invalid hub port.')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    (target / 'secrets').mkdir(mode=0o700)
    nodes = {}
    for name in ['hub', 'gpu', 'mac']:
        filename = f'secrets/node-{name}.token'
        _private_write(target / filename, secrets.token_urlsafe(36) + '\n')
        nodes[name] = {'token_file': filename, 'capabilities': ['text.hash', 'json.validate'],
                       'max_concurrency': 1, 'allowed_models': []}
    nodes['gpu']['capabilities'].append('ollama.generate')
    nodes['gpu']['allowed_models'] = ['gemma3:4b', 'qwen3.5:4b']
    _private_write(target / 'secrets/admin.token', secrets.token_urlsafe(36) + '\n')
    config = {'schema_version': 1, 'role': 'hub', 'state_dir': '.', 'bind': '127.0.0.1',
              'port': port, 'admin_token_file': 'secrets/admin.token', 'nodes': nodes,
              'projects': {}, 'readers': {}, 'cloud_enabled': False,
              'embeddings': {'enabled': False, 'base_url': 'http://127.0.0.1:11434',
                             'model': 'embeddinggemma'}}
    _json_write_new(target / 'config.json', config)
    return target / 'config.json'


def add_project(config_path: str | Path, project: str, root: str | Path) -> Path:
    identifier(project)
    path = Path(config_path).absolute()
    current = load_config(path)
    raw = _raw(path)
    if raw['role'] != 'hub':
        raise ConfigError('Project registration requires a hub configuration.')
    source = Path(root).expanduser().absolute()
    _no_symlink(source)
    source = source.resolve()
    if not source.is_dir() or source == Path(source.anchor) or source == Path.home().resolve():
        raise ConfigError('Choose a specific existing project folder, not a drive or home directory.')
    if project in raw['projects']:
        raise ConfigError('Project already exists; review configuration explicitly before changing its root.')
    try:
        validate_projects({**current['projects'], project: str(source)})
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    token_name = f'secrets/reader-{project}.token'
    token_path = path.parent / token_name
    _private_write(token_path, secrets.token_urlsafe(36) + '\n')
    raw['projects'][project] = str(source)
    raw['readers'][project] = {'token_file': token_name}
    temporary = path.with_name(path.name + '.new')
    try:
        _json_write_new(temporary, raw)
        os.replace(temporary, path)
    except Exception:
        token_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise
    return path


def _export_dir(destination: str | Path) -> Path:
    out = Path(destination).expanduser().absolute()
    _no_symlink(out)
    if out.exists():
        raise ConfigError('Export destination must not exist; credentials will not be overwritten.')
    out.mkdir(parents=True, mode=0o700)
    return out


def export_worker(config_path: str | Path, node: str, destination: str | Path, server_url: str) -> Path:
    config = load_config(config_path)
    url = validate_server_url(server_url)
    if config['role'] != 'hub' or node not in config['nodes']:
        raise ConfigError('Unknown configured node.')
    out = _export_dir(destination)
    policy = config['nodes'][node]
    _private_write(out / 'node.token', read_token(policy['token_file']) + '\n')
    worker = {'schema_version': 1, 'role': 'worker', 'node_id': node, 'server_url': url,
              'token_file': 'node.token', 'ollama_url': 'http://127.0.0.1:11434',
              'allowed_models': policy.get('allowed_models', [])}
    _json_write_new(out / 'worker.json', worker)
    return out / 'worker.json'


def export_client(config_path: str | Path, project: str, destination: str | Path, server_url: str) -> Path:
    config = load_config(config_path)
    url = validate_server_url(server_url)
    if config['role'] != 'hub' or project not in config['readers']:
        raise ConfigError('Register this project before exporting a reader.')
    out = _export_dir(destination)
    _private_write(out / 'reader.token', read_token(config['readers'][project]['token_file']) + '\n')
    _json_write_new(out / 'client.json', {'schema_version': 1, 'role': 'reader', 'project': project,
                                         'server_url': url, 'token_file': 'reader.token'})
    return out / 'client.json'
