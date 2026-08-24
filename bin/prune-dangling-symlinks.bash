#!/usr/bin/env bash
# bin/prune-dangling-symlinks.bash — remove symlinks under DIR whose target does
# not resolve.
#
# Why this exists: this repo tracks `.claude/hooks` and `.claude/README.md` as
# symlinks into `claude-guard/`, which is `.gitignore`d and therefore absent
# from every CI checkout. Claude Code stats `.claude/hooks` at startup, so in CI
# the reviewer dies with
#
#     ENOENT: no such file or directory, statx '.claude/hooks'
#
# on every rung of claude-review.yaml's credential ladder — which then reports
# the aggregate as "every configured Claude credential errored". That message is
# badly misleading: no credential is involved, and chasing it sends you looking
# for an expired token that does not exist. Pruning the dangling links before
# the action runs is what stops it.
#
# Distinct from bin/lib/stale-symlinks.sh, which is deliberately narrower: that
# one scans the *managed parent dirs* under $HOME and only ever touches links
# whose target is an absolute path inside $DOTFILES_DIR (the rename-leftover
# signature). `.claude/hooks` points at the relative `../claude-guard/hooks`, so
# it fails that check by design. Widening it to cover this case would cost it
# the property that makes it safe to run from setup.bash unattended.
#
# Safe by construction: only symlinks whose target is already missing are
# removed, so there is nothing to back up — the link is broken either way. Real
# files, real directories, and symlinks that resolve are never touched.
# Idempotent: a second run finds nothing.

set -euo pipefail

usage() {
    cat <<'EOF'
usage: prune-dangling-symlinks.bash [DIR]

Remove every symlink under DIR (default: .claude) whose target does not
resolve. Prints one line per removal and a final count.
EOF
}

case "${1:-}" in
-h | --help)
    usage
    exit 0
    ;;
-*)
    printf 'prune-dangling-symlinks: unexpected option %q\n\n' "$1" >&2
    usage >&2
    exit 2
    ;;
esac

TARGET_DIR="${1:-.claude}"

# Nothing to prune is the normal case on a machine where claude-guard is cloned,
# and on any repo without a .claude at all. Not an error.
if [[ ! -d "$TARGET_DIR" ]]; then
    printf 'prune-dangling-symlinks: %s is not a directory; nothing to do\n' "$TARGET_DIR"
    exit 0
fi

pruned=0
while IFS= read -r -d '' link; do
    # -e follows the link, so it is false exactly when the target is missing.
    # -L would be true for a dangling link too, which is why we test -e here.
    if [[ -e "$link" ]]; then
        continue
    fi
    # Read the target before removing it; afterwards there is nothing to read.
    target="$(readlink "$link")" || target="<unreadable>"
    if rm -f "$link"; then
        printf '  removed dangling symlink %s -> %s\n' "$link" "$target"
        pruned=$((pruned + 1))
    else
        printf '  WARN: could not remove dangling symlink %s\n' "$link" >&2
    fi
done < <(find "$TARGET_DIR" -type l -print0 2>/dev/null)

printf 'prune-dangling-symlinks: removed %d dangling symlink(s) under %s\n' "$pruned" "$TARGET_DIR"
