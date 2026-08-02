# Core tools
brew "gh"
brew "fish"
brew "neovim"
brew "tmux"
brew "mosh"
brew "envchain"
# Bitwarden CLI is installed via pnpm (@bitwarden/cli) — see setup.bash.
# The Rust brew flavor has scripting quirks our bin/bw-*.bash can't dodge.
brew "safe-rm"
brew "gcc"
brew "python"
brew "uv"
brew "node"
brew "pnpm"
brew "zoxide"
brew "shellcheck"
brew "fish-lsp"
brew "gitleaks"   # pre-push secrets scan; CI runs it too
# pre-commit installed in setup.bash via uv (needs the pre-commit-uv plugin).

# Modern Unix toolkit
brew "fzf"
brew "ripgrep"
brew "fd"
brew "jq"
brew "bat"
brew "eza"
brew "git-delta"
brew "tokei"      # LOC counter
brew "dust"       # du replacement (du -h with bars)
brew "bottom"     # htop/btop replacement; binary is `btm`
brew "mise"
brew "carapace"

# Charm AI: pipe shell output through an LLM. Routes through Venice (E2EE
# inference) only — see apps/mods/mods.yml. Wrapped by `mods` fish function
# in config.fish so envchain ai populates VENICE_INFERENCE_KEY.
brew "charmbracelet/tap/mods"

# Formatters (conform.nvim, lint.bash)
brew "stylua"
brew "ruff"
brew "shfmt"

# macOS-only
if OS.mac?
  tap "yakitrak/yakitrak"
  tap "rishikanthc/scriberr"
  tap "felixkratz/formulae"

  # Formulae
  brew "coreutils"
  brew "pyvim"
  brew "wget"
  brew "libusb"
  brew "pkg-config"
  brew "go"
  brew "tailscale"
  brew "duti"
  brew "git"
  brew "colima"
  brew "docker"
  brew "docker-buildx"
  brew "docker-compose"
  brew "fortune"
  brew "lolcat"
  brew "cowsay"
  brew "toilet"
  brew "yakitrak/yakitrak/obsidian-cli"
  brew "rishikanthc/scriberr/scriberr"

  # Fonts
  cask "font-fira-code-nerd-font"

  # Casks
  cask "orbstack"
  cask "hiddenbar"
  cask "bitwarden"
  cask "obsidian"
  cask "iterm2"
  cask "anki"
  cask "contexts"
  cask "zotero"
  cask "steam"
  cask "rectangle-pro"
  cask "superkey"
  cask "quitter"
  cask "jettison"
  cask "brave-browser"
  cask "tor-browser"
  cask "vscodium"
  cask "duplicati"
  cask "ente"
  cask "lookaway"
  cask "mullvad-vpn"
else 
  brew "xclip"
end
