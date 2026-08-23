"""bin/lib/tmux-snapshot.sh — is the resurrect snapshot still being written?

tmux-continuum can silently stop saving: its save hook is installed only when
`another_tmux_server_running_on_startup` says no rival server exists, and that
predicate miscounts concurrent tmux *clients* as a rival *server*. Nothing looks
wrong when it happens — the loss surfaces at the next reboot as every session
gone. Comparing the snapshot mtime against the server's `#{start_time}` is the
only check that catches it beforehand, so every branch of the classifier is
pinned here.

Driven against a stub tmux (TMUX_BIN), so no real server is touched.
"""

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
LIB = REPO / "bin" / "lib" / "tmux-snapshot.sh"

# Server state comes from $FIXDIR: `dead` suppresses the server, opt-*.txt
# supply option values, start_time.txt is #{start_time}.
STUB = """#!/bin/sh
case "$1" in
list-sessions)
    [ -e "$FIXDIR/dead" ] && exit 1
    echo "main: 1 windows"
    ;;
show-option)
    for a in "$@"; do
        case "$a" in
        @resurrect-dir) cat "$FIXDIR/opt-dir.txt" 2>/dev/null ;;
        @continuum-save-interval) cat "$FIXDIR/opt-interval.txt" 2>/dev/null ;;
        esac
    done
    ;;
display-message)
    cat "$FIXDIR/start_time.txt" 2>/dev/null
    ;;
esac
exit 0
"""

MINUTE = 60


class Snapshots:
    """A stubbed tmux server plus a resurrect snapshot dir on disk."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        stub = tmp_path / "tmux-stub"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.stub = stub
        self.snapshot_dir = tmp_path / "resurrect"
        self.snapshot_dir.mkdir()
        (self.dir / "opt-dir.txt").write_text(f"{self.snapshot_dir}\n")
        self.save_interval(15)
        # Default: a server that has been up long enough to owe a save.
        self.server_started(minutes_ago=60)

    def save_interval(self, minutes: int):
        (self.dir / "opt-interval.txt").write_text(f"{minutes}\n")
        return self

    def server_started(self, *, minutes_ago: int):
        self.start = int(time.time()) - minutes_ago * MINUTE
        (self.dir / "start_time.txt").write_text(f"{self.start}\n")
        return self

    def unreadable_start_time(self):
        (self.dir / "start_time.txt").write_text("\n")
        return self

    def assert_warming(self, expected_seconds: int):
        """`warming:<seconds>` is derived from wall clock, so a busy machine can
        drift a second or two between fixture setup and the classifier run."""
        state = self.health()
        kind, _, seconds = state.partition(":")
        assert kind == "warming", state
        assert abs(int(seconds) - expected_seconds) <= 5, state

    def no_server(self):
        (self.dir / "dead").touch()
        return self

    def snapshot(self, *, minutes_ago: int, readable: bool = True):
        last = self.snapshot_dir / "last"
        last.write_text("pane\tmain\t0\t1\n")
        when = int(time.time()) - minutes_ago * MINUTE
        os.utime(last, (when, when))
        if not readable:
            # A dangling symlink: the path exists but has no stat'able mtime.
            last.unlink()
            last.symlink_to(self.snapshot_dir / "gone")
        return self

    def health(self) -> str:
        result = subprocess.run(
            ["bash", "-c", f'source "{LIB}"\ntmux_snapshot_health'],
            env={
                **os.environ,
                "TMUX_BIN": str(self.stub),
                "FIXDIR": str(self.dir),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


@pytest.fixture
def snaps(tmp_path):
    return Snapshots(tmp_path)


def test_snapshot_saved_after_server_start_is_ok(snaps):
    snaps.server_started(minutes_ago=60).snapshot(minutes_ago=10)

    assert snaps.health() == "ok:10"


def test_snapshot_predating_the_server_is_stale(snaps):
    """The outage signature: a snapshot that stopped when the last server did."""
    snaps.server_started(minutes_ago=60).snapshot(minutes_ago=120)

    assert snaps.health() == "stale:60"


def test_young_server_is_warming_not_stale(snaps):
    """Two save intervals of grace, so a pending first save is never a failure."""
    snaps.save_interval(15).server_started(minutes_ago=5).snapshot(minutes_ago=120)

    snaps.assert_warming(5 * MINUTE)


def test_grace_ends_after_two_save_intervals(snaps):
    snaps.save_interval(15).server_started(minutes_ago=31).snapshot(minutes_ago=120)

    assert snaps.health() == "stale:89"


def test_save_interval_scales_the_grace_window(snaps):
    """A 60-minute interval must not be judged on a 15-minute clock."""
    snaps.save_interval(60).server_started(minutes_ago=90).snapshot(minutes_ago=200)

    snaps.assert_warming(90 * MINUTE)


def test_missing_snapshot(snaps):
    snaps.server_started(minutes_ago=60)

    assert snaps.health() == "no-snapshot"


def test_vanished_snapshot_reads_as_absent(snaps):
    """Continuum rotating a snapshot mid-check must not crash or lie."""
    snaps.server_started(minutes_ago=60).snapshot(minutes_ago=10, readable=False)

    assert snaps.health() == "no-snapshot"


def test_no_server_running(snaps):
    snaps.no_server().snapshot(minutes_ago=10)

    assert snaps.health() == "no-server"


def test_unreadable_start_time_is_warming_not_stale(snaps):
    """An older tmux with no #{start_time} must not read as 1970-vintage."""
    snaps.unreadable_start_time().snapshot(minutes_ago=10)

    assert snaps.health().startswith("warming:")


def test_missing_tmux(tmp_path):
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"\ntmux_snapshot_health'],
        env={**os.environ, "TMUX_BIN": str(tmp_path / "definitely-not-here")},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "no-tmux"


def test_snapshot_dir_falls_back_to_xdg_default(snaps, tmp_path):
    """No @resurrect-dir set: resurrect's own XDG default is where it looks."""
    (snaps.dir / "opt-dir.txt").write_text("\n")
    xdg = tmp_path / "xdg"

    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"\ntmux_snapshot_dir'],
        env={
            **os.environ,
            "TMUX_BIN": str(snaps.stub),
            "FIXDIR": str(snaps.dir),
            "XDG_DATA_HOME": str(xdg),
        },
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == str(xdg / "tmux" / "resurrect")


def test_doctor_fails_on_a_stale_snapshot(snaps, tmp_path):
    """End-to-end: the real doctor.bash must FAIL when saving has stopped.

    This is the branch that would have caught the 2026-08-23 loss before a
    reboot did, so it is driven through the actual script rather than asserted
    against its source. The stub tmux shadows the real one on PATH, which is how
    doctor's own `tmux` calls get redirected.
    """
    snaps.server_started(minutes_ago=60).snapshot(minutes_ago=120)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "tmux").symlink_to(snaps.stub)

    result = subprocess.run(
        ["bash", str(REPO / "bin" / "doctor.bash"), "--no-refresh", "--verbose"],
        env={
            **os.environ,
            "PATH": f"{shadow}{os.pathsep}{os.environ['PATH']}",
            "FIXDIR": str(snaps.dir),
        },
        capture_output=True,
        text=True,
        cwd=REPO,
    )

    # doctor prints the label and its detail on separate lines.
    labels = [ln for ln in result.stdout.splitlines() if "tmux snapshots" in ln]
    assert len(labels) == 1, result.stdout
    assert "FAIL" in labels[0]
    assert "continuum has stopped saving" in result.stdout
