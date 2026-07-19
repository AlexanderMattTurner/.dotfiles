#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
usage: bash setup.bash [--link-only]

  --link-only   refresh symlinks + run doctor; skip package installation
  -h, --help    show this message

With no flags, runs the full installer (Homebrew, Brewfile, launchd
agents, AI tooling) and finishes with a doctor.bash health summary.
EOF
}

# Validate args strictly — an unrecognized flag must not fall through to a
# full install (brew, sudo, chsh, launchd) the caller didn't ask for.
LINK_ONLY=false
for arg in "$@"; do
    case "$arg" in
    --link-only) LINK_ONLY=true ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        printf 'setup.bash: unknown argument %q\n\n' "$arg" >&2
        usage >&2
        exit 2
        ;;
    esac
done

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

status_msg() {
    echo ":: $1"
}

# Resolve dotfiles directory from this script's location
DOTFILES_DIR="$(cd "$(dirname "$0")" && pwd)"

# shellcheck source=bin/lib/safe_link.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/safe_link.sh"
# shellcheck source=bin/lib/symlinks.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/symlinks.sh"
# shellcheck source=bin/lib/retry.sh disable=SC1091
source "$DOTFILES_DIR/bin/lib/retry.sh"

# ── claude-guard (always run) ────────────────────────────────
# Clones/updates at the commit pinned in claude-guard.ref — the subrepo
# carries the AI-safety monitor, so it doesn't float at origin/main.
# Bump with: bash bin/clone-claude-guard.bash --bump
# Non-fatal: a fresh machine with a flaky network shouldn't be unable to
# reach the closing doctor.bash summary. doctor.bash's claude-guard check
# reports the state below (skips if never cloned, fails if drifted from the
# pin) with the exact remediation command.
bash "$DOTFILES_DIR/bin/clone-claude-guard.bash" ||
    status_msg "WARN: claude-guard clone/update failed; rerun 'bash bin/clone-claude-guard.bash' — doctor.bash reports its state below."

# ── Symlinks (always run) ────────────────────────────────────────────────────
status_msg "Linking dotfiles..."
# Iterate both shared lists. safe_link handles all clobber/backup semantics —
# including directory targets like ~/.config/nvim. Launchagent plists below
# stay inline because they need bootout/bootstrap calls.
while IFS='|' read -r target source _label; do
    mkdir -p "$(dirname "$target")"
    safe_link "$source" "$target"
done < <(managed_symlinks)

while IFS='|' read -r target source _label; do
    mkdir -p "$(dirname "$target")"
    safe_link "$source" "$target"
done < <(repo_hook_symlinks)

# Prune rename-leftover symlinks (e.g. ~/.local/bin/claude-private pointing at a
# since-renamed wrapper under $DOTFILES_DIR). Without this, stale links linger
# until someone answers doctor.bash's interactive "Refresh symlinks now?"
# prompt — which can't run on the --link-only path.
bash "$DOTFILES_DIR/bin/lib/stale-symlinks.sh" --prune

[[ -f "$HOME/.extras.bash" ]] || touch "$HOME/.extras.bash"
[[ -f "$HOME/.extras.fish" ]] || touch "$HOME/.extras.fish"
[[ -f "$HOME/.hushlogin" ]] || touch "$HOME/.hushlogin"
[[ -f "$HOME/.vimextras" ]] || touch "$HOME/.vimextras"

if [ "$LINK_ONLY" = true ]; then
    status_msg "Symlinks refreshed."
    # setup.bash just reconciled every symlink, so doctor must not re-offer its
    # interactive "Refresh symlinks now?" prompt here — that would block the
    # --link-only path on input (and recurse back into setup.bash).
    bash "$DOTFILES_DIR/bin/doctor.bash" --quiet --no-refresh || true
    exit 0
fi

# ── Package installation (skipped with --link-only) ──────────────────────────

# Install Homebrew first -- many subsequent steps depend on it
install_homebrew() {
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >/dev/null
}
if ! command_exists brew; then
    status_msg "Installing Homebrew..."
    retry 3 10 install_homebrew ||
        status_msg "WARN: Homebrew install failed after 3 attempts — rerun setup.bash."
