"""bin/lib/tailscale-resolve.sh — tailscale_health classification.

Drives the lib with a stubbed `tailscale` binary so the matrix of daemon
failure modes (socket EPERM, daemon down, logged out, ...) is exercised
without a real tailscaled. The states must track the consumers listed in
the tailscale_health comment (SwiftBar plugin, set-exit-node, doctor).
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


def _skew(
    tmp_path: Path, client: str, daemon: str | None
) -> subprocess.CompletedProcess[str]:
    """Stub a tailscale CLI whose `version` and `status --json` disagree, run the check.

    `daemon=None` omits the Version line so `status --json` looks unparseable
    (the skew check must stay silent rather than false-alarm).
    """
    status_json = f'  "Version": "{daemon}-tdeadbeef",' if daemon is not None else ""
    stub = tmp_path / "tailscale"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = version ]; then\n'
        f"  echo {client}\n"
        'elif [ "$1" = status ]; then\n'
        f"  cat <<'TS_EOF'\n{status_json}\nTS_EOF\n"
        "fi\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        ["bash", "-c", f'source "{RESOLVE_SH}" && tailscale_version_skew "$1"', "_", str(stub)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=5,
    )


def test_version_skew_detected(tmp_path: Path) -> None:
    proc = _skew(tmp_path, "1.98.8", "1.98.5")
    assert proc.returncode == 1
    assert "client=1.98.8" in proc.stdout
    assert "daemon=1.98.5" in proc.stdout


def test_version_skew_match_is_silent(tmp_path: Path) -> None:
    proc = _skew(tmp_path, "1.98.8", "1.98.8")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_version_skew_unreadable_daemon_is_silent(tmp_path: Path) -> None:
    # A daemon we can't probe must not be reported as skew (avoids EPERM/boot
    # transients flapping the SwiftBar menu into the skew warning).
    proc = _skew(tmp_path, "1.98.8", None)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def _lookup(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "{RESOLVE_SH}" && tailscale_node_lookup "$1"',
            "_",
            code,
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=5,
    )


def test_node_lookup_known() -> None:
    proc = _lookup("ca")
    assert proc.returncode == 0
    assert proc.stdout.split()[-1].endswith("mullvad.ts.net")


def test_node_lookup_unknown() -> None:
    assert _lookup("zz").returncode != 0
