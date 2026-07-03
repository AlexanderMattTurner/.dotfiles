#!/bin/bash
# Pull each item in the Bitwarden `envchain` folder down into envchain.
#
# Item naming convention: envchain/<namespace>/<VAR>. The Login item's
# password field holds the value. Values transit only stdin→stdin between
# bw and envchain — never on argv, never logged.
#
# Auth: bw must already be `bw login`-ed (one-time per machine via
# bin/bw-login.bash). This script picks up $BW_SESSION if exported, else
# unlocks via the cached master password (see bin/lib/bw-common.sh).
#
# Flags:
#   --quiet   suppress per-item output (used by shell-startup autosync).

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
# bw-common.sh transitively sources bin/lib/secret-store.sh, which defines
# secret_store_required_cmd used below.
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

QUIET=0
for arg in "$@"; do
    case "$arg" in
    --quiet) QUIET=1 ;;
    -h | --help)
        sed -n 's/^# \{0,1\}//p' "$0" | head -n 14
        exit 0
        ;;
    esac
done

log() { [ "$QUIET" -eq 1 ] || echo "$@"; }
err() { echo "$@" >&2; }

bw_require_cmds "$BW_CMD" jq envchain "$(secret_store_required_cmd)" awk || exit 1
bw_require_logged_in || exit 1
bw_ensure_session || exit 1
keychain_ensure_unlocked || exit 1 # envchain writes need an unlocked Keychain on macOS

"$BW_CMD" sync >/dev/null 2>&1 || true

# shellcheck disable=SC2119  # no args = lookup-only mode
folder_id=$(bw_envchain_folder_id) || exit 0 # nothing to seed yet

# seed_one: parse `envchain/<ns>/<VAR>`, fetch the password, pipe it into
# `envchain --set`. Skips items not matching the naming scheme.
# Note: envchain's --noecho requires a TTY; we're piping, so omit it.
#
# We fetch by item ID via `bw get item <id> | jq -r .login.password` rather
# than `bw get password <id>` because the latter is flaky across bw CLI
# versions (returns "Not found." on some Linux builds even for IDs that
# `bw list items` just returned). `bw get item` is the stable path.
seed_one() {
    local id="$1" name="$2"
    local prefix ns var pw item_json
    IFS=/ read -r prefix ns var <<<"$name"
    if [ "$prefix" != "envchain" ] || [ -z "$ns" ] || [ -z "$var" ]; then
        log "  skip   $name (not in envchain/<ns>/<VAR> format)"
        return 0
    fi
    # Split bw and jq so a bw failure doesn't trip pipefail+set-e and kill
    # the whole loop silently. `|| { ... }` keeps set -e quiet either way.
    item_json=$("$BW_CMD" get item "$id" 2>/dev/null) || {
        err "  FAIL   $ns/$var (bw get item rc=$?)"
        return 0
    }
    pw=$(printf '%s' "$item_json" | jq -r '.login.password // empty')
    if [ -z "$pw" ]; then
        err "  FAIL   $ns/$var (empty password field)"
        return 0
    fi
    if printf '%s' "$pw" | envchain --set "$ns" "$var" >/dev/null; then
        log "  ok     $ns/$var"
    else
        err "  FAIL   $ns/$var (envchain write failed)"
    fi
    unset pw item_json
}

items_json=$("$BW_CMD" list items --folderid "$folder_id")
count=$(printf '%s' "$items_json" | jq 'length')
log "Seeding $count items from Bitwarden folder envchain → envchain..."

while IFS=$'\t' read -r id name; do
    [ -z "$id" ] && continue
    seed_one "$id" "$name"
done < <(printf '%s' "$items_json" | jq -r '.[] | "\(.id)\t\(.name)"')

log "Seed complete."
