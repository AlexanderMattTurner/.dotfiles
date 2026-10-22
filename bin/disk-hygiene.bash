#!/bin/bash
# bin/disk-hygiene.bash — scheduled disk cleanup: package-manager store prunes
# plus a reaper for stale AI-agent git worktrees.
#
# Why this exists: on 2026-10-04 the Mac's data volume hit 99% full (6.5GiB
# free of 460GiB) and glovebox launches failed with ENOSPC. Hand cleanup that
# day recovered ~8GiB from `pnpm store prune` (~2GiB), `limactl prune`
# (~2GiB), and stale git worktrees left by AI agent sessions under
# /tmp/claude-worktrees/* and agent-glovebox/.claude/worktrees/* (each
# holding ~0.9GiB of .venv/node_modules). See CLAUDE.md "Disk space" for why
# the owner decided to move this from doctor naming prunes to a scheduled job
# running them.
#
# This NEVER touches a lima instance or runs `limactl delete` — lima VM
# images are live content, not garbage (CLAUDE.md "Disk space"). `glovebox gc
# --schedule install` already prunes glovebox's own state separately; this
# script does not duplicate it.
#
# Usage:
#   bash bin/disk-hygiene.bash [--dry-run]

set -euo pipefail

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -h | --help)
        cat <<'EOF'
usage: disk-hygiene.bash [--dry-run]

Prune package-manager stores, reap stale AI-agent worktrees, and warn on low
disk space. Never touches lima/glovebox VMs.
EOF
        exit 0
        ;;
    *)
        printf 'disk-hygiene.bash: unknown argument %q\n' "$arg" >&2
        exit 2
        ;;
    esac
done

# REPO_DIR always resolves to this script's own checkout, for lib sourcing.
# DOTFILES_DIR/GLOVEBOX_DIR are overridable so tests can point the reaper at
# fixture repos instead of this machine's real dotfiles/glovebox checkouts.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOTFILES_DIR="${DISK_HYGIENE_DOTFILES_DIR:-$REPO_DIR}"
GLOVEBOX_DIR="${DISK_HYGIENE_GLOVEBOX_DIR:-$DOTFILES_DIR/agent-glovebox}"

# shellcheck source=lib/disk-space.sh disable=SC1091
source "$REPO_DIR/bin/lib/disk-space.sh"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

log() {
    printf ':: disk-hygiene: %s\n' "$1"
}

# Each prune gets a bounded timeout and a failure here is logged, not fatal —
# one slow or broken tool must not stop the rest of the sweep.
PRUNE_TIMEOUT="${DISK_HYGIENE_PRUNE_TIMEOUT:-300}"

run_prune() {
    local name="$1"
    shift
    if $DRY_RUN; then
        log "would run: $name ($*)"
        return 0
    fi
    log "running $name..."
    local rc=0
    timeout "$PRUNE_TIMEOUT" "$@" >/dev/null 2>&1 || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        log "$name: ok"
    else
        log "$name: FAILED (exit $rc) — continuing"
    fi
}

if command_exists uv; then
    run_prune "uv cache prune" uv cache prune
fi
if command_exists pnpm; then
    run_prune "pnpm store prune" pnpm store prune
fi
if command_exists brew; then
    run_prune "brew cleanup" brew cleanup --prune=all
fi
if command_exists pre-commit; then
    run_prune "pre-commit gc" pre-commit gc
fi
if command_exists limactl; then
    # limactl prune reclaims unreferenced cache/template data only — it never
    # deletes an instance, so this does not touch lima VMs. See CLAUDE.md
    # "Disk space": never limactl delete, never prune a running instance away.
    run_prune "limactl prune" limactl prune
fi

# ── Stale AI-agent worktree reaper ──────────────────────────────────────────
#
# AI agent sessions (Claude Code, glovebox) create git worktrees under
# /tmp/claude-worktrees/* and under <repo>/.claude/worktrees/* for
# dotfiles and agent-glovebox. A session that ends without cleaning up
# leaves one behind forever — each holds a full .venv/node_modules checkout,
# so a handful silently eats several GiB. A worktree is removed only when it
# is safe to lose: unlocked, no uncommitted or untracked changes, its HEAD is
# already on some remote branch, and it has sat untouched for a week.
STALE_DAYS="${DISK_HYGIENE_STALE_DAYS:-7}"
STALE_SECS=$((STALE_DAYS * 86400))
NOW="$(date +%s)"

