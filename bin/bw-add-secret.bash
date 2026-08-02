#!/bin/bash
# Add (or update) a secret in the Bitwarden vault and in envchain in one shot.
#
# Usage:
#   bw-add-secret.bash [--update] <namespace> <VAR_NAME>
#
# Reads the secret value from stdin (pipe one in) or interactively prompts
# without echo. The value flows into envchain via stdin and into bw via
# `bw create item` / `bw edit item` JSON, with the value injected through
# env-var substitution in jq's `env.SECRET` — never on argv, never logged.
#
# Without --update, an existing vault item is left alone (only envchain is
# refreshed). With --update, the vault item's password is overwritten.

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
# bw-common.sh transitively sources bin/lib/secret-store.sh, which defines
# secret_store_required_cmd used below.
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

usage() {
    echo "Usage: $(basename "$0") [--update] <namespace> <VAR_NAME>" >&2
    echo "Reads value from stdin (pipe) or prompts (no echo)." >&2
    echo "  --update  overwrite an existing vault item's password." >&2
    exit 2
}

update=0
case "${1:-}" in
--update)
    update=1
    shift
    ;;
-h | --help) usage ;;
esac

[ $# -eq 2 ] || usage
ns="$1"
var="$2"
item_name="envchain/$ns/$var"

bw_require_cmds "$BW_CMD" jq envchain "$(secret_store_required_cmd)" || exit 1
bw_require_logged_in || exit 1
bw_ensure_session || exit 1
keychain_ensure_unlocked || exit 1

"$BW_CMD" sync >/dev/null 2>&1 || true

# Read value into the SECRET var. Hidden prompt on TTY, plain stdin read
# otherwise (for piping). Not exported here: jq needs it as env.SECRET, but
# only for the one jq invocation, via an inline `SECRET="$SECRET" jq ...`
# prefix — exporting it script-wide would leak the plaintext into the
# environment of every other child process spawned afterward (bw list
# folders, bw create folder, bw sync, …), none of which read it.
read_secret_into_SECRET() {
    if [ -t 0 ]; then
        printf 'Value for %s (hidden): ' "$item_name" >&2
        # read -rs handles the echo toggle; traps cover signal interruption
        # mid-read (bash's restore-on-signal isn't guaranteed across versions).
        trap 'stty echo 2>/dev/null; exit 130' INT
        trap 'stty echo 2>/dev/null; exit 143' TERM
        trap 'stty echo 2>/dev/null' EXIT
        IFS= read -rs SECRET || true # allow EOF without triggering set -e
        trap - INT TERM EXIT
        printf '\n' >&2
    else
        IFS= read -r SECRET || true
    fi
    [ -n "${SECRET:-}" ] || {
        echo "Empty value; aborting." >&2
        exit 1
    }
}

# Mirror the value into envchain locally. Pipe stdin→stdin.
# Note: envchain's --noecho requires a TTY; we're piping, so omit it.
write_envchain() {
    printf '%s' "$SECRET" | envchain --set "$ns" "$var" >/dev/null
}

# Create the vault item (folder envchain, name $item_name, value env.SECRET).
create_vault_item() {
    local folder_id="$1"
    "$BW_CMD" get template item |
        SECRET="$SECRET" jq --arg n "$item_name" --arg fid "$folder_id" \
            '.name=$n | .folderId=$fid | .login={"username":null,"password":env.SECRET,"totp":null,"uris":[]} | .notes=null' |
        "$BW_CMD" encode |
        "$BW_CMD" create item >/dev/null
}

# Overwrite the password on an existing vault item. The full item JSON is
# fetched once, mutated through jq (with the new password in env.SECRET), and
# piped back through `bw edit item`.
update_vault_item() {
    local item_json id
    item_json=$("$BW_CMD" get item "$item_name")
    id=$(printf '%s' "$item_json" | jq -r '.id')
    [ -n "$id" ] || {
        echo "bw: couldn't resolve id for '$item_name'." >&2
        return 1
    }
    printf '%s' "$item_json" |
        SECRET="$SECRET" jq '.login.password=env.SECRET' |
        "$BW_CMD" encode |
        "$BW_CMD" edit item "$id" >/dev/null
}

read_secret_into_SECRET

folder_id=$(bw_envchain_folder_id --create)

if bw_item_exists "$folder_id" "$item_name"; then
    if [ "$update" -eq 1 ]; then
        update_vault_item
        write_envchain
        unset SECRET
        echo "Updated $item_name in vault and envchain."
        exit 0
    fi
    write_envchain
    unset SECRET
    echo "Item '$item_name' already in vault; refreshed envchain only."
    echo "To overwrite the vault value, rerun with --update."
    exit 0
fi

create_vault_item "$folder_id"
write_envchain
unset SECRET
echo "Added $item_name to vault and envchain."
