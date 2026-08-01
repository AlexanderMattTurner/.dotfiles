#!/usr/bin/env bash
# claude-account — pick a Claude subscription that is not at its usage limit and
# run COMMAND under it, or (as Claude Code's apiKeyHelper) keep a RUNNING
# session on a usable account.
#
# Each account is one envchain namespace holding a CLAUDE_CODE_OAUTH_TOKEN
# (`envchain --set <ns> CLAUDE_CODE_OAUTH_TOKEN`, captured by `claude setup-token`
# while signed in as that account). Namespaces are tried in order and the first
# one Anthropic reports as usable wins. In launcher mode the token reaches
# COMMAND in its environment, so this works for anything that reads
# CLAUDE_CODE_OAUTH_TOKEN — `claude`, `claude-private`, `glovebox`, a bare
# `claude -p '...'`.
#
# Mid-session rotation rides Claude Code's apiKeyHelper: ~/.claude/settings.json
# points apiKeyHelper at `claude-account --helper`, and Claude Code re-invokes it
# every CLAUDE_CODE_API_KEY_HELPER_TTL_MS (pinned to 10s there) and on any 401.
# Denials are noticed within seconds, not on the next re-probe: each helper
# beat keeps a `--watch` process alive that tails the Claude Code session
# transcripts for a usage-limit API error and, on one, immediately probes the
# active account, records its cooldown, and re-selects — so the running
# session gets the next account's token on its next beat (~2s detection + one
# probe + <=10s pickup). The CLAUDE_ACCOUNT_PROBE_INTERVAL re-probe is only
# the backstop for denials the watcher cannot see. A turn already in flight
# when the limit hits may still fail once; the session recovers on its next
# request. Running glovebox sandboxes converge with no pickup delay at all:
# their host-side credential proxy swaps in whatever `glovebox login-sync`
# last registered, and rotation triggers that on every account change
# (VM-internal transcripts are invisible to the watcher, so sandbox-only
# exhaustion is caught by the backstop rather than the watcher).
#
# Usage:
#   claude-account COMMAND [ARGS...]
#   claude-account --helper
#   claude-account --watch
#   claude-account --help
#
# Environment:
#   CLAUDE_ACCOUNT_NAMESPACES     whitespace-separated namespaces, in preference
#                                 order. Unset: every envchain namespace holding a
#                                 CLAUDE_CODE_OAUTH_TOKEN, in `envchain --list` order.
#   CLAUDE_ACCOUNT_PROBE_INTERVAL seconds a healthy probe verdict is trusted in
#                                 --helper mode (default 300). The backstop
#                                 rotation latency when the watcher misses a
#                                 denial; lower = more probe requests against
#                                 the accounts' own limits.
#   CLAUDE_ACCOUNT_WATCH_POLL     watcher transcript-scan period in seconds
#                                 (default 2) — the detection latency.
#   CLAUDE_ACCOUNT_WATCH_IDLE_EXIT  watcher self-exits after this many seconds
#                                 without a helper heartbeat (default 600).
#
# Using several accounts to work past a usage limit is addressed by Anthropic's
# consumer terms. Read the clause on multiple accounts before relying on this.
set -euo pipefail

# shellcheck source=bin/lib/claude-account-lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/claude-account-lib.sh"

# When the helper's selection CHANGES, converge running glovebox sandboxes on
# the new account: the in-VM claude sends a constant sentinel and the host-side
# sbx proxy swaps in whatever token `glovebox login-sync` last registered, so
# one push re-points every live session. Detached and best-effort, because sbx
# store calls can outlast the ~10s budget Claude Code gives an apiKeyHelper and
# a helper beat must never block on them. The `current` record is what makes
# this fire once per change rather than once per beat.
_converge_glovebox() {
    # CLAUDE_ACCOUNT_NO_CONVERGE is doctor's knob: its helper self-test must
    # stay read-only, and a convergence it triggered could re-point live
    # sandboxes the user pinned to another account on purpose.
    [[ -n "${CLAUDE_ACCOUNT_NO_CONVERGE:-}" ]] && return 0
    local ns="$1" dir file cur tmp
    dir="$(_state_dir)"
    file="$dir/current"
    cur="$(cat "$file" 2>/dev/null || true)"
    [[ "$cur" == "$ns" ]] && return 0
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    tmp="$file.$$"
    if printf '%s\n' "$ns" >"$tmp" 2>/dev/null; then
        mv -f "$tmp" "$file" 2>/dev/null || true
    fi
    command -v glovebox >/dev/null 2>&1 || return 0
    printf 'claude-account: switching to %s — converging glovebox sandboxes.\n' "$ns" >&2
    (envchain "$ns" glovebox login-sync >/dev/null 2>&1 &)
    return 0
}

