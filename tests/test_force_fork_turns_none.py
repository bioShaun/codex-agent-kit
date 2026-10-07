"""Rewrite contract for the fork_turns PreToolUse hook. Stdlib only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "bundle" / "hooks" / "force_fork_turns_none.py"


def run_hook(raw: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw,
        capture_output=True,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )


def event(tool_input: dict, **extra) -> bytes:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "collaborationspawn_agent",
        "tool_input": tool_input,
    }
    payload.update(extra)
    return json.dumps(payload).encode()


class ForceForkTurnsNoneTests(unittest.TestCase):
    def assert_rewritten(self, tool_input: dict, **extra):
        proc = run_hook(event(tool_input, **extra))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        decision = json.loads(proc.stdout)
        self.assertEqual(list(decision), ["hookSpecificOutput"])
        body = decision["hookSpecificOutput"]
        self.assertEqual(
            list(body),
            ["hookEventName", "permissionDecision", "updatedInput"],
        )
        self.assertEqual(body["hookEventName"], "PreToolUse")
        self.assertEqual(body["permissionDecision"], "allow")
        expected = dict(tool_input)
        expected["fork_turns"] = "none"
        self.assertEqual(body["updatedInput"], expected)
        return body["updatedInput"]

    def assert_unchanged(self, raw: bytes):
        proc = run_hook(raw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertEqual(proc.stderr, b"")

    def test_omitted_fork_turns_is_rewritten_to_none(self):
        updated = self.assert_rewritten({
            "agent_type": "astra_locator",
            "task_name": "omitted",
            "message": "reply hi",
        })
        self.assertEqual(updated["message"], "reply hi")
        self.assertEqual(updated["agent_type"], "astra_locator")

    def test_fork_turns_all_is_rewritten_to_none(self):
        self.assert_rewritten({
            "agent_type": "astra_reviewer",
            "task_name": "inherit",
            "fork_turns": "all",
            "message": "neutral spec",
        })

    def test_fork_turns_none_allows_without_output(self):
        self.assert_unchanged(event({
            "agent_type": "astra_reviewer",
            "task_name": "fresh",
            "fork_turns": "none",
            "message": "already fresh",
        }))

    def test_extra_fields_are_preserved(self):
        updated = self.assert_rewritten({
            "agent_type": "astra_locator",
            "task_name": "keep-extra",
            "message": "保留原文",
            "custom_flag": True,
            "meta": {"attempt": 1, "tags": ["a", "b"]},
        })
        self.assertEqual(updated["custom_flag"], True)
        self.assertEqual(updated["meta"], {"attempt": 1, "tags": ["a", "b"]})
        self.assertNotIn("tool_name", updated)
        self.assertNotIn("hook_event_name", updated)

    def test_malformed_input_fails_open(self):
        samples = [
            b"",
            b"not json",
            b"[]",
            b"null",
            b'"string"',
            b"{}",
            b'{"tool_input": null}',
            b'{"tool_input": "command"}',
            b'{"tool_input": ["fork_turns"]}',
            b'{"tool_input": {"agent_type": "astra_worker"}} trailing',
            bytes([0xFF, 0xFE]),
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                self.assert_unchanged(raw)

    def test_unverified_tool_names_are_not_rewritten(self):
        # Issue #4: ^Agent$ and ^spawn_agent$ did not fire. Do not invent an alias.
        tool_input = {
            "agent_type": "astra_locator",
            "task_name": "alias",
            "fork_turns": "all",
            "message": "hi",
        }
        for tool_name in ("spawn_agent", "Agent", "Bash"):
            with self.subTest(tool_name=tool_name):
                self.assert_unchanged(event(tool_input, tool_name=tool_name))

    def test_non_pre_tool_use_event_is_left_alone(self):
        self.assert_unchanged(event(
            {"agent_type": "astra_locator", "task_name": "x", "message": "hi"},
            hook_event_name="PostToolUse",
        ))


if __name__ == "__main__":
    unittest.main()
