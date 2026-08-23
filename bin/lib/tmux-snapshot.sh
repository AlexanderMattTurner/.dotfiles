# shellcheck shell=bash
# tmux-resurrect snapshot health — single source of truth.
#
# A snapshot only helps if it is still being written, and "is it being written?"
# has no honest answer other than "has one landed since this tmux server
# started". tmux-continuum installs its save hook only when
# `another_tmux_server_running_on_startup` says no rival server exists, and that
# predicate counts `ps | grep "^tmux"` against 1 — so concurrent tmux *clients*
# at login can make it skip installing the hook. When that happens nothing looks
# wrong: the plugin is loaded, the status bar renders normally, and the stale
# snapshot on disk still parses. The loss surfaces only at the next reboot, as
# every session gone. Comparing the snapshot's mtime against the server's
# #{start_time} is what turns that into a check.
#
# Freshness is only ever *under*-reported: a save that is merely pending reads
# as `warming`, never as `ok`. That is the safe direction — this exists to catch
# saving that stopped, and it must not invent a save that never happened.
#
# Consumers that must stay in sync (see CLAUDE.md "tmux session restore"):
# bin/doctor.bash, tests/test_tmux_snapshot.py.

# Overridable so tests can inject a stub instead of driving a real tmux server.
TMUX_BIN="${TMUX_BIN:-tmux}"

# Save intervals of headroom before a missing save counts as stale: one for the
# save that is merely pending, one for the jitter in continuum's status-bar
# driven trigger.
TMUX_SNAPSHOT_GRACE_INTERVALS="${TMUX_SNAPSHOT_GRACE_INTERVALS:-2}"

# Where resurrect keeps its snapshots. Honors @resurrect-dir, else resurrect's
# own XDG default.
tmux_snapshot_dir() {
    local dir
    dir="$("$TMUX_BIN" show-option -gqv @resurrect-dir 2>/dev/null)" || dir=""
    if [ -n "$dir" ]; then
        printf '%s\n' "$dir"
        return 0
    fi
    printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/tmux/resurrect"
}

_tmux_snapshot_mtime() {
    if [ "$(uname)" = Darwin ]; then
        stat -f %m "$1" 2>/dev/null
    else
        stat -c %Y "$1" 2>/dev/null
    fi
}

# `list-sessions` is the only liveness probe that will not autostart a server;
# `show-option` and `display-message` both would, so nothing may call them
# before this returns true.
_tmux_server_alive() {
    "$TMUX_BIN" list-sessions >/dev/null 2>&1
}

# Classify snapshot health. Prints one of:
#   ok:<minutes>       a snapshot landed <minutes> ago, after this server started
#   stale:<minutes>    newest snapshot predates this server by <minutes> —
#                      continuum has stopped saving
#   warming:<seconds>  server is <seconds> old; the first save is not due yet
#   no-snapshot        server running but nothing to restore from at all
#   no-server          no tmux server running, so there is nothing to judge
#   no-tmux            tmux not installed
tmux_snapshot_health() {
    local snapshot interval start now uptime grace mtime

    command -v "$TMUX_BIN" >/dev/null 2>&1 || {
        echo no-tmux
        return 0
    }
    _tmux_server_alive || {
        echo no-server
        return 0
    }

    snapshot="$(tmux_snapshot_dir)/last"
    if [ ! -e "$snapshot" ]; then
        echo no-snapshot
        return 0
    fi

    interval="$("$TMUX_BIN" show-option -gqv @continuum-save-interval 2>/dev/null)" || interval=""
    case "$interval" in '' | *[!0-9]*) interval=15 ;; esac

    start="$("$TMUX_BIN" display-message -p '#{start_time}' 2>/dev/null)" || start=""
    case "$start" in '' | *[!0-9]*) start=0 ;; esac

    now="$(date +%s)"
    grace=$((interval * 60 * TMUX_SNAPSHOT_GRACE_INTERVALS))
    uptime=$((now - start))

    # start=0 means #{start_time} was unreadable (an older tmux). Treat it as
    # "cannot judge" rather than "server started in 1970 and is therefore
    # infinitely stale".
    if [ "$start" -eq 0 ] || [ "$uptime" -lt "$grace" ]; then
        printf 'warming:%s\n' "$uptime"
        return 0
    fi

    mtime="$(_tmux_snapshot_mtime "$snapshot")"
    case "$mtime" in '' | *[!0-9]*)
        # Only reachable if the file vanished between the existence check above
        # and this stat, i.e. continuum rotated snapshots mid-check. Report it
        # as absent rather than inventing a state: the next run will see the
        # new file. Guarding this also keeps the arithmetic below from tripping
        # doctor's `set -e` on an empty operand.
        echo no-snapshot
        return 0
        ;;
    esac

    if [ "$mtime" -lt "$start" ]; then
        printf 'stale:%s\n' "$(((start - mtime) / 60))"
    else
        printf 'ok:%s\n' "$(((now - mtime) / 60))"
    fi
}
