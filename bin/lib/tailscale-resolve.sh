# shellcheck shell=bash
# Tailscale.app CLI health and egress — single source of truth.
#
# Tailscale.app (cask `tailscale-app`) is both the tailnet and the egress VPN:
# egress is a Mullvad exit node from Tailscale's Mullvad add-on. See CLAUDE.md
# "VPN". The app bundles its CLI off PATH; `TAILSCALE_CLI` is a test seam.
TAILSCALE_CLI="${TAILSCALE_CLI:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"

# Print the path to the app's CLI; non-zero if Tailscale.app is absent.
find_tailscale() {
    [ -x "$TAILSCALE_CLI" ] || return 1
    printf '%s\n' "$TAILSCALE_CLI"
}

# Classify CLI↔app health for $1 (path to a tailscale CLI). Prints one of:
#   ok          connected, logged in
#   stopped     logged in but administratively down (`tailscale down`)
#   no-daemon   the app's backend is not running
#   eperm       CLI denied access to the backend
#   logged-out  backend up but node key gone/expired — needs `tailscale up`
#   error       any other non-zero `tailscale status`
#
# Consumers that must stay in sync: bin/doctor.bash,
# tests/test_tailscale_health.py.
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

# Classify the engaged exit node for $1 (CLI path). Prints one of:
#   mullvad:<node>  the engaged exit node is a Mullvad relay (*.mullvad.ts.net)
#   other:<node>    an exit node is engaged, but it is not a Mullvad relay
#   none            no exit node is engaged, so traffic leaves in the clear
#   unreachable     `status --json` failed, so the state is unknown
#
# Reads the peer whose "ExitNode" is true in pretty-printed `status --json`.
# Within each peer object "DNSName" precedes "ExitNode", so awk remembers the
# last DNSName. Output is captured first so the CLI never sees a closed pipe.
#
# Consumers that must stay in sync: setup.bash, bin/doctor.bash,
# tests/test_tailscale_health.py.
tailscale_exit_node() {
    local out node
    out="$("$1" status --json 2>/dev/null)" || {
        echo unreachable
        return
    }
    node="$(awk '
        $1 == "\"DNSName\":" {name = $2; gsub(/[",]/, "", name); sub(/\.$/, "", name)}
        $1 == "\"ExitNode\":" && $2 ~ /^true/ && !found {found = 1; print name}
    ' <<<"$out")"
    case "$node" in
    "") echo none ;;
    *.mullvad.ts.net) echo "mullvad:$node" ;;
    *) echo "other:$node" ;;
    esac
}
