#!/usr/bin/env python3
"""Run a command in its own process group with a TERM/KILL deadline."""
import argparse
import math
import os
import signal
import subprocess
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

process = subprocess.Popen(args.command, start_new_session=True)
try:
    raise SystemExit(shell_status(process.wait(timeout=args.timeout)))
except subprocess.TimeoutExpired:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        raise SystemExit(shell_status(process.wait()))
    deadline = time.monotonic() + args.grace
    while time.monotonic() < deadline:
        process.poll()  # Reap the group leader so a zombie does not look alive.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            if process.returncode is None:
                process.wait()
            raise SystemExit(124)
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        process.wait()
        raise SystemExit(124)
    if process.returncode is None:
        process.wait()
    raise SystemExit(137)
