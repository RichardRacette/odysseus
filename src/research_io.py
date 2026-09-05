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
_MAX_IO_BYTES = 4_194_304

async def _exchange(process, payload):
    async def write_input():
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def read_output():
        output = bytearray()
        while True:
            chunk = await process.stdout.read(min(65_536, _MAX_IO_BYTES + 1 - len(output)))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > _MAX_IO_BYTES:
                raise RuntimeError('Research I/O unavailable')

    tasks = [asyncio.create_task(write_input()), asyncio.create_task(read_output())]
    try:
        _, output = await asyncio.gather(*tasks)
        await process.wait()
        if process.returncode:
            raise RuntimeError('Research I/O unavailable')
        return output
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

async def run_process(command, payload, timeout):
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
    _active_workers.add(process)
    try:
        return await asyncio.wait_for(_exchange(process, payload), timeout)
    finally:
        if process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
        # Repeated cancellation cannot abandon process reaping.
        async def reap():
            # A killed child can leave a paused pipe transport. Drain without
            # retaining bytes so wait() can observe closure on Windows too.
            while await process.stdout.read(65_536):
                pass
            await process.wait()
        cleanup = asyncio.create_task(reap())
        while not cleanup.done():
            try: await asyncio.shield(cleanup)
            except asyncio.CancelledError: continue
        _active_workers.discard(process)

async def run_research_io(kind, args, timeout):
    payload = json.dumps([kind, args]).encode()
    if len(payload) > _MAX_IO_BYTES:
        raise ValueError('Research I/O input too large')
    output = await run_process([sys.executable, '-B', '-m', 'src.research_io'],
                               payload, timeout)
    return json.loads(output)

def _worker():
    # Source imports can print diagnostics; only the bounded JSON result leaves.
    import contextlib
    import os
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
        kind, args = json.loads(sys.stdin.buffer.read(_MAX_IO_BYTES + 1))
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
        if len(encoded) > _MAX_IO_BYTES: raise ValueError('Research I/O too large')
    sys.stdout.buffer.write(encoded)

if __name__ == '__main__':
    try: _worker()
    except Exception: sys.exit(1)
