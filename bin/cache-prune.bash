#!/usr/bin/env bash
# bin/cache-prune.bash — prune package-manager stores of entries nothing uses.
#
# pnpm, uv, Homebrew and pre-commit keep every version they ever fetched until
# told to prune (see CLAUDE.md "Disk space"). This runs each tool's own prune,
# so it stays correct as the tools change. A weekly LaunchAgent
# (launchagents/com.turntrout.cache-prune.plist) runs it.
#
# A missing tool is skipped. A failed prune is reported, and the others still
# run; the exit status is non-zero if any prune failed.

set -euo pipefail

# launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, so the tools'
# directories go first. CACHE_PRUNE_PATH is a test seam.
PATH="${CACHE_PRUNE_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin}:$PATH"

failed=0
prune() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "skip: $1 not installed"
        return 0
    }
    echo "run: $*"
    "$@" || {
        echo "FAIL: $* exited $?"
        failed=1
    }
}

prune pnpm store prune
prune uv cache prune
prune brew cleanup --prune=all
prune pre-commit gc
exit "$failed"
