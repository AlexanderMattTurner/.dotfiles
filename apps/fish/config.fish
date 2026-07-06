#!/usr/bin/env fish

# Kill detached tmux sessions with auto-numbered names. Run before reboot to
# keep continuum's snapshot from accumulating orphan sessions across restores.
# Named sessions (`main`, `website`, ...) are preserved.
function tmux-prune --description 'Kill detached, auto-numbered tmux sessions'
    set -l killed 0
    for line in (tmux list-sessions -F '#{session_name} #{session_attached}' 2>/dev/null)
        set -l parts (string split ' ' -- $line)
        if test "$parts[2]" = 0; and string match -qr '^[0-9]+$' -- $parts[1]
            tmux kill-session -t $parts[1]
            set killed (math $killed + 1)
        end
    end
    echo "Pruned $killed detached auto-numbered session(s)."
end

# Put homebrew on PATH before anything that needs it (e.g. the tmux auto-launch
# below). Cold-start login fish from `/usr/bin/login` only has the path_helper
# defaults on PATH, which don't include /opt/homebrew/bin.
if test (uname) = Darwin; and test -x /opt/homebrew/bin/brew
    eval "$(/opt/homebrew/bin/brew shellenv)"
else if test -d /home/linuxbrew/.linuxbrew
    fish_add_path /home/linuxbrew/.linuxbrew/bin /home/linuxbrew/.linuxbrew/sbin
end

# Auto-launch tmux if not already inside a tmux session.
# First iTerm2 window after reboot: no server -> start one, attach to `main`
# (continuum-restore fires here). Subsequent windows: server is up, so spawn a
# fresh independent session per window for parallel layouts.
# Escape hatch: set NO_AUTO_TMUX (any value) to skip the exec — editor-embedded
# terminals, one-off interactive shells, and remote sessions don't want it.
# One shell:  NO_AUTO_TMUX=1 fish     Permanently:  set -Ux NO_AUTO_TMUX 1
if status is-interactive; and not set -q TMUX; and not set -q NO_AUTO_TMUX; and command -q tmux
    if tmux has-session 2>/dev/null
        exec tmux new-session
    else
        exec tmux new-session -A -s main
    end
end

# No default greeting
set fish_greeting ''

set -l _self_dir (dirname (realpath (status filename)))
# config.fish is re-sourced by tide's async prompt subprocess on every repaint,
# so this git call runs once per prompt. Suppress its stderr (a bare copy of the
# dotfiles, or git not on PATH, would otherwise leak "fatal: not a git
# repository" into the terminal each enter) and fall back to apps/fish's parent.
set -gx DOTFILES_DIR (git -C $_self_dir rev-parse --show-toplevel 2>/dev/null; or realpath $_self_dir/../..)

# These elements don't scale with font size
set --universal tide_right_prompt_prefix ''
set --universal tide_left_prompt_suffix ''

abbr -a goosesay "cowsay -f $DOTFILES_DIR/apps/goose.cow"

# Check if the operating system is macOS and set IS_MAC flag
if test (uname) = Darwin
    set IS_MAC true
else
    set IS_MAC false
end

# Reattach to macOS launchd ssh-agent (tmux strips SSH_AUTH_SOCK)
if $IS_MAC; and test -z "$SSH_AUTH_SOCK"
    for sock in /private/tmp/com.apple.launchd.*/Listeners
        if test -S "$sock"
            set -gx SSH_AUTH_SOCK "$sock"
            break
        end
    end
end

# Custom settings
fish_vi_key_bindings

# zoxide (replaces autojump). `--cmd j` keeps the long-standing `j <dir>` workflow.
if command -q zoxide
    zoxide init fish --cmd j | source
end

if $IS_MAC
    if command -q AeroSpace
        # Custom goodness for my workflow https://nikitabobko.github.io/AeroSpace/goodness
        defaults write -g NSWindowShouldDragOnGesture YES

        # Disable windows opening animations
        defaults write -g NSAutomaticWindowAnimationsEnabled -bool false
    end
