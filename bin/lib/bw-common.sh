# shellcheck shell=bash
# Shared helpers for the bw-* scripts in this directory. Source from a
# script via:
#
#     _self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
#     DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
#     source "$DOTFILES_DIR/bin/lib/bw-common.sh"
#
# `pwd -P` follows symlinks, so the snippet works whether the caller is
# invoked by full path or via a ~/.local/bin/* symlink. `git rev-parse`
# is depth-independent — works for callers anywhere in the repo, not
# just bin/.
#
# All functions return non-zero on failure. Callers should `|| exit` after
# each call (or rely on `set -e`).

# shellcheck source=bin/lib/secret-store.sh disable=SC1091
source "${BASH_SOURCE[0]%/*}/secret-store.sh"
# shellcheck source=bin/lib/keychain.sh disable=SC1091
source "${BASH_SOURCE[0]%/*}/keychain.sh"

# Scripts require Node bw (`bw-node`, set up by setup.sh). Rust bw isn't
# a fallback — it has session/output quirks these scripts can't dodge.
BW_CMD="${BW_CMD:-bw-node}"

# Verify the required external commands are on PATH. Usage:
#   bw_require_cmds bw jq envchain security
# Empty arguments are skipped — callers can splice in
# `$(secret_store_required_cmd)`, which is empty when the file backend is
# in use (no external command needed).
bw_require_cmds() {
    local missing=()
    local cmd
    for cmd in "$@"; do
        [ -z "$cmd" ] && continue
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Missing required commands: ${missing[*]}" >&2
        return 1
    fi
}

# Return 0 if bw has any session at all (locked or unlocked), 1 otherwise.
bw_is_logged_in() {
    "$BW_CMD" status --raw 2>/dev/null | grep -qE '"status":"(locked|unlocked)"'
}

# Exit-style guard: errors out if not logged in.
bw_require_logged_in() {
    if ! bw_is_logged_in; then
        echo "bw: not logged in. Run bin/bw-login.bash first." >&2
        return 1
    fi
}

# Ensure $BW_SESSION is exported, unlocking via the master password cached
# in the OS secret store (service `bw-master-password`, see
# bin/lib/secret-store.sh) if needed. Password is passed via env var to
# `--passwordenv` — Node bw silently ignores `--passwordfile` and falls
# back to an interactive prompt that crashes inquirer on piped stdin
# ("readline was closed" on Node 20+). Env var scoped to the subprocess.
#
# The session key must reach bw ONLY via the exported BW_SESSION env var,
# never as a `--session` argument — argv is readable by any same-user
# process (`ps -eo args`), which is exactly the exposure the "secrets
# never on argv" rule in CLAUDE.md exists to prevent. bw reads
# BW_SESSION from the environment natively. Guarded by
# tests/test_security_argv.py::test_bw_session_never_on_argv.
bw_ensure_session() {
    if [ -n "${BW_SESSION:-}" ]; then
        export BW_SESSION
        return 0
    fi
    local mp
    mp=$(secret_get bw-master-password)
    if [ -z "${mp:-}" ]; then
        echo "bw: locked and no cached master password. Run bin/bw-login.bash." >&2
        return 1
    fi
    local out rc
    out=$(BW_PASSWORD="$mp" "$BW_CMD" unlock --raw --passwordenv BW_PASSWORD 2>/dev/null)
    rc=$?
    unset mp
    # Rust bw 2026.x writes ERROR lines to stdout on unlock failure, so a
    # non-empty $out doesn't prove success. A real session is a single
    # base64-ish line with no whitespace; bail on anything else.
    if [ "$rc" -ne 0 ] || [ -z "$out" ] || [[ "$out" == *[[:space:]]* ]] || [[ "$out" == ERROR* ]]; then
        echo "bw unlock: cached master password rejected. Re-run bin/bw-login.bash." >&2
        return 1
    fi
    BW_SESSION="$out"
    export BW_SESSION
}

# Echo the id of the `envchain` folder. If $1 == "--create", create it
# when missing; otherwise return 1 with a message if missing.
# shellcheck disable=SC2119  # callers intentionally invoke without args
bw_envchain_folder_id() {
    local fid
    fid=$("$BW_CMD" list folders |
        jq -r '.[] | select(.name=="envchain") | .id' | head -n1)
    if [ -n "$fid" ]; then
        printf '%s\n' "$fid"
        return 0
    fi
    if [ "${1:-}" != "--create" ]; then
        echo "No 'envchain' folder in vault." >&2
        return 1
    fi
    "$BW_CMD" get template folder |
        jq '.name="envchain"' |
        "$BW_CMD" encode |
        "$BW_CMD" create folder |
        jq -r '.id'
}

# Return 0 if an item with the given name exists in the given folder.
bw_item_exists() {
    local folder_id="$1" name="$2"
    "$BW_CMD" list items --folderid "$folder_id" |
        jq -e --arg n "$name" '.[] | select(.name==$n)' >/dev/null
}
