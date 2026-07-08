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

# A skewed CLI↔daemon pair (brew upgrade without a daemon restart) has
# mishandled exit-node teardown — clearing the exit node blackholes all
# traffic. Refuse rather than gamble with connectivity.
if ! skew="$(tailscale_version_skew "$TAILSCALE")"; then
    die "$target: CLI/daemon version skew ($skew) — run: sudo launchctl kickstart -k system/com.$USER.tailscaled"
    exit 4
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
