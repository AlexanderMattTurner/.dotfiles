"""bin/disk-hygiene.bash — scheduled prune + stale AI-agent worktree reaper.

Drives the real script against real git repos and worktrees (a stale,
clean, pushed worktree is the only thing it may ever remove), and against
stub prune tools on PATH to prove each tool runs once and a failing one
does not stop the rest. See CLAUDE.md "Disk space" for why this job exists:
on 2026-10-04 the machine filled to 99% full because nothing ran the
prunes doctor only used to name.
"""

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = DOTFILES / "bin" / "disk-hygiene.bash"

REAL_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
DAY = 86400


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"},
        check=True,
    )


def _set_mtime(path: Path, age_seconds: int) -> None:
    when = time.time() - age_seconds
    os.utime(path, (when, when))


def _wt_root(tmp_path: Path) -> Path:
    """A worktree directory the script's `/tmp/claude-worktrees/` filter
    matches — the reaper only looks under that substring or
    `/.claude/worktrees/`, so fixture worktrees must live under one."""
    root = tmp_path / "tmp" / "claude-worktrees"
    root.mkdir(parents=True)
    return root


def _gb_wt_root(tmp_path: Path) -> Path:
    """A worktree directory matching the `/.claude/worktrees/` filter."""
    root = tmp_path / "gb" / ".claude" / "worktrees"
    root.mkdir(parents=True)
    return root


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'origin' plus a clone with one commit pushed to it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")

    repo = tmp_path / "dotfiles"
    _git(tmp_path, "clone", str(origin), str(repo))
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "origin", "main")
    return repo, origin


def _add_worktree(
    repo: Path,
    worktrees_root: Path,
    name: str,
    *,
    age_days: float = 10,
    untracked: bool = False,
    dirty_change: bool = False,
    local_commit: bool = False,
    pushed: bool = True,
    locked: bool = False,
) -> Path:
    wt = worktrees_root / name
    _git(repo, "worktree", "add", "-b", f"branch-{name}", str(wt))

    # A committed-but-unpushed change, so HEAD diverges from any ref already
    # on the remote (status stays clean).
    if local_commit:
        (wt / "README.md").write_text("changed\n")
        _git(wt, "add", "README.md")
        _git(wt, "commit", "-m", "local change")

    # An uncommitted modification to a tracked file (status is dirty).
    if dirty_change:
        (wt / "README.md").write_text("dirty\n")

    if untracked:
        (wt / "scratch.txt").write_text("junk\n")

    if pushed:
        _git(wt, "push", "origin", f"branch-{name}")

    if locked:
        _git(wt, "worktree", "lock", str(wt))

    # Backdate the directory itself; git's own plumbing files would race the
    # reaper's own git status/rev-parse calls if mtimes were touched after.
    _set_mtime(wt, age_days * DAY)
    return wt


PRUNE_STUB = """#!/bin/sh
echo "$0 $*" >>"$CALLS_LOG"
exit {exit_code}
"""


def _make_stub(bin_dir: Path, name: str, exit_code: int = 0) -> None:
    stub = bin_dir / name
    stub.write_text(PRUNE_STUB.format(exit_code=exit_code))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


