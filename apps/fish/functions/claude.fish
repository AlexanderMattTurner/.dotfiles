function claude --wraps claude --description 'Claude Code via claude-guard, credentials from the apiKeyHelper'
    # ANTHROPIC_API_KEY is deliberately NOT exported here: an exported key
    # outranks the apiKeyHelper wired in ~/.claude/settings.json
    # (`claude-account --helper`), which would pin the whole session to
    # pay-per-token API billing and disable mid-session subscription rotation.
    # For a deliberate pay-per-token run:
    #   env ANTHROPIC_API_KEY=(envchain ai printenv ANTHROPIC_API_KEY) claude ...
    #
    # Only override the inherited environment when envchain actually returns a
    # value. A failed lookup (locked keychain, unseeded 'ai' namespace) must not
    # clobber a working key already present in the environment with an empty one.
    set -l venice (envchain ai printenv VENICE_INFERENCE_KEY 2>/dev/null)
    test -n "$venice"; or set venice $VENICE_INFERENCE_KEY

    if test -z "$venice"
        echo "claude: envchain 'ai' has no VENICE_INFERENCE_KEY (the monitor hook needs it); run bwseed" >&2
    end

    env VENICE_INFERENCE_KEY=$venice \
        $DOTFILES_DIR/claude-guard/bin/claude $argv
end
