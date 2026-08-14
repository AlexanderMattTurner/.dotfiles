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


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _install_macos_stubs(
    bin_dir: Path, tmp_path: Path, routers: list[str], recover_on_bounce: bool
) -> tuple[Path, Path]:
    """Stub the macOS surface the disconnect path drives, and return the files
    recording every `networksetup` and `osascript` invocation.

    `uname` reports Darwin so the `$IS_MAC` branch — the entire blackhole
    self-heal — actually executes under CI's ubuntu-latest, which is the only
    place `pytest tests/` runs. `sleep` is a no-op so the sample windows cost
    no wall clock. `scutil` replays `routers` one entry per call ("-" meaning
    "no Router line", i.e. the blackhole), repeating the last entry once
    exhausted, which is what lets a test express "route is up at t=0 and gone
    a second later".

    `recover_on_bounce` models the case the self-heal exists for: the route
    stays dead until `networksetup` powers the interface back on. Keying that
    off the bounce itself, rather than off a sample index, keeps the test from
    silently decaying into a no-op if the polling loop counts are ever retuned.
    """
    seq_file = tmp_path / "scutil-seq"
    # The disconnect path reads SC state once up front via sc_primary_interface,
    # before any route sampling. That call is indistinguishable from a route
    # sample (identical `show` request), so it consumes a slot — feed it a
    # throwaway copy of the first entry and let `routers` describe the samples.
    seq_file.write_text("\n".join([routers[0], *routers]) + "\n")
    count_file = tmp_path / "scutil-count"
    ns_calls = tmp_path / "networksetup-calls"
    osa_calls = tmp_path / "osascript-calls"
    bounced = tmp_path / "wifi-powered-on"

    _write_stub(bin_dir, "uname", "echo Darwin\n")
    _write_stub(bin_dir, "sleep", "exit 0\n")
    # Unstubbed, this fires a real desktop notification when the suite runs on
    # a Mac that has no homebrew tailscale (the only Mac where it doesn't skip).
    _write_stub(
        bin_dir, "osascript", f'printf \'%s\\n\' "$*" >>"{osa_calls}"\nexit 0\n'
    )
    _write_stub(
        bin_dir,
        "scutil",
        # Drain the piped `show State:/...` request: a stub that leaves stdin
        # unread can SIGPIPE its writer instead of failing the assertion.
        "cat >/dev/null\n"
        + (
            f'if [ -f "{bounced}" ]; then\n'
            "  printf '  PrimaryInterface : en0\\n  Router : 192.168.1.1\\n'\n"
            "  exit 0\n"
            "fi\n"
            if recover_on_bounce
            else ""
        )
        + f'n=$(cat "{count_file}" 2>/dev/null || echo 0)\n'
        "n=$((n + 1))\n"
        f'printf \'%s\\n\' "$n" >"{count_file}"\n'
        f'total=$(wc -l <"{seq_file}")\n'
        'if [ "$n" -gt "$total" ]; then n="$total"; fi\n'
        f'val=$(sed -n "${{n}}p" "{seq_file}")\n'
        "echo '  PrimaryInterface : en0'\n"
        'if [ "$val" != "-" ]; then echo "  Router : $val"; fi\n'
        "exit 0\n",
    )
    _write_stub(
        bin_dir,
        "networksetup",
        f'printf \'%s\\n\' "$*" >>"{ns_calls}"\n'
        'case "$1" in\n'
        # is_wifi_device matches these two lines verbatim; en0 must read as
        # Wi-Fi or bounce_interface refuses before it ever powers anything.
        "-listallhardwareports) printf 'Hardware Port: Wi-Fi\\nDevice: en0\\n' ;;\n"
        f'-setairportpower) [ "$3" = on ] && : >"{bounced}" ;;\n'
        "esac\n"
        "exit 0\n",
    )
    return ns_calls, osa_calls


def _run(
    tmp_path: Path,
    target: str,
    status_out: str = "",
    status_rc: int = 0,
    routers: list[str] | None = None,
    recover_on_bounce: bool = False,
):
    """Run the script against a stub CLI.

    Returns (proc, menu.log text, `tailscale set` args, recorded side effects),
    where side effects is the concatenated `networksetup` + `osascript` argv —
    the Wi-Fi bounce and the user-facing notification, the two things the
    disconnect path does to the machine beyond `tailscale set` itself.
    """
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
    ns_calls, osa_calls = _install_macos_stubs(
        bin_dir, tmp_path, routers or ["192.168.1.1"], recover_on_bounce
    )
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
    effects = "".join(f.read_text() for f in (ns_calls, osa_calls) if f.exists())
    return proc, log, set_args, effects


