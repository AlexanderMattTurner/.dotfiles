# SSOT for exported env vars shared between bash (../../.bashrc) and fish
# (../fish/config.fish). Format: KEY=value, one per line — first '=' splits
# key from value, everything after it is the literal value verbatim (no
# quoting, no $VAR expansion, no shell syntax). '#' starts a comment; blank
# lines are ignored. Only vars with a static value that's identical in both
# shells belong here — dynamic values (DOTFILES_DIR, SHELL) stay inline in
# each shell's own config.

EDITOR=nvim

# less draws on the alternate screen, where tmux forwards the wheel to the
# app rather than opening copy-mode. Without --mouse, less requests no mouse
# tracking and the event is dropped, so the pane looks unscrollable.
LESS=-R --mouse --wheel-lines=3

# 1h prompt-cache TTL instead of the 5min default: avoids paying full-context
# cache-write cost on every message after brief inactivity (e.g. cronjob-driven
# sessions checking on long-running experiments).
ENABLE_PROMPT_CACHING_1H=1
