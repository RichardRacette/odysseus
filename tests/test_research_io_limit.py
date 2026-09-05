"""Real child regressions for the research stdout boundary."""
import asyncio
import sys

import pytest

from src import research_io


@pytest.mark.asyncio
async def test_overflow_is_rejected_before_child_finishes():
    # The prior communicate() implementation waits for EOF, then checks size.
    script = "import sys,time; sys.stdout.buffer.write(b'x'*4194305); sys.stdout.flush(); time.sleep(60)"
    with pytest.raises(RuntimeError, match='Research I/O unavailable'):
        await research_io.run_process([sys.executable, '-B', '-c', script], b'', 2)
    assert not research_io._active_workers


@pytest.mark.asyncio
async def test_full_duplex_input_and_exact_output_boundary():
    script = "import sys; sys.stdout.buffer.write(b'x'*4194304); sys.stdout.flush(); sys.stdin.buffer.read()"
    result = await research_io.run_process([sys.executable, '-B', '-c', script], b'y'*4194304, 10)
    assert len(result) == 4194304
    assert not research_io._active_workers


@pytest.mark.asyncio
async def test_repeated_cancel_reaps_busy_child():
    script = "import time; time.sleep(60)"
    task=asyncio.create_task(research_io.run_process([sys.executable, '-B', '-c', script], b'', 30))
    for _ in range(200):
        if research_io._active_workers: break
        await asyncio.sleep(.01)
    assert research_io._active_workers
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not research_io._active_workers
