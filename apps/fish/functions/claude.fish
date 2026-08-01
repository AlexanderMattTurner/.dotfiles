function claude --wraps claude --description 'Claude Code via claude-guard, subscription rotation through the loopback proxy'
    # ANTHROPIC_API_KEY is deliberately NOT exported here, and is unset for the
    # proxied launch below: an exported key outranks CLAUDE_CODE_OAUTH_TOKEN in
    # Claude Code's credential precedence, so it would beat the sentinel, present the
    # session as pay-per-token API billing, and (sending no oauth beta) break the
    # proxy's value-only rewrite. For a deliberate pay-per-token run:
    #   env ANTHROPIC_API_KEY=(envchain ai printenv ANTHROPIC_API_KEY) claude ...
    if set -q ANTHROPIC_API_KEY
        echo "claude: ANTHROPIC_API_KEY is exported — it outranks the rotation proxy, so this session bills the API per-token and account rotation is off" >&2
    end

    set -l venice (envchain ai printenv VENICE_INFERENCE_KEY 2>/dev/null)
    test -n "$venice"; or set venice $VENICE_INFERENCE_KEY
    if test -z "$venice"
        echo "claude: envchain 'ai' has no VENICE_INFERENCE_KEY (the monitor hook needs it); run bwseed" >&2
    end

    set -l claude_bin $DOTFILES_DIR/claude-guard/bin/claude

    # Route through the rotation proxy only when at least one subscription account is
    # configured. With none, there is nothing to rotate, so launch straight through
    # (a bare CLAUDE_CODE_OAUTH_TOKEN in the environment, or the user's own login,
    # still works).
    if test -z "(claude-account --namespaces 2>/dev/null)"
        env VENICE_INFERENCE_KEY=$venice $claude_bin $argv
        return
    end

    set -l port $CLAUDE_ROTATE_PROXY_PORT
    test -n "$port"; or set port 8789

    # Start the per-machine proxy if nothing is listening yet; the proxy singleton
    # -guards by binding the port, so a race just makes the loser exit. Then wait
    # briefly for the listener so the client's first request does not race the bind.
    if not bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null
        nohup python3 $DOTFILES_DIR/bin/claude-rotate-proxy.py >/dev/null 2>&1 &
        disown
        for _ in (seq 20)
            bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null; and break
            sleep 0.1
        end
    end

    # The sentinel only has to LOOK like an oauth token so the client fixes the
    # subscription presentation; the proxy supplies the real token per request. The
    # session process never holds a credential. env -u drops any inherited
    # ANTHROPIC_API_KEY so the sentinel is the top credential.
    env -u ANTHROPIC_API_KEY \
        CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-CLAUDE-ROTATE-PROXY-SENTINEL \
        ANTHROPIC_BASE_URL=http://127.0.0.1:$port \
        VENICE_INFERENCE_KEY=$venice \
        $claude_bin $argv
end
