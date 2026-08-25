"""bin/lib/tailscale-resolve.sh — tailscale_health classification and DNS probes.

Drives the lib with a stubbed `tailscale` binary so the matrix of daemon
failure modes (socket EPERM, daemon down, logged out, ...) is exercised
without a real tailscaled. The states must track the consumers listed in
the tailscale_health comment (SwiftBar plugin, set-exit-node, doctor).
"""

import os
import shutil
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


# ── the DNS half of the exit-node teardown ──────────────────────────────────
#
# Clearing a Mullvad exit node leaves tailscaled's DNS pointed at a resolver
# reachable only through the tunnel it just tore down, while the default route
# stays healthy. These functions are the detector and the self-heal; every one
# of them is called from a `$IS_MAC` branch in set-exit-node, so this is where
# they get exercised on a Linux CI runner.
#
# The stub set and why each is needed:
#   dig        the probe itself; also the *real* /usr/bin/dig must never answer,
#              hence TAILSCALE_DIG_FALLBACK is repointed at a nonexistent path
#   sleep      no-op, so the retry pacing and the toggle's settle costs no wall
#              clock
#   tailscale  serves `debug prefs` and records `set` argv
#
# If the lib later shells out to something absent from this list the call dies
# on a missing command rather than silently passing, because no fallback path
# exists here.


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    """Drop an executable `name` into tmp_path/bin (first on PATH)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run_dns(
    tmp_path: Path, snippet: str, *, isolate_path: bool = False, **env: str
) -> subprocess.CompletedProcess[str]:
    """Source the lib and run `snippet` with the stubs in tmp_path/bin winning.

    `isolate_path` drops the inherited PATH entirely — the only way to prove the
    no-probe-tool path on a machine that ships /usr/bin/dig (i.e. every Mac).
    Safe because the functions it is used for shell out to nothing.
    """
    bin_dir = str(tmp_path / "bin")
    # Absolute bash: isolate_path scrubs the PATH that would otherwise find it.
    return subprocess.run(
        [
            shutil.which("bash") or "/bin/bash",
            "-c",
            f'source "{RESOLVE_SH}" && {snippet}',
        ],
        env={
            **os.environ,
            "PATH": bin_dir if isolate_path else f"{bin_dir}:{os.environ['PATH']}",
            "TAILSCALE_DIG_FALLBACK": str(tmp_path / "no-such-dig"),
            **env,
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )


def test_dns_healthy_when_a_probe_answers(tmp_path: Path) -> None:
    _stub(tmp_path, "dig", "#!/bin/sh\necho 1.2.3.4\n")
    assert _run_dns(tmp_path, "tailscale_dns_healthy").returncode == 0


def test_dns_unhealthy_on_empty_answer_despite_exit_zero(tmp_path: Path) -> None:
    """The blackhole's actual signature: `dig +short` exits 0 on SERVFAIL.

    Trusting exit status here is precisely the bug — the lib must read the
    (empty) answer instead.
    """
    _stub(tmp_path, "dig", "#!/bin/sh\nexit 0\n")
    assert _run_dns(tmp_path, "tailscale_dns_healthy").returncode == 1


def test_dns_unhealthy_when_dig_itself_fails(tmp_path: Path) -> None:
    _stub(tmp_path, "dig", "#!/bin/sh\nexit 9\n")
    assert _run_dns(tmp_path, "tailscale_dns_healthy").returncode == 1


def test_dns_healthy_tries_every_probe_until_one_answers(tmp_path: Path) -> None:
    """Any one name answering suffices, so a single dead name must not decide it.

    Also the guard on the probe list splitting into words at all: a quoted
    expansion would hand `dig` one bogus name and never reach the live one.
    """
    tried = tmp_path / "tried"
    # `$4` is the name in `dig +short +time=2 +tries=1 <name> A`. Recording that
    # one argument per invocation — rather than the whole line — is what makes
    # this non-vacuous: a collapsed list arrives as a single call whose name is
    # "dead.example live.example", which is one recorded line, not two.
    _stub(
        tmp_path,
        "dig",
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$4" >>"{tried}"\n'
        '[ "$4" = live.example ] && echo 1.2.3.4\n'
        "exit 0\n",
    )
    proc = _run_dns(
        tmp_path,
        "tailscale_dns_healthy",
        TAILSCALE_DNS_PROBES="dead.example live.example",
    )
    assert proc.returncode == 0, proc.stderr
    assert tried.read_text().splitlines() == ["dead.example", "live.example"]


def test_dns_probe_missing_never_false_alarms_but_is_reported(tmp_path: Path) -> None:
    """No dig ⇒ healthy (never blackhole-notify on a guess), but not `pass`.

    doctor.bash asks tailscale_dns_probe_available first so it can `skip` a
    check it never ran, per the CLAUDE.md optional-tool rule.
    """
    (tmp_path / "bin").mkdir(exist_ok=True)
    assert _run_dns(tmp_path, "tailscale_dns_healthy", isolate_path=True).returncode == 0
    avail = _run_dns(tmp_path, "tailscale_dns_probe_available", isolate_path=True)
    assert avail.returncode != 0
    # Not the 127 of a function that doesn't exist — that would pass for free.
    assert "not found" not in avail.stderr, avail.stderr


def test_dns_recovers_within_retries_until_it_answers(tmp_path: Path) -> None:
    """`tailscale set` returns before the DNS manager re-applies, so one probe
    at t=0 reads the stale config. Prove the loop actually re-probes."""
    counter = tmp_path / "calls"
    _stub(tmp_path, "sleep", "#!/bin/sh\nexit 0\n")
    _stub(
        tmp_path,
        "dig",
        "#!/bin/sh\n"
        f'echo x >>"{counter}"\n'
        f'[ "$(wc -l <"{counter}")" -ge 3 ] && echo 1.2.3.4\n'
        "exit 0\n",
    )
    proc = _run_dns(
        tmp_path,
        "tailscale_dns_recovers_within 5",
        TAILSCALE_DNS_PROBES="only.example",
    )
    assert proc.returncode == 0, proc.stderr
    assert counter.read_text().count("x") == 3


def test_dns_recovers_within_gives_up_after_the_attempt_budget(tmp_path: Path) -> None:
    counter = tmp_path / "calls"
    _stub(tmp_path, "sleep", "#!/bin/sh\nexit 0\n")
    _stub(tmp_path, "dig", f'#!/bin/sh\necho x >>"{counter}"\nexit 0\n')
    proc = _run_dns(
        tmp_path,
        "tailscale_dns_recovers_within 3",
        TAILSCALE_DNS_PROBES="only.example",
    )
    assert proc.returncode == 1
    assert counter.read_text().count("x") == 3


def _reapply(tmp_path: Path, corp: str, refuse: str = "") -> tuple[int, list[str]]:
    """Run tailscale_dns_reapply against a stub reporting CorpDNS=`corp`.

    `refuse` is a `set` flag the stub rejects, so the toggle-failure path is
    reachable. Returns (exit code, the `set` flags the lib actually issued).
    """
    set_args = tmp_path / "set-args"
    _stub(tmp_path, "sleep", "#!/bin/sh\nexit 0\n")
    stub = _stub(
        tmp_path,
        "tailscale",
        "#!/bin/sh\n"
        'if [ "$1 $2" = "debug prefs" ]; then\n'
        "  cat <<'PREFS'\n"
        "{\n"
        '  "ControlURL": "https://controlplane.tailscale.com",\n'
        f'  "CorpDNS": {corp},\n'
        '  "ExitNodeID": ""\n'
        "}\n"
        "PREFS\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = set ]; then\n'
        f'  printf \'%s\\n\' "$2" >>"{set_args}"\n'
        f'  [ "$2" = "{refuse}" ] && exit 1\n'
        "  exit 0\n"
        "fi\n"
        "exit 3\n",
    )
    proc = _run_dns(tmp_path, f'tailscale_dns_reapply "{stub}"')
    issued = set_args.read_text().split() if set_args.exists() else []
    return proc.returncode, issued


def test_dns_reapply_toggles_accept_dns_off_then_back_on(tmp_path: Path) -> None:
    """The whole point: force a teardown + re-apply, then restore the pref."""
    rc, issued = _reapply(tmp_path, "true")
    assert rc == 0
    assert issued == ["--accept-dns=false", "--accept-dns=true"]


def test_dns_reapply_noops_when_tailscaled_is_not_managing_dns(tmp_path: Path) -> None:
    """CorpDNS false ⇒ DNS was never hijacked, so this isn't the bug. Touch nothing."""
    rc, issued = _reapply(tmp_path, "false")
    assert rc != 0
    assert issued == []


def test_dns_reapply_reports_a_failed_toggle(tmp_path: Path) -> None:
    rc, issued = _reapply(tmp_path, "true", refuse="--accept-dns=true")
    assert rc != 0
    assert issued == ["--accept-dns=false", "--accept-dns=true"]