end

abbr -a e exit

function findfile --description 'Find files by name pattern'
    find . -type f -iname "*$argv*" 2>/dev/null
end

abbr -a editbashrc 'nvim ~/.bashrc'
abbr -a brc editbashrc

abbr -a fconf 'nvim ~/.config/fish/config.fish'
abbr -a editfishconfig 'nvim ~/.config/fish/config.fish'

function crontab
    env VISUAL=nvim command crontab $argv
end

function ls
    # eza is a drop-in for the most common ls flags (-l, -a, -A, -F).
    # Falls back to gls (coreutils) on macOS / bsd ls otherwise.
    if command -q eza
        eza --color=always --git --icons=auto $argv
    else if command -q gls
        gls --color="always" --ignore-backups --hide="*.bak" $argv
    else if $IS_MAC
        command ls -G $argv
    else
        command ls --color="always" --ignore-backups --hide="*.bak" $argv
    end
end

abbr -a ll 'ls -lF'
abbr -a la 'ls -laF'
abbr -a lt 'ls --tree --level=2'

function ms --description 'Connect via mosh (UDP-persistent; falls back to ssh)'
    if type -q mosh
        mosh $argv
    else
        command ssh $argv
    end
end

if $IS_MAC
    function finder
        open $argv
    end
end

function flash
    bash "$DOTFILES_DIR/bin/keyboard_flash.bash"
end

function pytest
    python -m pytest $argv
end

# Ensure current python env's pip is used
function pip
    python -m pip $argv
end

# Clipboard function differs between macOS and others
function yank
    if set -q SSH_TTY
        set -l tmp (mktemp)
        command cat >$tmp
        set -l data (base64 < $tmp | tr -d '\n')
        command rm $tmp &>/dev/null
        printf '\033]52;c;%s\a' $data
    else if $IS_MAC
        pbcopy
    else
        xclip -selection clipboard
    end
end

# Disable automatic paste bracketing in fish
if $IS_MAC
    set fish_clipboard_copy_cmd pbcopy
    set fish_clipboard_paste_cmd pbpaste
else
    set fish_clipboard_copy_cmd 'xclip -selection clipboard'
    set fish_clipboard_paste_cmd 'xclip -selection clipboard -o'
end

function get_ps
    echo (whoami)'@'(hostname)': '(pwd)
end

# Git aliases
abbr -a gac 'git add -A && git commit -m'
abbr -a gs 'git status'
abbr -a ga 'git add'
abbr -a gb 'git branch'
abbr -a gc 'git commit'
abbr -a gcm 'git commit -m'
abbr -a gca 'git commit --amend --no-edit'
abbr -a gr 'git restore'
abbr -a grs 'git restore --staged'
abbr -a gd 'git diff'
abbr -a gco 'git checkout'
abbr -a uncommit 'git reset --soft HEAD^'
abbr -a ghnew 'gh repo create --template alexander-turner/claude-automation-template --clone'

function gk
    gitk --all &
end

# Merge changes onto main and push
function merge_and_push
    git switch main
    and git pull
    and git merge -
    and git push
    and git switch -
end

# Run post-push hook after git push
function push
    # Store the original exit status after git push
    git push $argv
    set -l push_status $status

    # Find git root directory
    set -l git_root (git rev-parse --show-toplevel 2>/dev/null)
    if test $push_status -eq 0; and test -n "$git_root"
        set -l post_push_hook "$git_root/.git/hooks/post-push"

        # Check if post-push hook exists and is executable
        if test -x "$post_push_hook"
            echo "Running post-push hook..."
            $post_push_hook
        end
    end

    # Return the original push status
    return $push_status
end

fish_add_path ~/bin ~/.local/bin /usr/local/go/bin
set -gx EDITOR nvim
set -gx SHELL (status fish-path)

