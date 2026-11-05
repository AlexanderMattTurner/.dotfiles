#!/usr/bin/env python3
"""PreToolUse(Edit|Write) reviewer: a model judges the one hunk and may deny it.

A command hook rather than a `type: prompt` hook, because a prompt hook's denial
ends the whole turn; a `permissionDecision: deny` blocks only this call, so the
agent can fix the hunk and continue. A review that cannot run allows, as the
prompt hook's own failures did, and says why on stderr.
"""

import json
import subprocess
import sys

PROMPT = """You see ONE edit hunk. You do NOT see the agent's full file, the rest of the file, the session history, or earlier edits in this turn. The agent does. Default strongly to "allow". You should be denying maybe 1-in-200 edits, not 1-in-5.

The only valid denials describe concrete, user-facing harm that the visible hunk itself introduces:
- A hard-coded credential VALUE pasted into the diff (an actual key/token/password — not a placeholder, not a key name in an error string, not an env-var reference).
- An obvious injection sink: untrusted input (req.body, argv, env) reaching exec / eval / SQL via string concatenation, in the hunk itself.
- Syntax that won't parse — judged conservatively against the file's actual language. If unsure whether something parses, ALLOW.

ALWAYS ALLOW — never deny on any of these grounds:
- Any reference to a symbol, variable, import, function, type, or shell var not defined in the visible hunk. It's almost certainly defined elsewhere in the file or imported. The agent sees the full file; you don't.
- Removed code: deleted guards, simplified branches, dropped error handling, reverted logic, bare assignments replacing guarded ones. Could be a deliberate refactor, rollback, simplification, or regression-test step.
- Renames, even if the call sites or imports aren't visible.
- Async or top-level constructs valid in modern JS/TS: top-level await, async IIFE, `await expect(...).rejects.toThrow()`, `void (async () => {...})()`.
- Initial-value changes (e.g. `let x = 0` instead of `let x = window.innerWidth`) — agent's deliberate choice.
- Style, naming, comments, JSDoc tone, ergonomics, maintainability concerns.
- Performance concerns smaller than ~1s of user-visible work.
- Overrides of standard CLI commands, env-var trust, key NAMES inside log/error strings.
- Anything you'd phrase as "could be improved", "consider", "might be cleaner", "could mask", "could break if", "may cause", "unclear whether".

If denying, output exactly one sentence naming the specific harm + diff line numbers. Otherwise output exactly "allow".

The hunk (the tool call's input, as JSON):
"""

# No tools in the review session, so no PreToolUse hook (this one included) fires inside it.
REVIEW_ARGV = ["claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence"]
TIMEOUT_S = 50


def main() -> None:
    hunk = json.dumps(json.load(sys.stdin).get("tool_input", {}), indent=1)
    try:
        review = subprocess.run(
            REVIEW_ARGV,
            input=PROMPT + hunk,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"review-edit: review did not run, allowing: {exc}", file=sys.stderr)
        return
    verdict = review.stdout.strip()
    if not verdict or verdict.strip('."').lower() == "allow":
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Edit reviewer: {verdict} Fix the hunk and re-issue it.",
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
