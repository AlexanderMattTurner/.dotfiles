#!/usr/bin/env bash
# claude-account — pick a Claude subscription that is not at its usage limit and
# run COMMAND under it, or answer which account the rotation proxy should serve.
#
# Each account is one envchain namespace holding a CLAUDE_CODE_OAUTH_TOKEN
# (`envchain --set <ns> CLAUDE_CODE_OAUTH_TOKEN`, captured by `claude setup-token`
# while signed in as that account). Namespaces are tried in order and the first
# one Anthropic reports as usable wins. In launcher mode the token reaches
# COMMAND in its environment, so this works for anything that reads
# CLAUDE_CODE_OAUTH_TOKEN — `claude`, `claude-private`, `glovebox`, a bare
# `claude -p '...'`.
#
# Mid-session rotation is done by bin/claude-rotate-proxy.py, a loopback proxy the
# `claude` fish wrapper points ANTHROPIC_BASE_URL at (the session launches holding
# only a sentinel, so it fixes the subscription presentation without a real token).
# The proxy asks `--pick` for the namespace to serve, issues the upstream request as
# `envchain <ns> curl` so the token never leaves the envchain child, and on a
# usage-limit 429 it reads off the response it calls `--cooldown` and replays on the
# next account — denial to rotated token in one request, no transcript grep and no
# poll. On an account change the proxy's `--pick` converges running glovebox
# sandboxes through their own host-side proxy (`glovebox login-sync`).
#
# Usage:
#   claude-account COMMAND [ARGS...]     run COMMAND under the first usable account
#   claude-account --pick                print the namespace to serve now (proxy)
#   claude-account --cooldown NS [RESET] record NS as rate-limited (proxy)
#   claude-account --namespaces          list configured namespaces (wrapper routing)
#   claude-account --help
#
# Environment:
#   CLAUDE_ACCOUNT_NAMESPACES     whitespace-separated namespaces, in preference
#                                 order. Unset: every envchain namespace holding a
#                                 CLAUDE_CODE_OAUTH_TOKEN, in `envchain --list` order.
#   CLAUDE_ACCOUNT_PROBE_INTERVAL seconds a healthy probe verdict is trusted before
#                                 `--pick` re-probes (default 300). The proxy calls
#                                 `--pick` per request, so this stamp is what keeps a
#                                 healthy account from being re-probed every request.
#
# Using several accounts to work past a usage limit is addressed by Anthropic's
# consumer terms. Read the clause on multiple accounts before relying on this.
set -euo pipefail

# shellcheck source=bin/lib/claude-account-lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/claude-account-lib.sh"

# Record NAME as the identity the helper last served — a namespace, or
# __api-key__ for the pay-per-token fallback. This is the change-latch for
# both the glovebox convergence and the helper's switch diagnostics. Gated on
# CLAUDE_ACCOUNT_NO_CONVERGE so an invocation that must leave no trace
# (doctor's self-test) records nothing.
_record_current() {
    [[ -n "${CLAUDE_ACCOUNT_NO_CONVERGE:-}" ]] && return 0
    local dir file tmp
    dir="$(_state_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    file="$dir/current"
    tmp="$file.$$"
    if printf '%s\n' "$1" >"$tmp" 2>/dev/null; then
        mv -f "$tmp" "$file" 2>/dev/null || true
    fi
    return 0
}

# When --pick's selection CHANGES, converge running glovebox sandboxes on the new
# account: the in-VM claude sends a constant sentinel and the host-side sbx proxy
# swaps in whatever token `glovebox login-sync` last registered, so one push
# re-points every live session. Detached and best-effort, because sbx store calls
# can outlast the per-request budget the rotation proxy has and must never block a
# --pick. The `current` record is what makes this fire once per change, not per pick.
_converge_glovebox() {
    # CLAUDE_ACCOUNT_NO_CONVERGE is doctor's knob: a convergence triggered by a
    # health check could re-point live sandboxes the user pinned to another
    # account on purpose.
    [[ -n "${CLAUDE_ACCOUNT_NO_CONVERGE:-}" ]] && return 0
    local ns="$1" cur
    cur="$(cat "$(_state_dir)/current" 2>/dev/null || true)"
    [[ "$cur" == "$ns" ]] && return 0
    _record_current "$ns"
    command -v glovebox >/dev/null 2>&1 || return 0
    printf 'claude-account: switching to %s — converging glovebox sandboxes.\n' "$ns" >&2
    (envchain "$ns" glovebox login-sync >/dev/null 2>&1 &)
    return 0
}

# --pick: print the subscription namespace the proxy should serve right now, or
# exit 1 (printing nothing) when every account is at its usage limit. Runs the
# selection engine TRUSTING fresh healthy stamps, so a per-request call spends a
# live probe only when a stamp has gone stale, not every request. The winner must
# actually HOLD a token — a still-fresh stamp, or an explicit
# CLAUDE_ACCOUNT_NAMESPACES entry, can name a keychain entry that was since emptied,
# and printing that namespace would make the proxy send an empty Bearer. On the
# account that wins, converge running glovebox sandboxes (idempotent: fires only on
# a change of the recorded `current`).
_pick() {
    local ns
    ns="$(select_account 1 quiet-empty 2>/dev/null)" || return 1
    [[ -n "$ns" ]] || return 1
    # shellcheck disable=SC2016  # must expand in the envchain child, never here
    if ! envchain "$ns" sh -c '[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]' 2>/dev/null; then
        _clear_ok "$ns"
        return 1
    fi
    _converge_glovebox "$ns"
    printf '%s\n' "$ns"
}

# --cooldown NS [RESET]: record NS as rate-limited, the proxy's reaction to a 429 it
# saw. RESET is the epoch the account frees up (from the response's
# anthropic-ratelimit-unified-reset); an absent, non-numeric, or already-past value
# falls back to an hour, because a reset in the past cannot cool the account down and
# the next --pick would re-probe straight into the same 429.
_cooldown() {
    local ns="$1" reset="${2:-}" now
    [[ -n "$ns" ]] || return 2
    _clear_ok "$ns"
    now="$(date +%s)"
    if [[ ! "$reset" =~ ^[1-9][0-9]*$ ]] || ((reset <= now)); then
        reset=$((now + 3600))
    fi
    _set_cooldown "$ns" "$reset"
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

    if [[ "${1:-}" == --pick ]]; then
        _pick
        return
    fi

    if [[ "${1:-}" == --cooldown ]]; then
        _cooldown "${2:-}" "${3:-}"
        return
    fi

    if [[ "${1:-}" == --namespaces ]]; then
        _namespaces
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