fi

# Always (re)load brew shellenv into this script's environment — handles
# the case where brew was already installed but isn't in PATH (e.g., fresh
# bash invocation without ~/.profile sourced). Also ensures ~/.profile has
# the eval line on Linux for future login shells; the line is appended at
# most once.
if [ "$(uname)" = "Darwin" ]; then
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    if [ -x /home/linuxbrew/.linuxbrew/bin/brew ]; then
        eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
        # shellcheck disable=SC2016 # literal string, expanded at shell startup
        BREW_EVAL_LINE='eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"'
        if ! grep -qxF "$BREW_EVAL_LINE" "$HOME/.profile" 2>/dev/null; then
            echo "$BREW_EVAL_LINE" >>"$HOME/.profile"
        fi
    fi
fi

brew_quiet_install() {
    brew install --quiet "$@"
}

# Install all brew packages from Brewfile. Retry on transient failure —
# GitHub's HTTP/2 frontend occasionally drops `git clone` mid-tap, and a
# second attempt almost always succeeds. doctor.bash at the end of setup
# catches anything that's still missing after 3 tries.
status_msg "Installing from Brewfile..."
retry 3 10 brew bundle --quiet --file="$DOTFILES_DIR/Brewfile" ||
    status_msg "WARN: brew bundle failed after 3 attempts — doctor.bash will report missing packages."

# Bitwarden CLI bootstrap. Bitwarden is the cross-machine source of truth
# for secrets; envchain is the local runtime cache. We use the personal
# API key flow (so WebAuthn-only accounts work) and stash both the API
# credentials and the master password in macOS Keychain so the seeder
# can run unattended at shell startup.
#
# All prompts inside bw-login.bash are skippable (empty input).
if command_exists bw-node && [ -t 0 ]; then
    # `bw status` exits 0 with JSON containing status: "unauthenticated" |
    # "locked" | "unlocked". We grep for the logged-in markers; absence ==
    # not logged in (the safe default that triggers the bootstrap prompt).
    if ! bw-node status --raw 2>/dev/null | grep -qE '"status":"(locked|unlocked)"'; then
        status_msg "Bitwarden CLI not logged in."
        read -rp "Run bw login bootstrap now? (y/N) " choice
        case "$choice" in
        y | Y) bash "$DOTFILES_DIR/bin/bw-login.bash" || status_msg "bw bootstrap skipped/failed; rerun bin/bw-login.bash later." ;;
        *) status_msg "Skipping. Rerun later with: bash $DOTFILES_DIR/bin/bw-login.bash" ;;
        esac
    fi
elif ! command_exists bw-node; then
    status_msg "WARN: bw-node not on PATH — Bitwarden bootstrap skipped. Run bin/bw-login.bash after setup completes."
fi

# GitHub CLI — install and authenticate on first login
if ! command_exists gh; then
    retry 3 5 brew_quiet_install gh ||
        status_msg "WARN: 'brew install gh' failed after 3 attempts — doctor.bash will report it missing."
fi
if ! gh auth status &>/dev/null; then
    if bash "$DOTFILES_DIR/bin/gh-auth-from-bw.bash" 2>/dev/null; then
        status_msg "gh authenticated from Bitwarden."
    elif [ -t 0 ]; then
        status_msg "Falling back to interactive gh auth (scopes needed: repo, read:org)."
        gh auth login --git-protocol ssh || status_msg "gh auth skipped — run 'gh auth login' later."
    else
        status_msg "Skipping gh auth (non-interactive, no Bitwarden PAT). Run 'gh auth login' manually."
    fi
fi

# Install fish and configure
"$DOTFILES_DIR"/bin/install_fish.bash

