# shellcheck shell=bash
# Free-disk-space health — single source of truth.
#
# This machine filled to 98% (11GiB free of 460GiB) before anything noticed,
# because the things that eat the disk eat it silently: package-manager stores
# (pnpm, uv, Homebrew downloads, pre-commit) keep every version until pruned,
# and glovebox's lima kata VMs are tens of GiB apiece.
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
# routinely runs near 88% full and works fine, so a 40GiB threshold would be a
# standing FAIL during normal operation — and a doctor that is red when nothing
# is wrong trains you to stop reading it, which costs more than the check buys.
DISK_LOW_GIB="${DISK_LOW_GIB:-25}"
DISK_CRITICAL_GIB="${DISK_CRITICAL_GIB:-10}"

# Rounding lives here rather than in the consumer, so no caller needs to know
# what unit the readings are in.
disk_kib_to_gib() {
    printf '%s\n' "$(($1 / 1048576))"
}

# Print integer GiB free on the volume holding DISK_SPACE_TARGET (default $HOME;
# overridable so tests can point at a fixture).
# Exit codes let the caller tell the failure modes apart:
#   0  printed a GiB count
#   2  df unavailable, or its output could not be parsed
disk_free_gib() {
    local out avail_k

    command -v df >/dev/null 2>&1 || return 2
    out="$(df -Pk "${DISK_SPACE_TARGET:-$HOME}" 2>/dev/null)" || return 2

    # -P guarantees one record per filesystem on a single line, so the value is
    # always row 2 field 4 — without it a long device name wraps and shifts the
    # columns onto the next line.
    avail_k="$(printf '%s\n' "$out" | awk 'NR == 2 { print $4 }')"
    case "$avail_k" in '' | *[!0-9]*) return 2 ;; esac

    disk_kib_to_gib "$avail_k"
}

# Classify free-space health. Prints one of:
#   ok:<gib>        at or above DISK_LOW_GIB — nothing to do
#   low:<gib>       below DISK_LOW_GIB — prune before it bites
#   critical:<gib>  below DISK_CRITICAL_GIB — writes are about to start failing
#   unknown         df unreadable, so headroom is unknowable
#
# `unknown` rather than `ok` on an unreadable df is the whole point: this check
# exists to catch a disk that filled, so it must never invent headroom it did
# not measure.
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
# Sized separately from the prunable caches, and reported *without* a remedy,
# because unlike them it is not reclaimable garbage: discard is plumbed the
# whole way down (see CLAUDE.md "Disk space"), so an image already tracks the
# guest's live usage and a prune has nothing to reclaim. `limactl delete` is the
# only lever, which makes it a decision rather than a cleanup — so doctor prints
# the number and points at `limactl list` instead of suggesting a command.
#
# Reports KiB, not GiB, so this stays testable with kilobyte fixtures instead of
# requiring gigabyte-sized ones. `_`-prefixed entries (lima's own `_config` /
# `_disks`) are not instances and are excluded.
#
# Caveat: this sums images wherever LIMA_HOME points, which a relocated
# LIMA_HOME could place on a different volume from the one measured above.
disk_lima_image_kib() {
    local dir="${LIMA_HOME:-$HOME/.lima}"
    [ -d "$dir" ] || return 0

    local total_k=0 kb inst
    while IFS= read -r inst; do
        kb="$(du -skx "$inst" 2>/dev/null | awk '{ print $1 }')"
        case "$kb" in '' | *[!0-9]*) continue ;; esac
        total_k=$((total_k + kb))
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d -name '[!_]*' 2>/dev/null)

    [ "$total_k" -gt 0 ] || return 0
    printf '%s\n' "$total_k"
}
