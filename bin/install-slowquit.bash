#!/usr/bin/env bash
# install-slowquit.bash — install SlowQuit (https://github.com/dudukee/SlowQuit)
# from its pinned DMG release.
#
# SlowQuit is a macOS menu-bar app that adds a hold-to-quit delay to Cmd-Q so
# accidental quits don't kill an app. It ships only as a signed DMG on GitHub
# Releases — there is no Homebrew cask — so we fetch the DMG directly, verify it
# against a pinned sha256, and copy the app into /Applications.
#
# Pinned + checksummed on purpose: SlowQuit monitors global keystrokes and asks
# for Accessibility permission, so a swapped-out binary would be a keylogger.
# Treat it like any other security-sensitive dependency and bump deliberately:
#   1. find the new tag at https://github.com/dudukee/SlowQuit/releases
#   2. read the asset digest without downloading:
#        gh api repos/dudukee/SlowQuit/releases/tags/<tag> \
#          | jq -r '.assets[] | "\(.name) \(.digest)"'
#   3. update SLOWQUIT_VERSION + SLOWQUIT_SHA256 below and commit via PR.
set -euo pipefail

SLOWQUIT_VERSION="0.0.3"
SLOWQUIT_SHA256="6c1008701da6e36371912ae220e75410b05d53d13d8745a5dc893b4a87acc78b"
APP_DEST="/Applications/SlowQuit.app"
STAMP="$HOME/.cache/dotfiles/slowquit.pin"

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/retry.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/retry.sh"

log() { printf '  -> %s\n' "$1"; }

if [ "$(uname)" != "Darwin" ]; then
    log "SlowQuit is macOS-only; skipping."
    exit 0
fi

# Idempotency: the app is present and the stamp records this exact pin. The
# stamp (not the app's CFBundleShortVersionString) is the source of truth so a
# reinstall triggers precisely when we bump the pin above, never on every run.
if [ -d "$APP_DEST" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$SLOWQUIT_VERSION" ]; then
    log "SlowQuit $SLOWQUIT_VERSION already installed."
    exit 0
fi

URL="https://github.com/dudukee/SlowQuit/releases/download/$SLOWQUIT_VERSION/SlowQuit-$SLOWQUIT_VERSION.dmg"
WORKDIR="$(mktemp -d)"
DMG="$WORKDIR/SlowQuit-$SLOWQUIT_VERSION.dmg"
MOUNT="$WORKDIR/mnt"
cleanup() {
    ([ -d "$MOUNT" ] && hdiutil detach "$MOUNT" -quiet 2>/dev/null) || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

log "Downloading SlowQuit $SLOWQUIT_VERSION..."
retry 3 5 curl -fsSL -o "$DMG" "$URL"

log "Verifying checksum..."
if ! printf '%s  %s\n' "$SLOWQUIT_SHA256" "$DMG" | shasum -a 256 -c - >/dev/null 2>&1; then
    printf 'ERROR: SlowQuit DMG checksum mismatch — refusing to install.\n' >&2
    printf '  expected: %s\n' "$SLOWQUIT_SHA256" >&2
    printf '  got:      %s\n' "$(shasum -a 256 "$DMG" | awk '{print $1}')" >&2
    exit 1
fi

log "Mounting and installing to $APP_DEST..."
mkdir -p "$MOUNT"
hdiutil attach "$DMG" -mountpoint "$MOUNT" -nobrowse -quiet
APP_SRC="$(find "$MOUNT" -maxdepth 1 -name '*.app' -print -quit)"
if [ -z "$APP_SRC" ]; then
    printf 'ERROR: no .app found inside SlowQuit DMG.\n' >&2
    exit 1
fi
rm -rf "$APP_DEST"
cp -R "$APP_SRC" "$APP_DEST"
hdiutil detach "$MOUNT" -quiet
# Clear the download-quarantine flag so first launch doesn't hit Gatekeeper's
# "downloaded from the internet" block for an app we just checksum-verified.
# Call /usr/bin/xattr explicitly: a pip/uv-installed `xattr` shim on PATH
# shadows the system binary and doesn't understand the recursive -r flag.
/usr/bin/xattr -dr com.apple.quarantine "$APP_DEST" 2>/dev/null || true

mkdir -p "$(dirname "$STAMP")"
printf '%s\n' "$SLOWQUIT_VERSION" >"$STAMP"

log "SlowQuit $SLOWQUIT_VERSION installed. Launch it once and grant Accessibility permission."