abbr -a n nvim

# Trash-cli aliases 
abbr -a tp trash-put
abbr -a tl trash-list

function rm
    if command -q trash-put
        echo "rm is disabled; using the reversible 'trash-put' instead (aliased to 'tp'). To force rm, use 'command rm'." >&2
        trash-put $argv
    else
        echo "rm is disabled and 'trash-put' is not installed (uv tool install trash-cli). Refusing to delete — run 'command rm' yourself if you really mean it." >&2
        return 1
    end
end

# Load iTerm2 integration before the grep/cat shadows so its internal
# `grep -cvE` call hits the real grep rather than rg.
if test -e $HOME/.iterm2_shell_integration.fish
    if set -q TMUX
        source $HOME/.iterm2_shell_integration.fish
    end
end

# Interactive shadows: prefer ripgrep / bat when present. Bash scripts and
# subshells still get the real binaries (fish functions don't propagate).
# Escape hatch when a pasted invocation needs the real grep/cat: prefix
# with `command` (`command grep -P ...`) or backslash (`\grep`).
function grep
    if command -q rg
        rg $argv
    else
        command grep $argv --exclude="*~" --color=auto
    end
end

function cat
    if command -q bat
        bat $argv
    else
        command cat $argv
    end
end

abbr pytest_diff 'pytest -vv --tb=short'

function grp --description 'Recursively grep from current directory'
    if command -q rg
        rg $argv
    else
        # grep -r with no path defaults to '.', matching rg's behaviour
        command grep -r --exclude="*~" --color=auto $argv
    end
end

# Printing helpers 
function echo_color
    echo (set_color $argv[1])$argv[2](set_color normal)
end

# Run extra commands
set CONFIG_PATH ~/.extras.fish
touch $CONFIG_PATH
source $CONFIG_PATH

# Do NOT put API keys in here 
function editfishextras
    nvim $CONFIG_PATH $argv
end

abbr -a fxtra editfishextras

set -xg NODE_NO_WARNINGS 1

# Secret wrappers: envchain reads secrets from the macOS Keychain, which is
# auto-unlocked at GUI login (zero-prompt runtime). Bitwarden is the
# cross-machine source of truth; values are pulled into envchain by
# bin/bw-seed-envchain.bash (run on demand via `bwseed` and as a throttled
# shell-startup background job below). See README for the data flow.

function ai_secrets_wrap
    envchain ai $argv
end

# Charm `mods`: pipe shell output through an LLM. Routes exclusively
# through Venice (E2EE inference) per apps/mods/mods.yml. envchain ai
# populates VENICE_INFERENCE_KEY from the macOS Keychain.
#   git diff | mods 'review for issues'
function mods
    envchain ai command mods $argv
end

function npm
    envchain npm command npm $argv
end

function rclone
    envchain cloudflare command rclone $argv
end

function twine
    envchain pypi command twine $argv
end

# Aider via Venice (E2EE — the only permitted provider; see CLAUDE.md
# "AI provider routing"). envchain populates VENICE_INFERENCE_KEY into the
# bash -c's environment; the inline remap onto OPENAI_API_KEY (what
# aider's litellm backend reads for openai-compatible providers) happens
# there, at runtime, so the key never touches fish variable expansion or
# argv — no separate shim script needed for a three-var remap.
function aider_venice
    envchain ai bash -c 'OPENAI_API_KEY=$VENICE_INFERENCE_KEY OPENAI_API_BASE=https://api.venice.ai/api/v1 AIDER_MODEL=openai/claude-sonnet-4-6 exec "$0" --edit-format editor-diff "$@"' (type -p aider) $argv
end

# llm via Venice — same inline remap. Default model (venice-sonnet) is
# written by bin/setup_llm.bash into llm's extra-openai-models.yaml.
function llm
    envchain ai bash -c 'OPENAI_API_KEY=$VENICE_INFERENCE_KEY OPENAI_API_BASE=https://api.venice.ai/api/v1 exec "$0" "$@"' (command -s llm) $argv
