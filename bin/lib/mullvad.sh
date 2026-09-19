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
#   no-daemon  the CLI could not reach the daemon, so the setting is unknown
#              and `auto-connect set on` would fail the same way
#
# Consumers that must stay in sync: bin/doctor.bash, tests/test_mullvad.py.
# Output is captured, never piped into grep -q: a reader closing early would
# SIGPIPE the CLI under the caller's `set -o pipefail`.
mullvad_autoconnect() {
    local out
    out="$("$1" auto-connect get 2>/dev/null)" || {
        echo no-daemon
        return
    }
    case "$out" in
    *"Autoconnect: on"*) echo on ;;
    *"Autoconnect: off"*) echo off ;;
    *) echo no-daemon ;;
    esac
}
