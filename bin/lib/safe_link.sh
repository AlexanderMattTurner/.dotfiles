# shellcheck shell=bash
# Defines safe_link(). Source this file, or run directly:
#   bash safe_link.sh <source> <target>
#
# Real files that are about to be overwritten are first moved to
# ~/.dotfiles-backup/<UTC-timestamp>/ — preserving directory structure under
# $HOME — so a misclick on the y/N prompt is recoverable.
SAFE_LINK_BACKUP_ROOT="${SAFE_LINK_BACKUP_ROOT:-$HOME/.dotfiles-backup}"

_safe_link_backup() {
    local target_file="$1"
    # One timestamp per shell session so all backups from a single setup.bash run
    # land in the same directory. uninstall.bash's "restore from latest" then finds
    # every file that was backed up together, not just the last one.
    if [[ -z "${SAFE_LINK_BACKUP_STAMP:-}" ]]; then
        SAFE_LINK_BACKUP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
        export SAFE_LINK_BACKUP_STAMP
    fi
    local rel
    if [[ "$target_file" == "$HOME"/* ]]; then
        rel="${target_file#"$HOME"/}"
    else
        rel="${target_file#/}"
    fi
    local backup_path="$SAFE_LINK_BACKUP_ROOT/$SAFE_LINK_BACKUP_STAMP/$rel"
    mkdir -p "$(dirname "$backup_path")"
    mv "$target_file" "$backup_path"
    echo "  backed up to $backup_path"
}

safe_link() {
    local source_file="$1"
    local target_file="$2"
    # Already correct symlink — skip
    if [ -L "$target_file" ] && [ "$(readlink "$target_file")" = "$source_file" ]; then
        return
    fi
    # Target exists and is a real file (not a symlink) — prompt before clobbering
    if [ -e "$target_file" ] && [ ! -L "$target_file" ]; then
        # ~-collapsed so same-basename targets stay distinguishable
        # (apps/ssh/config vs apps/mods/config).
        local display="${target_file/#$HOME/\~}"
        local src_display="${source_file/#$HOME/\~}"
        # Open /dev/tty for the prompt: callers run safe_link inside a
        # `while ... done < <(...)` loop, so fd 0 is a pipe in interactive
        # shells too. The open fails ENXIO when there's no controlling
        # terminal (CI, setsid, sandboxed children) — that's our skip signal.
        if ! exec 3<>/dev/tty 2>/dev/null; then
            echo "Skipping $display (real file present, non-interactive)"
            return 0
        fi
        local choice=""
        printf '%s already exists (not a symlink). Overwrite with %s? (y/N) ' "$display" "$src_display" >&3
        read -r choice <&3 || true
        exec 3<&-
        case "$choice" in
        y | Y)
            _safe_link_backup "$target_file"
            ln -sfn "$source_file" "$target_file"
            ;;
        *) echo "Skipping $display" ;;
        esac
    else
        # Stale symlink (or no target) — ln -sfn handles atomically.
        # -n (--no-dereference) is load-bearing: when $target_file is an
        # existing symlink that resolves to a *directory* (e.g. ~/.claude/
        # commands, ~/.config/nvim, ~/.claude/hooks), a plain `ln -sf`
        # dereferences it and drops the new link *inside* that directory,
        # leaving the symlink itself still pointing at the old target. -n
        # replaces the symlink atomically instead.
        ln -sfn "$source_file" "$target_file"
    fi
}

# When executed directly, dispatch args to the function
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    set -euo pipefail
    if [[ $# -ne 2 ]]; then
        printf "usage: %s <source> <target>\n" "$(basename "$0")" >&2
        exit 1
    fi
    safe_link "$@"
fi
