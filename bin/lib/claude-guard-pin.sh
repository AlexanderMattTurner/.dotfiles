# shellcheck shell=bash
# Classify the claude-guard checkout against its pinned ref.
#
# Print one of, for $1 (path to the claude-guard checkout) and $2 (path to
# the claude-guard.ref file):
#   not-cloned    $1/.git doesn't exist (subrepo not checked out yet)
#   missing-ref   $2 doesn't exist
#   pinned        $1's HEAD matches the ref pinned in $2
#   drifted       $1's HEAD differs from the ref pinned in $2
#
# Consumers: bin/doctor.bash, tests/test_claude_guard_pin.py.
claude_guard_pin_status() {
    local cg_dir="$1" ref_file="$2"
    if [[ ! -d "$cg_dir/.git" ]]; then
        echo not-cloned
        return
    fi
    if [[ ! -f "$ref_file" ]]; then
        echo missing-ref
        return
    fi
    local pinned head
    pinned="$(<"$ref_file")"
    head="$(git -C "$cg_dir" rev-parse HEAD 2>/dev/null)"
    if [[ "$head" == "$pinned" ]]; then
        echo pinned
    else
        echo drifted
    fi
}
