# shellcheck shell=bash
# Mullvad VPN app — the egress VPN on this machine (see CLAUDE.md "VPN").
#
# The app bundles its CLI off PATH, so consumers resolve it here rather than
# each hard-coding the bundle path. `MULLVAD_CLI` is a test seam.
MULLVAD_CLI="${MULLVAD_CLI:-/Applications/Mullvad VPN.app/Contents/Resources/mullvad}"

# Print the path to an executable Mullvad CLI; non-zero if the app is absent.
find_mullvad() {
    [ -x "$MULLVAD_CLI" ] || return 1
    printf '%s\n' "$MULLVAD_CLI"
}

# True when the daemon will connect by itself at login, so the machine is
# behind the VPN without anyone opening the app.
mullvad_autoconnect_enabled() {
    "$1" auto-connect get 2>/dev/null | grep -q 'Autoconnect: on'
}