if [ "$(uname)" = "Darwin" ]; then
    status_msg "Configuring macOS packages..."

    # Escape $USER for literal sed replacement (guards against `/` and `&` in
    # unusual usernames). Used for both the sudoers and Tailscale templates.
    ESCAPED_USER="$(printf '%s' "$USER" | sed 's/[\/&]/\\&/g')"

    # Aerospace window manager setup (requires custom tap)
    retry 3 5 brew_quiet_install --cask nikitabobko/tap/aerospace ||
        status_msg "WARN: aerospace cask install failed after 3 attempts — rerun setup.bash to retry."

    # Brew autoupdate: update once a week (604800 seconds) with --sudo.
    # Uses a NOPASSWD sudoers fragment scoped to /opt/homebrew/bin/brew
    # so the background launchd job can run sudo without prompting.
    SUDOERS_TEMPLATE="$DOTFILES_DIR/etc/sudoers.d/brew-autoupdate.template"
    SUDOERS_DEST="/etc/sudoers.d/brew-autoupdate"
    if [ -f "$SUDOERS_TEMPLATE" ] && [ ! -f "$SUDOERS_DEST" ]; then
        SUDOERS_RENDERED="$(mktemp)"
        trap 'rm -f "$SUDOERS_RENDERED"' EXIT
        sed "s/__USERNAME__/$ESCAPED_USER/g" "$SUDOERS_TEMPLATE" >"$SUDOERS_RENDERED"
        if sudo visudo -cf "$SUDOERS_RENDERED" >/dev/null; then
            sudo install -o root -g wheel -m 0440 "$SUDOERS_RENDERED" "$SUDOERS_DEST"
        else
            status_msg "WARN: rendered sudoers fragment failed validation; skipping install."
        fi
        rm -f "$SUDOERS_RENDERED"
        trap - EXIT
    fi
    brew tap homebrew/autoupdate 2>/dev/null || true
    brew autoupdate start 604800 --upgrade --cleanup --sudo >/dev/null 2>&1 || true

    # OrbStack: lightweight Docker alternative for macOS
    retry 3 5 brew_quiet_install --cask orbstack ||
        status_msg "WARN: orbstack cask install failed after 3 attempts — rerun setup.bash to retry."

    # Tailscale VPN daemon — `com.$USER.tailscaled` is the sole daemon; boot
    # out homebrew's if present. Two daemons racing on /var/run/tailscaled.socket
    # leaves stale provenance on the socket → CLI hits EPERM on connect.
    TAILSCALE_PLIST_DEST="/Library/LaunchDaemons/com.$USER.tailscaled.plist"
    TAILSCALE_PLIST_RENDERED="$(mktemp)"
    trap 'rm -f "$TAILSCALE_PLIST_RENDERED"' EXIT
    sed "s/__USERNAME__/$ESCAPED_USER/g" "$DOTFILES_DIR/launchagents/com.tailscaled.plist.template" \
        >"$TAILSCALE_PLIST_RENDERED"
    needs_bootstrap=false
    if [ ! -f "$TAILSCALE_PLIST_DEST" ] || ! cmp -s "$TAILSCALE_PLIST_RENDERED" "$TAILSCALE_PLIST_DEST"; then
        sudo install -o root -g wheel -m 0644 "$TAILSCALE_PLIST_RENDERED" "$TAILSCALE_PLIST_DEST"
        needs_bootstrap=true
    fi
    rm -f "$TAILSCALE_PLIST_RENDERED"
    trap - EXIT

    HOMEBREW_TAILSCALED_PLIST="/Library/LaunchDaemons/homebrew.mxcl.tailscale.plist"
    if [ -f "$HOMEBREW_TAILSCALED_PLIST" ]; then
        status_msg "Removing homebrew.mxcl.tailscale (conflicts with com.$USER.tailscaled)"
        if sudo launchctl print system/homebrew.mxcl.tailscale &>/dev/null; then
            sudo launchctl bootout system/homebrew.mxcl.tailscale
        fi
        sudo rm -f "$HOMEBREW_TAILSCALED_PLIST"
        needs_bootstrap=true
    fi

    # App-Store Tailscale leaves /usr/local/bin/tailscale as a root-owned shim
    # pointing at /Applications/Tailscale.app/Contents/MacOS/tailscale. When
    # the app is uninstalled, the shim survives and shadows brew tailscale on
    # any Intel Mac (or any user with /usr/local/bin earlier in PATH).
    TAILSCALE_SHIM="/usr/local/bin/tailscale"
    if [ -e "$TAILSCALE_SHIM" ] && ! "$TAILSCALE_SHIM" version >/dev/null 2>&1; then
        status_msg "Removing broken App-Store tailscale shim at $TAILSCALE_SHIM"
        sudo rm -f "$TAILSCALE_SHIM"
    fi

    if $needs_bootstrap; then
        if sudo launchctl print "system/com.$USER.tailscaled" &>/dev/null; then
            sudo launchctl bootout "system/com.$USER.tailscaled"
        fi
        sudo launchctl bootstrap system "$TAILSCALE_PLIST_DEST"
    fi
    unset needs_bootstrap

    # claude-code-router (ccr): backs claude-{fast,private,think} wrappers.
    # Supervised by launchd so it's running before any wrapper invocation
    # and respawned if it crashes.
    CCR_PLIST_DEST="$HOME/Library/LaunchAgents/com.turntrout.ccr.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/com.turntrout.ccr"
    safe_link "$DOTFILES_DIR/claude-guard/launchagents/com.turntrout.ccr.plist" "$CCR_PLIST_DEST"
    launchctl bootout "gui/$(id -u)" "$CCR_PLIST_DEST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$CCR_PLIST_DEST" 2>/dev/null || true

    # Tailscale exit-node applier: reasserts the configured Mullvad exit node
    # at login, retrying while tailscaled finishes its handshake.
    # The plist bakes in absolute /Users/$USER paths, so render it from the
    # __USERNAME__ template — same convention as tailscaled/sudoers above.
    TS_EXIT_PLIST_DEST="$HOME/Library/LaunchAgents/com.turntrout.tailscale-exit-node.plist"
    mkdir -p "$HOME/Library/Logs/com.turntrout.tailscale-exit-node"
    TS_EXIT_PLIST_RENDERED="$(mktemp)"
    trap 'rm -f "$TS_EXIT_PLIST_RENDERED"' EXIT
    sed "s/__USERNAME__/$ESCAPED_USER/g" \
        "$DOTFILES_DIR/launchagents/com.turntrout.tailscale-exit-node.plist.template" \
        >"$TS_EXIT_PLIST_RENDERED"
    if [ ! -f "$TS_EXIT_PLIST_DEST" ] || ! cmp -s "$TS_EXIT_PLIST_RENDERED" "$TS_EXIT_PLIST_DEST"; then
        install -m 0644 "$TS_EXIT_PLIST_RENDERED" "$TS_EXIT_PLIST_DEST"
    fi
    rm -f "$TS_EXIT_PLIST_RENDERED"
    trap - EXIT
    launchctl bootout "gui/$(id -u)" "$TS_EXIT_PLIST_DEST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$TS_EXIT_PLIST_DEST" 2>/dev/null || true

    # Install wally-cli for keyboard flashing. Non-fatal: doctor reports it
    # as an optional skip, so a flaky download must not abort setup here.
    if ! command_exists wally-cli && command_exists go; then
        go install github.com/zsa/wally-cli@latest >/dev/null ||
            status_msg "WARN: 'go install wally-cli' failed; rerun setup.bash to retry."
    fi

    # iTerm2 shell integration. Non-fatal for the same reason.
    if [ ! -f "$HOME/.iterm2_shell_integration.bash" ]; then
        curl -fsSL https://iterm2.com/shell_integration/install_shell_integration_and_utilities.sh | bash >/dev/null ||
            status_msg "WARN: iTerm2 shell integration install failed; rerun setup.bash to retry."
    fi

    # iTerm2 reads/writes preferences from the tracked repo copy via "Load
    # preferences from a custom folder" (takes effect on next iTerm2 launch).
    # No symlink here: iTerm2 saves prefs atomically (write temp + rename),
    # which would replace a symlink with a real file and silently de-track it —
    # hence pointing PrefsCustomFolder at the repo directory itself. The old
    # ~/Library symlink from that scheme is retired below.
    defaults write com.googlecode.iterm2 PrefsCustomFolder -string "$DOTFILES_DIR/apps"
    defaults write com.googlecode.iterm2 LoadPrefsFromCustomFolder -bool true
    if [ -L "$HOME/Library/com.googlecode.iterm2.plist" ]; then
        rm -f "$HOME/Library/com.googlecode.iterm2.plist"
    fi

