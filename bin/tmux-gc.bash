#!/usr/bin/env bash
# bin/tmux-gc.bash — reap idle, empty tmux sessions.
#
# Why this exists: `@continuum-restore 'on'` in .tmux.conf makes tmux-resurrect
# replay its saved snapshot at every server start. An empty shell that happened
# to be open when a snapshot was taken is therefore recreated forever, so junk
# sessions pile up across restarts and bury the ones actually opened by hand.
# Because unnamed sessions get bare numeric names, the junk is also
# indistinguishable from real work at the picker. Reaping the empties lets the
# next continuum save (every 15 min) record a clean set, so the clutter does not
# come back after the next restart.
#
# A session is collected only when ALL of these hold:
#   * it is not attached
#   * its name is not in the keep list, and @gc-keep is unset on it
#   * it has been idle for longer than --idle minutes
#   * every one of its panes sits at a bare shell prompt with no child process
#
# So a session running anything real (nvim, a dev server, claude) is never
# touched, no matter how long it has sat idle. Protect a deliberately-empty
# session with:  tmux set-option -t <name> @gc-keep 1

set -euo pipefail

# Overridable so tests can inject a stub instead of driving a real tmux server.
TMUX_BIN="${TMUX_BIN:-tmux}"

IDLE_MIN=60
DRY_RUN=false
QUIET=false
KEEP=(main)

usage() {
    cat <<'EOF'
usage: tmux-gc.bash [--idle MINUTES] [--keep NAME]... [-n|--dry-run] [-q|--quiet]

Kill detached tmux sessions that are idle and hold nothing but bare shells.

options:
  --idle MINUTES  minimum idle time before a session is collected (default: 60)
  --keep NAME     never collect this session (repeatable; "main" is always kept)
  -n, --dry-run   report what would be killed, kill nothing
  -q, --quiet     print nothing on success
  -h, --help      show this message

A session is also spared when it has @gc-keep set:
  tmux set-option -t <name> @gc-keep 1
EOF
}

die() {
    printf 'tmux-gc: %s\n' "$1" >&2
    exit 2
}

need_value() {
    [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
    --idle)
        need_value "$@"
        IDLE_MIN="$2"
        shift 2
        ;;
    --idle=*)
        IDLE_MIN="${1#*=}"
        shift
        ;;
    --keep)
        need_value "$@"
        KEEP+=("$2")
        shift 2
        ;;
    --keep=*)
        KEEP+=("${1#*=}")
        shift
        ;;
    -n | --dry-run)
        DRY_RUN=true
        shift
        ;;
    -q | --quiet)
        QUIET=true
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        printf 'tmux-gc: unknown argument %q\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
done

[[ "$IDLE_MIN" =~ ^[0-9]+$ ]] || die "--idle must be a whole number of minutes, got ${IDLE_MIN@Q}"

# A pane whose foreground process is one of these is "just a prompt".
is_bare_shell() {
    case "$1" in
    fish | bash | zsh | sh | dash | tcsh | ksh) return 0 ;;
    *) return 1 ;;
    esac
}

# Basename of a pid's command, minus the leading "-" that marks a login shell
# (ps reports the pane shell as "-fish" but a child fork as "fish", and reports
# other processes by absolute path).
proc_name() {
    local comm
    comm="$(ps -o comm= -p "$1" 2>/dev/null || true)"
    comm="${comm##*/}"
    printf '%s' "${comm#-}"
}

# True when pid has any descendant that is not itself a bare shell. Plain
# subshells must not count: fish forks a copy of itself for command
# substitution and for its universal-variable notifier, so a bare "does it have
# children" test marks every idle prompt as busy.
has_real_descendant() {
    local pid="$1" child name
    while read -r child; do
        [[ -n "$child" ]] || continue
        name="$(proc_name "$child")"
        # Empty means the process exited between listing and inspection.
        [[ -n "$name" ]] || continue
        if ! is_bare_shell "$name"; then
            return 0
        fi
        if has_real_descendant "$child"; then
            return 0
        fi
    done < <(pgrep -P "$pid" 2>/dev/null || true)
    return 1
}

# True when any pane in the session is doing real work.
session_busy() {
    local name="$1" cmd pid
    while IFS='|' read -r cmd pid; do
        [[ -n "$cmd" ]] || continue
        if ! is_bare_shell "$cmd"; then
            return 0
        fi
        # A backgrounded job leaves the shell itself in the foreground, so the
        # pane's command name alone would read as idle — walk the tree too.
        if [[ -n "$pid" ]] && has_real_descendant "$pid"; then
            return 0
        fi
    done < <("$TMUX_BIN" list-panes -s -t "=$name" -F '#{pane_current_command}|#{pane_pid}' 2>/dev/null)
    return 1
}

is_protected() {
    local name="$1" keeper flag
    for keeper in "${KEEP[@]}"; do
        if [[ "$name" == "$keeper" ]]; then
            return 0
        fi
    done
    flag="$("$TMUX_BIN" show-options -qv -t "=$name" @gc-keep 2>/dev/null || true)"
    [[ -n "$flag" && "$flag" != "0" ]]
}

# Telling an idle prompt from a running job depends entirely on being able to
# inspect processes. A restricted sandbox can deny that while `ps` and `pgrep`
# still exist on PATH, and the failure is silent: every session then looks idle
# and the whole server gets reaped. Probe against our own pid and refuse to
# collect anything if we cannot even see ourselves.
if [[ -z "$(proc_name "$$")" ]]; then
    printf 'tmux-gc: cannot inspect processes; refusing to collect anything\n' >&2
    exit 0
fi

# No server running is the normal cold-start case, not an error: the hook fires
# before anything exists to collect.
sessions="$("$TMUX_BIN" list-sessions -F '#{session_name}|#{session_attached}|#{session_activity}' 2>/dev/null)" || exit 0
[[ -n "$sessions" ]] || exit 0

now="$(date +%s)"
threshold=$((IDLE_MIN * 60))
collected=()

while IFS='|' read -r name attached activity; do
    [[ -n "$name" ]] || continue
    # Never reap a session someone is looking at.
    [[ "$attached" == "0" ]] || continue
    [[ "$activity" =~ ^[0-9]+$ ]] || continue
    if is_protected "$name"; then
        continue
    fi
    ((now - activity >= threshold)) || continue
    if session_busy "$name"; then
        continue
    fi
    collected+=("$name")
done <<<"$sessions"

if ((${#collected[@]} == 0)); then
    $QUIET || printf 'tmux-gc: nothing to collect\n'
    exit 0
fi

for name in "${collected[@]}"; do
    if $DRY_RUN; then
        $QUIET || printf 'tmux-gc: would kill %s\n' "$name"
        continue
    fi
    "$TMUX_BIN" kill-session -t "=$name" 2>/dev/null || true
    $QUIET || printf 'tmux-gc: killed %s\n' "$name"
done
