#!/usr/bin/env python3
"""PreToolUse guardrail: force subagent fork_turns to "none".

Verified on Codex 0.160.1 with multi_agent_v2 (codex-agent-kit issue #4):

- The live tool_name is collaborationspawn_agent. Matchers ^Agent$ and
  ^spawn_agent$ did not fire. This script does not treat Agent or spawn_agent
  as aliases; that would invent a tool contract the test did not observe.
- tool_input is a JSON object with agent_type, task_name, message, and an
  optional fork_turns. When fork_turns is omitted the key is absent, and the
  child session_meta contained forked_from_id (full parent history).
- A rewrite is applied only for this exact stdout object:
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
  "permissionDecision":"allow","updatedInput":{...}}}.
  updatedInput without permissionDecision "allow" failed the hook and did not
  rewrite. updatedInput must be the original object with only fork_turns
  changed, because SpawnAgentArgs uses deny_unknown_fields.
- Top-level agent_type is the caller (absent on Root, a role name inside a
  subagent). This script does not read it. Nested spawns were not part of the
  verification; the same rewrite is used if an event arrives.
- Hooks are a guardrail, not an isolation boundary. Trust is granted once in
  /hooks against the hook definition hash. The TUI /hooks click-path was not
  verified; the test trusted the definition through the app-server API.

Malformed or unexpected input fails open: exit 0 and write nothing, including
no traceback. Codex treats a non-zero status, a traceback, or a partial
decision as a failed hook and continues the original tool call. Issue #4
showed that a failed rewrite leaves the spawn unchanged, so a full history
default stays in effect. Denying the spawn, or exiting non-zero, would turn a
parser mistake into a broken delegation. Exit 0 with empty stdout is the
documented "success, leave the call alone" result.
"""

from __future__ import annotations

import copy
import json
import sys

# Exact tool_name observed on Codex 0.160.1 multi_agent_v2. Not a prefix and
# not the documented Agent alias, which did not match in that test.
VERIFIED_TOOL_NAME = "collaborationspawn_agent"


def decision(event: object) -> dict | None:
    """Return the allow+updatedInput object, or None to leave the call alone."""
    if not isinstance(event, dict):
        return None
    # An explicit foreign event or tool must not gain a fork_turns field.
    # Missing tool_name still rewrites: the installed matcher already selected
    # the call, and issue #4's payload always included the verified name.
    if "hook_event_name" in event and event["hook_event_name"] != "PreToolUse":
        return None
    if "tool_name" in event and event["tool_name"] != VERIFIED_TOOL_NAME:
        return None
    if "tool_input" not in event or not isinstance(event["tool_input"], dict):
        return None
    tool_input = event["tool_input"]
    if tool_input.get("fork_turns") == "none":
        return None
    # Copy every original field. Adding or dropping keys fails closed inside
    # Codex (deny_unknown_fields) and would not force a fresh spawn.
    updated = copy.deepcopy(tool_input)
    updated["fork_turns"] = "none"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }


def main() -> int:
    try:
        payload = decision(json.loads(sys.stdin.buffer.read()))
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False) + "\n"
            sys.stdout.buffer.write(encoded.encode("utf-8"))
    except Exception:
        # Fail open. See the module docstring for why this is empty success.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
