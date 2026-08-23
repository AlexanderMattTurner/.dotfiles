"""bin/tmux-bootstrap.bash — who restores the snapshot, and who must not.

The script exists because tmux-continuum's restore gate counts
`ps | grep "^tmux"` against 1, so concurrent tmux *clients* read as a competing
tmux *server* and the restore is skipped in silence. The contract that replaces
it is narrow and load-bearing: across any number of shells racing at login,
exactly one starts the server and exactly one replays the snapshot; a server
that was already up is never restored into; and no shell is left without a tmux.

These tests drive the script against a stubbed tmux (TMUX_BIN) whose "server
state" is files in a fixture dir, so the real tmux server is never touched.
"""

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = DOTFILES / "bin" / "tmux-bootstrap.bash"

# Server state lives under $FIXDIR: `alive` marks a running server, calls.txt
# logs every invocation, opt@NAME.txt supplies option values.
STUB = """#!/bin/sh
printf '%s\\n' "$*" >>"$FIXDIR/calls.txt"
cmd="$1"
shift
case "$cmd" in
list-sessions)
    [ -e "$FIXDIR/alive" ] || exit 1
    echo "main: 1 windows"
    ;;
new-session)
    : >"$FIXDIR/alive"
    ;;
show-option)
    opt=""
    for a in "$@"; do
        case "$a" in
        @*) opt="$a" ;;
        esac
    done
    cat "$FIXDIR/opt$opt.txt" 2>/dev/null || true
    ;;
esac
exit 0
"""

RESTORE_STUB = """#!/bin/sh
# Stand-in for tmux-resurrect's restore.sh. Sleeps on request: the caller holds
# the bootstrap lock for its whole duration, which is what the concurrency test
# needs in order to observe a real race.
sleep "${RESTORE_DELAY:-0}"
printf 'restored\\n' >>"$FIXDIR/restored.txt"
exit "${RESTORE_EXIT:-0}"
"""

DEAD_PID = 999999


