"""bin/cache-prune.bash runs each installed tool's prune and survives failures.

Drives the script against stub tools on PATH, so no real store is touched.
"""

import stat
import subprocess
from pathlib import Path

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = DOTFILES / "bin" / "cache-prune.bash"


def _stub(bindir: Path, name: str, log: Path, rc: int = 0) -> None:
    stub = bindir / name
    stub.write_text(f'#!/bin/sh\necho "{name} $*" >>"{log}"\nexit {rc}\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


def _run(bindir: Path) -> subprocess.CompletedProcess[str]:
    # Only the stubs are visible, so no real store is ever pruned.
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env={"CACHE_PRUNE_PATH": str(bindir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_runs_every_prune_skips_missing_and_reports_failure(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bindir, "pnpm", log, rc=1)
    _stub(bindir, "uv", log)
    _stub(bindir, "pre-commit", log)
    proc = _run(bindir)
    assert log.read_text().splitlines() == [
        "pnpm store prune",
        "uv cache prune",
        "pre-commit gc",
    ]
    assert "skip: brew not installed" in proc.stdout
    assert "FAIL: pnpm store prune exited 1" in proc.stdout
    assert proc.returncode == 1


def test_exits_zero_when_every_prune_succeeds(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("pnpm", "uv", "brew", "pre-commit"):
        _stub(bindir, name, log)
    proc = _run(bindir)
    assert proc.returncode == 0, proc.stdout
    assert len(log.read_text().splitlines()) == 4
