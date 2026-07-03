#!/bin/bash
# One-time migration: copy each envchain secret into Bitwarden as a
# Login item named envchain/<namespace>/<VAR>, with the secret value in
# the password field. Existing items with the same name are skipped, so
# reruns are idempotent.
#
# Flags:
#   --fill-empty   for existing items whose `login.password` is empty,
#                  overwrite from envchain. Items with a non-empty
#                  password are still skipped. Use this to repair vaults
#                  where a prior migration created the items but didn't
#                  populate the values.
#
# Values flow stdin → stdin between envchain and bw via jq's env.SECRET
# substitution. Nothing is logged.

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
# bw-common.sh transitively sources bin/lib/secret-store.sh, which defines
# secret_store_required_cmd used below.
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

FILL_EMPTY=0
for arg in "$@"; do
    case "$arg" in
    --fill-empty) FILL_EMPTY=1 ;;
    -h | --help)
        sed -n 's/^# \{0,1\}//p' "$0" | head -n 14
        exit 0
        ;;
    esac
done

bw_require_cmds "$BW_CMD" jq envchain awk "$(secret_store_required_cmd)" || exit 1
bw_require_logged_in || exit 1
bw_ensure_session || exit 1
keychain_ensure_unlocked || exit 1 # envchain reads need the Keychain unlocked too

"$BW_CMD" sync >/dev/null 2>&1 || true
folder_id=$(bw_envchain_folder_id --create)

# Pre-compute existing items as `<name>\t<id>\t<password_len>` so we can
# decide skip-vs-fill in O(1) without re-fetching per item.
existing_items_file=$(mktemp)
trap 'rm -f "$existing_items_file"' EXIT
"$BW_CMD" list items --folderid "$folder_id" |
    jq -r '.[] | [.name, .id, ((.login.password // "") | length)] | @tsv' \
        >"$existing_items_file"

# Look up an existing item by name. Echoes `<id>\t<password_len>`, exits
# non-zero if not found. awk over the tsv is plenty fast for ~hundreds.
lookup_existing() {
    awk -F'\t' -v n="$1" '$1==n {print $2"\t"$3; found=1; exit} END {exit !found}' \
        "$existing_items_file"
}

# migrate_var: push one envchain (NS, VAR) into the vault. Returns 0 on
# success/skip; only ever prints names + status to stdout (no values).
migrate_var() {
    local ns="$1" var="$2"
    local item_name="envchain/$ns/$var"
    local existing id pwlen value

    if existing=$(lookup_existing "$item_name"); then
        IFS=$'\t' read -r id pwlen <<<"$existing"
        if [ "$pwlen" -gt 0 ]; then
            echo "  skip   $item_name (already populated)"
            return 0
        fi
        if [ "$FILL_EMPTY" -ne 1 ]; then
            echo "  skip   $item_name (empty; rerun with --fill-empty)"
            return 0
        fi
        value=$(envchain "$ns" printenv "$var")
        if [ -z "$value" ]; then
            echo "  WARN   $item_name (envchain returned empty)"
            return 0
        fi
        # Fetch full item, patch .login.password, re-encode, edit. Each
        # bw call is checked explicitly so one item's failure doesn't
        # trip pipefail+set-e and abort the whole loop silently.
        local item_json
        item_json=$("$BW_CMD" get item "$id" 2>/dev/null) || {
            echo "  FAIL   $item_name (bw get item rc=$?)"
            return 0
        }
        if [ -z "$item_json" ]; then
            echo "  FAIL   $item_name (bw get item returned empty)"
            return 0
        fi
        # Inline env on jq only — SECRET is scoped to that single process,
        # no subshell needed, no stale-export risk.
        if {
            printf '%s' "$item_json" |
                SECRET="$value" jq '.login.password=env.SECRET' |
                "$BW_CMD" encode |
                "$BW_CMD" edit item "$id" >/dev/null
        } 2>/dev/null; then
            echo "  fill   $item_name"
        else
            echo "  FAIL   $item_name (bw edit failed)"
        fi
        unset value item_json
        return 0
    fi

    value=$(envchain "$ns" printenv "$var")
    if [ -z "$value" ]; then
        echo "  WARN   $item_name (envchain returned empty)"
        return 0
    fi

    # Inline env on jq only — SECRET is scoped to that single process.
    "$BW_CMD" get template item |
        SECRET="$value" jq --arg n "$item_name" --arg fid "$folder_id" \
            '.name=$n | .folderId=$fid | .login={"username":null,"password":env.SECRET,"totp":null,"uris":[]} | .notes=null' |
        "$BW_CMD" encode |
        "$BW_CMD" create item >/dev/null
    unset value
    echo "  ok     $item_name"
}

# Iterate every namespace except brew-sudo (the brew autoupdate flow now
# uses a NOPASSWD sudoers fragment — see etc/sudoers.d/brew-autoupdate).
for ns in $(envchain --list); do
    if [ "$ns" = "brew-sudo" ]; then
        echo "Skipping brew-sudo (replaced by NOPASSWD sudoers)."
        continue
    fi
    echo "Namespace: $ns"
    while IFS= read -r var; do
        [ -n "$var" ] && migrate_var "$ns" "$var"
    done < <(envchain --list "$ns")
done

"$BW_CMD" sync >/dev/null
echo "Migration complete. Verify with:"
echo "  bw list items --folderid $folder_id | jq -r '.[].name'"
