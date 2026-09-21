"""bin/lib/mullvad.sh — the doctor-facing view of the Mullvad app.

Drives the lib against a stubbed `mullvad` CLI, same pattern as
tests/test_tailscale_health.py, so no real daemon is consulted.
"""

import stat
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
MULLVAD_SH = DOTFILES / "bin" / "lib" / "mullvad.sh"


def _stub(tmp_path: Path, stdout: str, rc: int = 0) -> Path:
    stub = tmp_path / "mullvad"
    stub.write_text(f'#!/bin/sh\ncat <<"MV_EOF"\n{stdout}\nMV_EOF\nexit {rc}\n')
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


# Real CLI output for each state; the daemon-down text is what the CLI prints
# (exit 1) when the management socket is unreachable.
@pytest.mark.parametrize(
    "stdout,rc,expected",
    [
        ("Autoconnect: on", 0, "on"),
        ("Autoconnect: off", 0, "off"),
        ("Error: Management RPC server or client error", 1, "unreachable"),
        ("Autoconnect: on", 1, "unreachable"),
        ("", 0, "unreachable"),
    ],
)
def test_autoconnect_classification(
    tmp_path: Path, stdout: str, rc: int, expected: str
) -> None:
    stub = _stub(tmp_path, stdout, rc)
    proc = _run('mullvad_autoconnect "$MULLVAD_CLI"', stub)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


# Real `mullvad status` first lines.
@pytest.mark.parametrize(
    "stdout,rc,expected",
    [
        ("Connected\n    Relay:  us-chi-wg-301", 0, "connected"),
        ("Disconnected\n    Visible location: Canada", 0, "disconnected"),
        ("Connecting", 0, "disconnected"),
        ("Error: Management RPC server or client error", 1, "unreachable"),
    ],
)
def test_tunnel_classification(
    tmp_path: Path, stdout: str, rc: int, expected: str
) -> None:
    stub = _stub(tmp_path, stdout, rc)
    proc = _run('mullvad_tunnel "$MULLVAD_CLI"', stub)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected
