# shellcheck shell=bash
# uninstall-lib.sh — the symlink-removal + backup-restore core of
# bin/uninstall.bash.
#
# Extracted so the safety-by-construction contract (only touch a target that is
# a symlink pointing into this repo; never a real file or a foreign symlink;
# restore from the lex-largest UTC-stamped backup) has direct unit tests
# (tests/test_uninstall.py) rather than only the full-roundtrip integration in
# tests/test_uninstall_roundtrip.py.
#
# Reads SAFE_LINK_BACKUP_ROOT and HOME from the environment, same as the script.

# Most recent backup dir. Stamps are UTC ISO 8601 (e.g. 20260506T143022Z),
# so lexical max == chronological max. Filter to that pattern so a stray
# non-stamp directory under ~/.dotfiles-backup (e.g. user notes) can't get
# picked as "latest" just because it lex-sorts after the stamps.
latest_backup_root() {
    [[ -d "$SAFE_LINK_BACKUP_ROOT" ]] || return 1
    local latest="" entry name
    for entry in "$SAFE_LINK_BACKUP_ROOT"/*; do
        [[ -d "$entry" ]] || continue
        name="${entry##*/}"
        [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
        [[ "$name" > "$latest" ]] && latest="$name"
    done
    [[ -n "$latest" ]] || return 1
    printf '%s\n' "$SAFE_LINK_BACKUP_ROOT/$latest"
}

remove_dotfile_symlink() {
    local target="$1"
    local expected_source="$2"
    if [[ ! -L "$target" ]]; then
        if [[ -e "$target" ]]; then
            echo "  skip $target (not a symlink — leaving alone)"
        fi
        return
    fi
    local actual
    actual="$(readlink "$target")"
    if [[ "$actual" != "$expected_source" ]]; then
        echo "  skip $target -> $actual (not pointing into this dotfiles repo)"
        return
    fi
    if rm -f "$target"; then
        echo "  removed $target"
    else
        echo "  FAILED to remove $target"
        return 1
    fi
    # Try to restore from most recent backup.
    local backup_root rel src
    if ! backup_root="$(latest_backup_root)"; then
        return 0
    fi
    if [[ "$target" == "$HOME"/* ]]; then
        rel="${target#"$HOME"/}"
    else
        rel="${target#/}"
    fi
    src="$backup_root/$rel"
    if [[ -e "$src" ]]; then
        if mv "$src" "$target"; then
            echo "  restored backup from $src"
        else
            echo "  FAILED to restore backup from $src (still at that path)"
        fi
    fi
}
