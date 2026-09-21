# shellcheck shell=bash
# Free-disk-space health — single source of truth.
#
# This machine filled to 98% (11GiB free of 460GiB) before anything noticed,
# because the things that eat the disk eat it silently:
#
#   - glovebox's lima kata VMs. Five had reached 66GiB between them; one was
#     33GiB and in `Broken` state. Their host footprint is *live* content, not
#     accumulated garbage — see disk_lima_image_kib for why that distinction
#     decides what doctor is allowed to say about them.
#   - Package-manager stores (pnpm, uv, Homebrew downloads, pre-commit) keep
#     every version forever until explicitly pruned. pnpm's store alone held
#     7.4GiB, of which 5.3GiB was unreferenced.
#
# Health is measured against *free* GiB rather than percent used: percent is a
# ratio to total capacity, but what actually breaks a build, a VM boot or a
# Duplicati run is absolute headroom.
#
# Consumers that must stay in sync (see CLAUDE.md "Disk space"):
# bin/doctor.bash, tests/test_disk_space.py.

# Free space below this many GiB is worth acting on; below the critical
# threshold, things start failing outright.
#
# Deliberately not set to a kata VM's 40GiB provisioned ceiling. This machine
# routinely runs near 88% full with ~37GiB free and works fine, so a 40GiB
# threshold would be a standing FAIL during normal operation — and a doctor
# that is red when nothing is wrong trains you to stop reading it, which costs
# more than the check buys. 25GiB still fires with ample runway ahead of the
# 11GiB state that prompted this check.
DISK_LOW_GIB="${DISK_LOW_GIB:-25}"
DISK_CRITICAL_GIB="${DISK_CRITICAL_GIB:-10}"

# Volume to measure. Overridable so tests can point at a fixture path.
disk_space_target() {
    printf '%s\n' "${DISK_SPACE_TARGET:-$HOME}"
}

# Print integer GiB free on the volume holding the target.
# Exit codes let the caller tell the failure modes apart:
#   0  printed a GiB count
#   2  df unavailable, or its output could not be parsed
disk_free_gib() {
    local target out avail_k
    target="$(disk_space_target)"

    command -v df >/dev/null 2>&1 || return 2
    out="$(df -Pk "$target" 2>/dev/null)" || return 2

    # -P guarantees one record per filesystem on a single line, so the value is
    # always row 2 field 4 — without it a long device name wraps and shifts the
    # columns onto the next line.
    avail_k="$(printf '%s\n' "$out" | awk 'NR == 2 { print $4 }')"
    case "$avail_k" in '' | *[!0-9]*) return 2 ;; esac

    printf '%s\n' "$((avail_k / 1048576))"
}

# Classify free-space health. Prints one of:
#   ok:<gib>        at or above DISK_LOW_GIB — nothing to do
#   low:<gib>       below DISK_LOW_GIB — prune before it bites
#   critical:<gib>  below DISK_CRITICAL_GIB — writes are about to start failing
#   unknown         df unreadable, so headroom is unknowable
disk_space_health() {
    local gib rc=0

    gib="$(disk_free_gib)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo unknown
        return 0
    fi

    if [ "$gib" -lt "$DISK_CRITICAL_GIB" ]; then
        printf 'critical:%s\n' "$gib"
    elif [ "$gib" -lt "$DISK_LOW_GIB" ]; then
        printf 'low:%s\n' "$gib"
    else
        printf 'ok:%s\n' "$gib"
    fi
}

# Print total KiB held by lima VM instances, or nothing when there are none.
#
# Called only from the low/critical branch, and cheap even then: a lima instance
# directory holds one large image file plus a handful of logs and sockets, so
# `du` walks a few dozen entries rather than a package store's hundreds of
# thousands.
#
# Sized separately from the generic caches, and *reported without a remedy*,
# because unlike them it is not reclaimable garbage. Discard is plumbed the
# whole way down (see CLAUDE.md "Disk space"), so the image already tracks the
# guest's live usage: measured at 10.12GiB host against 9.8GiB used in-guest,
# with `fstrim` finding 0B left to return. There is therefore nothing for a
# prune to reclaim — the space is real content, and `limactl delete` is the
# only lever. That makes it a decision, not a cleanup, so doctor prints the
# number and points at `limactl list` rather than suggesting a command.
#
# Reports KiB, not GiB, so the caller owns rounding: a helper that rounded here
# could only be tested with GiB-sized fixtures, which no test should have to
# write. `_`-prefixed entries (lima's own `_config` / `_disks`) are not
# instances and are excluded.
disk_lima_image_kib() {
    local dir="${LIMA_HOME:-$HOME/.lima}"
    [ -d "$dir" ] || return 0

    local total_k=0 kb inst
    while IFS= read -r inst; do
        [ -n "$inst" ] || continue
        kb="$(du -skx "$inst" 2>/dev/null | awk '{ print $1 }')"
        case "$kb" in '' | *[!0-9]*) continue ;; esac
        total_k=$((total_k + kb))
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d -name '[!_]*' 2>/dev/null)

    [ "$total_k" -gt 0 ] || return 0
    printf '%s\n' "$total_k"
}
