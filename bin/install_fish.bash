#!/bin/bash
set -euo pipefail

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

if ! command_exists fish; then
    echo ":: Installing fish shell..."
    brew install --quiet fish
fi

# Ensure fish is in PATH (brew may install to /opt/homebrew/bin on Apple Silicon)
if ! command_exists fish; then
    for brew_prefix in /opt/homebrew /usr/local /home/linuxbrew/.linuxbrew; do
        if [ -x "$brew_prefix/bin/fish" ]; then
            export PATH="$brew_prefix/bin:$PATH"
            break
        fi
    done
fi

if ! command_exists fish; then
    echo "Error: fish shell not found after installation." >&2
    exit 1
fi

# Set the correct permissions for the Fish configuration directory
# Use SUDO_USER when running under sudo so we don't chown everything to root
REAL_USER="${SUDO_USER:-$USER}"
# Only chown if something inside is owned by another user (avoids a spurious
# sudo password prompt on every re-run when ownership is already correct).
if [ -d "$HOME/.config/fish" ] &&
    find "$HOME/.config/fish" ! -user "$REAL_USER" -print -quit 2>/dev/null | grep -q .; then
    sudo chown -R "$REAL_USER" "$HOME/.config/fish" || true
fi

# Set Fish as the default shell (skip if already set)
FISH_PATH=$(command -v fish)
grep -qxF "$FISH_PATH" /etc/shells || echo "$FISH_PATH" | sudo tee -a /etc/shells >/dev/null

# Detect the user's actual login shell from the password DB rather than $SHELL
# ($SHELL reflects the parent process and can be misleading inside tmux/scripts).
if [ "$(uname)" = "Darwin" ]; then
    current_login_shell=$(dscl . -read "/Users/${REAL_USER}" UserShell | awk '{print $2}')
else
    current_login_shell=$(getent passwd "$REAL_USER" | cut -d: -f7)
fi

if [ "$current_login_shell" = "$FISH_PATH" ]; then
    echo ":: Login shell already set to $FISH_PATH; skipping chsh."
else
    # When running as root (via sudo), pass $REAL_USER so we change the right
    # user's shell. As a non-root user, passing a username argument causes PAM
    # to reject the call on many Linux distros, so omit it in that case.
    if [ "$(id -u)" -eq 0 ]; then
        chsh -s "$FISH_PATH" "$REAL_USER" || echo ":: chsh failed — set fish as login shell manually with 'chsh -s $FISH_PATH'." >&2
    else
        chsh -s "$FISH_PATH" || echo ":: chsh failed — set fish as login shell manually with 'chsh -s $FISH_PATH'." >&2
    fi
fi

# Skip fisher/tide install if tide is already present
if fish -c 'functions -q tide' 2>/dev/null; then
    echo ":: tide already installed; skipping fisher/tide setup."
    tide_already_configured=1
else
    tide_already_configured=0
    # Remove conflicting fish_prompt.fish before tide install (tide provides its own)
    rm -f "$HOME/.config/fish/functions/fish_prompt.fish"

    echo ":: Installing fish plugins..."
    # Non-fatal: a network blip here must not abort the whole setup run.
    # Everything downstream tolerates a missing tide (the tide tweaks below
    # are already || true, and the preset prompt questions gate on
    # tide_already_configured).
    if ! fish <<FISH_SCRIPT
      curl -fsSL https://raw.githubusercontent.com/jorgebucaran/fisher/main/functions/fisher.fish | source
      fisher install jorgebucaran/fisher >/dev/null

      fisher install IlanCosman/tide@v6 >/dev/null
FISH_SCRIPT
    then
        echo ":: WARN: fisher/tide install failed (network?); rerun bin/install_fish.bash to retry." >&2
    fi
fi

# Drop `jobs` from tide's right prompt: the bundled _tide_item_jobs trips on
# non-numeric values from background daemons (e.g. tailscaled). Strip from
# both the canonical config (tide_right_prompt_items) and the runtime list
# (_tide_right_items) — otherwise _tide_remove_unusable_items rebuilds the
# runtime list from the canonical one on the next prompt and reintroduces it.
fish -c '_tide_find_and_remove jobs tide_right_prompt_items; _tide_find_and_remove jobs _tide_right_items' 2>/dev/null || true

# Resolve DOTFILES_DIR from this script's location (bin/ is one level down)
DOTFILES_DIR="$(cd "$(dirname "$0")/.." && pwd)"
bash "$DOTFILES_DIR"/bin/font_install.bash

# Create Fish configuration directory if it doesn't exist
FISH_CONFIG_DIR="$HOME/.config/fish"
mkdir -p "$FISH_CONFIG_DIR/functions"

if [ "$tide_already_configured" = "1" ]; then
    echo ":: tide already configured; leaving existing settings untouched."
else
    # See if user wants preset settings
    read -rp "Accept preset tide settings? (Y/n) " answer || true

    if [ -z "$answer" ] || echo "$answer" | grep -iq "^y"; then
        # Populate the fish config dir from the repo, but DON'T clobber
        # files that already exist. Two classes of "already exist":
        #   (a) symlinks placed by setup.bash → managed_symlinks
        #       (config.fish, completions/dotfiles.fish, conf.d/*.fish):
        #       leaving these as-is means the repo stays the source of
        #       truth and the resymlink dance below is unnecessary.
        #   (b) fisher/tide-managed files (functions/{fisher,tide}.fish,
        #       completions/{fisher,tide}.fish, fish_plugins) that the
        #       user may have updated via `fisher install`: keep their
        #       version rather than reverting to the bundled snapshot.
        # Earlier this was `cp -rf` (clobbering both), then `cp -Rn`
        # (no-clobber, correct semantics) — but GNU coreutils 9.x emits
        # a deprecation warning for `-n`, and the suggested replacement
        # `--update=none` is GNU-only. find + per-file cp is portable.
        while IFS= read -r -d '' src; do
            rel=${src#"$DOTFILES_DIR/apps/fish/"}
            dst="$FISH_CONFIG_DIR/$rel"
            [ -e "$dst" ] || { mkdir -p "$(dirname "$dst")" && cp -P "$src" "$dst"; }
        done < <(find "$DOTFILES_DIR/apps/fish" \( -type f -o -type l \) -print0 2>/dev/null)
        # fish_prompt.fish isn't in managed_symlinks (the decline-presets
        # branch below runs `tide configure`, which writes the user's
        # generated prompt here — we don't want to symlink-trap that).
        # In the accept-presets branch we DO want the symlink so repo
        # updates propagate, so place it explicitly here.
        ln -sf "$DOTFILES_DIR"/apps/fish/functions/fish_prompt.fish "$FISH_CONFIG_DIR/functions/fish_prompt.fish"
    else
        fish -c "tide configure"
    fi
fi
# Clear any stale dangling symlink left by older versions of this script.
if [ -L "$FISH_CONFIG_DIR/functions/_tide_item_jobs.fish" ] &&
    [ ! -e "$FISH_CONFIG_DIR/functions/_tide_item_jobs.fish" ]; then
    rm -f "$FISH_CONFIG_DIR/functions/_tide_item_jobs.fish"
fi

fish "$DOTFILES_DIR"/bin/install_fish_plugins.fish >/dev/null