end

# ── Bitwarden sync helpers ────────────────────────────────────────────────
# bw is the source of truth across machines; envchain is the runtime cache.
# `bwseed` pulls vault → envchain. `bwadd` adds a new secret to both at once.
# Auto-sync on shell startup is throttled by ~/.cache/bw-envchain-sync.stamp.

function bwseed --description 'Refresh envchain from Bitwarden vault'
    bash "$DOTFILES_DIR/bin/bw-seed-envchain.bash" $argv
end

function bwadd --description 'Add a new secret to Bitwarden + envchain'
    bash "$DOTFILES_DIR/bin/bw-add-secret.bash" $argv
end

function _bw_envchain_autosync
    # Throttle: skip if last successful run was within the past 6h.
    # stat syntax differs across platforms; try GNU (-c %Y) then BSD (-f %m).
    # Either errors when the stamp doesn't exist; the `or echo 0` forces
    # mtime=0 in that case so the throttle fails through to a sync.
    set -l stamp $HOME/.cache/bw-envchain-sync.stamp
    set -l interval 21600
    mkdir -p (path dirname $stamp) 2>/dev/null
    set -l mtime (stat -c %Y $stamp 2>/dev/null; or stat -f %m $stamp 2>/dev/null; or echo 0)
    if test (math (date +%s) - $mtime) -lt $interval
        return 0
    end
    fish -c "if bash '$DOTFILES_DIR/bin/bw-seed-envchain.bash' --quiet >/dev/null 2>&1; touch '$stamp'; end" &
    disown
end

if status is-interactive; and type -q bw
    _bw_envchain_autosync 2>/dev/null
end

# Tailscale's Mullvad exit node — choice persists in daemon prefs across reboots.
# Routes set/clear through tailscale-set-exit-node.bash so the exit-node list
# and daemon health checks live in one place (bin/lib/tailscale-resolve.sh).
function mullvad --description 'Switch Tailscale Mullvad exit node'
    if test (count $argv) -eq 0
        echo "usage: mullvad [ca|jp|us|off|ls|st]" >&2
        return 1
    end
    switch $argv[1]
        case ls list
            tailscale exit-node list
        case st status
            tailscale status | head -3
        case '*'
            if bash "$DOTFILES_DIR/bin/tailscale-set-exit-node.bash" "$argv[1]"
                tailscale status | head -3
            end
    end
end

abbr -a mvca 'mullvad ca'
abbr -a mvjp 'mullvad jp'
abbr -a mvus 'mullvad us'
abbr -a mvoff 'mullvad off'

# `brew services start tailscale` registers homebrew.mxcl.tailscale, a second
# tailscaled that races com.$USER.tailscaled on /var/run/tailscaled.socket and
# leaves the CLI hitting EPERM (see CLAUDE.md "Tailscale daemon"). Refuse the
# service-starting forms here: brew rejects the non-sudo attempt and tells you
# to re-run with sudo, so catching it now stops the footgun before sudo is
# reached. Caveat: `sudo brew …` runs brew as root and never sees this
# function — doctor.bash is the backstop that flags the conflict if it lands.
function brew --wraps brew --description 'Guard against starting homebrew tailscaled'
    if contains -- services $argv; and contains -- tailscale $argv
        for action in start run restart load
            if contains -- $action $argv
                echo "brew: refusing to start homebrew's tailscale service — it races com.$USER.tailscaled on /var/run/tailscaled.socket." >&2
                echo "      tailscaled already runs via our LaunchDaemon; pick an exit node with: mullvad ca|us|jp|off" >&2
                return 1
            end
        end
    end
    command brew $argv
end

fish_add_path $HOME/go/bin

# claude-guard: ~/.local/bin on PATH
fish_add_path --move "$HOME/.local/bin"
