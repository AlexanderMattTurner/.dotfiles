# shellcheck shell=bash
# Tailscale CLI + daemon health — single source of truth.
#
# Tailscale here is the tailnet only (ssh to mac-mini and friends). Egress
# belongs to the Mullvad app; `tailscale set --exit-node=…` is never used, see
# CLAUDE.md "VPN".
#
# A leftover /usr/local/bin/tailscale shim from the (uninstalled) Mac App
# Store Tailscale exec's a missing binary, so `command -v` alone isn't
# enough — each candidate is probed with `tailscale version`.

# Classify CLI↔daemon health for $1 (path to a tailscale CLI). Prints one of:
#   ok          daemon up, logged in
#   stopped     logged in but administratively down (`tailscale down`)
#   no-daemon   tailscaled is not running
#   eperm       CLI denied access to the socket (stale provenance after two
#               daemons raced on /var/run/tailscaled.socket)
#   logged-out  daemon up but node key gone/expired — needs `tailscale up`
#   error       any other non-zero `tailscale status`
#
# Consumers that must stay in sync (see CLAUDE.md "Tailscale daemon"):
# bin/doctor.bash, tests/test_tailscale_health.py.
tailscale_health() {
    local out rc=0
    out="$("$1" status 2>&1)" || rc=$?
    case "$out" in
    *"operation not permitted"*) echo eperm ;;
    *"failed to connect"*) echo no-daemon ;;
    *"Logged out"* | *"unexpected state: NoState"* | *NeedsLogin* | *"Log in at"*) echo logged-out ;;
    *"Tailscale is stopped"*) echo stopped ;;
    *) [ "$rc" -eq 0 ] && echo ok || echo error ;;
    esac
}

# Detect CLI↔daemon version skew for $1 (path to a tailscale CLI).
# `brew upgrade tailscale` swaps the CLI binary but leaves the old tailscaled
# running. Returns 0 (silent) when versions match or either side is
# unreadable; returns 1 and prints "client=X daemon=Y" on skew.
#
# Consumers that must stay in sync (see CLAUDE.md "Tailscale daemon"):
# bin/doctor.bash, setup.bash, tests/test_tailscale_health.py.
tailscale_version_skew() {
    local client daemon
    client="$("$1" version 2>/dev/null | head -n1)"
    daemon="$(_tailscale_json_value "$1" status --json -- Version)"
    daemon="${daemon%%-*}"
    if [ -z "$client" ] || [ -z "$daemon" ] || [ "$client" = "$daemon" ]; then
        return 0
    fi
    printf 'client=%s daemon=%s\n' "$client" "$daemon"
    return 1
}

# Print the string value of top-level key $N from the pretty-printed JSON that
# `$1 <args...> -- KEY` emits, e.g. `_tailscale_json_value tailscale status
# --json -- Version`. Empty when the key is absent, null, or the command fails.
# awk reads to EOF rather than `exit`ing on the match, so the writer never
# sees a closed pipe under the caller's `set -o pipefail`.
_tailscale_json_value() {
    local cli="$1" key
    shift
    local args=()
    while [ "$1" != -- ]; do
        args+=("$1")
        shift
    done
    key="$2"
    "$cli" "${args[@]}" 2>/dev/null |
        awk -v key="\"$key\"" '
            $1 == key ":" && !seen {seen = 1; v = $2; sub(/,$/, "", v); gsub(/"/, "", v)}
            END {print v}'
}

# True when $1 (CLI path) has an exit node in its *persisted prefs*. On this
# Mac that is a misconfiguration, not a feature: the Homebrew tailscaled's BSD
# userspace router deletes the physical default route when the exit node is
# cleared, so doctor flags it before anyone clicks "Disconnect". See CLAUDE.md
# "VPN".
#
# Prefs, not `status --json`: a stopped daemon reports no ExitNodeStatus while
# still carrying the pref that re-engages on the next `tailscale up`. Either
# field set means engaged; an unreadable daemon reads as off, since the health
# check already covers that.
tailscale_exit_node_engaged() {
    local id ip
    id="$(_tailscale_json_value "$1" debug prefs -- ExitNodeID)"
    ip="$(_tailscale_json_value "$1" debug prefs -- ExitNodeIP)"
    [ -n "$id$ip" ]
}

# Print absolute path to a working tailscale CLI; non-zero if none found.
find_tailscale() {
    local c
    for c in /opt/homebrew/bin/tailscale /usr/local/bin/tailscale \
        "$(command -v tailscale 2>/dev/null || true)"; do
        [ -n "$c" ] && [ -x "$c" ] && "$c" version >/dev/null 2>&1 && {
            printf '%s\n' "$c"
            return 0
        }
    done
    return 1
}
