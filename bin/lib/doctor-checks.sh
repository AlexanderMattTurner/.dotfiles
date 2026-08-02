# shellcheck shell=bash
# doctor-checks.sh — the core symlink/command assertions doctor.bash runs.
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
