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
#   sleep      no-op, so route_stable_for's 1s sample windows and restore_dns's
#              retry pacing cost no wall clock
#   tailscale  the CLI stub (find_tailscale would otherwise prefer the real
#              binary and drive an actual VPN — see REAL_CLI above)
#   dig        the DNS probe. Unstubbed it either goes missing (restore_dns
#              silently no-ops, so nothing below is really exercised) or does
#              live lookups against the runner's resolver — nondeterministic
#              either way.
#   netstat    the routing table restore_default_route judges. Unstubbed it
#              reads the runner's real table, whose `default` row is never a
#              Mac's — the route path would go unexercised or false-alarm.
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


# DNS states the disconnect can land in. "heals" is the real bug's shape: dead
# until tailscaled's DNS manager re-applies, which the accept-dns toggle forces.
DNS_ALIVE = "alive"
DNS_DEAD = "dead"
DNS_HEALS_ON_REAPPLY = "heals"

# Route states. "heals" is the 2026-09-19 shape: no physical default in the
# kernel table (SystemConfiguration still reporting a Router the whole time)
# until Wi-Fi is bounced and macOS re-elects one.
ROUTE_ALIVE = "alive"
ROUTE_DEAD = "dead"
ROUTE_HEALS_ON_BOUNCE = "heals"
# Present, then absent for one sample while macOS promotes it, then present.
ROUTE_BLINKS = "blinks"

ROUTE_ROW = "default            192.168.1.1        UGScg                 en0"
# `!`-marked rows are what a `$NF`-based reader would misparse as an interface.
ROUTE_FILLER = """100.64/10          utun0              USc                 utun0
169.254            link#14            UCS                   en0      !
192.168.1.1/32     link#14            UCS                   en0      !"""

# Answers -listallhardwareports so is_wifi_device accepts en0, and records the
# power cycle. Only present in the runs that are *meant* to bounce.
NETWORKSETUP_STUB = """#!/bin/sh
case "$1" in
-listallhardwareports)
    echo "Hardware Port: Wi-Fi"
    echo "Device: en0"
    exit 0 ;;
-setairportpower)
    echo "$2 $3" >>"{bounces}"
    [ "$3" = on ] && : >"{healed}"
    exit 0 ;;
esac
exit 1
"""


