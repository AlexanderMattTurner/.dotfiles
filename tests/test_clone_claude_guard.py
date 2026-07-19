"""Git-state logic in bin/clone-claude-guard.bash.

claude-guard carries the AI-safety monitor and is pinned like a
security-critical dependency (see CLAUDE.md), so the script's decisions —
clean up an interrupted clone, honor --bump, detach to the pinned ref, and
crucially DON'T clobber a checkout with local changes — are worth guarding.
Only the sibling claude-guard-pin.sh version parsing was tested before this.

Each test builds a throwaway DOTFILES_DIR containing just the script + retry.sh
and runs it with a stub `git` on PATH that logs its argv and answers
rev-parse/status from env vars, so no real network or repo is touched.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)

PIN = "1111111111111111111111111111111111111111"
OTHER = "2222222222222222222222222222222222222222"

GIT_STUB = r"""#!/bin/bash
# Stub git: log argv, then answer just the subcommands the script uses.
printf '%s\n' "$*" >> "$GIT_LOG"
args=("$@")
# Strip a leading `-C <dir>`.
if [[ "${args[0]}" == "-C" ]]; then
    args=("${args[@]:2}")
fi
case "${args[0]}" in
clone)
    # Last arg is the destination; fake a checkout there.
    dest="${args[-1]}"
    mkdir -p "$dest/.git"
    exit 0
    ;;
fetch) exit 0 ;;
rev-parse)
    if [[ "${args[1]}" == "origin/main" ]]; then
        printf '%s\n' "$GIT_ORIGIN_MAIN"
    else
        printf '%s\n' "$GIT_HEAD"
    fi
    ;;
status) printf '%s' "$GIT_STATUS" ;;
checkout) exit 0 ;;
*) exit 0 ;;
esac
"""


def _make_dotfiles(tmp_path: Path, *, ref: str, precreate_git: bool, leftover: bool):
    dot = tmp_path / "dotfiles"
    (dot / "bin" / "lib").mkdir(parents=True)
    shutil.copy(REPO / "bin" / "clone-claude-guard.bash", dot / "bin")
    shutil.copy(REPO / "bin" / "lib" / "retry.sh", dot / "bin" / "lib")
    (dot / "claude-guard.ref").write_text(ref + "\n")
    cg = dot / "claude-guard"
    if precreate_git:
        (cg / ".git").mkdir(parents=True)
    elif leftover:
        cg.mkdir()
        (cg / "stale-file").write_text("from an interrupted clone")
    return dot


def _run(dot: Path, bindir: Path, *, args=(), **git_env):
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GIT_LOG": str(bindir.parent / "git.log"),
        "GIT_ORIGIN_MAIN": git_env.get("origin_main", OTHER),
        "GIT_HEAD": git_env.get("head", PIN),
        "GIT_STATUS": git_env.get("status", ""),
    }
    return subprocess.run(
        ["bash", str(dot / "bin" / "clone-claude-guard.bash"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    b = tmp_path / "stubbin"
    b.mkdir()
    stub = b / "git"
    stub.write_text(GIT_STUB)
    stub.chmod(0o755)
    return b


def _git_log(bindir: Path) -> str:
    log = bindir.parent / "git.log"
    return log.read_text() if log.exists() else ""


def test_leftover_non_git_dir_is_removed_then_cloned(tmp_path: Path, bindir: Path) -> None:
    dot = _make_dotfiles(tmp_path, ref=PIN, precreate_git=False, leftover=True)
    # HEAD == pin after the clone, so it exits 0 without a checkout.
    result = _run(dot, bindir, head=PIN)
    assert result.returncode == 0, result.stderr
    assert "Removing leftover non-git" in result.stdout
    # The stale file is gone and a fresh clone (.git) took its place.
    assert not (dot / "claude-guard" / "stale-file").exists()
    assert (dot / "claude-guard" / ".git").is_dir()
    assert "clone" in _git_log(bindir)


def test_already_at_pin_is_a_noop(tmp_path: Path, bindir: Path) -> None:
    dot = _make_dotfiles(tmp_path, ref=PIN, precreate_git=True, leftover=False)
    result = _run(dot, bindir, head=PIN)
    assert result.returncode == 0, result.stderr
    # No checkout when HEAD already matches the pin.
    assert "checkout" not in _git_log(bindir)


def test_drift_detaches_to_pinned_ref(tmp_path: Path, bindir: Path) -> None:
    dot = _make_dotfiles(tmp_path, ref=PIN, precreate_git=True, leftover=False)
    # HEAD drifted away from the pin, working tree clean → detach to the pin.
    result = _run(dot, bindir, head=OTHER, status="")
    assert result.returncode == 0, result.stderr
    log = _git_log(bindir)
    assert "checkout --quiet --detach " + PIN in log
    assert PIN[:7] in result.stdout


def test_local_changes_block_clobber(tmp_path: Path, bindir: Path) -> None:
    dot = _make_dotfiles(tmp_path, ref=PIN, precreate_git=True, leftover=False)
    # Drifted HEAD but a dirty working tree → must NOT check out over local work.
    result = _run(dot, bindir, head=OTHER, status=" M monitor.bash\n")
    assert result.returncode == 0, result.stderr
    assert "local changes" in result.stderr
    assert "checkout" not in _git_log(bindir)


def test_bump_writes_origin_main_to_ref_file(tmp_path: Path, bindir: Path) -> None:
    dot = _make_dotfiles(tmp_path, ref=PIN, precreate_git=True, leftover=False)
    # --bump rewrites the ref file to origin/main's HEAD. After the bump the
    # pin equals origin/main; HEAD is set to match so it exits without checkout.
    result = _run(dot, bindir, args=("--bump",), origin_main=OTHER, head=OTHER)
    assert result.returncode == 0, result.stderr
    assert (dot / "claude-guard.ref").read_text().strip() == OTHER
    assert "bumped to" in result.stdout
