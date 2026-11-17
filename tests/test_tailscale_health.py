"""bin/lib/tailscale-resolve.sh — Tailscale.app health and exit-node classifiers.

Drives the lib with a stubbed `tailscale` binary so every state is exercised
without a real Tailscale.app. The states must track the consumers listed in
each classifier's comment (setup.bash, doctor).
"""

import stat
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
RESOLVE_SH = DOTFILES / "bin" / "lib" / "tailscale-resolve.sh"

# Abridged real outputs from `tailscale status`.
LOGGED_OUT = """\
# Health check:
#     - You are logged out. The last login error was: fetch control key: \
Get "https://controlplane.tailscale.com/key?v=138": failed to resolve \
"controlplane.tailscale.com": no DNS fallback candidates remain
unexpected state: NoState"""

EPERM = (
    'Get "http://local-tailscaled.sock/localapi/v0/status": dial unix '
    "/var/run/tailscaled.socket: connect: operation not permitted"
)

NO_DAEMON = (
    "failed to connect to local tailscaled; it doesn't appear to be running "
    "(sudo systemctl start tailscaled ?)"
)

ACTIVE = (
    "100.64.0.1   mac          turntrout@   macOS   -\n"
    "100.64.0.2   ca-mtr-wg-001.mullvad.ts.net  turntrout@  linux  active; "
    "exit node"
)


def _run_lib(tmp_path: Path, func: str, stdout: str = "", rc: int = 0) -> str:
    """Stub a tailscale CLI emitting `stdout` with exit code `rc`, run `func` on it."""
    stub = tmp_path / "tailscale"
    stub.write_text(f'#!/bin/sh\ncat <<"TS_EOF"\n{stdout}\nTS_EOF\nexit {rc}\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    proc = subprocess.run(
        ["bash", "-c", f'source "{RESOLVE_SH}" && {func} "$1"', "_", str(stub)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=5,
    )
    assert proc.returncode == 0, (
        f"{func} exited {proc.returncode}\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
    return proc.stdout.strip()


@pytest.mark.parametrize(
    "stdout,rc,expected",
    [
        (ACTIVE, 0, "ok"),
        ("Tailscale is stopped.", 1, "stopped"),
        (NO_DAEMON, 1, "no-daemon"),
        ("The Tailscale CLI failed to start: Failed to load preferences.", 1, "no-daemon"),
        (EPERM, 1, "eperm"),
        (LOGGED_OUT, 1, "logged-out"),
        ("Logged out.", 1, "logged-out"),
        ("something unforeseen", 7, "error"),
    ],
)
def test_health_classification(
    tmp_path: Path, stdout: str, rc: int, expected: str
) -> None:
    assert _run_lib(tmp_path, "tailscale_health", stdout, rc) == expected


def _peer(dns: str, exit_node: bool) -> str:
    """One abridged peer object from real Tailscale.app `status --json` output."""
    return (
        f'    "{dns}": {{\n'
        f'      "HostName": "{dns.split(".")[0]}",\n'
        f'      "DNSName": "{dns}.",\n'
        '      "Online": true,\n'
        f'      "ExitNode": {"true" if exit_node else "false"},\n'
        '      "ExitNodeOption": true\n'
        "    },\n"
    )


def _status_json(*peers: str) -> str:
    return (
        "{\n"
        '  "BackendState": "Running",\n'
        '  "Self": {\n'
        '    "DNSName": "macbook-pro-81.tailnet.ts.net.",\n'
        '    "ExitNode": false,\n'
        '    "ExitNodeOption": false\n'
        "  },\n"
        '  "Peer": {\n' + "".join(peers) + "  }\n}"
    )


@pytest.mark.parametrize(
    "stdout,rc,expected",
    [
        (
            _status_json(
                _peer("us-lax-wg-402.mullvad.ts.net", False),
                _peer("us-sjc-wg-508.mullvad.ts.net", True),
            ),
            0,
            "mullvad:us-sjc-wg-508.mullvad.ts.net",
        ),
        (
            _status_json(
                _peer("us-lax-wg-402.mullvad.ts.net", False),
                _peer("mac-mini.tailnet.ts.net", True),
            ),
            0,
            "other:mac-mini.tailnet.ts.net",
        ),
        (_status_json(_peer("us-lax-wg-402.mullvad.ts.net", False)), 0, "none"),
        (_status_json(), 0, "none"),
        ("The Tailscale CLI failed to start: Failed to load preferences.", 1, "unreachable"),
    ],
)
def test_exit_node_classification(
    tmp_path: Path, stdout: str, rc: int, expected: str
) -> None:
    assert _run_lib(tmp_path, "tailscale_exit_node", stdout, rc) == expected


def _find(cli: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{RESOLVE_SH}" && find_tailscale'],
        env={"PATH": "/usr/bin:/bin", "TAILSCALE_CLI": str(cli)},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=5,
    )


def test_find_tailscale_reports_the_app_cli(tmp_path: Path) -> None:
    stub = tmp_path / "tailscale"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    proc = _find(stub)
    assert proc.returncode == 0
    assert proc.stdout.strip() == str(stub)


def test_find_tailscale_fails_without_the_app(tmp_path: Path) -> None:
    proc = _find(tmp_path / "missing")
    assert proc.returncode != 0
    assert proc.stdout == ""
