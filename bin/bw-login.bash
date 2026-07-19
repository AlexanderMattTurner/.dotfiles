#!/bin/bash
# One-time-per-machine bw login using a Bitwarden personal API key.
#
# Why API key: WebAuthn-only accounts can't `bw login` interactively (bw
# doesn't support WebAuthn). The personal API key bypasses 2FA at login
# but does not unlock the vault, so the master password is still required
# — encryption boundary preserved.
#
# Get your API key from the Bitwarden web vault:
#   Account settings → Security → Keys → "View API Key"
#
# This script:
#   1. If bw is already logged in, skip the API-key step (resumable).
#      Otherwise prompt for client_id/client_secret (skippable) and
#      stash them in the OS secret store.
#   2. Run `bw login --apikey` (env-var creds, not argv).
#   3. Prompt for the master password (skippable). Stash in the OS secret
#      store so the seed/add scripts can auto-unlock unattended.
#   4. Unlock and run an initial seed.
#
# Re-running is safe: existing secret-store items are overwritten.
# To remove cached creds, use the platform-appropriate tool:
#   macOS: security delete-generic-password -s <service> -a $USER
#   Linux: secret-tool clear service <service> account $USER
# where <service> is bw-api-credentials or bw-master-password.

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
# bw-common.sh transitively sources bin/lib/secret-store.sh, which defines
# the secret_set / secret_get / secret_store_required_cmd helpers used below.
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

bw_require_cmds "$BW_CMD" "$(secret_store_required_cmd)" || exit 1

# Prompt for a value into the named variable; empty input is an explicit
# skip signal. If $3 == "hidden", echo is suppressed.
prompt_skippable() {
    local prompt="$1" varname="$2" hidden="${3:-}" value
    printf '%s (empty to skip): ' "$prompt" >&2
    if [ "$hidden" = "hidden" ]; then
        # read -rs handles the echo toggle; traps cover signal interruption
        # mid-read (bash's restore-on-signal isn't guaranteed across versions).
        trap 'stty echo 2>/dev/null; exit 130' INT
        trap 'stty echo 2>/dev/null; exit 143' TERM
        trap 'stty echo 2>/dev/null' EXIT
        IFS= read -rs value || true # allow EOF without triggering set -e
        trap - INT TERM EXIT
        printf '\n' >&2
    else
        IFS= read -r value || true
    fi
    printf -v "$varname" '%s' "$value"
}

api_login() {
    local CLIENT_ID CLIENT_SECRET

    prompt_skippable "Bitwarden API client_id" CLIENT_ID
    if [ -z "${CLIENT_ID:-}" ]; then
        echo "Skipping bw bootstrap (no API client_id provided)."
        exit 0
    fi
    prompt_skippable "Bitwarden API client_secret" CLIENT_SECRET hidden
    [ -n "${CLIENT_SECRET:-}" ] ||
        {
            echo "client_secret required when client_id is set." >&2
            exit 1
        }

    secret_set bw-api-credentials "$CLIENT_ID:$CLIENT_SECRET"

    BW_CLIENTID="$CLIENT_ID" BW_CLIENTSECRET="$CLIENT_SECRET" \
        "$BW_CMD" login --apikey >/dev/null
}

cache_master_and_seed() {
    local MASTER
    prompt_skippable "Bitwarden master password" MASTER hidden
    if [ -z "${MASTER:-}" ]; then
        echo "Logged in but skipping unlock-cache and initial seed."
        echo "Run later: bash $DOTFILES_DIR/bin/bw-seed-envchain.bash"
        return 0
    fi

    secret_set bw-master-password "$MASTER"

    # Pass password via --passwordenv — Node bw silently ignores
    # --passwordfile and falls back to an interactive prompt that crashes
    # inquirer on piped stdin (Node 20+). Env var scoped to subprocess.
    # 2>/dev/null hides bw's stderr (inquirer crash noise, network errors,
    # cryptography errors all look alike there) so the validation below
    # phrases the failure generically rather than claiming a specific cause.
    # `CMD || rc=$?` (not a plain `VAR=$(CMD)` followed by `rc=$?`): under
    # set -e, a failing command-substitution assignment used as a bare
    # top-level command aborts the script right there, before the check
    # below ever runs — the friendly error message becomes dead code and
    # the user just sees a silent exit. Putting the assignment on the left
    # of `||` exempts it from -e (same reasoning as bin/lib/retry.sh's
    # `"$@" && return 0` idiom).
    local rc=0
    BW_SESSION=$(BW_PASSWORD="$MASTER" "$BW_CMD" unlock --raw --passwordenv BW_PASSWORD 2>/dev/null) || rc=$?
    unset MASTER
    if [ "$rc" -ne 0 ] || [ -z "$BW_SESSION" ]; then
        echo "bw unlock failed (wrong password, network error, or bw binary issue). Re-run bin/bw-login.bash." >&2
        return 1
    fi
    export BW_SESSION

    bash "$DOTFILES_DIR/bin/bw-seed-envchain.bash"
    echo "bw login + initial envchain seed complete."
}

if bw_is_logged_in; then
    echo "bw already logged in; skipping API-key step."
else
    api_login
fi

cache_master_and_seed
