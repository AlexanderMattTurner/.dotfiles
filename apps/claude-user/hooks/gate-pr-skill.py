#!/usr/bin/env python3
"""PreToolUse(Bash) gate: `gh pr create` must go through the /pr-creation skill.

Reads the hook event JSON on stdin. Prints a deny verdict when the command opens a
PR without the sanctioned PR_VIA_SKILL marker, and nothing otherwise, which allows.

This lives in a file rather than inline in settings.json because glovebox's sandbox
seeder refuses to lift a hook command carrying shell characters: it cannot see which
file such a command reads, so the hook would not run in a sandboxed session at all.
Python, not shell, because the guest image ships no jq.
"""

import json
import re
import sys

# `gh pr create` as its own word, so a path ending in "gh" does not match.
GH_PR_CREATE = re.compile(r"(?:^|[^0-9A-Za-z_])gh[ \t]+pr[ \t]+create(?:[ \t]|$)")

REASON = (
    "Use the /pr-creation skill before opening a PR; it runs the compress-critique-fix "
    "loop and creates the PR with the sanctioned PR_VIA_SKILL=1 marker."
)


def main() -> None:
    command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return
    if not GH_PR_CREATE.search(command) or "PR_VIA_SKILL" in command:
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON,
            }
        },
        sys.stdout,
    )


main()
