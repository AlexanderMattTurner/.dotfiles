# shellcheck shell=bash
# bin/lib/claude-account-lib.sh — the account-selection engine shared by
# bin/claude-account.bash's launcher and apiKeyHelper modes.
#
# Each account is one envchain namespace holding a CLAUDE_CODE_OAUTH_TOKEN.
# select_account walks the namespaces in preference order and echoes the first
# usable one. All of its state — cooldown records and healthy-probe stamps
# under $XDG_STATE_HOME/claude-accounts — is shared, atomically written files,
# so every caller (a launch, a helper beat, a concurrent session's helper
# beat) reads one consistent view without a daemon or a lock.

# Where a namespace's usage limit is recorded as an epoch-seconds "not before" time.
_state_dir() {
    printf '%s/claude-accounts\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

_until_file() {
    printf '%s/%s.until\n' "$(_state_dir)" "$1"
}

# NAMESPACE's "probed healthy" stamp; its mtime is the probe time.
_ok_file() {
    printf '%s/%s.ok\n' "$(_state_dir)" "$1"
}

# True (0) while NAMESPACE's recorded reset time is still in the future. A missing or
# non-numeric record is NOT a cooldown: a corrupt file must cause a re-probe, never an
# indefinite skip of an account that may be perfectly usable.
_on_cooldown() {
    local file not_before
    file="$(_until_file "$1")"
    [[ -s "$file" ]] || return 1
    read -r not_before <"$file" || return 1
    [[ "$not_before" =~ ^[0-9]+$ ]] || return 1
    (($(date +%s) < not_before))
}

# Record NAMESPACE as unusable until epoch-seconds $2. Written to a temp file and
# renamed so a concurrent launch reads the old value or the new one, never a partial
# line. ALWAYS returns 0: the record is an optimization, and a read-only or full state
# directory must not abort the rotation before the other accounts have been tried.
_set_cooldown() {
    local dir file tmp
    dir="$(_state_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    chmod 700 "$dir" 2>/dev/null || true
    file="$(_until_file "$1")"
    tmp="$file.$$"
    if (
        umask 077
        printf '%s\n' "$2" >"$tmp"
    ); then
        mv -f "$tmp" "$file" 2>/dev/null || true
    fi
    return 0
}

# True (0) while NAMESPACE's last healthy probe is younger than
# CLAUDE_ACCOUNT_PROBE_INTERVAL seconds (default 300). The interval MUST exceed
# the helper's re-invocation TTL (settings.json pins 60s), or the stamp
# suppresses nothing and every helper beat spends a live request. It is also
# the worst-case time for a helper to NOTICE the current account exhausted, so
# it is the cost/latency dial: probing faster buys faster rotation, paid for
# in requests against the very usage limit being conserved.
_ok_fresh() {
    local file mtime
    file="$(_ok_file "$1")"
    [[ -e "$file" ]] || return 1
    # GNU stat and BSD/macOS stat spell "mtime" differently; try both.
    mtime="$(stat -c %Y "$file" 2>/dev/null || stat -f %m "$file" 2>/dev/null)" || return 1
    [[ "$mtime" =~ ^[0-9]+$ ]] || return 1
    (($(date +%s) - mtime < ${CLAUDE_ACCOUNT_PROBE_INTERVAL:-300}))
}

# Stamp NAMESPACE as probed-healthy now. ALWAYS returns 0 for the same reason
# _set_cooldown does: the stamp only saves probes, so an unwritable state
# directory degrades to probing every beat, never to a failure.
_touch_ok() {
    local dir
    dir="$(_state_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    [[ -d "$dir" ]] || return 0
    touch "$(_ok_file "$1")" 2>/dev/null || true
    return 0
}

_clear_ok() {
    rm -f "$(_ok_file "$1")" 2>/dev/null || true
    return 0
}

# Echo the namespaces to try, in preference order.
_namespaces() {
    if [[ -n "${CLAUDE_ACCOUNT_NAMESPACES:-}" ]]; then
        local -a listed=()
        read -ra listed <<<"$CLAUDE_ACCOUNT_NAMESPACES"
        printf '%s\n' "${listed[@]}"
        return 0
    fi
    local ns
    while IFS= read -r ns; do
        [[ -n "$ns" ]] || continue
        # Presence is probed by the child's exit status, so the token's VALUE
        # never leaves the envchain child.
        # shellcheck disable=SC2016  # must expand in the child, never here
        envchain "$ns" sh -c '[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]' 2>/dev/null &&
            printf '%s\n' "$ns"
    done < <(envchain --list 2>/dev/null)
}

# The request body a subscription probe must send. An OAuth subscription token is
# only honoured on a request shaped like Claude Code: this system line AND the
# `anthropic-beta: oauth-2025-04-20` header below. Drop either and a perfectly
# healthy account answers 401, which the loop would read as "revoked" and skip
# forever. max_tokens 1 keeps a healthy probe to a few tokens of usage.
_PROBE_BODY='{"model":"claude-haiku-4-5","max_tokens":1,"system":[{"type":"text","text":"You are Claude Code, Anthropic'"'"'s official CLI for Claude."}],"messages":[{"role":"user","content":"ping"}]}'

# Echo "<status> <reset-epoch>" for NAMESPACE, where status is one of
# allowed | allowed_warning | rejected | revoked | credits | unknown.
#
# Anthropic reports usage state on EVERY response, not only on refusals, via
# anthropic-ratelimit-unified-status and -reset, so one request answers both "is this
# account usable" and "when does it free up". The two unambiguous REFUSALS are read
# off the status code and override the header: a 401 is a dead token whatever the
# usage state says, and a drained credit balance is a condition the header does not
# model at all.
#
# The whole probe runs INSIDE the envchain child and only the verdict crosses back,
# so the token never enters this script's environment or variables. Within that child
# it reaches curl over a pipe (-H @-) from a printf BUILTIN, so it never lands on an
# argv that `ps` can read either.
_probe() {
    local raw
    # shellcheck disable=SC2016  # the single quotes are the security property:
    # $CLAUDE_CODE_OAUTH_TOKEN must expand inside the envchain CHILD, never here.
    # Expanding it in this shell would put the token in this process and, via the
    # `sh -c` argv, in the process table for every other user on the machine.
    raw="$(
        envchain "$1" sh -c '
      printf "Authorization: Bearer %s\n" "$CLAUDE_CODE_OAUTH_TOKEN" |
        curl -sS --max-time 10 -D - -o - -w "\n%{http_code}" -H @- \
          -H "content-type: application/json" \
          -H "anthropic-version: 2023-06-01" \
          -H "anthropic-beta: oauth-2025-04-20" \
          -X POST https://api.anthropic.com/v1/messages --data "$1"
    ' _ "$_PROBE_BODY" 2>/dev/null
    )" || raw=""

    if [[ -z "$raw" ]]; then
        printf 'unknown 0\n'
        return 0
    fi

    # curl's -w appends "\n<code>" after the (possibly multi-line) body, so the status
    # code is everything after the LAST newline.
    local code="${raw##*$'\n'}"
    local line name value header_status="" reset_raw=""
    while IFS= read -r line; do
        line="${line%$'\r'}"
        [[ "$line" == *:* ]] || continue
        name="${line%%:*}"
        value="${line#*:}"
        value="${value# }"
        # Only the NAME is lowercased: HTTP/2 forbids uppercase field names outright
        # while HTTP/1.1 sends them as written, so the same server answers
        # `anthropic-ratelimit-unified-status` or `Anthropic-RateLimit-Unified-Status`
        # depending on which protocol the connection negotiated. Lowercasing the value
        # too would corrupt an ISO-8601 reset.
        case "$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')" in
        anthropic-ratelimit-unified-status) header_status="$value" ;;
        anthropic-ratelimit-unified-reset) reset_raw="$value" ;;
        esac
    done <<<"$raw"

    local status=unknown
    if [[ "$code" == 401 ]]; then
        status=revoked
    elif [[ "$code" == 4[0-9][0-9] ]] && grep -qi 'credit balance' <<<"$raw"; then
        status=credits
    elif [[ "$header_status" == allowed || "$header_status" == allowed_warning ||
        "$header_status" == rejected ]]; then
        status="$header_status"
    elif [[ "$code" == 429 ]]; then
        status=rejected
    elif [[ "$code" == 200 ]]; then
        status=allowed
    fi
    printf '%s %s\n' "$status" "$(_reset_epoch "$reset_raw")"
}

