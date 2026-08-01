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
# every CLAUDE_CODE_API_KEY_HELPER_TTL_MS (pinned to 60s there) and on any 401 —
# so when the active account exhausts, a later beat notices (within
# CLAUDE_ACCOUNT_PROBE_INTERVAL) and hands the session the next account's token.
# A turn already in flight when the limit hits may still fail once; the session
# recovers on its next request. Running glovebox sandboxes converge too: their
# host-side credential proxy swaps in whatever `glovebox login-sync` last
# registered, and --helper triggers that on every account change.
#
# Usage:
#   claude-account COMMAND [ARGS...]
#   claude-account --helper
#   claude-account --help
#
# Environment:
#   CLAUDE_ACCOUNT_NAMESPACES     whitespace-separated namespaces, in preference
#                                 order. Unset: every envchain namespace holding a
#                                 CLAUDE_CODE_OAUTH_TOKEN, in `envchain --list` order.
#   CLAUDE_ACCOUNT_PROBE_INTERVAL seconds a healthy probe verdict is trusted in
#                                 --helper mode (default 300). Lower = faster
#                                 rotation after an exhaustion, paid for in probe
#                                 requests against the accounts' own limits.
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
    ns="$(select_account 1)" || rc=$?
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
