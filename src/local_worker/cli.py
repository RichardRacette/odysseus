"""Fixed batch CLI. Task contents never select destinations or executable code."""
import argparse
import asyncio
import json
import sys
import time

from .contract import Contract, Refused, read_bounded, sha
from .queue import Attempt
from .runtime import Ollama, endpoint, apply_response


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused('INVALID_TASK')


async def perform(blob, contract, node, model, state, runtime):
    if sys.version_info < (3, 11) or not hasattr(asyncio, 'timeout'):
        raise Refused('UNAVAILABLE_NODE')
    task = contract.task(blob)
    attempt = Attempt(state, node, blob)
    cached = attempt.cached()
    if cached is not None:
        if cached['node_id'] != node or cached['model']['name'] != model:
            raise Refused('UNAVAILABLE_MODEL')
        return contract.result(cached, task, blob)
    attempt.claim()
    start = time.monotonic()
    result = {'schema': 'hcf.result/1', 'task_id': task['task_id'], 'task_sha256': sha(blob),
              'node_id': node, 'execution_mode': 'LOCAL_INFERENCE', 'status': 'BLOCKED',
              'completion_state': 'UNKNOWN', 'model': {'name': model, 'digest': None},
              'metrics': {'elapsed_ms': 0, 'input_tokens': None, 'output_tokens': None, 'finish_reason': None},
              'observations': [], 'claim_checks': [], 'uncertainties': [], 'error_code': 'UNAVAILABLE_NODE'}
    try:
        async with asyncio.timeout(task['budget']['timeout_seconds']):
            response = await runtime.run(task, contract)
            result['model']['digest'] = runtime.evidence.get('digest')
            apply_response(result, response, task, contract)
            contract.result(result, task, blob)
    except TimeoutError:
        result.update(status='TIMED_OUT', error_code='TIMEOUT')
    except asyncio.CancelledError:
        result.update(status='CANCELLED', error_code='CANCELLED')
    except Refused as error:
        code = str(error) if str(error) in ('UNAVAILABLE_NODE', 'UNAVAILABLE_MODEL', 'UNVERIFIED_LOCAL_RUNTIME', 'INVALID_TASK') and not runtime.started else 'INVALID_OUTPUT'
        result.update(status='INVALID_OUTPUT' if runtime.started else 'BLOCKED', error_code=code)
    except Exception:
        result.update(status='FAILED', error_code='IO_FAILED')
    if result['status'] != 'COMPLETED':
        result.update(observations=[], claim_checks=[], uncertainties=[])
    result['model']['digest'] = runtime.evidence.get('digest')
    result['metrics']['elapsed_ms'] = round((time.monotonic()-start)*1000, 3)
    contract.result(result, task, blob)
    attempt.finish(result, {'attempt_id': attempt.id, 'model_call_attempted': runtime.started,
                            'generation_finished_observed': runtime.finished, **runtime.evidence},
                   release=not runtime.started or runtime.finished)
    return result


def main(argv=None):
    if sys.version_info < (3, 11):
        raise Refused('UNAVAILABLE_NODE')
    parser = Parser(description='Inspect a bounded synthetic artifact packet using one installed local model.')
    parser.add_argument('--task', required=True)
    parser.add_argument('--contract-dir', required=True)
    parser.add_argument('--node', required=True, choices=['pc', 'mac'])
    parser.add_argument('--model', required=True)
    options = parser.parse_args(argv)
    if (options.node == 'mac') != (sys.platform == 'darwin'):
        raise Refused('UNAVAILABLE_NODE')
    contract = Contract(options.contract_dir)
    blob = read_bounded(options.task)
    runtime = Ollama(endpoint(), options.model)
    # One operator-configured data root, reused from Odysseus. Per-job callers
    # cannot choose another queue/lock directory. Changing this configuration or
    # deleting its journals is an operator action, never a retry mechanism.
    from src.constants import ARTIFACT_WORKER_DIR
    if ARTIFACT_WORKER_DIR is None:
        raise Refused('UNAVAILABLE_NODE')
    result = asyncio.run(perform(blob, contract, options.node, options.model, ARTIFACT_WORKER_DIR, runtime))
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return 0 if result['status'] == 'COMPLETED' else 2


def entry():
    try:
        return main()
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 0:
            return 0
        # No parser echo, task text, traceback, arbitrary provider diagnostic,
        # machine path, model response or exception message reaches stderr.
        code = str(error) if isinstance(error, Refused) and str(error) in ('INVALID_TASK', 'UNAVAILABLE_NODE', 'UNAVAILABLE_MODEL', 'UNVERIFIED_LOCAL_RUNTIME', 'IO_FAILED') else 'IO_FAILED'
        print(json.dumps({'error_code': code}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(entry())
