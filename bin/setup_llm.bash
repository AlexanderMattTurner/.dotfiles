#!/bin/bash
# setup_llm.bash — installer for the AI/LLM tooling layer: claude-code +
# ccr (pnpm), aider (uv), VSCodium + extensions, wut-cli (uv), llm +
# commit-message git template hook. Idempotent.

set -euo pipefail

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

status_msg() {
    echo ":: $1"
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOTFILES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IS_MAC=false
[[ "$(uname)" == "Darwin" ]] && IS_MAC=true

# ── claude-code + claude-code-router (ccr) ──────────────────────────────────
# pnpm keeps both under $PNPM_HOME, matching the path baked into
# claude-guard/launchagents/com.turntrout.ccr.plist.
#
# Pin both to the versions in claude-guard/package.json — the canonical pin
# that claude-guard's own setup.bash reads and tests/test_claude_code_version.py
# enforces, so the guardrails are validated against exactly this claude-code.
# Installing unpinned "latest" here would drift the CLI ahead of that tested
# version. Fall back to unpinned only when the pin can't be read (subrepo not
# cloned yet, or jq missing) so a partial bootstrap still installs something.
if command_exists pnpm; then
    cc_pkg="$DOTFILES_DIR/claude-guard/package.json"
    cc_spec="@anthropic-ai/claude-code"
    ccr_spec="@musistudio/claude-code-router"
    if [[ -f "$cc_pkg" ]] && command_exists jq; then
        if cc_ver="$(jq -re '.devDependencies["@anthropic-ai/claude-code"]' "$cc_pkg" 2>/dev/null)"; then
            cc_spec="@anthropic-ai/claude-code@${cc_ver}"
        else
            status_msg "WARN: could not read claude-code pin from $cc_pkg — installing latest"
        fi
        if ccr_ver="$(jq -re '.devDependencies["@musistudio/claude-code-router"]' "$cc_pkg" 2>/dev/null)"; then
            ccr_spec="@musistudio/claude-code-router@${ccr_ver}"
        fi
    fi
    status_msg "Installing ${cc_spec} + ${ccr_spec} via pnpm..."
    # Retry transient registry/network failures (same pattern as the brew
    # bundle loop in setup.bash). A final failure is a WARN, not an abort —
    # setup.bash must still reach its closing doctor.bash summary, which
    # reports the missing claude/ccr commands.
    pnpm_ok=false
    for attempt in 1 2 3; do
        if pnpm add --global --reporter=append-only "$cc_spec" "$ccr_spec"; then
            pnpm_ok=true
            break
        fi
        if [ "$attempt" -lt 3 ]; then
            status_msg "pnpm add failed (attempt $attempt/3); retrying in $((attempt * 10))s..."
            sleep $((attempt * 10))
        fi
    done
    [ "$pnpm_ok" = true ] ||
        status_msg "WARN: claude-code + ccr install failed after 3 attempts — rerun bin/setup_llm.bash."

    CLAUDE_INSTALLER="$(pnpm root -g)/@anthropic-ai/claude-code/install.cjs"
    if [[ -f "$CLAUDE_INSTALLER" ]] && command_exists node; then
        # Best-effort: the pnpm-global claude-code already works without the
        # native binary. Surface failure as a WARN instead of discarding it.
        node "$CLAUDE_INSTALLER" || status_msg "WARN: claude-code native installer failed; continuing with pnpm-global claude"
    fi
else
    status_msg "WARN: pnpm not found — skipping claude-code + ccr install"
fi

# ── aider ───────────────────────────────────────────────────────────────────
if command_exists uv; then
    if ! command_exists aider; then
        status_msg "Installing aider-chat via uv..."
        uv tool install --quiet aider-chat
    fi
    # Disable analytics before first interactive invocation prompts for it.
    aider --analytics-disable --yes --exit >/dev/null 2>&1 || true
fi

# ── VSCodium + extensions ───────────────────────────────────────────────────
# extensions.txt is the source of truth — add/remove there.
if $IS_MAC && command_exists brew; then
    brew install --quiet --cask vscodium 2>/dev/null || true
fi

if command_exists codium; then
    status_msg "Installing VSCodium extensions..."
    _ext_failures=0
    while IFS= read -r ext; do
        [[ -z "$ext" || "$ext" =~ ^# ]] && continue
        codium --install-extension "$ext" >/dev/null 2>&1 || {
            status_msg "WARN: failed to install VSCodium extension: $ext"
            _ext_failures=$((_ext_failures + 1))
        }
    done <"$DOTFILES_DIR/apps/vscodium/extensions.txt"
    [[ $_ext_failures -eq 0 ]] || status_msg "WARN: $_ext_failures VSCodium extension(s) failed to install"
fi

# ── wut-cli ─────────────────────────────────────────────────────────────────
if command_exists uv && ! command_exists wut; then
    status_msg "Installing wut-cli via uv..."
    uv tool install --quiet wut-cli
fi

# ── Venice default_code resolver cache ──────────────────────────────────────
# Refresh the cached model id that claude-private / claude-paranoid read
# from. Falls back internally if the API is unreachable.
# shellcheck source=../claude-guard/bin/lib/venice-resolve.bash disable=SC1091
source "$DOTFILES_DIR/claude-guard/bin/lib/venice-resolve.bash"
status_msg "Resolving Venice default_code model..."
cache_venice_trait default_code "$VENICE_DEFAULT_CODE_FALLBACK"

# ── llm CLI (ad-hoc shell prompts; unrelated to the commit-msg hook) ────────
if command_exists uv && ! command_exists llm; then
    status_msg "Installing llm via uv..."
    uv tool install --quiet llm
fi

if command_exists llm; then
    LLM_DIR="$(dirname "$(llm logs path)")"
    mkdir -p "$LLM_DIR"
    cat >"$LLM_DIR/extra-openai-models.yaml" <<'YAML'
- model_id: redpill-sonnet
  model_name: anthropic/claude-sonnet-4.5
  api_base: "https://api.redpill.ai/v1"
YAML
    llm models default redpill-sonnet >/dev/null 2>&1 || true
fi

# ── prepare-commit-msg template hook (Venice/qwen3-coder-480b via `mods`) ──
# Installed as a template hook (init.templateDir) so new repos pick it up at
# clone/init time. Repos that override core.hooksPath (e.g. this dotfiles
# repo's .hooks/) need a per-repo symlink — see setup.bash.
mkdir -p "$HOME/.config/prompts" "$HOME/.git_templates/hooks"
install -m 0644 "$DOTFILES_DIR/bin/.system-prompt" \
    "$HOME/.config/prompts/commit-system-prompt.txt"
install -m 0755 "$DOTFILES_DIR/bin/.prepare-commit-msg" \
    "$HOME/.git_templates/hooks/prepare-commit-msg"

git config --global --unset core.hooksPath 2>/dev/null || true
