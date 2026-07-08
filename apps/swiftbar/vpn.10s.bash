#!/usr/bin/env bash
# <bitbar.title>VPN</bitbar.title>
# <bitbar.author>turntrout</bitbar.author>
# <bitbar.desc>Status and control for Tailscale Mullvad exit node.</bitbar.desc>

_self="${BASH_SOURCE[0]}"
while [[ -L "$_self" ]]; do
    _link="$(readlink "$_self")"
    case "$_link" in /*) _self="$_link" ;; *) _self="${_self%/*}/$_link" ;; esac
done
_self_dir="$(dirname "$_self")"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=../../bin/lib/tailscale-resolve.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/tailscale-resolve.sh"

APPLY="$DOTFILES_DIR/bin/tailscale-set-exit-node.bash"

if ! TAILSCALE="$(find_tailscale)"; then
    printf '⚠️ vpn\n---\nno working tailscale CLI — brew install tailscale\n'
    exit 0
fi

# Surface daemon failures distinctly so "🔴 off" strictly means "healthy,
# exit node deliberately off" — not "daemon dead and traffic blackholed".
# States come from tailscale_health in bin/lib/tailscale-resolve.sh.
health="$(tailscale_health "$TAILSCALE")"
case "$health" in
no-daemon)
    echo "⚠️ vpn"
    echo "---"
    echo "tailscaled is not running | font=Menlo size=11"
    echo "Start daemon… | shell=/usr/bin/sudo param1=launchctl param2=bootstrap param3=system param4=/Library/LaunchDaemons/com.$USER.tailscaled.plist terminal=true refresh=true"
    exit 0
    ;;
eperm)
    echo "⚠️ vpn"
    echo "---"
    echo "socket EPERM — daemons raced on tailscaled.socket | font=Menlo size=11"
    echo "Restart daemon… | shell=/usr/bin/sudo param1=launchctl param2=kickstart param3=-k param4=system/com.$USER.tailscaled terminal=true refresh=true"
    exit 0
    ;;
stopped)
    echo "⏸ vpn"
    echo "---"
    echo "tunnel is down (tailscale down) | font=Menlo size=11"
    echo "Connect… | shell=$TAILSCALE param1=up terminal=false refresh=true"
    exit 0
    ;;
logged-out)
    echo "🔓 vpn"
    echo "---"
    echo "logged out — node key gone or expired | font=Menlo size=11"
    echo "Log in… | shell=$TAILSCALE param1=up terminal=true refresh=true"
    exit 0
    ;;
error)
    echo "⚠️ vpn"
    echo "---"
    echo "tailscale status failed — run it in a terminal | font=Menlo size=11"
    exit 0
    ;;
esac
# ok → check for version skew, then show the exit-node picker below.

# CLI upgraded (brew) while the old daemon kept running. Disconnecting in
# this state has blackholed all traffic, so surface it instead of the picker.
if ! skew="$(tailscale_version_skew "$TAILSCALE")"; then
    echo "⚠ vpn"
    echo "---"
    echo "version skew: $skew | font=Menlo size=11"
    echo "disconnect/switch may blackhole traffic | font=Menlo size=11"
    echo "Restart daemon… | shell=/usr/bin/sudo param1=launchctl param2=kickstart param3=-k param4=system/com.$USER.tailscaled terminal=true refresh=true"
    exit 0
fi

line=$("$TAILSCALE" status 2>/dev/null | awk '/mullvad\.ts\.net.*exit node/ {print; exit}')
host=$(awk '{print $2}' <<<"$line")
country=${host:0:2}
state=$(grep -q active <<<"$line" && echo active || echo idle)

if [ -z "$host" ]; then
    echo "🔴 off"
else
    flag=$(tailscale_node_lookup "$country" | awk '{print $1}')
    [ "$state" = active ] && echo "$flag $country" || echo "$flag $country (idle)"
fi
echo "---"

if [ -n "$host" ]; then
    printf '%s | font=Menlo size=11\nstate: %s | font=Menlo size=11\n---\n' "$host" "$state"
fi

for row in "${TAILSCALE_EXIT_NODES[@]}"; do
    read -r c flag node <<<"$row"
    if [ "$c" = "$country" ]; then
        echo "$flag $c ✓ | shell=$APPLY param1=$c terminal=false refresh=true"
    else
        echo "$flag $c — $node | shell=$APPLY param1=$c terminal=false refresh=true"
    fi
done

if [ -n "$host" ]; then
    echo "---"
    echo "🔴 Disconnect | shell=$APPLY param1=off terminal=false refresh=true"
fi

echo "---"
echo "Refresh | refresh=true"
