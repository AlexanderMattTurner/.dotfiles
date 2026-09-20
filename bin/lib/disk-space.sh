# shellcheck shell=bash
# Free-disk-space health — single source of truth.
#
# Why free GiB rather than percent used, why the thresholds are what they are,
# and why a lima image is not prunable: see CLAUDE.md "Disk space".
#
# Consumers that must stay in sync: bin/lib/doctor-checks.sh (disk_space_check),
# tests/test_disk_space.py.

# Overridable so tests can drive each state without a full disk.
DISK_LOW_GIB="${DISK_LOW_GIB:-25}"
DISK_CRITICAL_GIB="${DISK_CRITICAL_GIB:-10}"

# Rounding lives here rather than in the consumer, so no caller needs to know
# what unit the readings are in. Truncates: 1.5GiB reads as 1.
#
# The digits-only guard is not decorative. An unvalidated argument inside
# `$(( ))` is both a `set -u` crash when absent (doctor runs `set -u`, and this
# is sourced into its global namespace) and arbitrary command execution when
# present — bash evaluates array subscripts recursively, so `a[$(cmd)]` runs
# `cmd`. The input here is `df`/`du` output, i.e. external. Validating once here
# means no caller has to be trusted to have done it.
disk_kib_to_gib() {
    local kib="${1:-}"
    case "$kib" in '' | *[!0-9]*) return 2 ;; esac
    printf '%s\n' "$((kib / 1048576))"
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
# Not reclaimable garbage — see CLAUDE.md "Disk space" for why doctor reports
# this number without offering a prune.
#
# Facts about this code rather than about VMs:
#   - Reports KiB, not GiB, so it stays testable with kilobyte fixtures instead
#     of requiring gigabyte-sized ones. The caller owns rounding.
#   - `_`-prefixed entries (lima's own `_config` / `_disks`) are not instances.
#   - Sums images wherever LIMA_HOME points, which a relocated LIMA_HOME could
#     place on a different volume from the one disk_free_gib measured.
#   - A missing `du` or an unreadable instance yields no output, so the note is
#     silently omitted. That is deliberate and the opposite of disk_free_gib's
#     `unknown`-never-`ok` rule: this figure is advisory context, not the
#     verdict, so failing to size it must not change the verdict.
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
