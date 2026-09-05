"""Strict adapter for the separately supplied, byte-pinned hcf/1 contract.

The small validator implements only keywords used by these pinned schemas.
Unknown keywords fail closed. It performs no schema/network resolution.
"""
import hashlib
import json
import math
from pathlib import Path
import re

TASK_LIMIT = 65_536
RESULT_LIMIT = 16_384
HASHES = {
    'task.schema.json': '9a7d97644a3fa5928bd75dd8efbe8fa1cd2b494feaca6b462b464d76ce8146e9',
    'result.schema.json': 'c052056aef9384511ebc7f07132209359542a593446c95f57c7bced2579ad180',
}


class Refused(ValueError):
    """Only fixed public error codes may be passed to this exception."""


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise Refused('INVALID_TASK')
        result[key] = value
    return result


def decode(blob, limit=TASK_LIMIT):
    if len(blob) > limit:
        raise Refused('INVALID_TASK')
    try:
        return json.loads(blob.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refused('INVALID_TASK')))
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('INVALID_TASK') from None


def read_bounded(path, limit=TASK_LIMIT):
    with Path(path).open('rb') as handle:
        blob = handle.read(limit + 1)
    if len(blob) > limit:
        raise Refused('INVALID_TASK')
    return blob


def matches(value, kind):
    return {'object': isinstance(value, dict), 'array': isinstance(value, list),
            'string': isinstance(value, str), 'null': value is None,
            'integer': type(value) in (int, float) and math.isfinite(value) and int(value) == value,
            'number': type(value) in (int, float) and math.isfinite(value)}.get(kind, False)


def equal(a, b):
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def validate(value, schema):
    allowed = {'$schema', '$id', 'type', 'const', 'enum', 'properties', 'required',
               'additionalProperties', 'items', 'minItems', 'maxItems', 'uniqueItems',
               'minLength', 'maxLength', 'minimum', 'maximum', 'pattern'}
    if set(schema) - allowed:
        raise Refused('INVALID_TASK')
    types = schema.get('type')
    if types and not any(matches(value, t) for t in (types if isinstance(types, list) else [types])):
        raise Refused('INVALID_TASK')
    if 'const' in schema and not equal(value, schema['const']):
        raise Refused('INVALID_TASK')
    if 'enum' in schema and not any(equal(value, option) for option in schema['enum']):
        raise Refused('INVALID_TASK')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - value.keys():
            raise Refused('INVALID_TASK')
        if schema.get('additionalProperties') is False and value.keys() - properties.keys():
            raise Refused('INVALID_TASK')
        for key in value.keys() & properties.keys():
            validate(value[key], properties[key])
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', TASK_LIMIT):
            raise Refused('INVALID_TASK')
        if schema.get('uniqueItems') and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise Refused('INVALID_TASK')
        for item in value:
            validate(item, schema.get('items', {}))
    if isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', TASK_LIMIT):
            raise Refused('INVALID_TASK')
        if 'pattern' in schema and re.fullmatch(schema['pattern'], value) is None:
            raise Refused('INVALID_TASK')
        value.encode('utf-8')  # Reject unpaired surrogates before hashing/prompt construction.
    if type(value) in (int, float):
        if not math.isfinite(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            raise Refused('INVALID_TASK')


class Contract:
    def __init__(self, directory):
        schemas = {}
        for name, expected in HASHES.items():
            blob = read_bounded(Path(directory) / name)
            if sha(blob) != expected:
                raise Refused('INVALID_TASK')
            schemas[name] = decode(blob)
        self.task_schema = schemas['task.schema.json']
        self.result_schema = schemas['result.schema.json']

    def task(self, blob):
        task = decode(blob)
        validate(task, self.task_schema)
        sources = task['sources']
        ids = [s['source_id'] for s in sources]
        claims = [c['claim_id'] for c in task['proposed_claims']]
        if len(ids) != len(set(ids)) or len(claims) != len(set(claims)):
            raise Refused('INVALID_TASK')
        if sum(len(s['content'].encode()) for s in sources) > 8192:
            raise Refused('INVALID_TASK')
        if any(sha(s['content'].encode()) != s['sha256'] for s in sources):
            raise Refused('INVALID_TASK')
        if task['kind'] == 'challenge_claims' and not claims:
            raise Refused('INVALID_TASK')
        return task

    def result(self, result, task, blob):
        validate(result, self.result_schema)
        if result['task_id'] != task['task_id'] or result['task_sha256'] != sha(blob):
            raise Refused('INVALID_OUTPUT')
        if len(json.dumps(result, ensure_ascii=False).encode()) > RESULT_LIMIT:
            raise Refused('INVALID_OUTPUT')
        if result['status'] == 'COMPLETED' and (result['completion_state'] != 'COMPLETE' or result['error_code'] is not None):
            raise Refused('INVALID_OUTPUT')
        if result['execution_mode'] == 'SYNTHETIC_EXAMPLE' and result['node_id'] != 'fixture':
            raise Refused('INVALID_OUTPUT')
        if result['execution_mode'] == 'LOCAL_INFERENCE' and (result['node_id'] == 'fixture' or not result['model']['name']):
            raise Refused('INVALID_OUTPUT')
        known = {s['source_id'] for s in task['sources']}
        for row in result['observations'] + result['claim_checks']:
            if not set(row['source_ids']) <= known:
                raise Refused('INVALID_OUTPUT')
        expected = {c['claim_id'] for c in task['proposed_claims']}
        actual = [c['claim_id'] for c in result['claim_checks']]
        if len(actual) != len(set(actual)) or not set(actual) <= expected:
            raise Refused('INVALID_OUTPUT')
        if result['status'] == 'COMPLETED':
            if task['kind'] == 'challenge_claims' and set(actual) != expected:
                raise Refused('INVALID_OUTPUT')
            if task['kind'] == 'summarize_evidence' and not result['observations']:
                raise Refused('INVALID_OUTPUT')
        return result
