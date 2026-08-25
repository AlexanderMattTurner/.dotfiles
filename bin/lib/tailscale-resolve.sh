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

# --- Exit-node teardown: the DNS half ---------------------------------------
#
# Clearing a Mullvad exit node breaks DNS, not routing. While the exit node is
# engaged, tailscaled points *itself* at Mullvad's resolver
# (`dns: Set: {DefaultResolvers:[194.242.2.2] ...}`) and points macOS at
# tailscaled (`/etc/resolv.conf` + `State:/Network/Global/DNS` →
# 100.100.100.100). 194.242.2.2 is reachable *only through the tunnel*, so when
# the tunnel goes and that pref stays, every lookup dies at a resolver with no
# path to it — while the physical default route stays perfectly healthy. Hence
# `sc_default_router` never fired (17 disconnects, 0 recoveries in menu.log),
# a Wi-Fi bounce can't fix it (tailscaled just re-Sets the same DNS), and only
# a reboot did. See CLAUDE.md "Tailscale daemon".
#
# Consumers that must stay in sync: bin/tailscale-set-exit-node.bash,
# bin/doctor.bash, tests/test_tailscale_health.py.

# Names proving the resolver path is alive. Any one answering suffices —
# requiring all would false-alarm on a single upstream outage.
TAILSCALE_DNS_PROBES="${TAILSCALE_DNS_PROBES:-controlplane.tailscale.com one.one.one.one}"

# PATH first (so tests can stub it), then the absolute path — SwiftBar runs us
# detached with a minimal environment. The fallback is a variable so a test can
# point it at nothing and exercise the no-probe-tool path on a machine that
# does ship /usr/bin/dig (i.e. every Mac).
TAILSCALE_DIG_FALLBACK="${TAILSCALE_DIG_FALLBACK:-/usr/bin/dig}"

_tailscale_dig() {
    command -v dig 2>/dev/null && return 0
    [ -x "$TAILSCALE_DIG_FALLBACK" ] && printf '%s\n' "$TAILSCALE_DIG_FALLBACK" && return 0
    return 1
}

# Non-zero when no probe tool exists. doctor.bash needs this separately from
# tailscale_dns_healthy: that one deliberately answers "healthy" when it cannot
# probe (see below), and a check that never ran must `skip`, not `pass`.
tailscale_dns_probe_available() {
    _tailscale_dig >/dev/null
}

# Resolve through the system's *configured* resolvers (/etc/resolv.conf, which
# tailscaled rewrites to 100.100.100.100) rather than a hardcoded server: that
# rewritten path is precisely the one that breaks, so it is the one to probe.
# Non-zero when nothing answers.
#
# `dig +short` exits 0 on SERVFAIL with empty output, so an empty answer — not
# exit status — is the signal.
tailscale_dns_healthy() {
    local dig name answer
    local probes=()
    dig="$(_tailscale_dig)" || return 0 # no dig ⇒ can't judge; never false-alarm
    # read -ra, not an unquoted expansion: shellharden would quote the latter
    # and silently collapse the list to one bogus name.
    read -ra probes <<<"$TAILSCALE_DNS_PROBES"
    for name in "${probes[@]}"; do
        answer="$("$dig" +short +time=2 +tries=1 "$name" A 2>/dev/null)" || answer=""
        [ -n "$answer" ] && return 0
    done
    return 1
}

# True as soon as DNS answers within $1 attempts. `tailscale set` returns when
# the daemon *accepts* the pref, not when its DNS manager has re-applied, so a
# single probe at t=0 reads the pre-teardown config — the same trap
# route_stable_for exists to avoid.
#
# An attempt is NOT one second: the 1s pace is on top of dig's own wait, which
# is what dominates when the resolver is unreachable rather than merely
# answering SERVFAIL (up to +time per probe name). Budget accordingly — this
# runs on the disconnect path while the user has no working DNS.
tailscale_dns_recovers_within() {
    local attempts="$1" i
    for ((i = 0; i < attempts; i++)); do
        [ "$i" -eq 0 ] || sleep 1 # probe first, then pace — no trailing sleep
        tailscale_dns_healthy && return 0
    done
    return 1
}

# Force tailscaled's DNS manager through a teardown + re-apply for $1 (CLI
# path). With the exit node already cleared, the re-apply derives resolvers
# from the netmap alone and so drops the stale Mullvad DefaultResolvers.
# Toggling --accept-dns is the only lever that does this without sudo, which is
# required: SwiftBar runs the applier detached and cannot prompt. Non-zero if
# tailscaled isn't managing DNS (nothing to re-apply) or a toggle fails.
tailscale_dns_reapply() {
    local ts="$1" corp
    # awk reads to EOF rather than `exit`ing on the match: an early exit closes
    # the pipe under the caller's `set -o pipefail` and reports a SIGPIPE'd
    # writer as failure. See CLAUDE.md and .claude/rules/shell-style.md.
    corp="$("$ts" debug prefs 2>/dev/null |
        awk -F'[:,]' '/"CorpDNS"/ && !seen {gsub(/[ \t]/, "", $2); v = $2; seen = 1}
                      END {print v}')"
    [ "$corp" = true ] || return 1
    "$ts" set --accept-dns=false >/dev/null 2>&1 || return 1
    sleep 1
    "$ts" set --accept-dns=true >/dev/null 2>&1 || return 1
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
