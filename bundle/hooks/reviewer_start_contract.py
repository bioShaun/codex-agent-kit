#!/usr/bin/env python3
"""SubagentStart: inject astra_reviewer first-line contract.

Codex SubagentStart matcher filters agent_type. This kit installs
matcher ^astra_reviewer$ (see developers.openai.com/codex/hooks).

Stdout JSON shape:
  {"hookSpecificOutput":{"hookEventName":"SubagentStart",
   "additionalContext":"..."}}
Plain text is also accepted as additional context; JSON is preferred so the
event name is explicit. continue: false is parsed by Codex but does not stop
the subagent; this hook never relies on it as a gate.

Malformed or unexpected input fails open: exit 0 and write nothing. A failed
hook must not prevent Reviewer from starting.
"""

from __future__ import annotations

import json
import sys

REVIEWER_AGENT_TYPE = "astra_reviewer"

# Keep aligned with bundle/protocols/review.md first-line contract.
ADDITIONAL_CONTEXT = (
    "Reviewer first-line contract (codex-agent-kit): the first line of the "
    "result must be exactly one of PASS (code), REQUEST_CHANGES (code), "
    "BLOCKED (code), PASS (final), REQUEST_CHANGES (final), or BLOCKED (final), "
    "matching TaskSpec review_kind. One space before (code) or (final); no "
    "Markdown and no leading blank line. Bare BLOCKED (no kind suffix) only "
    "when required review fields are missing or invalid. Root matches "
    "^(PASS|REQUEST_CHANGES|BLOCKED) \\((code|final)\\)$ on the first line."
)


def decision(event: object) -> dict | None:
    """Return the additionalContext payload, or None to leave startup alone."""
    if not isinstance(event, dict):
        return None
    if "hook_event_name" in event and event["hook_event_name"] != "SubagentStart":
        return None
    # Missing agent_type still injects when the event is SubagentStart: the
    # installed matcher already selected astra_reviewer. An explicit foreign
    # agent_type must not gain the contract.
    if "agent_type" in event and event["agent_type"] != REVIEWER_AGENT_TYPE:
        return None
    # Require a positive signal so empty/unrelated JSON fails open.
    if (event.get("hook_event_name") != "SubagentStart"
            and event.get("agent_type") != REVIEWER_AGENT_TYPE):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": ADDITIONAL_CONTEXT,
        }
    }


def main() -> int:
    try:
        payload = decision(json.loads(sys.stdin.buffer.read()))
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False) + "\n"
            sys.stdout.buffer.write(encoded.encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