else # Assume linux
    status_msg "Installing Linux packages..."

    retry 3 10 sudo apt-get update -qq ||
        status_msg "WARN: apt-get update failed after 3 attempts; package installs below may fail."
    # libsecret-tools provides `secret-tool`, the Linux equivalent of macOS
    # `security` used by bin/lib/secret-store.sh for caching bw credentials.
    retry 3 10 sudo apt-get install -y -qq python3-pynvim pipx cron libsecret-tools ||
        status_msg "WARN: apt-get install failed after 3 attempts; rerun setup.bash to retry."
fi

# Install CLI tools via uv (not in Brewfile -- they're Python packages).
# retry+WARN like the other installers so a transient PyPI blip doesn't abort
# setup before doctor.bash runs — doctor reports either tool if still missing.
if ! command_exists trash-put; then
    retry 3 5 uv tool install --quiet trash-cli ||
        status_msg "WARN: 'uv tool install trash-cli' failed after 3 attempts — doctor.bash will report trash-put missing."
fi

if command_exists uv; then
    retry 3 5 uv tool install --quiet pre-commit --with pre-commit-uv ||
        status_msg "WARN: 'uv tool install pre-commit' failed after 3 attempts — doctor.bash will report pre-commit missing."
fi

# Clear trash which is over 30 days old, monthly
if command_exists crontab && command_exists trash-empty; then
    CRON_ENTRY="@monthly $(command -v trash-empty) 30"
    if [ "$(crontab -l 2>/dev/null | grep "trash-empty" || true)" != "$CRON_ENTRY" ]; then
        # `grep -v` exits 1 on empty input; under `set -e`/pipefail that would
        # abort with an EMPTY crontab installed. `|| true` keeps the existing
        # entries (minus any stale trash-empty line) and appends the new one.
        {
            crontab -l 2>/dev/null | grep -v "trash-empty" || true
            echo "$CRON_ENTRY"
        } | crontab -
    fi
