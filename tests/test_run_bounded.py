import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "bundle/run-bounded.py"

# The leader exits only after its child has installed the requested signal handler.
LEADER = """
import os, signal, sys, time
from pathlib import Path
directory, mode = Path(sys.argv[1]), sys.argv[2]
reader, writer = os.pipe()
if os.fork():
    os.close(writer)
    os.read(reader, 1)
    sys.exit(7)
os.close(reader)
if mode == 'kill':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(directory / 'pid').write_text(str(os.getpid()))
os.write(writer, b'1')
os.close(writer)
time.sleep(0.25 if mode == 'finish' else 5)
(directory / 'done').touch()
"""


class BoundedTests(unittest.TestCase):
    def run_group(self, mode, timeout):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as folder:
            root = Path(folder)
            worker_finished = False
            try:
                result = subprocess.run(
                    [sys.executable, WATCHDOG, "--timeout", str(timeout), "--grace", "0.25",
                     sys.executable, "-c", LEADER, folder, mode],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    timeout=6, check=False)
                self.assertTrue((root / "pid").is_file())
                pid = int((root / "pid").read_text())
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    worker_finished = True
                self.assertTrue(worker_finished, "worker still present when watchdog returned")
                return result.returncode, (root / "done").exists()
            finally:
                # Clean up the finite test worker even when exercising a broken watchdog.
                if not worker_finished and (root / "pid").exists():
                    try:
                        os.kill(int((root / "pid").read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_waits_for_worker_and_preserves_early_leader_status(self):
        self.assertEqual(self.run_group("finish", 3), (7, True))

    def test_deadline_terminates_worker_after_leader_exits(self):
        self.assertEqual(self.run_group("term", 0.8), (124, False))

    def test_kills_worker_ignoring_term_after_grace(self):
        self.assertEqual(self.run_group("kill", 0.8), (137, False))

    def test_ignored_sigchld_is_preserved_for_the_command(self):
        launcher = ("import os, signal, sys; signal.signal(signal.SIGCHLD, signal.SIG_IGN); "
                    "os.execv(sys.executable, sys.argv[1:])")
        command = ("import signal, sys; "
                   "sys.exit(7 if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN else 2)")
        result = subprocess.run([sys.executable, "-c", launcher, sys.executable, WATCHDOG,
                                 "--timeout", "1", sys.executable, "-c", command], timeout=5)
        self.assertEqual(result.returncode, 7)
