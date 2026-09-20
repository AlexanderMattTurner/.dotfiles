# shellcheck shell=bash
# doctor-checks.sh — the core assertions doctor.bash runs.
#
# Extracted from bin/doctor.bash so the "doctor contract" CLAUDE.md emphasises
# (a managed symlink must point at exactly its expected source, and a required
# command must be on PATH) has direct unit tests (tests/test_doctor_checks.py)
# instead of only being exercised through a full integration run.
#
# These call `pass` and `fail` (and check_symlink bumps MANAGED_LINK_FAIL),
# which the sourcing script owns — doctor.bash defines them as its PASS/FAIL
# counters + printers; the test defines lightweight recorders. Keeping the
# reporters in the caller is what lets the same check logic drive both.
#
# disk_space_check additionally needs bin/lib/disk-space.sh sourced first, for
# the classifier it reports on.

check_symlink() {
    local target="$1"
    local expected_source="$2"
    local label="$3"
    if [[ ! -L "$target" ]]; then
        if [[ -e "$target" ]]; then
            fail "$label" "$target exists but is not a symlink"
        else
            fail "$label" "$target missing (run setup.bash --link-only)"
        fi
        MANAGED_LINK_FAIL=$((MANAGED_LINK_FAIL + 1))
        return
    fi
    local actual
    actual="$(readlink "$target")"
    if [[ "$actual" != "$expected_source" ]]; then
        fail "$label" "$target -> $actual, expected $expected_source"
        MANAGED_LINK_FAIL=$((MANAGED_LINK_FAIL + 1))
    elif [[ ! -e "$target" ]]; then
        fail "$label" "$target -> $expected_source (dangling — source does not exist)"
        MANAGED_LINK_FAIL=$((MANAGED_LINK_FAIL + 1))
    else
        pass "$label"
    fi
}

check_command() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "$cmd"
    else
        fail "$cmd" "not on PATH"
    fi
}

# Report free-disk-space health. Every state disk_space_health can emit needs an
# arm here; see CLAUDE.md "Disk space".
#
# The remedies are spelled out rather than hidden behind a wrapper: each is the
# tool's own prune, so they stay correct as those tools change.
#
# Continuation lines carry their own 7-space indent because fail() indents only
# the first line of its detail argument -- without it the block lands flush-left
# against the report's detail column.
_disk_remedies() {
    printf '%s\n' \
        "       reclaim: brew cleanup --prune=all; pnpm store prune;" \
        "                uv cache prune; pre-commit gc; limactl prune; glovebox gc" \
        "       review VM images (not prunable) with: limactl list"
}

# Sized only on the unhealthy branches: du walks every lima instance, which is
# wasted work on the overwhelmingly common ok path. Suppressed under a whole
# GiB, where a rounded-to-zero figure would be noise rather than a lead.
_disk_lima_note() {
    local kib gib
    kib="$(disk_lima_image_kib)"
    [ -n "$kib" ] || return 0
    gib="$(disk_kib_to_gib "$kib")" || return 0
    [ "$gib" -ge 1 ] || return 0
    printf ' — lima VM images hold %sGiB' "$gib"
}

disk_space_check() {
    local state free
    state="$(disk_space_health)"
    free="${state#*:}"

    case "$state" in
    ok:*)
        pass "free space (${free}GiB)"
        ;;
    low:*)
        fail "free space" "$(printf '%sGiB free, under %sGiB%s\n%s' \
            "$free" "$DISK_LOW_GIB" "$(_disk_lima_note)" "$(_disk_remedies)")"
        ;;
    critical:*)
        fail "free space" "$(printf '%sGiB free, under %sGiB — writes will start failing%s\n%s' \
            "$free" "$DISK_CRITICAL_GIB" "$(_disk_lima_note)" "$(_disk_remedies)")"
        ;;
    unknown)
        skip "free space" "df unavailable or unparseable"
        ;;
    *)
        fail "free space" "unhandled disk_space_health state: $state"
        ;;
    esac
}
