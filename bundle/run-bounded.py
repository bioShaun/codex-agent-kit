#!/usr/bin/env python3
"""Run a command in its own process group with a TERM/KILL deadline."""
import argparse
import math
import os
import signal
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--timeout", type=float, required=True)
parser.add_argument("--grace", type=float, default=10.0)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
if not args.command:
    parser.error("command is required")
if not math.isfinite(args.timeout) or args.timeout <= 0:
    parser.error("--timeout must be finite and greater than zero")
if not math.isfinite(args.grace) or args.grace < 0:
    parser.error("--grace must be finite and non-negative")

def shell_status(returncode):
    return returncode if returncode >= 0 else 128 - returncode

def adopt_orphans():
    # Linux containers may lack a reaping PID 1. Adopt orphaned workers so
    # a finished worker cannot keep the process group alive as a zombie.
    if sys.platform == "linux":
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))


def signal_group(group, sig):
    try:
        os.killpg(group, sig)
        return True
    except ProcessLookupError:
        return False


def wait_group(process, deadline):
    while True:
        # This watchdog has one job. Collect the leader's status and any
        # adopted workers that have already exited, including during grace.
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            pid = 0
        if pid == process.pid:
            process.returncode = os.waitstatus_to_exitcode(status)

        if process.returncode is not None and not signal_group(process.pid, 0):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not pid:
            time.sleep(min(remaining, 0.05))


adopt_orphans()
child_setup = None
if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
    # Keep exit statuses available to the watcher, while preserving the
    # caller's signal disposition in the command itself.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    def child_setup():
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

process = subprocess.Popen(args.command, start_new_session=True, preexec_fn=child_setup)
if wait_group(process, time.monotonic() + args.timeout):
    raise SystemExit(shell_status(process.returncode))
signal_group(process.pid, signal.SIGTERM)
if wait_group(process, time.monotonic() + args.grace):
    raise SystemExit(124)
if not signal_group(process.pid, signal.SIGKILL):
    raise SystemExit(124)
# Allow the kernel to deliver KILL and collect adopted workers. A task stuck
# in uninterruptible I/O must not hold the watchdog open indefinitely.
wait_group(process, time.monotonic() + 0.1)
raise SystemExit(137)