_watcher_pid_file() { printf '%s/watcher.pid\n' "$(_state_dir)"; }
_watcher_heartbeat_file() { printf '%s/watcher.heartbeat\n' "$(_state_dir)"; }

# Keep one denial watcher alive per machine. Called from every helper beat:
# touches the heartbeat that keeps the watcher from idling out, and respawns it
# when the recorded pid is gone. CLAUDE_ACCOUNT_NO_WATCH is for invocations
# that must not leave a process behind (doctor's read-only self-test, tests).
_ensure_watcher() {
    [[ -n "${CLAUDE_ACCOUNT_NO_WATCH:-}" ]] && return 0
    local dir pid
    dir="$(_state_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    touch "$(_watcher_heartbeat_file)" 2>/dev/null || true
    pid="$(cat "$(_watcher_pid_file)" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    ("${BASH_SOURCE[0]}" --watch >/dev/null 2>&1 &)
    return 0
}

# The mtime of FILE, or 0 when unreadable (GNU stat, BSD fallback).
_mtime() {
    local m
    m="$(stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null)" || m=0
    [[ "$m" =~ ^[0-9]+$ ]] || m=0
    printf '%s' "$m"
}

# True (0) when a transcript modified since the last scan carries a fresh
# usage-limit API error. Trigger-only: the transcript format is undocumented,
# so a missed signature merely degrades to the CLAUDE_ACCOUNT_PROBE_INTERVAL
# backstop, and a false hit costs one probe — the probe stays the source of
# truth for whether the account is actually exhausted.
_watch_saw_denial() {
    local root="${CLAUDE_ACCOUNT_TRANSCRIPT_DIR:-$HOME/.claude/projects}" stamp file
    [[ -d "$root" ]] || return 1
    stamp="$(_state_dir)/watcher.scanned"
    local -a fresh=()
    if [[ -e "$stamp" ]]; then
        mapfile -t fresh < <(find "$root" -name '*.jsonl' -newer "$stamp" 2>/dev/null)
    else
        mapfile -t fresh < <(find "$root" -name '*.jsonl' -mmin -1 2>/dev/null)
    fi
    touch "$stamp" 2>/dev/null || true
    ((${#fresh[@]})) || return 1
    for file in "${fresh[@]}"; do
        if tail -n 25 "$file" 2>/dev/null |
            grep -q -iE '"isApiErrorMessage": ?true.*(usage limit|rate.?limit|429|529)'; then
            return 0
        fi
    done
    return 1
}

# The denial watcher: poll the transcripts, and on a denial probe-and-rotate
# right away instead of waiting out the probe interval. Exactly one runs per
# machine (the pidfile is the claim; a losing twin notices and exits), and it
# exits on its own once no helper has heartbeat for WATCH_IDLE_EXIT seconds —
# so it lives exactly as long as sessions do, with nothing to install.
_watch() {
    local dir pidfile hb tmp now hb_mtime
    dir="$(_state_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    pidfile="$(_watcher_pid_file)"
    hb="$(_watcher_heartbeat_file)"
    [[ -e "$hb" ]] || touch "$hb" 2>/dev/null || true
    tmp="$pidfile.$$"
    printf '%s\n' "$$" >"$tmp" 2>/dev/null && mv -f "$tmp" "$pidfile" 2>/dev/null || return 0
    while :; do
        [[ "$(cat "$pidfile" 2>/dev/null)" == "$$" ]] || return 0
        now="$(date +%s)"
        hb_mtime="$(_mtime "$hb")"
        if ((now - hb_mtime > ${CLAUDE_ACCOUNT_WATCH_IDLE_EXIT:-600})); then
            rm -f "$pidfile" 2>/dev/null || true
            return 0
        fi
        if _watch_saw_denial; then
            # Clear only the active account's stamp, then re-select TRUSTING
            # the other stamps: the active account gets a real probe (rejected
            # writes its cooldown), the winner is served from its fresh stamp,
            # and a denial storm that keeps appending error lines re-triggers
            # into cooldown skips and stamp hits — zero further requests.
            local cur ns
            cur="$(cat "$dir/current" 2>/dev/null || true)"
            [[ -n "$cur" ]] && _clear_ok "$cur"
            if ns="$(select_account 1 2>/dev/null)" && [[ -n "$ns" ]]; then
                _converge_glovebox "$ns"
            fi
        fi
        sleep "${CLAUDE_ACCOUNT_WATCH_POLL:-2}"
    done
}

# The apiKeyHelper contract: print the credential Claude Code should use RIGHT
# NOW on stdout, and nothing else. Printing the token on stdout is the one
# sanctioned exception to "the token stays inside the envchain child": stdout
# here is a pipe read only by the invoking claude process, so the token still
# appears on no argv and in no other process.
#
# A failing apiKeyHelper does NOT fall through to lower-precedence credentials —
# Claude Code hard-fails requests after three helper failures — and
# settings.json wires this helper on every machine, so this must succeed
# whenever ANY credential exists: a usable subscription namespace first, else
# the `ai` namespace's pay-per-token ANTHROPIC_API_KEY.
# shellcheck disable=SC2016  # the single quotes are the security property on
# every sh -c body below: the credential must expand inside the envchain CHILD,
# never in this shell (and never on an argv).
_helper() {
    local ns="" rc=0
    _ensure_watcher
    # A machine with no subscription namespaces at all goes straight to the
    # API-key fallback: calling select_account would print its seed-an-account
    # guidance on EVERY beat — a nag every TTL on machines that deliberately
    # run pay-per-token only. Doctor carries that guidance instead.
    if [[ -n "$(_namespaces)" ]]; then
        ns="$(select_account 1)" || rc=$?
    fi
    if [[ -n "$ns" ]]; then
        _converge_glovebox "$ns"
        exec envchain "$ns" sh -c 'printf %s "$CLAUDE_CODE_OAUTH_TOKEN"'
    fi
    if envchain ai sh -c '[ -n "${ANTHROPIC_API_KEY:-}" ]' 2>/dev/null; then
        ((rc == 1)) &&
            printf 'claude-account: every subscription account is exhausted — serving the pay-per-token ANTHROPIC_API_KEY from envchain ai until one resets.\n' >&2
        exec envchain ai sh -c 'printf %s "$ANTHROPIC_API_KEY"'
    fi
    printf 'claude-account: no usable Claude credential — no subscription account is available and envchain ai holds no ANTHROPIC_API_KEY (seed with bwseed).\n' >&2
    return 1
}

main() {
    if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
        awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
        return 0
    fi

    if ! command -v envchain >/dev/null 2>&1; then
        printf 'claude-account: envchain is not installed — it is where each account'\''s token lives.\n' >&2
        printf '  install it, then: envchain --set <namespace> CLAUDE_CODE_OAUTH_TOKEN\n' >&2
        return 2
    fi

    if [[ "${1:-}" == --helper ]]; then
        _helper
        return
    fi

    if [[ "${1:-}" == --watch ]]; then
        _watch
        return
    fi

    if [[ $# -eq 0 ]]; then
        printf 'claude-account: needs a command to run (try: claude-account --help)\n' >&2
        return 2
    fi

    local ns="" rc=0
    ns="$(select_account 0)" || rc=$?
    if [[ -n "$ns" ]]; then
        printf 'claude-account: using %s.\n' "$ns" >&2
        exec envchain "$ns" "$@"
    fi
    # select_account already named the failure on stderr; keep "none configured"
    # as the setup-error exit (2) and "all exhausted" as plain failure (1).
    ((rc == 2)) && return 2
    return 1
}

main "$@"
