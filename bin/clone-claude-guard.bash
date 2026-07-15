#!/bin/bash
# clone-claude-guard.bash — clone/update the claude-guard checkout at the
# commit pinned in claude-guard.ref.
#
# claude-guard carries the AI-safety monitor, hooks, and Claude Code
# config, so it is pinned like any other security-critical dependency
# instead of floating at origin/main (a compromised or regressed upstream
# HEAD would otherwise flow straight into the monitor on the next
# setup.bash run). Used by setup.bash and the lint/idempotency workflows;
# doctor.bash fails when the checkout drifts from the pin.
#
# Bump the pin:
#   bash bin/clone-claude-guard.bash --bump   # writes origin/main HEAD to
#                                             # claude-guard.ref
# then commit the ref change via PR like any dependency bump.
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_GUARD_DIR="$DOTFILES_DIR/claude-guard"
CLAUDE_GUARD_URL="https://github.com/AlexanderMattTurner/agent-glovebox.git"
REF_FILE="$DOTFILES_DIR/claude-guard.ref"

# shellcheck source=lib/retry.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/retry.sh"

if [[ ! -d "$CLAUDE_GUARD_DIR/.git" ]]; then
    # An interrupted first clone can leave a non-git directory that makes
    # `git clone` fail on every subsequent run. It's a gitignored clone
    # with no local state, so clearing the leftover is safe.
    if [[ -e "$CLAUDE_GUARD_DIR" ]]; then
        echo ":: Removing leftover non-git claude-guard/ from an interrupted clone..."
        rm -rf "$CLAUDE_GUARD_DIR"
    fi
    retry 3 5 git clone --quiet "$CLAUDE_GUARD_URL" "$CLAUDE_GUARD_DIR"
else
    git -C "$CLAUDE_GUARD_DIR" fetch --quiet origin ||
        echo ":: WARN: claude-guard fetch failed (network?); using existing objects." >&2
fi

if [[ "${1:-}" == "--bump" ]]; then
    git -C "$CLAUDE_GUARD_DIR" rev-parse origin/main >"$REF_FILE"
    echo ":: claude-guard.ref bumped to $(cat "$REF_FILE") — commit it via PR."
fi

ref="$(cat "$REF_FILE")"
current="$(git -C "$CLAUDE_GUARD_DIR" rev-parse HEAD)"
if [[ "$current" == "$ref" ]]; then
    exit 0
fi
if [[ -n "$(git -C "$CLAUDE_GUARD_DIR" status --porcelain)" ]]; then
    echo ":: WARN: claude-guard has local changes; leaving HEAD at ${current:0:7} (pinned: ${ref:0:7})." >&2
    exit 0
fi
git -C "$CLAUDE_GUARD_DIR" checkout --quiet --detach "$ref"
echo ":: claude-guard checked out at pinned ${ref:0:7}"
