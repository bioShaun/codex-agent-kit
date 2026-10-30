"""Injection contract for the Reviewer SubagentStart hook. Stdlib only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "bundle" / "hooks" / "reviewer_start_contract.py"


def run_hook(raw: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw,
        capture_output=True,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )


def event(agent_type: str | None = "astra_reviewer", **extra) -> bytes:
    payload = {"hook_event_name": "SubagentStart"}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    payload.update(extra)
    return json.dumps(payload).encode()


class ReviewerStartContractTests(unittest.TestCase):
    def assert_injected(self, raw: bytes):
        proc = run_hook(raw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        decision = json.loads(proc.stdout)
        self.assertEqual(list(decision), ["hookSpecificOutput"])
        body = decision["hookSpecificOutput"]
        self.assertEqual(list(body), ["hookEventName", "additionalContext"])
        self.assertEqual(body["hookEventName"], "SubagentStart")
        context = body["additionalContext"]
        self.assertIn("PASS (code)", context)
        self.assertIn("REQUEST_CHANGES (code)", context)
        self.assertIn("BLOCKED (code)", context)
        self.assertIn("(final)", context)
        self.assertIn("review_kind", context)
        self.assertNotIn("continue", decision)
        self.assertNotIn("continue", body)
        return context

    def assert_unchanged(self, raw: bytes):
        proc = run_hook(raw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertEqual(proc.stderr, b"")

    def test_reviewer_start_injects_first_line_contract(self):
        self.assert_injected(event("astra_reviewer", agent_id="ag_1"))

    def test_missing_agent_type_still_injects_when_matcher_selected(self):
        # Installed matcher already selected astra_reviewer; payload may omit it.
        self.assert_injected(event(None))

    def test_non_reviewer_agent_is_left_alone(self):
        for agent_type in ("astra_locator", "astra_worker", "explorer"):
            with self.subTest(agent_type=agent_type):
                self.assert_unchanged(event(agent_type))

    def test_non_subagent_start_event_is_left_alone(self):
        self.assert_unchanged(event(
            "astra_reviewer",
            hook_event_name="SubagentStop",
        ))

    def test_malformed_input_fails_open(self):
        samples = [
            b"",
            b"not json",
            b"[]",
            b"null",
            b'"string"',
            b"{}",
            b'{"agent_type": null}',
            b'{"hook_event_name": "SubagentStart", "agent_type": ["astra_reviewer"]}',
            b'{"hook_event_name": "SubagentStart" trailing',
            bytes([0xFF, 0xFE]),
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                self.assert_unchanged(raw)


if __name__ == "__main__":
    unittest.main()
