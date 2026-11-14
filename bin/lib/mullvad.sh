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

# Apps that must bypass the tunnel. AnkiWeb refuses Mullvad exit IPs, so Anki
# sync fails behind the VPN; excluding the one app beats disabling the VPN.
# Tailscale exit nodes are not the alternative (see CLAUDE.md "VPN").
# shellcheck disable=SC2034 # read by setup.bash and bin/doctor.bash
MULLVAD_SPLIT_APPS=("/Applications/Anki.app")

# Classify whether app $2 bypasses the tunnel for $1 (CLI path). Prints one of:
#   excluded       split tunneling is on and lists the app
#   not-excluded   split tunneling is on but does not list the app
#   off            split tunneling is off, so every app goes through the tunnel
#   unreachable    the CLI failed or printed output this parser does not know
# Consumers: setup.bash, bin/doctor.bash, tests/test_mullvad.py.
mullvad_split_tunnel() {
    local out
    out="$("$1" split-tunnel get 2>/dev/null)" || {
        echo unreachable
        return
    }
    case "$out" in
    *"Split tunneling state: on"*) ;;
    *"Split tunneling state: off"*)
        echo off
        return
        ;;
    *)
        echo unreachable
        return
        ;;
    esac
    if grep -qxF "$2" <<<"$out"; then echo excluded; else echo not-excluded; fi
}
