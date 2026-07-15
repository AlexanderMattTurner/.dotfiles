# shellcheck shell=bash
# The claude-guard subrepo contract: canonical clone URL, pin classification,
# and origin-URL classification/repair. The checkout is a managed artifact
# (gitignored, pinned via claude-guard.ref), so its origin remote is owned by
# this machinery, not hand-edited.
#
# The canonical URL matters beyond cloning: glovebox derives its cosign
# signer-identity pin and its GitHub App token scope from the checkout's
# origin owner/repo, so an origin left at a superseded URL (GitHub redirects
# make plain pulls keep working) silently breaks image verification and
# token minting. clone-claude-guard.bash repoints stale origins on every
# setup run; doctor.bash FAILs on them.
CLAUDE_GUARD_CANONICAL_URL="https://github.com/AlexanderMattTurner/agent-glovebox.git"

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

# Classify the claude-guard checkout's origin remote.
#
# Print one of, for $1 (path to the claude-guard checkout) and optional $2
# (canonical URL, defaulting to CLAUDE_GUARD_CANONICAL_URL — injectable so
# tests never touch the network):
#   not-cloned    $1/.git doesn't exist (subrepo not checked out yet)
#   no-origin     no 'origin' remote configured
#   canonical     origin matches the canonical URL
#   stale         origin points anywhere else
#
# Consumers: bin/clone-claude-guard.bash, bin/doctor.bash,
# tests/test_claude_guard_pin.py.
claude_guard_origin_status() {
    local cg_dir="$1" canonical="${2:-$CLAUDE_GUARD_CANONICAL_URL}" url
    if [[ ! -d "$cg_dir/.git" ]]; then
        echo not-cloned
        return
    fi
    if ! url="$(git -C "$cg_dir" remote get-url origin 2>/dev/null)"; then
        echo no-origin
        return
    fi
    if [[ "$url" == "$canonical" ]]; then
        echo canonical
    else
        echo stale
    fi
}

# Make the checkout's origin point at the canonical URL, whether origin
# currently exists (set-url) or not (add). Same optional-URL injection as
# claude_guard_origin_status.
claude_guard_repoint_origin() {
    local cg_dir="$1" canonical="${2:-$CLAUDE_GUARD_CANONICAL_URL}"
    if git -C "$cg_dir" remote get-url origin >/dev/null 2>&1; then
        git -C "$cg_dir" remote set-url origin "$canonical"
    else
        git -C "$cg_dir" remote add origin "$canonical"
    fi
}
