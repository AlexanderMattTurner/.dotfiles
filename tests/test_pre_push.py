"""bin/pre-push — devcontainer force-push / branch-deletion guard.

CLAUDE.md documents this as a security control: inside the devcontainer it
blocks remote-ref deletion and non-fast-forward pushes at the OS level "so
deny-list string-construction bypasses can't delete remote branches." That
guard had zero test coverage — a regression here would silently disable the
anti-force-push/anti-delete protection. Drives the real script end-to-end
against a throwaway git repo, stubbing out bin/lint.bash (the script's final
step) so these stay fast, hermetic unit tests instead of full lint runs.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
PRE_PUSH = REPO / "bin" / "pre-push"

ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "bin").mkdir()
    lint_stub = repo / "bin" / "lint.bash"
    lint_stub.write_text("#!/bin/bash\necho STUB_LINT_RAN\n")
    lint_stub.chmod(0o755)
    (repo / "f.txt").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _run_pre_push(repo: Path, stdin: str, devcontainer: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if devcontainer is None:
        env.pop("DEVCONTAINER", None)
    else:
        env["DEVCONTAINER"] = devcontainer
    return subprocess.run(
        ["bash", str(PRE_PUSH)],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_blocks_branch_deletion_in_devcontainer(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    stdin = f"refs/heads/trunk {ZERO} refs/heads/trunk {head}\n"

    result = _run_pre_push(repo, stdin, devcontainer="true")

    assert result.returncode == 1
    assert "remote ref deletion blocked" in result.stderr
    assert "STUB_LINT_RAN" not in result.stdout


def test_allows_branch_deletion_outside_devcontainer(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    stdin = f"refs/heads/trunk {ZERO} refs/heads/trunk {head}\n"

    result = _run_pre_push(repo, stdin, devcontainer=None)

    assert result.returncode == 0
    assert "STUB_LINT_RAN" in result.stdout


def test_blocks_non_fast_forward_push_in_devcontainer(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "branch-b")
    (repo / "f.txt").write_text("b\n")
    _git(repo, "commit", "-q", "-am", "b")
    sha_b = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", base, "-b", "branch-c")
    (repo / "f.txt").write_text("c\n")
    _git(repo, "commit", "-q", "-am", "c")
    sha_c = _git(repo, "rev-parse", "HEAD")

    # Pretend the remote is at sha_c and we're pushing sha_b — sha_c is not
    # an ancestor of sha_b, so this is a rewrite/force-push from the remote's
    # perspective.
    stdin = f"refs/heads/trunk {sha_b} refs/heads/trunk {sha_c}\n"

    result = _run_pre_push(repo, stdin, devcontainer="true")

    assert result.returncode == 1
    assert "non-fast-forward push blocked" in result.stderr
    assert "STUB_LINT_RAN" not in result.stdout


def test_allows_fast_forward_push_in_devcontainer(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "f.txt").write_text("b\n")
    _git(repo, "commit", "-q", "-am", "b")
    sha_b = _git(repo, "rev-parse", "HEAD")

    stdin = f"refs/heads/trunk {sha_b} refs/heads/trunk {base}\n"

    result = _run_pre_push(repo, stdin, devcontainer="true")

    assert result.returncode == 0
    assert "STUB_LINT_RAN" in result.stdout