fi

status_msg "Setting up tmux..."
# Tmux plugin manager setup (must come after tmux is installed)
TPM_DIR="$HOME/.tmux/plugins/tpm"
if [ ! -d "$TPM_DIR/.git" ]; then
    rm -rf "$TPM_DIR"
    retry 3 5 git clone --quiet https://github.com/tmux-plugins/tpm "$TPM_DIR" >/dev/null ||
        status_msg "WARN: tpm clone failed after 3 attempts — rerun setup.bash."
fi
tmux source ~/.tmux.conf >/dev/null 2>&1 || true
~/.tmux/plugins/tpm/bin/install_plugins >/dev/null ||
    status_msg "WARN: TPM plugin install failed; rerun ~/.tmux/plugins/tpm/bin/install_plugins manually"

strip_pnpm_from_shell_configs() {
    local file
    for file in "$HOME/.bashrc" "$HOME/.zshrc" \
        "$DOTFILES_DIR/apps/fish/config.fish" \
        "$DOTFILES_DIR/.bashrc"; do
        [[ -f "$file" ]] || continue
        if grep -q '^# pnpm$' "$file" 2>/dev/null; then
            if [[ "$(uname)" == "Darwin" ]]; then
                sed -i '' '/^# pnpm$/,/^# pnpm end$/d' "$file"
            else
                sed -i '/^# pnpm$/,/^# pnpm end$/d' "$file"
            fi
        fi
    done
}

write_pnpm_extras() {
    local bash_file="$HOME/.extras.bash"
    local fish_file="$HOME/.extras.fish"

    if ! grep -q '^# pnpm$' "$bash_file" 2>/dev/null; then
        cat >>"$bash_file" <<'BASH_PNPM'

# pnpm
if [ "$(uname)" = "Darwin" ]; then
    export PNPM_HOME="$HOME/Library/pnpm"
else
    export PNPM_HOME="$HOME/.local/share/pnpm"
fi
case ":$PATH:" in
*":$PNPM_HOME:"*) ;;
*) export PATH="$PNPM_HOME:$PATH" ;;
esac
case ":$PATH:" in
*":$PNPM_HOME/bin:"*) ;;
*) export PATH="$PNPM_HOME/bin:$PATH" ;;
esac
# pnpm end
BASH_PNPM
    fi

    if ! grep -q '^# pnpm$' "$fish_file" 2>/dev/null; then
        cat >>"$fish_file" <<'FISH_PNPM'