def _executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Bootstrap:
    """A stubbed tmux server plus the knobs bin/tmux-bootstrap.bash reads."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.stub = _executable(tmp_path / "tmux-stub", STUB)
        self.restore = _executable(tmp_path / "restore-stub", RESTORE_STUB)
        self.lock = tmp_path / "bootstrap.lock"
        # A snapshot dir holding a `last`, i.e. "there is something to restore".
        self.snapshot_dir = tmp_path / "resurrect"
        self.snapshot_dir.mkdir()
        (self.snapshot_dir / "last").write_text("pane\tmain\t0\t1\n")
        self.option("@resurrect-dir", str(self.snapshot_dir))
        self.option("@resurrect-restore-script-path", str(self.restore))

    def option(self, name: str, value: str):
        (self.dir / f"opt{name}.txt").write_text(f"{value}\n")
        return self

    def server_running(self):
        (self.dir / "alive").touch()
        return self

    def no_snapshot(self):
        (self.snapshot_dir / "last").unlink()
        return self

    def hold_lock(self, pid: int):
        self.lock.write_text(f"{pid}\n")
        return self

    def env(self, **overrides):
        env = {
            **os.environ,
            "TMUX_BIN": str(self.stub),
            "FIXDIR": str(self.dir),
            "TMUX_BOOTSTRAP_LOCK": str(self.lock),
            "TMUX_BOOTSTRAP_WAIT": "2",
            "RESTORE_DELAY": "0",
            "RESTORE_EXIT": "0",
        }
        env.update(overrides)
        return env

    def run(self, *args, **overrides):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env(**overrides),
            capture_output=True,
            text=True,
        )

    def spawn(self, **overrides):
        return subprocess.Popen(
            ["bash", str(SCRIPT)],
            env=self.env(**overrides),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _lines(self, name) -> list[str]:
        path = self.dir / name
        return path.read_text().splitlines() if path.exists() else []

    @property
    def restores(self) -> list[str]:
        return self._lines("restored.txt")

    @property
    def calls(self) -> list[str]:
        return self._lines("calls.txt")

    def calls_to(self, subcommand: str) -> list[str]:
        return [c for c in self.calls if c.split(" ")[0] == subcommand]


@pytest.fixture
def boot(tmp_path):
    return Bootstrap(tmp_path)


@pytest.fixture
def live_pid():
    """A pid that is alive for the duration of the test."""
    proc = subprocess.Popen(["sleep", "30"])
    yield proc.pid
    proc.kill()
    proc.wait()


def test_cold_start_restores_and_claims_primary(boot):
    result = boot.run()

    assert result.stdout.strip() == "primary"
    assert boot.restores == ["restored"]
    assert boot.calls_to("new-session") == ["new-session -d -s main"]


def test_running_server_is_never_restored_into(boot):
    """A server the user started by hand is theirs; replaying onto it duplicates
    every window."""
    boot.server_running()

    result = boot.run()

    assert result.stdout.strip() == "secondary"
    assert boot.restores == []
    assert boot.calls_to("new-session") == []


def test_marks_the_server_as_restored(boot):
    boot.run()

    assert boot.calls_to("set-option") == ["set-option -g @dotfiles-tmux-restored 1"]


def test_primary_session_name_is_configurable(boot):
    boot.run(TMUX_BOOTSTRAP_SESSION="work")

    assert boot.calls_to("new-session") == ["new-session -d -s work"]


def test_missing_snapshot_still_yields_a_session(boot):
    """A fresh machine has no snapshot; the shell must still get a tmux."""
    boot.no_snapshot()

    result = boot.run()

    assert result.stdout.strip() == "primary"
    assert boot.calls_to("new-session") == ["new-session -d -s main"]
    # Restore is skipped entirely rather than run and left to flash
    # "resurrect file not found!" into the session we are about to hand over.
    assert boot.restores == []


def test_missing_resurrect_plugin_still_yields_a_session(boot):
    boot.option("@resurrect-restore-script-path", "")

    result = boot.run()

    assert result.stdout.strip() == "primary"
    assert boot.calls_to("new-session") == ["new-session -d -s main"]
    assert boot.restores == []


def test_failed_restore_still_yields_a_claimed_session(boot):
    """A half-restored session beats dropping the shell into nothing.

    `set -e` would otherwise abort before the marker and the `primary` verdict,
    leaving a server nobody claims and a lock nobody thinks to re-check.
    """
    result = boot.run(RESTORE_EXIT="1")

    assert result.stdout.strip() == "primary"
    assert boot.calls_to("set-option") == ["set-option -g @dotfiles-tmux-restored 1"]
    assert "restore failed" in result.stderr
    assert not boot.lock.exists()


def test_lock_held_by_live_process_defers(boot, live_pid):
    """A shell that cannot get the lock opens its own session; it does not restore."""
    boot.hold_lock(live_pid)

    result = boot.run(TMUX_BOOTSTRAP_WAIT="1")

    assert result.stdout.strip() == "secondary"
    assert boot.restores == []
    assert boot.calls_to("new-session") == []


def test_stale_lock_is_taken_over(boot):
    """A holder that died mid-bootstrap must not strand every future login."""
    boot.hold_lock(DEAD_PID)

    result = boot.run()

    assert result.stdout.strip() == "primary"
    assert boot.restores == ["restored"]


def test_lock_is_released(boot):
    boot.run()

    assert not boot.lock.exists()


def test_concurrent_logins_restore_exactly_once(boot):
    """The regression this script exists for: iTerm2 relaunching N windows.

    Whichever shell wins, the snapshot is replayed once and exactly one shell
    claims `primary`; no other shell starts a second server or re-restores.
    """
    procs = [boot.spawn(RESTORE_DELAY="1", TMUX_BOOTSTRAP_WAIT="30") for _ in range(4)]
    roles = sorted(proc.communicate(timeout=60)[0].strip() for proc in procs)

    assert roles == ["primary", "secondary", "secondary", "secondary"]
    assert boot.restores == ["restored"]
    assert boot.calls_to("new-session") == ["new-session -d -s main"]


def test_waiting_does_not_probe_tmux(boot, live_pid):
    """Waiting must not run `tmux …`.

    A tmux process inside the wait loop would land in the very
    `ps | grep "^tmux"` snapshot continuum uses to decide whether to install its
    save hook, so a polling waiter would recreate the miscount this script
    exists to work around. Exactly one probe is expected: the liveness check
    before the lock is attempted.
    """
    boot.hold_lock(live_pid)

    result = boot.run(TMUX_BOOTSTRAP_WAIT="3")

    assert result.stdout.strip() == "secondary"
    assert boot.calls == ["list-sessions"]


def test_wait_timeout_is_bounded(boot, live_pid):
    """A stuck holder must not hang the login shell indefinitely."""
    boot.hold_lock(live_pid)

    start = time.monotonic()
    result = boot.run(TMUX_BOOTSTRAP_WAIT="2")
    elapsed = time.monotonic() - start

    assert result.stdout.strip() == "secondary"
    assert elapsed < 15


def test_rejects_unexpected_argument(boot):
    result = boot.run("--idle")

    assert result.returncode == 2
    assert boot.calls == []


def test_help_exits_clean_without_touching_tmux(boot):
    result = boot.run("--help")

    assert result.returncode == 0
    assert boot.calls == []
