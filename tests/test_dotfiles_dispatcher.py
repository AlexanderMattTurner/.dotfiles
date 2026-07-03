"""Routing and exit-code contract for the `dotfiles` dispatcher (bin/dotfiles).

The dispatcher is the single user-facing entry point for repo chores; a
routing regression (wrong script, wrong exit code, silent fall-through)
would be invisible to the other tests. Everything here runs read-only
paths: help/usage output and argument-validation failures of the
underlying scripts.
"""

import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REPO / "bin" / "dotfiles"), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_no_args_prints_usage_and_exits_2() -> None:
    result = _run()
    assert result.returncode == 2
    assert "usage: dotfiles" in result.stdout + result.stderr


def test_help_exits_0() -> None:
    for flag in ("help", "-h", "--help"):
        result = _run(flag)
        assert result.returncode == 0, f"{flag}: rc={result.returncode}"
        assert "subcommands" in result.stdout


def test_unknown_subcommand_exits_2() -> None:
    result = _run("frobnicate")
    assert result.returncode == 2
    assert "unknown subcommand" in result.stderr


def test_usage_lists_every_dispatched_subcommand() -> None:
    """Adding a case-arm without updating usage leaves the command
    undiscoverable; keep the two in sync."""
    usage = _run("help").stdout
    for sub in ("doctor", "uninstall", "link", "setup", "lint"):
        assert sub in usage, f"usage text missing subcommand: {sub}"


def test_setup_route_forwards_args() -> None:
    """`dotfiles setup --help` must reach setup.bash's usage (rc 0), and an
    unknown flag must surface setup.bash's rc 2 — proving args pass through
    and typos can't trigger a full install."""
    ok = _run("setup", "--help")
    assert ok.returncode == 0
    assert "setup.bash" in ok.stdout

    bad = _run("setup", "--bogus-flag")
    assert bad.returncode == 2
    assert "unknown argument" in bad.stderr


def test_doctor_route_forwards_args() -> None:
    """doctor.bash rejects unknown flags with rc 2; seeing that through the
    dispatcher proves the doctor arm execs the right script."""
    result = _run("doctor", "--bogus-flag")
    assert result.returncode == 2
    assert "unknown flag" in result.stderr
