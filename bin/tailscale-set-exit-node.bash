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
#
# The awk side must consume scutil's output to the end rather than `exit`-ing on
# the first match. Under `set -o pipefail` an early awk exit closes the pipe
# while scutil still has lines buffered, killing it with SIGPIPE (141) — which
# pipefail reports as a failed pipeline. `sc_primary_interface`'s result is
# assigned directly (`primary_if="$(sc_primary_interface)"`), so `set -e` would
# then abort the disconnect mid-run, stranding the machine on a torn-down exit
# node. It is timing-dependent — invisible whenever scutil finishes writing
# before awk exits, which is why it has not bitten yet. Keeping the first match
# and printing it in END is the same result without the early close.
sc_default_router() {
    printf 'show State:/Network/Global/IPv4\n' | scutil 2>/dev/null |
        awk '/Router/ && !seen {value = $NF; seen = 1} END {if (seen) print value}'
}
sc_primary_interface() {
    printf 'show State:/Network/Global/IPv4\n' | scutil 2>/dev/null |
        awk '/PrimaryInterface/ && !seen {value = $NF; seen = 1} END {if (seen) print value}'
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
    notify "No internet after disconnecting the VPN. Bounce Wi-Fi or reboot."
}

# Message goes in as argv, never interpolated into the AppleScript source.
notify() {
    if command -v osascript >/dev/null 2>&1; then
        osascript \
            -e 'on run argv' \
            -e 'display notification (item 1 of argv) with title "Tailscale"' \
            -e 'end run' \
            -- "$1" >/dev/null 2>&1 || true
    fi
}

# True when sc_default_router is non-empty on $1 consecutive 1s samples; a
# single empty sample fails immediately.
#
# One sample is not enough, and sampling at t=0 is actively wrong: `tailscale
# set` returns once the daemon has *accepted* the pref change, not once it has
# rebuilt the routing table, so the pre-teardown route is still in SC state for
# a second or two afterwards. A t=0 probe therefore reads the route that is
# about to be torn down, declares the disconnect clean, and returns before the
# drop it exists to catch. That is the false-clean `off → off` in menu.log at
# 2026-08-09T21:34Z — no bounce, no warning, and no internet until a reboot.
route_stable_for() {
    local samples="$1" _
    for _ in $(seq 1 "$samples"); do
        [ -n "$(sc_default_router)" ] || return 1
        sleep 1
    done
    return 0
}

# Self-heal the exit-node-teardown blackhole: require a route that stays up
# across the teardown window, and if it drops, give tailscaled a chance to
# re-elect before bouncing the (pre-captured) primary interface. Surfaces a
# real failure — log line, notification, non-zero exit — rather than silently
# pretending the disconnect succeeded.
restore_default_route() {
    local primary="$1" _
    # Span the teardown window before believing the route survived it.
    route_stable_for 6 && return 0
    # It dropped. tailscaled sometimes re-elects on its own; wait before
    # reaching for the bounce, which costs the user their Wi-Fi link.
    for _ in 1 2 3 4; do
        if route_stable_for 3; then
            log "default route returned on its own after teardown"
            return 0
        fi
        sleep 2
    done
    log "disconnect blackholed the default route; bouncing ${primary:-unknown}"
    if ! bounce_interface "$primary"; then
        notify_blackhole "$primary"
        return 1
    fi
    for _ in $(seq 1 12); do
        if route_stable_for 3; then
            log "default route restored after bouncing $primary"
            return 0
        fi
        sleep 2
    done
    notify_blackhole "$primary"
    return 1
}

# Self-heal the *other* half of the teardown, and the one that actually bites:
# tailscaled leaves DNS pointed at Mullvad's resolver, which is reachable only
# through the tunnel it just tore down. The default route stays healthy
# throughout, which is why restore_default_route above never catches this.
# See CLAUDE.md "Tailscale daemon" and tailscale_dns_healthy in the lib.
restore_dns() {
    # Same grace the route check gets: the DNS manager re-applies a beat after
    # `tailscale set` returns, so an immediate probe reads the stale config.
    tailscale_dns_recovers_within 8 && return 0
    log "disconnect left DNS on an unreachable resolver; re-applying"
    if ! tailscale_dns_reapply "$TAILSCALE"; then
        die "could not re-apply DNS (accept-dns toggle failed)"
        notify "DNS is still pointed at the VPN resolver. Run: tailscale set --accept-dns=false; tailscale set --accept-dns=true"
        return 1
    fi
    if tailscale_dns_recovers_within 15; then
        log "DNS restored by re-applying accept-dns"
        return 0
    fi
    die "DNS still dead after re-applying accept-dns"
    notify "DNS is still pointed at the VPN resolver after disconnecting. Reboot, or run: tailscale set --accept-dns=false; tailscale set --accept-dns=true"
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

# Clearing a Mullvad exit node can blackhole traffic two independent ways, so
# verify both before calling the disconnect clean. DNS is the one that actually
# fires in practice; the route drop is kept because it is cheap to check and
# was observed at least once. Run both even if the first fails — reporting only
# half a broken teardown is what sent us chasing routes for months. See
# CLAUDE.md "Tailscale daemon".
if $IS_MAC && [ -z "$host" ]; then
    teardown_rc=0
    restore_default_route "$primary_if" || teardown_rc=5
    restore_dns || teardown_rc=6
    [ "$teardown_rc" -eq 0 ] || exit "$teardown_rc"
fi
