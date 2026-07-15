"""bin/lib/claude-guard-pin.sh — the one invariant CLAUDE.md calls out by
name: "doctor.bash FAILs when the checkout drifts from the pin."

Drives the classifier directly against throwaway git repos rather than the
real claude-guard/ checkout, matching the tailscale_health test pattern.
"""

import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
CLAUDE_GUARD_PIN_SH = REPO / "bin" / "lib" / "claude-guard-pin.sh"


def _status(cg_dir: Path, ref_file: Path) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{CLAUDE_GUARD_PIN_SH}"; claude_guard_pin_status "{cg_dir}" "{ref_file}"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _init_repo(path: Path) -> str:
    """Create a tiny git repo at `path`; return its HEAD sha."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=path,
        check=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()


def test_not_cloned_when_no_git_dir(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    ref_file = tmp_path / "claude-guard.ref"
    ref_file.write_text("deadbeef\n")
    assert _status(cg_dir, ref_file) == "not-cloned"


def test_missing_ref_when_ref_file_absent(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    ref_file = tmp_path / "claude-guard.ref"
    assert _status(cg_dir, ref_file) == "missing-ref"


def test_pinned_when_head_matches_ref(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    head = _init_repo(cg_dir)
    ref_file = tmp_path / "claude-guard.ref"
    ref_file.write_text(head + "\n")
    assert _status(cg_dir, ref_file) == "pinned"


def test_drifted_when_head_differs_from_ref(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    ref_file = tmp_path / "claude-guard.ref"
    ref_file.write_text("0" * 40 + "\n")
    assert _status(cg_dir, ref_file) == "drifted"


CANONICAL = "https://example.invalid/new/canonical.git"
STALE = "https://example.invalid/old/renamed.git"


def _origin_status(cg_dir: Path) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{CLAUDE_GUARD_PIN_SH}"; '
            f'claude_guard_origin_status "{cg_dir}" "{CANONICAL}"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repoint(cg_dir: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{CLAUDE_GUARD_PIN_SH}"; '
            f'claude_guard_repoint_origin "{cg_dir}" "{CANONICAL}"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def _origin_url(cg_dir: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cg_dir), "remote", "get-url", "origin"], text=True
    ).strip()


def test_origin_not_cloned_when_no_git_dir(tmp_path: Path) -> None:
    assert _origin_status(tmp_path / "claude-guard") == "not-cloned"


def test_origin_no_origin_when_remote_absent(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    assert _origin_status(cg_dir) == "no-origin"


def test_origin_canonical_when_url_matches(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    subprocess.run(
        ["git", "remote", "add", "origin", CANONICAL], cwd=cg_dir, check=True
    )
    assert _origin_status(cg_dir) == "canonical"


def test_origin_stale_when_url_differs(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    subprocess.run(["git", "remote", "add", "origin", STALE], cwd=cg_dir, check=True)
    assert _origin_status(cg_dir) == "stale"


def test_repoint_rewrites_a_stale_origin(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    subprocess.run(["git", "remote", "add", "origin", STALE], cwd=cg_dir, check=True)
    _repoint(cg_dir)
    assert _origin_url(cg_dir) == CANONICAL
    assert _origin_status(cg_dir) == "canonical"


def test_repoint_adds_origin_when_remote_absent(tmp_path: Path) -> None:
    cg_dir = tmp_path / "claude-guard"
    _init_repo(cg_dir)
    _repoint(cg_dir)
    assert _origin_url(cg_dir) == CANONICAL
    assert _origin_status(cg_dir) == "canonical"


def test_default_canonical_url_is_the_renamed_repo() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{CLAUDE_GUARD_PIN_SH}"; printf %s "$CLAUDE_GUARD_CANONICAL_URL"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "https://github.com/AlexanderMattTurner/agent-glovebox.git"