ONLINE = "100.64.0.1 mac turntrout@ macOS -"


def test_invalid_target_exits_2_and_lists_valid_codes(tmp_path: Path) -> None:
    proc, log, set_args, _ = _run(tmp_path, "param1=ca")
    assert proc.returncode == 2
    assert "valid: off" in proc.stderr and "ca" in proc.stderr
    assert "FAIL" in log
    assert set_args is None


def test_logged_out_exits_4_with_remediation(tmp_path: Path) -> None:
    proc, log, set_args, _ = _run(tmp_path, "ca", "Logged out.", 1)
    assert proc.returncode == 4
    assert "tailscale up" in proc.stderr
    assert "tailscale up" in log
    assert set_args is None, "must not call `tailscale set` on an unhealthy daemon"


def test_healthy_set_passes_node_and_lan_flag(tmp_path: Path) -> None:
    proc, log, set_args, effects = _run(tmp_path, "ca", ONLINE)
    assert proc.returncode == 0, proc.stderr
    assert "ca-mtr-wg-001.mullvad.ts.net" in log
    assert set_args == [
        "--exit-node=ca-mtr-wg-001.mullvad.ts.net",
        "--exit-node-allow-lan-access=true",
    ]
    assert "setairportpower" not in effects, "connect path must never touch Wi-Fi"


def test_off_clears_exit_node_without_lan_flag(tmp_path: Path) -> None:
    proc, log, set_args, effects = _run(tmp_path, "off", ONLINE)
    assert proc.returncode == 0, proc.stderr
    assert "off" in log
    assert set_args == ["--exit-node="]
    assert "setairportpower" not in effects, "a route that never drops needs no bounce"
    assert "bouncing" not in log


def test_route_present_at_t0_then_gone_is_not_treated_as_clean(tmp_path: Path) -> None:
    """The 2026-08-09T21:34Z false-clean `off → off`.

    `tailscale set` returns once the daemon accepts the pref change, not once
    macOS has rebuilt its routing table, so the pre-teardown route is still in
    SC state for a second or so. Sampling once at t=0 read that doomed route,
    called the disconnect clean, and exited 0 — no bounce, no warning, and no
    internet until a reboot. A route that is up on the first sample and gone on
    every later one must escalate, not pass.
    """
    proc, log, set_args, effects = _run(
        tmp_path, "off", ONLINE, routers=["192.168.1.1", "-"]
    )
    assert set_args == ["--exit-node="], "the disconnect itself must still happen"
    assert "setairportpower en0 off" in effects, "must bounce the captured interface"
    assert "bouncing en0" in log
    # Bounce succeeded but the route never came back: fail loudly, don't
    # pretend the disconnect worked.
    assert proc.returncode == 5
    assert "no default route" in proc.stderr
    assert "FAIL" in log
    # SwiftBar runs this detached, so the notification is the only signal the
    # user actually sees — an exit code alone reaches nobody.
    assert "display notification" in effects


def test_route_returning_on_its_own_avoids_the_wifi_bounce(tmp_path: Path) -> None:
    """A drop tailscaled re-elects out of must not cost the user their link."""
    proc, log, set_args, effects = _run(
        tmp_path, "off", ONLINE, routers=["192.168.1.1", "-", "192.168.1.1"]
    )
    assert proc.returncode == 0, proc.stderr
    assert set_args == ["--exit-node="]
    assert "returned on its own" in log
    assert "setairportpower" not in effects
    assert "display notification" not in effects, "a recovered route is not an alarm"


def test_bounce_recovers_the_route(tmp_path: Path) -> None:
    """Route gone until the interface bounce brings it back: exit 0, no alarm."""
    proc, log, set_args, effects = _run(
        tmp_path,
        "off",
        ONLINE,
        routers=["192.168.1.1", "-"],
        recover_on_bounce=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert set_args == ["--exit-node="]
    assert "setairportpower en0 off" in effects and "setairportpower en0 on" in effects
    assert "restored after bouncing en0" in log
    assert "FAIL" not in log
    assert "display notification" not in effects, "recovery succeeded; don't alarm"
