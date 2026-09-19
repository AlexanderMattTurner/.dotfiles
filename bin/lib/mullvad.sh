# shellcheck shell=bash
# Mullvad VPN app — the egress VPN on this machine (see CLAUDE.md "VPN").
#
# The app bundles its CLI off PATH, so consumers resolve it here rather than
# each hard-coding the bundle path. `MULLVAD_CLI` is a test seam; the `mullvad`
# fish function in apps/fish/config.fish carries the same path.
MULLVAD_CLI="${MULLVAD_CLI:-/Applications/Mullvad VPN.app/Contents/Resources/mullvad}"

# Print the path to an executable Mullvad CLI; non-zero if the app is absent.
find_mullvad() {
    [ -x "$MULLVAD_CLI" ] || return 1
    printf '%s\n' "$MULLVAD_CLI"
}

# Classify the auto-connect setting for $1 (CLI path). Prints one of:
#   on         the daemon connects by itself at login
#   off        it does not — the machine boots in the clear
#   unreachable  the CLI could not talk to the daemon (down, socket denied,
#                version mismatch), so the setting is unknown and
#                `auto-connect set on` would fail the same way
#
# Consumers that must stay in sync: bin/doctor.bash, tests/test_mullvad.py.
# Output is captured, never piped into grep -q: a reader closing early would
# SIGPIPE the CLI under the caller's `set -o pipefail`.
mullvad_autoconnect() {
    local out
    out="$("$1" auto-connect get 2>/dev/null)" || {
        echo unreachable
        return
    }
    case "$out" in
    *"Autoconnect: on"*) echo on ;;
    *"Autoconnect: off"*) echo off ;;
    *) echo unreachable ;;
    esac
}

# Classify the tunnel for $1 (CLI path): connected / disconnected /
# unreachable. Auto-connect only says what happens at login; this is whether
# the machine is behind the VPN *now*. Same consumers as above.
mullvad_tunnel() {
    local out
    out="$("$1" status 2>/dev/null)" || {
        echo unreachable
        return
    }
    case "$out" in
    Connected*) echo connected ;;
    Disconnected* | Disconnecting* | Connecting*) echo disconnected ;;
    *) echo unreachable ;;
    esac
}
