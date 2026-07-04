#!/bin/bash
# Authenticate `gh` non-interactively using a PAT stored in Bitwarden.
#
# Looks up the Login item `envchain/github/PAT` (created via
# `bin/bw-add-secret.bash github PAT`) in the unlocked vault and pipes its
# password field to `gh auth login --with-token`. Exits non-zero if
# anything is missing, so callers can fall back to an interactive flow.

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

bw_require_cmds "$BW_CMD" jq gh || exit 1
bw_require_logged_in || exit 1
bw_ensure_session || exit 1

item_name="envchain/github/PAT"
pat=$("$BW_CMD" get item "$item_name" 2>/dev/null |
    jq -r '.login.password // empty')
[ -n "$pat" ] || {
    echo "gh-auth-from-bw: no '$item_name' item in vault. Add with: bin/bw-add-secret.bash github PAT" >&2
    exit 1
}

printf '%s' "$pat" | gh auth login --git-protocol ssh --with-token
