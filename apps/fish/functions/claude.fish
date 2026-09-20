function claude --wraps claude --description 'Claude Code inside the glovebox sandbox'
    # glovebox deliberately installs itself as `claude-glovebox` and leaves the
    # real `claude` alone as an escape hatch. This function opts that escape
    # hatch out by default: an interactive `claude` starts a sandboxed session.
    # The unsandboxed client is still one word away as `command claude`.
    set -l glovebox $DOTFILES_DIR/agent-glovebox/bin/glovebox
    if not test -x $glovebox
        set glovebox (command -v glovebox)
    end
    if test -z "$glovebox"
        echo 'claude: glovebox not found — run bash setup.bash, or use "command claude" for an unsandboxed session' >&2
        return 127
    end

    # ANTHROPIC_API_KEY outranks the subscription credentials, so a session that
    # inherits one silently bills per token. glovebox manages its own monitor key
    # (GLOVEBOX_MONITOR_API_KEY), so nothing here needs the native var.
    if set -q ANTHROPIC_API_KEY
        echo "claude: ANTHROPIC_API_KEY is exported — this session bills the API per token" >&2
    end

    $glovebox $argv
end