# pnpm
if test (uname) = Darwin
    set -gx PNPM_HOME "$HOME/Library/pnpm"
else
    set -gx PNPM_HOME "$HOME/.local/share/pnpm"
end
if not string match -q -- "$PNPM_HOME" $PATH
    set -gx PATH "$PNPM_HOME" $PATH
end
if not string match -q -- "$PNPM_HOME/bin" $PATH
    set -gx PATH "$PNPM_HOME/bin" $PATH
end
# pnpm end
FISH_PNPM
    fi
}

if command_exists pnpm; then
    write_pnpm_extras
    strip_pnpm_from_shell_configs
    if [ "$(uname)" = "Darwin" ]; then
        export PNPM_HOME="${PNPM_HOME:-$HOME/Library/pnpm}"
    else
        export PNPM_HOME="${PNPM_HOME:-$HOME/.local/share/pnpm}"
    fi
    mkdir -p "$PNPM_HOME/bin"
    case ":$PATH:" in
    *":$PNPM_HOME/bin:"*) ;;
    *) export PATH="$PNPM_HOME/bin:$PATH" ;;
    esac
    case ":$PATH:" in
    *":$PNPM_HOME:"*) ;;
    *) export PATH="$PNPM_HOME:$PATH" ;;
    esac
    retry 3 5 pnpm install -g prettier ||
        status_msg "WARN: 'pnpm install -g prettier' failed; formatting may be unavailable"
    retry 3 5 pnpm install -g @bitwarden/cli ||
        status_msg "WARN: 'pnpm install -g @bitwarden/cli' failed; rerun to get bw CLI"
fi

# Make sure mise has a Node 22 install available for bin/bw-node, which
# pins Node 22 to avoid the inquirer/readline crash on Node 26+. Doesn't
# change the user's per-project Node default.
if command_exists mise; then
    if ! mise_err="$(mise install node@22 2>&1)"; then
        status_msg "WARN: mise install node@22 failed; bin/bw-node will fall back to PATH's node."
        [[ -n "$mise_err" ]] && printf '%s\n' "$mise_err" >&2
    fi
    unset mise_err
fi

# devcontainer CLI — used by the host-side `claude` wrappers
# (claude-guard/bin/claude and apps/fish/functions/claude.fish)
# to bring up .devcontainer/ on demand.
# pnpm is configured above (PNPM_HOME + PATH), so this lands alongside the
# other globals (claude-code, ccr, prettier, @bitwarden/cli).
if command_exists pnpm; then
    retry 3 5 pnpm add --global @devcontainers/cli >/dev/null 2>&1 ||
        status_msg "WARN: 'pnpm add -g @devcontainers/cli' failed. The claude wrapper will fall back to running on the host."
fi

# AI tooling: claude-code + ccr, aider, VSCodium + Roo, wut, llm
# commit-msg hook, Venice default_code resolver cache.
bash "$DOTFILES_DIR/bin/setup_llm.bash"

if [ "$(uname)" != "Darwin" ] && ! command_exists xmllint; then
    retry 3 10 sudo apt-get install -y libxml2-utils ||
        status_msg "WARN: 'apt-get install libxml2-utils' failed after 3 attempts — doctor.bash will report xmllint missing."
fi

# Backup existing neovim data (only on first run — skip if .bak already exists to
# avoid clobbering the original pre-dotfiles snapshot on repeated setup.bash runs).
for directory in ~/.local/{share,state}/nvim ~/.cache/nvim; do
    [[ -d "$directory" && ! -d "${directory}.bak" ]] || continue
    cp -r "$directory" "${directory}.bak" >/dev/null 2>&1 || true
done

status_msg "Setup complete. Running doctor.bash..."
bash "$DOTFILES_DIR/bin/doctor.bash" --no-refresh || true
