"""bin/lib/mullvad.sh — the doctor-facing view of the Mullvad app.

Drives the lib against a stubbed `mullvad` CLI, same pattern as
tests/test_tailscale_health.py, so no real daemon is consulted.
"""

import stat
import subprocess
from pathlib import Path

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
MULLVAD_SH = DOTFILES / "bin" / "lib" / "mullvad.sh"


def _stub(tmp_path: Path, stdout: str) -> Path:
    stub = tmp_path / "mullvad"
    stub.write_text(f'#!/bin/sh\ncat <<"MV_EOF"\n{stdout}\nMV_EOF\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def _run(snippet: str, cli: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{MULLVAD_SH}" && {snippet}'],
        env={"PATH": "/usr/bin:/bin", "MULLVAD_CLI": str(cli)},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=5,
    )


def test_find_mullvad_reports_the_configured_cli(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "")
    proc = _run("find_mullvad", stub)
    assert proc.returncode == 0
    assert proc.stdout.strip() == str(stub)


def test_find_mullvad_fails_when_app_absent(tmp_path: Path) -> None:
    assert _run("find_mullvad", tmp_path / "no-such-mullvad").returncode != 0


def test_autoconnect_on(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "Autoconnect: on")
    assert _run('mullvad_autoconnect_enabled "$MULLVAD_CLI"', stub).returncode == 0


def test_autoconnect_off(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "Autoconnect: off")
    assert _run('mullvad_autoconnect_enabled "$MULLVAD_CLI"', stub).returncode != 0
