"""Bounded process recovery for Windows user-logon tasks, without a shell.

The child waits on a pipe until assigned to a kill-on-close Windows job. This
prevents an orphan both when the supervisor dies and during the startup race.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import sys
import threading
import time


class KillOnCloseJob:
    def __init__(self):
        if sys.platform != 'win32':
            raise OSError('Windows process supervision requires Windows.')
        kernel = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io_counters', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid):
        process = self.kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


_BOOTSTRAP = '''import pathlib,runpy,sys
if sys.stdin.buffer.read(1) != b'1':
    sys.exit(1)
sys.stdin.close()
sys.argv = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).resolve().parent))
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def make_logger(path, max_bytes=1024 * 1024):
    logger = logging.getLogger(f'homelab.supervisor.{Path(path).resolve()}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=2, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)sZ %(levelname)s %(message)s')
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def _capture(pipe, logger):
    # Fixed-size reads also bound memory when a child writes without newlines.
    while chunk := pipe.read1(2048):
        logger.info('child: %s', chunk.decode('utf-8', errors='replace').rstrip())


def supervise(arguments, logger, *, restart_delay=60, max_restarts=3):
    for attempt in range(max_restarts + 1):
        job = KillOnCloseJob()
        child = reader = None
        try:
            child = subprocess.Popen([sys.executable, '-u', '-c', _BOOTSTRAP, *arguments],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, close_fds=True)
            job.assign(child.pid)
            reader = threading.Thread(target=_capture, args=(child.stdout, logger), daemon=True)
            reader.start()
            logger.info('started child pid=%d attempt=%d', child.pid, attempt + 1)
            child.stdin.write(b'1')
            child.stdin.close()
            returncode = child.wait()
        finally:
            job.close()
            if child is not None:
                # Closing an unreleased pipe also handles failed job assignment.
                if not child.stdin.closed:
                    child.stdin.close()
                if child.poll() is None:
                    child.terminate()
                child.wait()
                if reader is not None:
                    reader.join()
                child.stdout.close()
        logger.info('child exited code=%d attempt=%d', returncode, attempt + 1)
        if returncode == 0:
            return 0
        if attempt < max_restarts:
            logger.warning('restarting in %s seconds (%d/%d)', restart_delay, attempt + 1, max_restarts)
            time.sleep(restart_delay)
    logger.error('restart budget exhausted; operator inspection and task start required')
    return returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['hub', 'worker'], required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    from .config import load_config
    config_path = Path(args.config).resolve(strict=True)
    config = load_config(config_path)
    if config['role'] != args.role:
        parser.error('Selected role must match the configuration.')
    logger = make_logger(config_path.with_suffix('.supervisor.log'))
    try:
        lab = Path(__file__).resolve().parents[1] / 'lab.py'
        command = 'serve' if args.role == 'hub' else 'worker'
        return supervise([str(lab), command, '--config', str(config_path)], logger)
    except KeyboardInterrupt:
        return 130
    except Exception:
        logger.exception('supervisor stopped; operator inspection required')
        return 1
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


if __name__ == '__main__':
    raise SystemExit(main())
