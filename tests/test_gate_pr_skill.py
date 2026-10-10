"""apps/claude-user/hooks/gate-pr-skill.py — the `gh pr create` PreToolUse gate.

Drives the real script with hook-event JSON on stdin and asserts the verdict it
emits, because that JSON *is* the contract Claude Code reads: a deny object
blocks the tool call, empty output allows it.

Also pins the wiring, which is the part that fails silently: settings.json names
a path under $HOME, so the hook is a no-op on any machine where symlinks.sh
doesn't put the script there.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
HOOK = DOTFILES / "apps" / "claude-user" / "hooks" / "gate-pr-skill.py"
SETTINGS = DOTFILES / "apps" / "claude-user" / "settings.json"


def _verdict(command: str) -> dict | None:
    """Run the hook on a Bash event for `command`; return its parsed verdict."""
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _managed_pairs(home: str) -> dict[str, str]:
    """Map each target managed_symlinks emits under `home` to its source."""
    # Run managed_symlinks rather than grepping it: the list is generated (it
    # branches on uname and globs .aider*), so only its output is the contract.
    # setup.bash, doctor.bash and uninstall.bash all iterate exactly this.
    #
    # A synthetic HOME and an explicit env, because the developer's own shell
    # exports DOTFILES_DIR: inheriting it lets this pass for the wrong reason
    # on a laptop and fail on a runner.
    emitted = subprocess.run(
        ["bash", "-c", f'. "{DOTFILES}/bin/lib/symlinks.sh" && managed_symlinks'],
        env={"PATH": os.environ["PATH"], "HOME": home, "DOTFILES_DIR": str(DOTFILES)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert emitted.returncode == 0, emitted.stderr
    return dict(
        line.split("|")[:2] for line in emitted.stdout.splitlines() if "|" in line
    )


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill",
        "gh pr create",
        "cd /tmp && gh pr create --title x",
        "gh   pr\tcreate --draft",
        "git push && gh pr create",
    ],
)
def test_unmarked_pr_create_is_denied(command: str) -> None:
    verdict = _verdict(command)
    assert verdict is not None, f"{command!r} slipped through the gate"
    hook_out = verdict["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    assert "pr-creation" in hook_out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        # The sanctioned escape hatch the skill itself uses.
        "PR_VIA_SKILL=1 gh pr create --fill",
        # Word-boundary cases: a path *ending* in gh, and other gh subcommands.
        "/usr/local/bin/ungh pr create",
        "gh pr list",
        "gh pr edit 12 --body x",
        "echo gh pr createx",
        "git commit -m 'gh pr creating'",
    ],
)
def test_allowed_commands_produce_no_verdict(command: str) -> None:
    assert _verdict(command) is None, f"{command!r} was wrongly denied"


def test_non_string_command_is_allowed() -> None:
    """A malformed event must not deny; the gate is not the schema validator."""
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"command": ["gh", "pr", "create"]}}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


def test_settings_hook_command_resolves_to_a_managed_symlink() -> None:
    """The class of bug this whole file guards: a hook pointing at nothing.

    settings.json is tracked and symlinked onto every machine, but it names the
    script by an absolute $HOME path. Unless symlinks.sh also links the script
    there, the hook silently fails on every machine but the one it was written
    on — and a hook that errors out does not block anything.
    """
    settings = json.loads(SETTINGS.read_text())
    commands = [
        hook["command"]
        for matchers in settings["hooks"].values()
        for matcher in matchers
        for hook in matcher.get("hooks", [])
        if hook.get("type") == "command"
    ]
    referencing = [c for c in commands if HOOK.name in c]
    assert referencing, f"settings.json no longer invokes {HOOK.name}"

    home = "/nonexistent-home"
    pairs = _managed_pairs(home)
    target = f"{home}/.claude/hooks/{HOOK.name}"
    assert pairs.get(target) == str(HOOK), (
        f"{target} is not linked to {HOOK} by managed_symlinks"
    )
    for command in referencing:
        assert "~/.claude/hooks/" in command, command


def test_the_skill_the_gate_requires_is_a_managed_symlink() -> None:
    """The gate is user-level, so the skill it demands must be too.

    The skill otherwise loads only in a repo that carries it; anywhere else the
    gate denies every PR it sees, with no way through.
    """
    skill = DOTFILES / ".claude" / "skills" / "pr-creation"
    assert (skill / "SKILL.md").is_file()
    pairs = _managed_pairs("/nonexistent-home")
    assert pairs.get("/nonexistent-home/.claude/skills/pr-creation") == str(skill)
