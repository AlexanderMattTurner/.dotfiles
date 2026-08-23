#!/usr/bin/env bash
# bin/tmux-bootstrap.bash — bring up the tmux server and replay the
# tmux-resurrect snapshot exactly once per boot, then tell the caller whether it
# was the shell that did it.
#
# Why this exists: `@continuum-restore 'on'` cannot be trusted to fire. Restore
# is gated in tmux-continuum's continuum_restore.sh on
#
#     auto_restore_enabled && ! another_tmux_server_running_on_startup
#
# and that second predicate (scripts/helpers.sh) is
#
#     ps -u $uid -o "command pid" | grep "^tmux" | grep -v "^tmux source"
#
# counted against 1. That pattern matches tmux *clients* exactly as readily as
# tmux *servers*. Every interactive fish runs `tmux new-session ...`, and iTerm2
# restores its saved windows in parallel at login, so two or three such
# processes exist in the instant the server starts. Continuum concludes another
# server is running and returns without restoring — no message, no log entry,
# and a snapshot file that still looks perfectly healthy on disk. That is how
# the 2026-08-23 reboot came up with every session gone while
# `~/.local/share/tmux/resurrect/last` held them all.
#
# The same predicate also gates `add_resurrect_save_interpolation`, so the race
# can silently disable *saving* too. Serializing the server start here fixes
# both: when continuum loads there is exactly one client and one server, so its
# count is 1 and both its hooks install.
#
# Restore therefore runs from here, serialized by an O_EXCL lock file so
# concurrent logins cannot race, and recorded with a tmux global option that
# dies with the server (no stale state to garbage-collect).
#
# Prints exactly one word on stdout:
#   primary    — this call started the server and ran the restore; the caller
#                should attach to $PRIMARY_SESSION
#   secondary  — a server was already up, or another shell is building one; the
#                caller should open its own independent session
#
# An already-running server is never restored into: a server the user started
# by hand is theirs, and replaying a snapshot on top of it would duplicate
# windows. `secondary` is therefore also the answer for "not ours to touch".

set -euo pipefail

# tmux_snapshot_dir lives here, so this script and doctor.bash agree on where
# resurrect keeps its snapshots. Getting that path wrong would make the restore
# below silently no-op on a machine whose snapshots are perfectly healthy.
BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bin/lib/tmux-snapshot.sh
source "$BOOTSTRAP_DIR/lib/tmux-snapshot.sh"

# Overridable so tests can inject a stub instead of driving a real tmux server.
TMUX_BIN="${TMUX_BIN:-tmux}"

# Session the snapshot is expected to carry, and that `primary` attaches to.
PRIMARY_SESSION="${TMUX_BOOTSTRAP_SESSION:-main}"

# How long a shell waits for another shell's bootstrap before giving up and
# opening its own session. Sourcing .tmux.conf through tpm takes ~12s cold, so
# this needs real headroom.
WAIT_SECONDS="${TMUX_BOOTSTRAP_WAIT:-45}"

LOCK_FILE="${TMUX_BOOTSTRAP_LOCK:-${TMPDIR:-/tmp}/tmux-bootstrap-$(id -u).lock}"

# Set on the server once the snapshot has been replayed. Server-scoped, so it
# vanishes with the server and cannot go stale across a reboot.
RESTORE_MARKER='@dotfiles-tmux-restored'

usage() {
    cat <<'EOF'
usage: tmux-bootstrap.bash

Start the tmux server and replay the tmux-resurrect snapshot exactly once,
serialized against concurrent logins. Takes no options.

Prints "primary" when this call built the server (attach to `main`), or
"secondary" when a server was already up or another shell is building one
(open your own session).

environment:
  TMUX_BOOTSTRAP_SESSION  session the snapshot carries (default: main)
  TMUX_BOOTSTRAP_WAIT     seconds to wait for another shell (default: 45)
  TMUX_BOOTSTRAP_LOCK     lock file path (default: $TMPDIR/tmux-bootstrap-$UID.lock)
  TMUX_BIN                tmux binary to drive (default: tmux)
EOF
}