# Echo VALUE as epoch-seconds: passed through when already numeric, parsed when it is a
# timestamp (GNU `date -d`, else BSD `date -j -f`), else 0. Both dialects are tried
# because this runs on macOS and Linux and the header's format is not contractual.
# An ABSENT value must be 0 and never reach `date`, which reads "" as NOW — that would
# report an exhausted account as free this instant.
_reset_epoch() {
    local value="$1" epoch
    if [[ "$value" =~ ^[0-9]+$ ]]; then
        printf '%s' "$value"
        return 0
    fi
    if [[ -z "$value" ]]; then
        printf '0'
        return 0
    fi
    epoch="$(date -u -d "$value" +%s 2>/dev/null ||
        date -u -j -f '%Y-%m-%dT%H:%M:%S%z' "${value/%Z/+0000}" +%s 2>/dev/null || true)"
    [[ "$epoch" =~ ^[0-9]+$ ]] || epoch=0
    printf '%s' "$epoch"
}

# Echo EPOCH as a human-readable local time, or nothing when it is 0/unparseable.
_reset_human() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 0
    date -d "@$1" 2>/dev/null || date -r "$1" 2>/dev/null || true
}

# Echo the first usable namespace, walking them in preference order. With
# TRUST_OK=1 (the helper's beat cadence) a namespace whose healthy-probe stamp
# is still fresh is chosen without spending a request; TRUST_OK=0 (a launch)
# probes every candidate. ONLY the chosen namespace lands on stdout — every
# diagnostic goes to stderr, because the helper contract hands stdout verbatim
# to Claude Code as a credential. Returns 1 when every configured account is
# unavailable, 2 when none is configured at all.
select_account() {
    local trust_ok="$1"
    local -a namespaces=()
    mapfile -t namespaces < <(_namespaces)
    if ((${#namespaces[@]} == 0)); then
        printf 'claude-account: no envchain namespace holds a CLAUDE_CODE_OAUTH_TOKEN.\n' >&2
        printf '  capture one with '\''claude setup-token'\'', then: envchain --set <namespace> CLAUDE_CODE_OAUTH_TOKEN\n' >&2
        return 2
    fi

    local ns status reset now
    for ns in "${namespaces[@]}"; do
        if _on_cooldown "$ns"; then
            printf 'claude-account: %s is at its usage limit until %s — skipping.\n' \
                "$ns" "$(_reset_human "$(<"$(_until_file "$ns")")")" >&2
            continue
        fi
        if [[ "$trust_ok" == 1 ]] && _ok_fresh "$ns"; then
            printf '%s\n' "$ns"
            return 0
        fi
        # A probe that answers nothing leaves `read` non-zero; treat that as the
        # unclassifiable case, never as a reason to abort the launch under set -e.
        status=unknown reset=0
        read -r status reset < <(_probe "$ns") || status=unknown
        case "$status" in
        allowed | allowed_warning)
            rm -f "$(_until_file "$ns")"
            _touch_ok "$ns"
            [[ "$status" == allowed_warning ]] &&
                printf 'claude-account: %s is close to its usage limit.\n' "$ns" >&2
            printf '%s\n' "$ns"
            return 0
            ;;
        rejected)
            _clear_ok "$ns"
            # A reset that is absent, unparseable, or ALREADY PAST cannot cool the
            # account down, so all three fall back to an hour. The past case is not
            # hypothetical: a header carrying seconds-until-reset rather than an
            # absolute epoch would record 1970, which _on_cooldown reads as expired,
            # so the very re-probe this guard prevents would happen next launch.
            now="$(date +%s)"
            if [[ ! "$reset" =~ ^[1-9][0-9]*$ ]] || ((reset <= now)); then
                reset=$((now + 3600))
            fi
            _set_cooldown "$ns" "$reset"
            printf 'claude-account: %s is at its usage limit until %s.\n' \
                "$ns" "$(_reset_human "$reset")" >&2
            ;;
        revoked)
            _clear_ok "$ns"
            printf 'claude-account: %s was rejected by Anthropic — re-capture it with '\''claude setup-token'\'' signed in as that account, then: envchain --set %s CLAUDE_CODE_OAUTH_TOKEN\n' \
                "$ns" "$ns" >&2
            ;;
        credits)
            printf 'claude-account: %s is out of credits.\n' "$ns" >&2
            ;;
        *)
            printf 'claude-account: could not check %s (no network, or its keychain entry would not open) — skipping it this launch.\n' "$ns" >&2
            ;;
        esac
    done

    printf 'claude-account: none of these accounts is available right now: %s\n' "${namespaces[*]}" >&2
    return 1
}
