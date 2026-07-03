# shellcheck shell=bash
# retry — run a command up to N times with linear backoff.
#
#   retry <attempts> <base-delay-seconds> <cmd> [args...]
#
# Sleeps attempt*base seconds between tries (10 → 10s, 20s, ...), the same
# cadence the old inline brew-bundle loop used. Returns the last exit code
# on final failure so callers can `|| status_msg WARN` and continue. Use
# for network operations (clones, installers, package managers) — see the
# "Conventions" section of CLAUDE.md.
retry() {
    local attempts="$1" base="$2"
    shift 2
    local attempt rc=0
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if "$@"; then
            return 0
        fi
        rc=$?
        if ((attempt < attempts)); then
            echo ":: retry: '$1' failed (attempt $attempt/$attempts); retrying in $((attempt * base))s..." >&2
            sleep $((attempt * base))
        fi
    done
    return "$rc"
}