if [[ $# -gt 0 ]]; then
    case "$1" in
    -h | --help)
        usage
        exit 0
        ;;
    *)
        printf 'tmux-bootstrap: unexpected argument %q\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
fi

say() {
    printf '%s\n' "$1"
}

# `list-sessions` is the only liveness probe that does NOT autostart a server.
# `show-option` and `set-option` both do, which would spin up an unmanaged
# server (sourcing .tmux.conf) purely to answer a question.
server_alive() {
    "$TMUX_BIN" list-sessions >/dev/null 2>&1
}

release_lock() {
    rm -f "$LOCK_FILE"
}

# O_EXCL create-and-write in one step, so the pid is always readable by anyone
# who loses the race. Returns 1 when the lock could not be taken and the caller
# should proceed as `secondary`.
acquire_lock() {
    local waited=0 holder
    while true; do
        if (
            set -o noclobber
            printf '%s\n' "$$" >"$LOCK_FILE"
        ) 2>/dev/null; then
            return 0
        fi
        holder="$(cat "$LOCK_FILE" 2>/dev/null)" || holder=""
        # A holder that died mid-bootstrap will never release; take the lock over
        # rather than making every future login sit through the full timeout.
        if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
            rm -f "$LOCK_FILE"
            continue
        fi
        # Holder finished and released. Retry immediately: the post-acquire
        # server_alive check below is what turns this into a `secondary`.
        #
        # Note this loop deliberately polls the lock file rather than tmux. A
        # `tmux …` probe here would add processes to the very ps snapshot
        # continuum uses to decide whether to install its save hook, recreating
        # the bug this script exists to work around.
        [[ -e "$LOCK_FILE" ]] || continue
        ((waited < WAIT_SECONDS)) || return 1
        sleep 1
        waited=$((waited + 1))
    done
}

# Ask resurrect where its restore script is rather than hardcoding a plugin
# path, so a relocated tpm install keeps working. tmux_snapshot_dir comes from
# bin/lib/tmux-snapshot.sh, sourced above.
run_restore() {
    local script
    script="$("$TMUX_BIN" show-option -gqv @resurrect-restore-script-path 2>/dev/null)" || script=""
    if [[ -z "$script" || ! -x "$script" ]]; then
        printf 'tmux-bootstrap: tmux-resurrect not loaded; nothing to restore\n' >&2
        return 0
    fi
    if [[ ! -e "$(tmux_snapshot_dir)/last" ]]; then
        # Fresh machine. restore.sh would flash "resurrect file not found!" into
        # the session we are about to hand over, so skip it quietly instead.
        return 0
    fi
    # A restore that fails part-way still beats handing over a bare shell, and
    # must not abort before the session is marked and claimed below.
    if ! "$script" >/dev/null 2>&1; then
        printf 'tmux-bootstrap: resurrect restore failed; session may be incomplete\n' >&2
    fi
}

# An established server is not ours to replay a snapshot into. This is the fast
# path, and it is deliberately duplicated after the lock is taken: without it
# every new terminal opened hours later would queue on the lock for no reason,
# and without the copy below a shell that wins the lock right as the previous
# holder releases it would restore a second time. Both are load-bearing; the
# one below is the actual safety net.
if server_alive; then
    say secondary
    exit 0
fi

if ! acquire_lock; then
    say secondary
    exit 0
fi
trap release_lock EXIT

# We may have won the lock only after the previous holder finished building the
# server. Re-check now that we hold it.
if server_alive; then
    say secondary
    exit 0
fi

# This is what starts the server, and therefore what sources .tmux.conf.
"$TMUX_BIN" new-session -d -s "$PRIMARY_SESSION"
run_restore
"$TMUX_BIN" set-option -g "$RESTORE_MARKER" 1
say primary
