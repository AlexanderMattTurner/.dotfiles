# shellcheck shell=bash
# bin/lib/symlinks.sh — shared lists of managed symlinks.
#
# Each emitted line is `target|source|label`, pipe-delimited. Callers iterate
# with `while IFS='|' read -r target source label; do …; done < <(managed_symlinks)`.
# DOTFILES_DIR must be set in the caller before invoking.
#
# Two lists:
#   * managed_symlinks   — user-facing dotfiles whose targets live under $HOME.
#                          Mirrored by uninstall.bash for backup-restoring removal.
#   * repo_hook_symlinks — repo-internal git-hook symlinks under $DOTFILES_DIR/.hooks/.
#                          Relative sources, not in $HOME; uninstall leaves these alone.
#
# Maintenance invariant: every safe_link call in setup.bash should iterate one
# of these lists rather than hardcoding the pair. Genuinely bespoke entries
# (the ccr launch agent under claude-guard/) stay inline.
managed_symlinks() {
    cat <<EOF
$HOME/.bashrc|$DOTFILES_DIR/.bashrc|.bashrc
$HOME/.vimrc|$DOTFILES_DIR/.vimrc|.vimrc
$HOME/.gitconfig|$DOTFILES_DIR/.gitconfig|.gitconfig
$HOME/.tmux.conf|$DOTFILES_DIR/.tmux.conf|.tmux.conf
$HOME/.npmrc|$DOTFILES_DIR/.npmrc|.npmrc
$HOME/.pnpmrc|$DOTFILES_DIR/.pnpmrc|.pnpmrc
$HOME/.config/fish/config.fish|$DOTFILES_DIR/apps/fish/config.fish|fish config
$HOME/.config/fish/fish_plugins|$DOTFILES_DIR/apps/fish/fish_plugins|fish_plugins (fisher manifest)
$HOME/.config/fish/completions/dotfiles.fish|$DOTFILES_DIR/apps/fish/completions/dotfiles.fish|dotfiles fish completion
$HOME/.config/fish/conf.d/carapace.fish|$DOTFILES_DIR/apps/fish/conf.d/carapace.fish|carapace conf.d snippet
$HOME/.config/fish/conf.d/mise.fish|$DOTFILES_DIR/apps/fish/conf.d/mise.fish|mise conf.d snippet
$HOME/.local/bin/dotfiles|$DOTFILES_DIR/bin/dotfiles|dotfiles dispatcher
$HOME/.config/mods/mods.yml|$DOTFILES_DIR/apps/mods/mods.yml|mods config
$HOME/.ssh/config|$DOTFILES_DIR/apps/ssh/config|ssh config
$HOME/.config/mise/config.toml|$DOTFILES_DIR/apps/mise/config.toml|mise config
$HOME/.config/nvim|$DOTFILES_DIR/apps/nvim|nvim config
$HOME/.local/bin/bw-node|$DOTFILES_DIR/bin/bw-node|bw-node wrapper
$HOME/.claude/settings.json|$DOTFILES_DIR/apps/claude-user/settings.json|Claude Code settings
$HOME/.claude/CLAUDE.md|$DOTFILES_DIR/claude-guard/user-config/CLAUDE.md|Claude Code global CLAUDE.md
$HOME/.claude/commands|$DOTFILES_DIR/claude-guard/user-config/skills|Claude Code slash-command dir
$HOME/.devcontainer|$DOTFILES_DIR/.devcontainer|.devcontainer
EOF
    if [[ "$(uname)" == "Darwin" ]]; then
        cat <<EOF
$HOME/.aerospace.toml|$DOTFILES_DIR/.aerospace.toml|.aerospace.toml
$HOME/.config/swiftbar/vpn.10s.bash|$DOTFILES_DIR/apps/swiftbar/vpn.10s.bash|swiftbar vpn plugin
$HOME/Library/Application Support/VSCodium/User/settings.json|$DOTFILES_DIR/apps/vscodium/settings.json|VSCodium settings (macOS)
$HOME/Library/Application Support/VSCodium/User/keybindings.json|$DOTFILES_DIR/apps/vscodium/keybindings.json|VSCodium keybindings (macOS)
EOF
    else
        cat <<EOF
$HOME/.config/VSCodium/User/settings.json|$DOTFILES_DIR/apps/vscodium/settings.json|VSCodium settings (Linux)
$HOME/.config/VSCodium/User/keybindings.json|$DOTFILES_DIR/apps/vscodium/keybindings.json|VSCodium keybindings (Linux)
EOF
    fi
    for aider_file in "$DOTFILES_DIR"/.aider*; do
        [[ -f "$aider_file" ]] || continue
        local name
        name="$(basename "$aider_file")"
        printf '%s|%s|%s\n' "$HOME/$name" "$aider_file" "$name"
    done
}

# Repo-internal git-hook symlinks. Sources are *relative* link contents
# (e.g. ../bin/pre-push) so the link remains valid wherever the repo is
# cloned. These targets are under $DOTFILES_DIR, not $HOME — uninstall.bash
# intentionally ignores them.
repo_hook_symlinks() {
    cat <<EOF
$DOTFILES_DIR/.hooks/pre-push|../bin/pre-push|.hooks/pre-push
$DOTFILES_DIR/.hooks/prepare-commit-msg|../bin/.prepare-commit-msg|.hooks/prepare-commit-msg
EOF
}