mtime_of() {
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f %m "$1" 2>/dev/null
    else
        stat -c %Y "$1" 2>/dev/null
    fi
}

# Remove $2 (a worktree path) from the repo at $1 if every safety condition
# holds. Logs the outcome either way.
reap_worktree() {
    local repo="$1" wt="$2" locked="$3"
    local label="$wt"

    if [[ "$locked" == "locked" ]]; then
        log "skip $label: locked"
        return 0
    fi

    if [[ ! -d "$wt" ]]; then
        log "skip $label: directory already gone"
        return 0
    fi

    local mtime age
    mtime="$(mtime_of "$wt")"
    if [[ -z "$mtime" ]]; then
        log "skip $label: cannot stat mtime"
        return 0
    fi
    age=$((NOW - mtime))
    if ((age < STALE_SECS)); then
        log "skip $label: modified $((age / 86400))d ago, under ${STALE_DAYS}d"
        return 0
    fi

    local status
    status="$(git -C "$wt" status --porcelain 2>/dev/null)" || {
        log "skip $label: git status failed"
        return 0
    }
    if [[ -n "$status" ]]; then
        log "skip $label: uncommitted or untracked changes"
        return 0
    fi

    local head
    head="$(git -C "$wt" rev-parse HEAD 2>/dev/null)" || {
        log "skip $label: cannot resolve HEAD"
        return 0
    }
    local remote_branches
    remote_branches="$(git -C "$repo" branch -r --contains "$head" 2>/dev/null)"
    if [[ -z "$remote_branches" ]]; then
        log "skip $label: HEAD not contained in any remote-tracking branch"
        return 0
    fi

    if $DRY_RUN; then
        log "would remove $label"
        return 0
    fi

    if git -C "$repo" worktree remove "$wt" 2>/dev/null; then
        log "removed $label"
    else
        log "skip $label: git worktree remove failed"
    fi
}

# Parse `git worktree list --porcelain` into path|locked pairs, one per
# worktree (skipping the first, which is the repo's primary checkout).
reap_repo_worktrees() {
    local repo="$1" filter="$2"
    [[ -d "$repo/.git" || -f "$repo/.git" ]] || return 0

    local path="" locked=""
    while IFS= read -r line; do
        case "$line" in
        worktree\ *)
            if [[ -n "$path" && "$path" == *"$filter"* ]]; then
                reap_worktree "$repo" "$path" "$locked"
            fi
            path="${line#worktree }"
            locked=""
            ;;
        locked*) locked="locked" ;;
        esac
    done < <(git -C "$repo" worktree list --porcelain 2>/dev/null)
    if [[ -n "$path" ]] && [[ "$path" == *"$filter"* ]]; then
        reap_worktree "$repo" "$path" "$locked"
    fi
}

# /tmp/claude-worktrees/* belongs to whichever repo each worktree was added
# from, so git-worktree-list must be read per owning repo. Both repos this
# machine uses AI-agent worktrees in are checked explicitly.
reap_repo_worktrees "$DOTFILES_DIR" "/tmp/claude-worktrees/"
reap_repo_worktrees "$DOTFILES_DIR" "/.claude/worktrees/"
if [[ -d "$GLOVEBOX_DIR" ]]; then
    reap_repo_worktrees "$GLOVEBOX_DIR" "/tmp/claude-worktrees/"
    reap_repo_worktrees "$GLOVEBOX_DIR" "/.claude/worktrees/"
fi

if $DRY_RUN; then
    log "dry run: skipping git worktree prune"
else
    git -C "$DOTFILES_DIR" worktree prune 2>/dev/null || true
    if [[ -d "$GLOVEBOX_DIR" ]]; then
        git -C "$GLOVEBOX_DIR" worktree prune 2>/dev/null || true
    fi
fi

# ── Low-disk notification ───────────────────────────────────────────────────
DISK_STATE="$(disk_space_health)"
case "$DISK_STATE" in
low:* | critical:*)
    FREE_GIB="${DISK_STATE#*:}"
    log "$DISK_STATE free — notifying"
    if command_exists osascript && ! $DRY_RUN; then
        osascript -e "display notification \"${FREE_GIB}GiB free — run: dotfiles doctor\" with title \"Low disk space\"" 2>/dev/null || true
    fi
    ;;
esac

log "done"