def _run_macos_disconnect(
    tmp_path: Path, dns: str = DNS_ALIVE, route: str = ROUTE_ALIVE
):
    """Drive `off` down the macOS branch with the platform stubs above.

    Returns (proc, menu.log text, the `set` flags the script actually issued).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    args_file = tmp_path / "set-args"
    healed = tmp_path / "dns-healed"
    route_healed = tmp_path / "route-healed"
    bounces = tmp_path / "bounces"

    if dns == DNS_ALIVE:
        dig_body = "#!/bin/sh\necho 1.2.3.4\n"
    elif dns == DNS_DEAD:
        # `dig +short` exits 0 with no answer on SERVFAIL — the blackhole's
        # actual signature, and why exit status can't be the signal.
        dig_body = "#!/bin/sh\nexit 0\n"
    else:
        dig_body = f'#!/bin/sh\n[ -f "{healed}" ] && echo 1.2.3.4\nexit 0\n'

    if route == ROUTE_ALIVE:
        netstat_body = (
            f'#!/bin/sh\necho "{ROUTE_ROW}"\ncat <<"EOF"\n{ROUTE_FILLER}\nEOF\n'
        )
    elif route == ROUTE_DEAD:
        netstat_body = f'#!/bin/sh\ncat <<"EOF"\n{ROUTE_FILLER}\nEOF\n'
    elif route == ROUTE_BLINKS:
        counter = tmp_path / "netstat-calls"
        netstat_body = (
            f'#!/bin/sh\nn=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1))\n'
            f'echo "$n" >"{counter}"\n'
            f'[ "$n" -ne 3 ] && echo "{ROUTE_ROW}"\n'
            f'cat <<"EOF"\n{ROUTE_FILLER}\nEOF\n'
        )
    else:
        netstat_body = (
            f'#!/bin/sh\n[ -f "{route_healed}" ] && echo "{ROUTE_ROW}"\n'
            f'cat <<"EOF"\n{ROUTE_FILLER}\nEOF\n'
        )

    stubs = {
        "uname": "#!/bin/sh\necho Darwin\n",
        "scutil": SCUTIL_STUB,
        "sleep": "#!/bin/sh\nexit 0\n",
        "dig": dig_body,
        "netstat": netstat_body,
        "tailscale": (
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            '"debug prefs") echo \'  "CorpDNS": true,\'; exit 0 ;;\n'
            "esac\n"
            'case "$1" in\n'
            "version) echo 1.86.0; exit 0 ;;\n"
            'status) echo "100.64.0.1 mac turntrout@ macOS -"; exit 0 ;;\n'
            # Appends, so re-applying DNS can't erase the exit-node call that
            # came before it.
            f'set) shift; printf \'%s\\n\' "$@" >>"{args_file}"\n'
            f'    case " $* " in *--accept-dns=true*) : >"{healed}" ;; esac\n'
            "    exit 0 ;;\n"
            "esac\n"
        ),
    }
    if route not in (ROUTE_ALIVE, ROUTE_BLINKS):
        stubs["networksetup"] = NETWORKSETUP_STUB.format(
            bounces=bounces, healed=route_healed
        )
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
    return (
        proc,
        (menu_log.read_text() if menu_log.exists() else ""),
        args_file.read_text().split() if args_file.exists() else [],
    )


def _bounces(tmp_path: Path) -> list[str]:
    path = tmp_path / "bounces"
    return path.read_text().splitlines() if path.exists() else []


def test_sc_readers_survive_a_still_writing_scutil(tmp_path: Path) -> None:
    """The disconnect must not die just because scutil had more to say.

    `sc_primary_interface`'s result is assigned directly, so a pipeline that
    fails under `set -o pipefail` aborts the whole script via `set -e` — and it
    does so *after* `tailscale set --exit-node=` has already torn the tunnel
    down, leaving the user half-disconnected with the self-heal never reached.

    An awk that `exit`s on its first match closes the pipe while scutil is still
    writing, killing it with SIGPIPE (141). Reading to EOF is the fix.
    """
    proc, log, _ = _run_macos_disconnect(tmp_path)

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
    proc, log, _ = _run_macos_disconnect(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "blackhole" not in log.lower()
    assert "no default route" not in log
    assert "bouncing" not in log


def test_healthy_dns_is_left_alone(tmp_path: Path) -> None:
    """A teardown that didn't break DNS must not toggle the user's accept-dns.

    The self-heal is a real pref change on a live daemon; running it
    unconditionally would make every disconnect churn DNS.
    """
    proc, log, set_args = _run_macos_disconnect(tmp_path, DNS_ALIVE)

    assert proc.returncode == 0, proc.stderr
    assert set_args == ["--exit-node="]
    assert "re-applying" not in log


def test_stale_resolver_is_healed_by_reapplying_accept_dns(tmp_path: Path) -> None:
    """The bug this whole path exists for: DNS left on the tunnel's resolver.

    The route is stable throughout — restore_default_route finds nothing wrong —
    so a teardown that checked only routing would call this disconnect clean.
    """
    proc, log, set_args = _run_macos_disconnect(tmp_path, DNS_HEALS_ON_REAPPLY)

    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
    assert set_args == [
        "--exit-node=",
        "--accept-dns=false",
        "--accept-dns=true",
    ]
    assert "DNS restored" in log
    assert "no default route" not in log, "the route was never the problem"


def test_dns_that_stays_dead_exits_6_and_says_so(tmp_path: Path) -> None:
    """Reporting a failed self-heal beats pretending the disconnect was clean."""
    proc, log, set_args = _run_macos_disconnect(tmp_path, DNS_DEAD)

    assert proc.returncode == 6
    assert "--accept-dns=true" in set_args, "must have attempted the re-apply"
    assert "DNS still dead" in log
    assert "DNS still dead" in proc.stderr


def test_route_drop_is_healed_by_bouncing_wifi_despite_sc_router(
    tmp_path: Path,
) -> None:
    """2026-09-19: no physical default in the kernel table, internet dead.

    The scutil stub reports `Router : 192.168.1.1` throughout — exactly what
    the real machine said — so a probe that trusted SystemConfiguration would
    log `off → off` and return 0 with the user offline. The routing table is
    the signal, and the bounce is the fix.
    """
    proc, log, set_args = _run_macos_disconnect(
        tmp_path, DNS_ALIVE, ROUTE_HEALS_ON_BOUNCE
    )

    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
    assert _bounces(tmp_path) == ["en0 off", "en0 on"]
    assert "blackholed the default route; bouncing en0" in log
    assert "restored after bouncing en0" in log
    assert set_args == ["--exit-node="], "DNS was fine; must not be toggled"


def test_route_that_stays_dead_exits_5_and_says_so(tmp_path: Path) -> None:
    proc, log, _ = _run_macos_disconnect(tmp_path, DNS_ALIVE, ROUTE_DEAD)

    assert proc.returncode == 5
    assert _bounces(tmp_path) == ["en0 off", "en0 on"], "must have tried once"
    assert "no default route" in log
    assert "no default route" in proc.stderr


def test_both_halves_run_even_when_the_route_fails(tmp_path: Path) -> None:
    """Reporting half a broken teardown is what hid the DNS bug for months."""
    proc, log, set_args = _run_macos_disconnect(
        tmp_path, DNS_HEALS_ON_REAPPLY, ROUTE_DEAD
    )

    assert proc.returncode == 5
    assert "--accept-dns=true" in set_args
    assert "DNS restored" in log


def test_route_blink_during_teardown_does_not_bounce(tmp_path: Path) -> None:
    """A route that appears, blinks once while macOS promotes it, and returns
    is a normal teardown. `networksetup` is absent, so a bounce would exit 5."""
    proc, log, _ = _run_macos_disconnect(tmp_path, DNS_ALIVE, ROUTE_BLINKS)

    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
    assert "blinked" in log
    assert "bouncing" not in log
