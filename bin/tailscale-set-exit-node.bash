#!/usr/bin/env bash
set -euo pipefail
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/tailscale-resolve.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/tailscale-resolve.sh"

LOG="$HOME/Library/Logs/com.turntrout.tailscale-exit-node/menu.log"
mkdir -p "${LOG%/*}"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }
# Failures go to the log (SwiftBar runs us detached) AND stderr (terminal use).
die() {
    log "FAIL $*"
    echo "tailscale-set-exit-node: $*" >&2
}

IS_MAC=$([[ "$(uname)" == "Darwin" ]] && echo true || echo false)

# Router / interface of the global IPv4 primary service — the exact signal
# tailscaled's network monitor reads as `defaultRoute`. Empty ⇒ macOS has no
# primary route, i.e. the exit-node-teardown blackhole (traffic dies, both
# families report "network is unreachable"). Reading SC state, not the routing
# table, dodges the stale utun / OrbStack `!` reject routes that make
# `route get default` succeed while the machine is actually offline.
sc_default_router() {
    printf 'show State:/Network/Global/IPv4\n' | scutil 2>/dev/null |
        awk '/Router/ {print $NF; exit}'
}
sc_primary_interface() {
    printf 'show State:/Network/Global/IPv4\n' | scutil 2>/dev/null |
        awk '/PrimaryInterface/ {print $NF; exit}'
}

# True when $1 (a device like en0) is the Wi-Fi interface — the only medium we
# can power-cycle without sudo (SwiftBar runs us detached, so we can't prompt).
is_wifi_device() {
    # A bare `exit` in a rule still runs END, so stash the verdict and let END
    # emit it (an `exit N` in the rule would be clobbered by END's exit).
    networksetup -listallhardwareports 2>/dev/null | awk -v dev="$1" '
        /^Hardware Port:/ {wifi = ($0 == "Hardware Port: Wi-Fi")}
        /^Device:/ && $2 == dev {found = 1; code = (wifi ? 0 : 1); exit}
        END {exit (found ? code : 1)}'
}

# Force macOS to re-elect a default route by bouncing the captured primary
# interface — the minimal equivalent of the reboot that fixes the blackhole
# (both trigger tailscaled's `DefaultRoute: ""->"enX"` rebind).
bounce_interface() {
    local ifc="$1"
    [ -n "$ifc" ] || return 1
    is_wifi_device "$ifc" || return 1
    networksetup -setairportpower "$ifc" off || return 1
    sleep 2
    networksetup -setairportpower "$ifc" on || return 1
}

notify_blackhole() {
    local ifc="${1:-unknown}"
    die "disconnect left no default route (interface: $ifc); bounce Wi-Fi or reboot"
    if command -v osascript >/dev/null 2>&1; then
        osascript -e 'display notification "No internet after disconnecting the VPN. Bounce Wi-Fi or reboot." with title "Tailscale"' >/dev/null 2>&1 || true
    fi
}

# Self-heal the exit-node-teardown blackhole: poll for a primary default route,
# and if it never returns, bounce the (pre-captured) primary interface and poll
# again. Surfaces a real failure — log line, notification, non-zero exit —
# rather than silently pretending the disconnect succeeded.
restore_default_route() {
    local primary="$1" _
    for _ in 1 2 3 4; do
        if [ -n "$(sc_default_router)" ]; then return 0; fi
        sleep 2
    done
    log "disconnect blackholed the default route; bouncing ${primary:-unknown}"
    if ! bounce_interface "$primary"; then
        notify_blackhole "$primary"
        return 1
    fi
    for _ in $(seq 1 12); do
        if [ -n "$(sc_default_router)" ]; then
            log "default route restored after bouncing $primary"
            return 0
        fi
        sleep 2
    done
    notify_blackhole "$primary"
    return 1
}

target="${1-off}"
case "$target" in
off | "") host="" ;;
*) host="$(tailscale_node_lookup "$target" | awk '{print $2}')" ||
    {
        die "invalid target '$target' (valid: off ${TAILSCALE_EXIT_NODES[*]%% *})"
        exit 2
    } ;;
esac

TAILSCALE="$(find_tailscale)" || {
    die "no working tailscale CLI"
    exit 127
}

# `tailscale set --exit-node` errors are opaque when the daemon is the real
# problem (e.g. logged out yields "invalid value ... must be IP or hostname"
# because the netmap is gone). Diagnose the daemon first.
health="$(tailscale_health "$TAILSCALE")"
case "$health" in
ok | stopped) ;;
logged-out)
    die "$target: tailscaled is logged out — run: tailscale up"
    exit 4
    ;;
eperm)
    die "$target: socket EPERM — run: sudo launchctl kickstart -k system/com.$USER.tailscaled"
    exit 4
    ;;
no-daemon)
    die "$target: tailscaled not running — run: sudo launchctl bootstrap system /Library/LaunchDaemons/com.$USER.tailscaled.plist"
    exit 4
    ;;
*)
    die "$target: tailscaled unhealthy ($health) — run: tailscale status"
    exit 4
    ;;
esac

# A skewed CLI↔daemon pair (brew upgrade without a daemon restart) is a known
# instability, but it is NOT the cause of the disconnect blackhole (that's the
# macOS default-route drop handled by restore_default_route below). Warn and
# proceed rather than refuse — refusing would only strand us on the exit node,
# and recovery has our back regardless of cause.
if ! skew="$(tailscale_version_skew "$TAILSCALE")"; then
    log "WARN $target: CLI/daemon version skew ($skew) — consider: sudo launchctl kickstart -k system/com.$USER.tailscaled"
fi

# Capture the physical primary interface *before* disconnecting — in the
# blackhole state macOS reports no primary service, so it can't be learned
# afterward. Only needed on the disconnect (off) path.
primary_if=""
if $IS_MAC && [ -z "$host" ]; then
    primary_if="$(sc_primary_interface)"
fi

args=(set "--exit-node=$host")
[ -n "$host" ] && args+=(--exit-node-allow-lan-access=true)

if out=$("$TAILSCALE" "${args[@]}" 2>&1); then
    log "${target} → ${host:-off}${out:+: $out}"
else
    rc=$?
    die "${target} → ${host:-off} rc=$rc out=$out"
    exit "$rc"
fi

# Clearing a Mullvad exit node sometimes leaves macOS with no primary default
# route (open-source tailscaled teardown bug), blackholing all traffic until an
# interface bounce forces re-election. Confirmed via tailscaled's own log:
# `link state: ...defaultRoute=` (empty) after the disconnect, restored only by
# the reboot's `DefaultRoute: ""->"enX"` rebind. Self-heal it. See CLAUDE.md.
if $IS_MAC && [ -z "$host" ]; then
    restore_default_route "$primary_if" || exit 5
fi
