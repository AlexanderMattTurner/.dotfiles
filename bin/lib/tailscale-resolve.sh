# shellcheck shell=bash
# Tailscale CLI + Mullvad exit-node config — single source of truth.
#
# A leftover /usr/local/bin/tailscale shim from the (uninstalled) Mac App
# Store Tailscale exec's a missing binary, so `command -v` alone isn't
# enough — each candidate is probed with `tailscale version`.

# "code flag host"  — append a row to add a country.
# shellcheck disable=SC2034
TAILSCALE_EXIT_NODES=(
    "us 🇺🇸 us-chi-wg-301.mullvad.ts.net"
    "ca 🇨🇦 ca-mtr-wg-001.mullvad.ts.net"
    "jp 🇯🇵 jp-tyo-wg-001.mullvad.ts.net"
)

# Print "flag host" for $1 (country code). Non-zero on unknown code.
tailscale_node_lookup() {
    local row
    for row in "${TAILSCALE_EXIT_NODES[@]}"; do
        [ "${row%% *}" = "$1" ] && printf '%s\n' "${row#* }" && return 0
    done
    return 1
}

# Classify CLI↔daemon health for $1 (path to a tailscale CLI). Prints one of:
#   ok          daemon up, logged in (exit node may be on or off)
#   stopped     logged in but administratively down (`tailscale down`)
#   no-daemon   tailscaled is not running
#   eperm       CLI denied access to the socket (stale provenance after two
#               daemons raced on /var/run/tailscaled.socket)
#   logged-out  daemon up but node key gone/expired — needs `tailscale up`
#   error       any other non-zero `tailscale status`
#
# Consumers that must stay in sync (see CLAUDE.md "Tailscale daemon"):
# apps/swiftbar/vpn.10s.bash, bin/tailscale-set-exit-node.bash,
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
# running; a skewed pair has mishandled exit-node teardown (`tailscale set
# --exit-node=` blackholes all traffic instead of restoring the default
# route). Returns 0 (silent) when versions match or either side is
# unreadable; returns 1 and prints "client=X daemon=Y" on skew.
#
# Consumers that must stay in sync (see CLAUDE.md "Tailscale daemon"):
# apps/swiftbar/vpn.10s.bash, bin/tailscale-set-exit-node.bash,
# bin/doctor.bash, setup.bash, tests/test_tailscale_health.py.
tailscale_version_skew() {
    local client daemon
    client="$("$1" version 2>/dev/null | head -n1)"
    daemon="$("$1" status --json 2>/dev/null | grep -m1 '"Version"')"
    daemon="${daemon#*: \"}"
    daemon="${daemon%%-*}"
    if [ -z "$client" ] || [ -z "$daemon" ] || [ "$client" = "$daemon" ]; then
        return 0
    fi
    printf 'client=%s daemon=%s\n' "$client" "$daemon"
    return 1
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
