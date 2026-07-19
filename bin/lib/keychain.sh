# shellcheck shell=bash
# keychain_ensure_unlocked — make sure the macOS login keychain is open
# before envchain (or anything else that needs the Security framework)
# tries to write. On headless macs the keychain doesn't auto-unlock at
# boot, so without this `envchain --set` fails with errSecInteractionNotAllowed
# (rc -25308). No-op on non-macOS.
#
# Caches the keychain password at $HOME/.config/dotfiles/keychain-password
# (mode 0600 in a 0700 dir) so subsequent runs unlock without prompting.

# Escape a string as a double-quoted argument for `security -i`. The parser
# (SecurityTool's split_line) handles backslash escapes for " and \ inside
# double quotes.
_security_quote() {
    local s=${1//\\/\\\\}
    printf '"%s"' "${s//\"/\\\"}"
}

_keychain_unlock() {
    local pw="$1" kc="$2"
    # `security -i` is line-oriented — a raw newline in $pw can't be embedded
    # safely: at best it truncates the password mid-command; at worst, if
    # SecurityTool's line parser doesn't treat a newline inside "..." as a
    # quoted continuation, the remainder is read back as a separate security
    # command. _security_quote escapes only " and \, not newlines, so refuse
    # rather than find out (security is macOS-only, untestable in CI here);
    # rejecting is safe regardless of the parser's actual behavior. No known
    # path into this function can currently carry a newline — $pw comes from a
    # single-line `read -rs` or a cache file written without one — so this
    # mirrors the guard in secret-store.sh's secret_set() for defense in depth.
    case "$pw" in
    *$'\n'*)
        echo "_keychain_unlock: password contains a newline; the \`security -i\` protocol is line-oriented and can't embed one safely. Refusing to attempt unlock." >&2
        return 1
        ;;
    esac
    printf 'unlock-keychain -p %s %s\n' \
        "$(_security_quote "$pw")" \
        "$(_security_quote "$kc")" |
        security -i >/dev/null 2>&1
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

    local cache="$HOME/.config/dotfiles/keychain-password"
    local pw
    if [ -f "$cache" ]; then
        pw=$(cat "$cache")
        if _keychain_unlock "$pw" "$kc"; then
            unset pw
            security set-keychain-settings "$kc" 2>/dev/null || true
            return 0
        fi
        unset pw
        # Stale cache — fall through to interactive prompt.
    fi

    if [ ! -t 0 ]; then
        echo "keychain: locked and no usable cache at $cache (non-interactive shell)" >&2
        return 1
    fi

    printf 'macOS keychain is locked. Enter login password to unlock: ' >&2
    IFS= read -rs pw || pw=""
    printf '\n' >&2

    if _keychain_unlock "$pw" "$kc"; then
        mkdir -p "$(dirname "$cache")"
        chmod 0700 "$(dirname "$cache")"
        (umask 077 && printf '%s' "$pw" >"$cache")
        unset pw
        security set-keychain-settings "$kc" 2>/dev/null || true
        return 0
    fi

    unset pw
    echo "keychain: unlock-keychain rejected the password" >&2
    return 1
}
