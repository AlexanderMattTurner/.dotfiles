"""bin/tmux-gc.bash — which sessions get collected, and which are spared.

The script runs unattended from a tmux hook, so a false positive silently
destroys work. These tests drive it against a stubbed tmux (TMUX_BIN) with
fixture files standing in for server state, and against real process trees
for the "is this session actually busy" checks.
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
SCRIPT = DOTFILES / "bin" / "tmux-gc.bash"

STUB = """#!/bin/sh
# Stub tmux. Reads server state from $FIXDIR, records kills to kills.txt.
cmd="$1"
shift
name=""
while [ $# -gt 0 ]; do
    case "$1" in
    -t) name="${2#=}"; shift 2 ;;
    *) shift ;;
    esac
done
case "$cmd" in
list-sessions) cat "$FIXDIR/sessions.txt" 2>/dev/null || exit 1 ;;
list-panes) cat "$FIXDIR/panes-$name.txt" 2>/dev/null || true ;;
show-options) cat "$FIXDIR/keep-$name.txt" 2>/dev/null || true ;;
kill-session) printf '%s\\n' "$name" >>"$FIXDIR/kills.txt" ;;
esac
exit 0
"""

DAY_AGO = 86400


class Server:
    """A stubbed tmux server: sessions, their panes, and recorded kills."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.sessions: list[str] = []
        stub = tmp_path / "tmux-stub"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.stub = stub

    def session(self, name, *, idle=DAY_AGO, attached=0, panes=(("fish", 999999),)):
        activity = int(time.time()) - idle
        self.sessions.append(f"{name}|{attached}|{activity}")
        (self.dir / f"panes-{name}.txt").write_text(
            "".join(f"{cmd}|{pid}\n" for cmd, pid in panes)
        )
        return self

    def keep_flag(self, name, value="1"):
        (self.dir / f"keep-{name}.txt").write_text(f"{value}\n")
        return self

    def run(self, *args):
        (self.dir / "sessions.txt").write_text("\n".join(self.sessions) + "\n")
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**os.environ, "TMUX_BIN": str(self.stub), "FIXDIR": str(self.dir)},
            capture_output=True,
            text=True,
        )

    @property
    def killed(self) -> list[str]:
        killfile = self.dir / "kills.txt"
        return killfile.read_text().split() if killfile.exists() else []


@pytest.fixture
def server(tmp_path):
    return Server(tmp_path)


@pytest.fixture
def procs():
    """Spawn real process trees; terminate them all on teardown."""
    spawned = []

    def spawn(script: str) -> int:
        proc = subprocess.Popen(
            ["bash", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned.append(proc)
        time.sleep(0.4)  # let the child settle into the tree before we look
        return proc.pid

    yield spawn
    for proc in spawned:
        proc.kill()
        proc.wait()


def test_collects_idle_empty_session(server):
    server.session("junk")
    assert server.run().returncode == 0
    assert server.killed == ["junk"]


def test_spares_attached_session(server):
    server.session("live", attached=1)
    server.run()
    assert server.killed == []


def test_spares_main_by_default(server):
    server.session("main")
    server.run()
    assert server.killed == []


def test_spares_explicitly_kept_session(server):
    server.session("keepme")
    server.run("--keep", "keepme")
    assert server.killed == []


def test_spares_session_flagged_gc_keep(server):
    server.session("pinned").keep_flag("pinned")
    server.run()
    assert server.killed == []


def test_gc_keep_zero_does_not_protect(server):
    server.session("notpinned").keep_flag("notpinned", "0")
    server.run()
    assert server.killed == ["notpinned"]


def test_spares_recently_active_session(server):
    server.session("fresh", idle=60)
    server.run()
    assert server.killed == []


def test_idle_threshold_is_configurable(server):
    server.session("fresh", idle=600)
    server.run("--idle", "5")
    assert server.killed == ["fresh"]


def test_spares_session_running_a_real_command(server):
    """An editor in any pane means the session is in use, however long idle."""
    server.session("editing", panes=(("fish", 999999), ("nvim", 999998)))
    server.run()
    assert server.killed == []


def test_spares_session_with_a_background_job(server, procs):
    """A backgrounded job leaves the shell in the foreground, so the pane's
    command name reads as idle — the process tree is what gives it away."""
    pid = procs("sleep 30; true")
    server.session("building", panes=(("bash", pid),))
    server.run()
    assert server.killed == []


def test_shell_only_descendants_do_not_protect(server, procs):
    """Regression: fish forks a copy of itself for command substitution and for
    its universal-variable notifier, so "has any child" marked every idle
    prompt as busy and the reaper collected nothing."""
    pid = procs("bash -c 'read line'; true")
    server.session("idle-prompt", panes=(("bash", pid),))
    server.run()
    assert server.killed == ["idle-prompt"]


def test_dry_run_kills_nothing(server):
    server.session("junk")
    result = server.run("--dry-run")
    assert server.killed == []
    assert "would kill junk" in result.stdout


def test_quiet_is_silent(server):
    server.session("junk")
    assert server.run("--quiet").stdout == ""


def test_no_server_is_not_an_error(server, tmp_path):
    """The hook fires on cold start, before any server exists."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "TMUX_BIN": str(server.stub), "FIXDIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_refuses_to_collect_when_process_inspection_is_unavailable(server, tmp_path):
    """A sandbox can deny ps/pgrep while leaving them on PATH. Every session
    then looks idle, so failing open would reap the entire server."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    blind_ps = fake_bin / "ps"
    blind_ps.write_text("#!/bin/sh\nexit 1\n")
    blind_ps.chmod(blind_ps.stat().st_mode | stat.S_IXUSR)

    server.session("junk")
    (server.dir / "sessions.txt").write_text("\n".join(server.sessions) + "\n")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "TMUX_BIN": str(server.stub),
            "FIXDIR": str(server.dir),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert server.killed == []
    assert "cannot inspect processes" in result.stderr


def test_rejects_non_numeric_idle(server):
    server.session("junk")
    result = server.run("--idle", "forever")
    assert result.returncode == 2
    assert server.killed == []


def test_rejects_unknown_flag(server):
    server.session("junk")
    assert server.run("--demolish").returncode == 2
    assert server.killed == []
