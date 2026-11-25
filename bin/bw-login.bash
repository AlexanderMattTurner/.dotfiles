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
#      Otherwise prompt for client_id/client_secret (skippable).
#   2. Run `bw login --apikey` (env-var creds, not argv).
#   3. Prompt for the master password (skippable), unlock, and run an
#      initial seed.
#
# Nothing here is stored. Older versions kept the API key and the master
# password in the OS secret store, where any process running as the user
# could read both; `doctor.bash` FAILs while either item remains. Confirm
# you know the master password, then delete them:
#   macOS: security delete-generic-password -s <service> -a $USER
#   Linux: secret-tool clear service <service> account $USER
# where <service> is bw-api-credentials or bw-master-password.

set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_DIR="${DOTFILES_DIR:-$(git -C "$_self_dir" rev-parse --show-toplevel)}"
# shellcheck source=bin/lib/bw-common.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/bw-common.sh"

bw_require_cmds "$BW_CMD" || exit 1

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

    BW_CLIENTID="$CLIENT_ID" BW_CLIENTSECRET="$CLIENT_SECRET" \
        "$BW_CMD" login --apikey >/dev/null
}

unlock_and_seed() {
    if ! bw_ensure_session; then
        echo "Logged in but skipping the initial seed."
        echo "Run later: bash $DOTFILES_DIR/bin/bw-seed-envchain.bash"
        return 0
    fi
    bash "$DOTFILES_DIR/bin/bw-seed-envchain.bash"
    echo "bw login + initial envchain seed complete."
}

if bw_is_logged_in; then
    echo "bw already logged in; skipping API-key step."
else
    api_login
fi

unlock_and_seed
