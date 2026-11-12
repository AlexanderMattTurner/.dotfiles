"""apps/claude-user/hooks/review-edit.py — the Edit|Write reviewer command hook.

Drives the real script with hook-event JSON on stdin. `claude` is a PATH stub
that answers a fixed verdict and records its stdin, because the real one bills a
model call per edit; the stub stands in for the model's answer only.
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
HOOK = DOTFILES / "apps" / "claude-user" / "hooks" / "review-edit.py"
SETTINGS = DOTFILES / "apps" / "claude-user" / "settings.json"
EVENT = {"tool_name": "Edit", "tool_input": {"file_path": "/x/a.py", "new_string": "y = f(1)"}}


def _run(tmp_path: Path, stub_body: str | None) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if stub_body is not None:
        stub = bin_dir / "claude"
        stub.write_text(f"#!/bin/sh\ncat > {tmp_path}/stdin\n{stub_body}\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(EVENT),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


@pytest.mark.parametrize("answer", ["allow", "Allow.", '"allow"', ""])
def test_an_allow_answer_emits_nothing(tmp_path: Path, answer: str) -> None:
    assert _run(tmp_path, f"echo '{answer}'").stdout == ""


def test_a_denial_blocks_only_this_call_with_its_reason(tmp_path: Path) -> None:
    proc = _run(tmp_path, "echo 'Line 1 hard-codes an API key.'")
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "Line 1 hard-codes an API key." in out["permissionDecisionReason"]
    assert '"new_string": "y = f(1)"' in (tmp_path / "stdin").read_text()


@pytest.mark.parametrize("stub_body", ["exit 1", None])
def test_a_review_that_cannot_run_allows_and_says_so(tmp_path: Path, stub_body: str | None) -> None:
    proc = _run(tmp_path, stub_body)
    assert proc.stdout == ""
    assert "review did not run" in proc.stderr


def test_settings_wire_the_command_hook_and_symlinks_install_it() -> None:
    entries = json.loads(SETTINGS.read_text())["hooks"]["PreToolUse"]
    (edit,) = [e for e in entries if e["matcher"] == "Edit|Write"]
    assert edit["hooks"] == [
        {"type": "command", "command": "python3 ~/.claude/hooks/review-edit.py", "timeout": 60}
    ]
    links = subprocess.check_output(
        ["bash", "-c", f"DOTFILES_DIR={DOTFILES}; source {DOTFILES}/bin/lib/symlinks.sh; managed_symlinks"],
        text=True,
    )
    assert f"$HOME/.claude/hooks/review-edit.py|{HOOK}|".replace("$HOME", os.environ["HOME"]) in links