def _run(
    tmp_path: Path,
    dotfiles_repo: Path,
    *,
    glovebox_repo: Path | None = None,
    extra_args: tuple[str, ...] = (),
    stub_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    path = f"{stub_dir}:{REAL_PATH}" if stub_dir else REAL_PATH
    env = {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "DISK_HYGIENE_DOTFILES_DIR": str(dotfiles_repo),
        "DISK_HYGIENE_GLOVEBOX_DIR": str(glovebox_repo or (tmp_path / "no-glovebox")),
        "DISK_SPACE_TARGET": str(tmp_path),
        **(extra_env or {}),
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(SCRIPT), *extra_args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _worktree_paths(repo: Path) -> list[str]:
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    return [line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")]


# ── reaper: who gets removed, who is kept ───────────────────────────────────


def test_removes_clean_old_pushed_worktree(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "clean-old")

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert not wt.exists(), result.stdout
    assert str(wt) not in _worktree_paths(repo)


def test_keeps_worktree_with_untracked_file(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "untracked", untracked=True)

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert wt.exists()
    assert str(wt) in _worktree_paths(repo)


def test_keeps_worktree_with_tracked_change(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "tracked-change", dirty_change=True)

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert wt.exists()


def test_keeps_worktree_with_unpushed_head(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    # A clean-but-unpushed commit: local_commit commits so the working tree
    # is clean, pushed=False leaves that commit unreachable from any remote
    # branch (otherwise its HEAD would equal the already-pushed initial
    # commit and pass the "contained in a remote branch" check by accident).
    wt = _add_worktree(repo, wt_root, "unpushed", local_commit=True, pushed=False)

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert wt.exists()


def test_keeps_locked_worktree(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "locked", locked=True)

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert wt.exists()


def test_keeps_worktree_newer_than_stale_threshold(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "fresh", age_days=1)

    result = _run(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert wt.exists()


def test_dry_run_removes_nothing(tmp_path: Path) -> None:
    repo, _origin = _make_repo(tmp_path)
    wt_root = _wt_root(tmp_path)
    wt = _add_worktree(repo, wt_root, "clean-old")

    result = _run(tmp_path, repo, extra_args=("--dry-run",))
    assert result.returncode == 0, result.stderr
    assert wt.exists()
    assert str(wt) in _worktree_paths(repo)
    assert "would remove" in result.stdout


def test_both_repos_are_swept(tmp_path: Path) -> None:
    """A stale worktree under agent-glovebox is reaped too, not just dotfiles."""
    repo, _origin = _make_repo(tmp_path)
    gb_repo, _gb_origin = _make_repo(tmp_path / "gb-repo")
    wt_root = _wt_root(tmp_path)
    dotfiles_wt = _add_worktree(repo, wt_root, "d-clean-old")
    gb_wt_root = _gb_wt_root(tmp_path)
    gb_wt = _add_worktree(gb_repo, gb_wt_root, "g-clean-old")

    result = _run(tmp_path, repo, glovebox_repo=gb_repo)
    assert result.returncode == 0, result.stderr
    assert not dotfiles_wt.exists()
    assert not gb_wt.exists()


# ── prune tool stubs ─────────────────────────────────────────────────────────


@pytest.fixture
def stub_tools(tmp_path: Path) -> tuple[Path, Path]:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    calls_log = tmp_path / "calls.log"
    for name, rc in (
        ("uv", 0),
        ("pnpm", 0),
        ("brew", 1),  # one deliberately fails
        ("pre-commit", 0),
        ("limactl", 0),
    ):
        _make_stub(stub_dir, name, exit_code=rc)
    return stub_dir, calls_log


def test_every_prune_tool_runs_once_and_a_failure_does_not_stop_the_rest(
    tmp_path: Path, stub_tools: tuple[Path, Path]
) -> None:
    stub_dir, calls_log = stub_tools
    repo, _origin = _make_repo(tmp_path)

    result = _run(
        tmp_path,
        repo,
        stub_dir=stub_dir,
        extra_env={"CALLS_LOG": str(calls_log)},
    )
    assert result.returncode == 0, result.stderr

    calls = calls_log.read_text().splitlines() if calls_log.exists() else []
    called_names = [Path(line.split()[0]).name for line in calls]
    for tool in ("uv", "pnpm", "brew", "pre-commit", "limactl"):
        assert called_names.count(tool) == 1, f"{tool} called {called_names.count(tool)} times: {calls}"

    # brew "failed" (stub exits 1); the script must still report completion.
    assert "FAILED" in result.stdout
    assert "done" in result.stdout


def test_dry_run_does_not_invoke_prune_tools(
    tmp_path: Path, stub_tools: tuple[Path, Path]
) -> None:
    stub_dir, calls_log = stub_tools
    repo, _origin = _make_repo(tmp_path)

    result = _run(
        tmp_path,
        repo,
        extra_args=("--dry-run",),
        stub_dir=stub_dir,
        extra_env={"CALLS_LOG": str(calls_log)},
    )
    assert result.returncode == 0, result.stderr
    assert not calls_log.exists() or calls_log.read_text() == ""
    assert "would run" in result.stdout
