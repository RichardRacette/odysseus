"""Own and terminate synchronous research I/O without orphaned worker threads.

The canonical search/fetch functions include retries and parsing beyond their
HTTP timeouts. Run them in disposable children, bound by the remaining research
budget. No service, credentials or persistent configuration is created here.
"""
import asyncio
import json
import subprocess
import sys

_active_workers = set()

async def run_process(command, payload, timeout):
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
    _active_workers.add(process)
    try:
        output, _ = await asyncio.wait_for(process.communicate(payload), timeout)
        if process.returncode or len(output) > 4_194_304:
            raise RuntimeError('Research I/O unavailable')
        return output
    finally:
        if process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
        # Repeated cancellation cannot abandon process reaping.
        cleanup = asyncio.create_task(process.wait())
        while not cleanup.done():
            try: await asyncio.shield(cleanup)
            except asyncio.CancelledError: continue
        _active_workers.discard(process)

async def run_research_io(kind, args, timeout):
    payload = json.dumps([kind, args]).encode()
    if len(payload) > 4_194_304:
        raise ValueError('Research I/O input too large')
    output = await run_process([sys.executable, '-B', '-m', 'src.research_io'],
                               payload, timeout)
    return json.loads(output)

def _worker():
    # Source imports can print diagnostics; only the bounded JSON result leaves.
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        kind, args = json.loads(sys.stdin.buffer.read(4_194_305))
        if kind == 'search':
            from src.search.core import _call_provider
            result = _call_provider(*args)
        elif kind == 'fetch':
            from src.search import fetch_webpage_content
            result = fetch_webpage_content(*args)
        elif kind == 'save':
            from core.atomic_io import atomic_write_json
            atomic_write_json(*args)
            result = True
        else:
            raise ValueError('Unsupported research I/O')
        encoded = json.dumps(result).encode()
        if len(encoded) > 4_194_304: raise ValueError('Research I/O too large')
    sys.stdout.buffer.write(encoded)

if __name__ == '__main__':
    try: _worker()
    except Exception: sys.exit(1)
