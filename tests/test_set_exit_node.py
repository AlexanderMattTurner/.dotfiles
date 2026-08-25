"""bin/tailscale-set-exit-node.bash — exit-code, stderr, and menu.log contract.

SwiftBar invokes this script detached, so its menu.log lines and distinct
exit codes (2 invalid target, 4 daemon unhealthy, 127 no CLI) are the only
observable failure surface. A stubbed `tailscale` on PATH drives it.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = DOTFILES / "bin" / "tailscale-set-exit-node.bash"

# find_tailscale prefers /opt/homebrew and /usr/local over PATH, so on a
# machine with a real CLI the stub would be shadowed and the test would
# drive the user's actual VPN. Never do that.
REAL_CLI = any(
    Path(p).exists()
    for p in ("/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale")
)
pytestmark = pytest.mark.skipif(
    REAL_CLI, reason="real tailscale CLI would shadow the stub"
)


def _run(tmp_path: Path, target: str, status_out: str = "", status_rc: int = 0):
    """Run the script against a stub CLI; return (proc, menu.log text, set args)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    args_file = tmp_path / "set-args"
    stub = bin_dir / "tailscale"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "version) echo 1.86.0; exit 0 ;;\n"
        f'status) cat <<"TS_EOF"\n{status_out}\nTS_EOF\nexit {status_rc} ;;\n'
        f'set) shift; printf \'%s\\n\' "$@" >"{args_file}"; exit 0 ;;\n'
        "esac\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    proc = subprocess.run(
        ["bash", str(SCRIPT), target],
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    menu_log = tmp_path / "Library/Logs/com.turntrout.tailscale-exit-node/menu.log"
    log = menu_log.read_text() if menu_log.exists() else ""
    set_args = args_file.read_text().split() if args_file.exists() else None
    return proc, log, set_args


def test_invalid_target_exits_2_and_lists_valid_codes(tmp_path: Path) -> None:
    proc, log, set_args = _run(tmp_path, "param1=ca")
    assert proc.returncode == 2
    assert "valid: off" in proc.stderr and "ca" in proc.stderr
    assert "FAIL" in log
    assert set_args is None


def test_logged_out_exits_4_with_remediation(tmp_path: Path) -> None:
    proc, log, set_args = _run(tmp_path, "ca", "Logged out.", 1)
    assert proc.returncode == 4
    assert "tailscale up" in proc.stderr
    assert "tailscale up" in log
    assert set_args is None, "must not call `tailscale set` on an unhealthy daemon"


def test_healthy_set_passes_node_and_lan_flag(tmp_path: Path) -> None:
    proc, log, set_args = _run(tmp_path, "ca", "100.64.0.1 mac turntrout@ macOS -")
    assert proc.returncode == 0, proc.stderr
    assert "ca-mtr-wg-001.mullvad.ts.net" in log
    assert set_args == [
        "--exit-node=ca-mtr-wg-001.mullvad.ts.net",
        "--exit-node-allow-lan-access=true",
    ]


def test_off_clears_exit_node_without_lan_flag(tmp_path: Path) -> None:
    proc, log, set_args = _run(tmp_path, "off", "100.64.0.1 mac turntrout@ macOS -")
    assert proc.returncode == 0, proc.stderr
    assert "off" in log
    assert set_args == ["--exit-node="]


# ── the macOS disconnect path ───────────────────────────────────────────────
#
# Everything below forces open the `$IS_MAC` gate, which is false on
# `ubuntu-latest` where `pytest tests/` actually runs — so without this the
# whole self-heal path is dead code no green suite has ever executed.
#
# The stub set holding the gate open, and why each is needed:
#   uname      reports Darwin; this is what opens the branch at all
#   scutil     stands in for SystemConfiguration, and is the pipe writer whose
#              early death is the bug under test
#   sleep      no-op, so route_stable_for's 1s sample windows cost no wall clock
#   tailscale  the CLI stub (find_tailscale would otherwise prefer the real
#              binary and drive an actual VPN — see REAL_CLI above)
#
# If the script later shells out to a platform command absent from this list,
# the branch stops executing and these tests keep passing on the fallback path.
# Nothing goes red when that coverage evaporates, so this list must stay honest.

# Big enough to overrun the pipe buffer many times over, so scutil is certain to
# still be writing when awk reaches its first match. That is what makes the
# SIGPIPE deterministic rather than a race that only bites a slow runner.
SCUTIL_STUB = """#!/bin/sh
# Drain the `show State:...` request first: a stub that exits without reading
# hands its writer EPIPE, which pipefail would report as an unrelated failure.
cat >/dev/null
echo "  PrimaryInterface : en0"
echo "  Router : 192.168.1.1"
seq 1 200000 | sed 's/^/  filler : /'
"""


def _run_macos_disconnect(tmp_path: Path):
    """Drive `off` down the macOS branch with the platform stubs above."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    args_file = tmp_path / "set-args"

    stubs = {
        "uname": "#!/bin/sh\necho Darwin\n",
        "scutil": SCUTIL_STUB,
        "sleep": "#!/bin/sh\nexit 0\n",
        "tailscale": (
            "#!/bin/sh\n"
            'case "$1" in\n'
            "version) echo 1.86.0; exit 0 ;;\n"
            'status) echo "100.64.0.1 mac turntrout@ macOS -"; exit 0 ;;\n'
            f'set) shift; printf \'%s\\n\' "$@" >"{args_file}"; exit 0 ;;\n'
            "esac\n"
        ),
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    proc = subprocess.run(
        ["bash", str(SCRIPT), "off"],
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    menu_log = tmp_path / "Library/Logs/com.turntrout.tailscale-exit-node/menu.log"
    return proc, (menu_log.read_text() if menu_log.exists() else "")


def test_sc_readers_survive_a_still_writing_scutil(tmp_path: Path) -> None:
    """The disconnect must not die just because scutil had more to say.

    `sc_primary_interface`'s result is assigned directly, so a pipeline that
    fails under `set -o pipefail` aborts the whole script via `set -e` — and it
    does so *after* `tailscale set --exit-node=` has already torn the tunnel
    down, leaving the user half-disconnected with the self-heal never reached.

    An awk that `exit`s on its first match closes the pipe while scutil is still
    writing, killing it with SIGPIPE (141). Reading to EOF is the fix.
    """
    proc, log = _run_macos_disconnect(tmp_path)

    assert proc.returncode != 141, "scutil was SIGPIPE'd by an early awk exit"
    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
    assert "off" in log


def test_macos_disconnect_actually_entered_the_gated_branch(tmp_path: Path) -> None:
    """Non-vacuity for the stub set: prove the gated branch really executed.

    A stub set that failed to open the `$IS_MAC` gate would sail down the Linux
    fallback and pass the test above for entirely the wrong reason. Here the
    route is stable, so a branch that ran finds it clean: no bounce, no
    blackhole notification.

    `networksetup` is deliberately absent from the stub set — a run that tried
    to bounce the interface would die on the missing command rather than quietly
    power-cycling the developer's Wi-Fi.
    """
    proc, log = _run_macos_disconnect(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "blackhole" not in log.lower()
    assert "no default route" not in log
    assert "bouncing" not in log
