# shellcheck shell=bash
# Cross-platform secret storage wrapper.
#
# Backends, in detection order:
#   security    — macOS Keychain. Auto-unlocked at GUI login.
#   secret-tool — Linux libsecret (gnome-keyring / kwallet via D-Bus).
#                 Selected only if a probe call to the daemon succeeds.
#   file        — Headless fallback: $XDG_CONFIG_HOME/dotfiles/secrets/<svc>,
#                 mode 0600 in a 0700 directory. FS perms only — no
#                 encryption at rest. Acceptable on a single-user box where
#                 the disk is already trusted; not acceptable on shared
#                 hosts.
#
# Override detection by setting DOTFILES_SECRET_BACKEND to one of the names
# above. Items are keyed by ($service, $USER). Values transit only stdin
# (security via `-i` interactive mode; secret-tool) or a temp file → atomic
# mv (file) — never argv, never logged.

SECRET_STORE_BACKEND=""
SECRET_STORE_FILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/secrets"

# shellcheck source=bin/lib/security-cmd.sh disable=SC1091
source "${BASH_SOURCE[0]%/*}/security-cmd.sh"

# Pick a backend exactly once per shell. Cached in $SECRET_STORE_BACKEND.
secret_store_init() {
    [ -n "$SECRET_STORE_BACKEND" ] && return 0

    if [ -n "${DOTFILES_SECRET_BACKEND:-}" ]; then
        SECRET_STORE_BACKEND="$DOTFILES_SECRET_BACKEND"
        return 0
    fi

    if [ "$(uname)" = "Darwin" ]; then
        SECRET_STORE_BACKEND="security"
        return 0
    fi

    if command -v secret-tool >/dev/null 2>&1; then
        # Probe: look up a dummy key and capture stdout+stderr. We can't
        # rely on the exit code alone — secret-tool returns rc=1 for both
        # "not found" (silent) and "D-Bus broken" (prints an error). So
        # consider the daemon healthy iff the lookup either succeeded (rc 0,
        # value on stdout) or failed silently (rc 1, no output at all).
        # A 3-second timeout prevents a hung D-Bus daemon from blocking
        # every new shell.
        local probe_output probe_rc
        probe_output=$(timeout 3 secret-tool lookup service __dotfiles_probe__ \
            account "$USER" 2>&1)
        probe_rc=$?
        if [ "$probe_rc" -eq 0 ] ||
            { [ "$probe_rc" -eq 1 ] && [ -z "$probe_output" ]; }; then
            SECRET_STORE_BACKEND="secret-tool"
            return 0
        fi
    fi

    SECRET_STORE_BACKEND="file"
}

# Print the platform-appropriate command name for `bw_require_cmds` checks.
# Empty string for the file backend (no external command needed).
secret_store_required_cmd() {
    secret_store_init
    case "$SECRET_STORE_BACKEND" in
    security) printf 'security\n' ;;
    secret-tool) printf 'secret-tool\n' ;;
    file) printf '\n' ;;
    esac
}

_secret_set_file() {
    local service="$1" value="$2"
    mkdir -p "$SECRET_STORE_FILE_DIR"
    chmod 0700 "$SECRET_STORE_FILE_DIR"
    local tmp
    tmp=$(mktemp "$SECRET_STORE_FILE_DIR/.tmp.XXXXXX")
    chmod 0600 "$tmp"
    printf '%s' "$value" >"$tmp"
    mv "$tmp" "$SECRET_STORE_FILE_DIR/$service"
}

_secret_get_file() {
    local path="$SECRET_STORE_FILE_DIR/$1"
    [ -f "$path" ] || return 1
    cat "$path"
}

_secret_delete_file() {
    rm -f "$SECRET_STORE_FILE_DIR/$1"
}

# secret_set <service> <value> — overwrites any existing entry.
secret_set() {
    secret_store_init
    local service="$1" value="$2"
    case "$SECRET_STORE_BACKEND" in
    security)
        # security_build_line refuses newline-bearing arguments (the `-i`
        # protocol is line-oriented); surface that as a set failure rather
        # than silently storing a truncated secret.
        local line
        if ! line=$(security_build_line add-generic-password \
            -s "$service" -a "$USER" -U -w "$value"); then
            return 1
        fi
        printf '%s\n' "$line" | security -i >/dev/null
        ;;
    secret-tool)
        printf '%s' "$value" | secret-tool store \
            --label="$service" service "$service" account "$USER"
        ;;
    file)
        _secret_set_file "$service" "$value"
        ;;
    esac
}

# secret_get <service> — echoes value to stdout; nonzero if missing.
secret_get() {
    secret_store_init
    local service="$1"
    case "$SECRET_STORE_BACKEND" in
    security)
        security find-generic-password -s "$service" -a "$USER" -w \
            2>/dev/null
        ;;
    secret-tool)
        secret-tool lookup service "$service" account "$USER" \
            2>/dev/null
        ;;
    file)
        _secret_get_file "$service"
        ;;
    esac
}

# secret_delete <service> — removes the entry if present.
secret_delete() {
    secret_store_init
    local service="$1"
    case "$SECRET_STORE_BACKEND" in
    security)
        security delete-generic-password -s "$service" -a "$USER" \
            >/dev/null 2>&1
        ;;
    secret-tool)
        secret-tool clear service "$service" account "$USER" \
            2>/dev/null
        ;;
    file)
        _secret_delete_file "$service"
        ;;
    esac
}
