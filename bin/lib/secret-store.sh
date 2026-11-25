# shellcheck shell=bash
# Cross-platform probe of the OS secret store.
#
# Backends, in detection order:
#   security    — macOS Keychain. Auto-unlocked at GUI login.
#   secret-tool — Linux libsecret (gnome-keyring / kwallet via D-Bus).
#                 Selected only if a probe call to the daemon succeeds.
#   file        — Headless fallback: $XDG_CONFIG_HOME/dotfiles/secrets/<svc>.
#
# Override detection by setting DOTFILES_SECRET_BACKEND to one of the names
# above. Items are keyed by ($service, $USER).
#
# This library only answers "does an item exist". It used to store and read
# the Bitwarden master password and API key; nothing writes here any more,
# because every process running as the user can read such an item back.
# bin/doctor.bash uses secret_exists to find the items older versions left.

SECRET_STORE_BACKEND=""
SECRET_STORE_FILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/secrets"

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

# secret_exists <service> — 0 if an item exists, 1 if it does not, 2 if the
# store could not answer (a locked Keychain refuses even a lookup). Never
# reads the value: the security backend asks for attributes only.
secret_exists() {
    secret_store_init
    local service="$1" out
    case "$SECRET_STORE_BACKEND" in
    security)
        out=$(security find-generic-password -s "$service" -a "$USER" 2>&1 >/dev/null) && return 0
        [[ "$out" == *"could not be found"* ]] && return 1
        return 2
        ;;
    secret-tool)
        secret-tool lookup service "$service" account "$USER" >/dev/null 2>&1
        ;;
    file)
        [ -f "$SECRET_STORE_FILE_DIR/$service" ]
        ;;
    *)
        return 2
        ;;
    esac
}
