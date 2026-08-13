# ~/.bashrc: executed by bash(1) for non-login shells.

# If not running interactively, don't do anything
case $- in
*i*) ;;
*) return ;;
esac

# Detect macOS
IS_MAC=false
if [[ "$OSTYPE" == "darwin"* ]]; then
    IS_MAC=true
fi

# Homebrew (needed before fish handoff so brew-installed fish is in PATH)
if [[ "$IS_MAC" = true ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -d /home/linuxbrew/.linuxbrew ]; then
    eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi

# Prepend ~/.local/bin so the dotfiles `claude` shim wins over any brew/
# pnpm-installed claude (the shim's whole job is to relaunch in the
# devcontainer, which a brew-installed claude can't do).
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

# SSOT for vars also exported by apps/fish/config.fish — see
# apps/shared/exported-env.sh for the format and why a var does/doesn't
# belong there.
_dotfiles_bashrc_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
while IFS='=' read -r _key _val; do
    case "$_key" in '' | '#'*) continue ;; esac
    export "${_key?}=${_val?}"
done <"$_dotfiles_bashrc_dir/apps/shared/exported-env.sh"
unset _dotfiles_bashrc_dir _key _val

# Hand off to fish for interactive sessions.
# If the user explicitly typed 'bash', $SHELL is already fish, so we stay in bash.
WHICH_FISH="$(which fish 2>/dev/null)"
if [[ "$-" =~ i && -x "${WHICH_FISH}" && ! "${SHELL}" -ef "${WHICH_FISH}" ]]; then
    exec env SHELL="${WHICH_FISH}" "${WHICH_FISH}" -i
fi

# ---------- Below here only runs when the user explicitly wants bash ----------

HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000
HISTFILESIZE=2000
shopt -s checkwinsize

# Prompt
case "$TERM" in
xterm-color | *-256color) color_prompt=yes ;;
esac

if [ "$color_prompt" = yes ]; then
    PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='\u@\h:\w\$ '
fi
unset color_prompt

# Color support
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b "$HOME"/.dircolors)" || eval "$(dircolors -b)"
fi
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias ll='ls -alF'
alias la='ls -A'

# Custom aliases
alias e='exit'
alias rm='rm -I --preserve-root=all'
findfile() { find . -type f -iname "*${1}*" 2>/dev/null; }
alias editbashrc='nvim $HOME/.bashrc'
alias crontab="VISUAL=nvim crontab"
alias gac="git add :/; git commit -m"

# Git aliases
alias gs='git status '
alias ga='git add '
alias gb='git branch '
alias gc='git commit'
alias gd='git diff'
alias gco='git checkout '

function cdls() {
    cd "$1" || return
    ls
}

# Bash completion
if ! shopt -oq posix; then
    if [ -f /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    elif [ -f /etc/bash_completion ]; then
        . /etc/bash_completion
    fi
fi
if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# Vi mode
set -o vi
if [ -t 0 ]; then
    stty -ixon
fi

# zoxide (replaces autojump). `--cmd j` keeps the long-standing `j <dir>` workflow.
command -v zoxide >/dev/null 2>&1 && eval "$(zoxide init bash --cmd j)"

# Machine-specific extras
EXTRAS_PATH="$HOME/.extras.bash"
if [[ -f "$EXTRAS_PATH" ]]; then
    source "$EXTRAS_PATH"
fi

# NVM
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
