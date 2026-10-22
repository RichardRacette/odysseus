import ctypes
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Windows logon-task supervisor')
class WindowsSupervisorTests(unittest.TestCase):
    def setUp(self):
        from homelab.windows_supervisor import supervise
        self.supervise = supervise
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = logging.getLogger(self.id())
        self.log.addHandler(logging.NullHandler())

    def script(self, source):
        script = self.root / 'child with spaces.py'
        script.write_text(source, encoding='utf-8')
        return script

    def counter_script(self, failures):
        return self.script('import pathlib,sys\n'
                           'p=pathlib.Path(sys.argv[1])\n'
                           'n=int(p.read_text())+1 if p.exists() else 1\n'
                           'p.write_text(str(n))\n'
                           f'sys.exit(7 if n <= {failures} else 0)\n')

    def test_crashed_process_is_restarted_with_delay_until_success(self):
        counter = self.root / 'count'
        started = time.monotonic()
        result = self.supervise([str(self.counter_script(2)), str(counter)], self.log,
                                restart_delay=0.05)
        self.assertEqual(result, 0)
        self.assertEqual(counter.read_text(), '3')
        self.assertGreaterEqual(time.monotonic() - started, 0.1)

    def test_permanent_failure_stops_after_three_restarts(self):
        counter = self.root / 'count'
        result = self.supervise([str(self.counter_script(100)), str(counter)], self.log,
                                restart_delay=0)
        self.assertEqual(result, 7)
        self.assertEqual(counter.read_text(), '4')

    def test_clean_exit_is_not_restarted(self):
        counter = self.root / 'count'
        self.assertEqual(self.supervise([str(self.counter_script(0)), str(counter)],
                                       self.log, restart_delay=0), 0)
        self.assertEqual(counter.read_text(), '1')

    def test_arguments_are_passed_literally_without_a_shell(self):
        target = self.root / 'args.json'
        script = self.script('import json,pathlib,sys\n'
                             'pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        args = ['space in value', '"quote"', '$(`bad`); & exit 9', 'unicode-é']
        self.assertEqual(self.supervise([str(script), str(target), *args], self.log), 0)
        self.assertEqual(json.loads(target.read_text()), args)

    def test_assignment_failure_never_runs_child_payload(self):
        target = self.root / 'ran'
        script = self.script(f'from pathlib import Path\nPath({str(target)!r}).touch()\n')
        with patch('homelab.windows_supervisor.KillOnCloseJob.assign',
                   side_effect=OSError('assignment denied')):
            with self.assertRaisesRegex(OSError, 'assignment denied'):
                self.supervise([str(script)], self.log)
        self.assertFalse(target.exists())

    def test_killing_supervisor_also_terminates_its_child(self):
        target = self.root / 'pid'
        script = self.script('import os,pathlib,time,sys\n'
                             'pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n'
                             'time.sleep(60)\n')
        code = ('from homelab.windows_supervisor import supervise\n'
                'import logging,sys\n'
                'sys.exit(supervise(sys.argv[1:],logging.getLogger("test")))\n')
        parent = subprocess.Popen([sys.executable, '-c', code, str(script), str(target)],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not target.exists() and parent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(target.exists(), 'child did not start')
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, int(target.read_text()))
            self.assertTrue(handle)
            try:
                parent.kill()
                parent.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0,
                                 'orphaned child survived supervisor termination')
            finally:
                kernel.CloseHandle(handle)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            parent.stderr.close()

    def test_child_output_is_captured_in_bounded_rotating_logs(self):
        from homelab.windows_supervisor import make_logger
        log_path = self.root / 'supervisor.log'
        logger = make_logger(log_path, max_bytes=4096)
        script = self.script('print("diagnostic " * 20000)\n')
        try:
            self.assertEqual(self.supervise([str(script)], logger), 0)
        finally:
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
        logs = list(self.root.glob('supervisor.log*'))
        self.assertLessEqual(len(logs), 3)
        self.assertLess(sum(p.stat().st_size for p in logs), 16000)
        self.assertIn('exited', log_path.read_text())
