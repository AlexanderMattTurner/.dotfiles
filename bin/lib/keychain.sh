# shellcheck shell=bash
# keychain_ensure_unlocked — make sure the macOS login keychain is open
# before envchain (or anything else that needs the Security framework)
# tries to write. On headless macs the keychain doesn't auto-unlock at
# boot, so without this `envchain --set` fails with errSecInteractionNotAllowed
# (rc -25308). No-op on non-macOS.
#
# The login password is asked for each time and never written down. A
# cached copy would be a plain file any process running as the user can
# read, and the login password opens every item in the Keychain.

# shellcheck source=bin/lib/security-cmd.sh disable=SC1091
source "${BASH_SOURCE[0]%/*}/security-cmd.sh"

_keychain_unlock() {
    local pw="$1" kc="$2" line
    # security_build_line rejects newline-bearing arguments before emitting
    # anything; treat that as an unlock failure (the password only ever comes
    # from a single-line `read -rs`, so this is defense in depth).
    if ! line=$(security_build_line unlock-keychain -p "$pw" "$kc"); then
        return 1
    fi
    printf '%s\n' "$line" | security -i >/dev/null 2>&1
}

keychain_ensure_unlocked() {
    [ "$(uname)" = "Darwin" ] || return 0
    local kc="$HOME/Library/Keychains/login.keychain-db"
    # Probe: looking up a guaranteed-missing item reports "interaction is
    # not allowed" to stderr when the keychain is locked, and a benign
    # "could not be found" when unlocked.
    if ! security find-generic-password -s __dotfiles_lock_probe__ -a "$USER" 2>&1 >/dev/null |
        grep -q "interaction is not allowed"; then
        return 0
    fi

    if [ ! -t 0 ]; then
        echo "keychain: locked, and no terminal to ask for the login password" >&2
        return 1
    fi

    local pw
    printf 'macOS keychain is locked. Enter login password to unlock: ' >&2
    IFS= read -rs pw || pw=""
    printf '\n' >&2

    if _keychain_unlock "$pw" "$kc"; then
        unset pw
        security set-keychain-settings "$kc" 2>/dev/null || true
        return 0
    fi

    unset pw
    echo "keychain: unlock-keychain rejected the password" >&2
    return 1
}
