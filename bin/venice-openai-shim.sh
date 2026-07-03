#!/bin/sh
# Re-export the Venice API key under OPENAI_API_KEY so OpenAI-compatible
# clients (aider, llm, ...) target Venice's endpoint, then exec the
# command passed in $@. Venice is the only permitted inference provider —
# it offers end-to-end encryption between client and inference, so
# prompts/outputs aren't inspectable by the provider (see CLAUDE.md
# "AI provider routing"; Redpill support was removed in favor of this).
#
# Invoked via:
#   envchain ai bin/venice-openai-shim.sh /path/to/tool [args...]
#
# envchain populates VENICE_INFERENCE_KEY in the environment; this script
# just remaps and execs. No user input is concatenated into any shell
# string. Tool-specific env (e.g. AIDER_MODEL) is set by the caller.

export OPENAI_API_KEY="$VENICE_INFERENCE_KEY"
export OPENAI_API_BASE="https://api.venice.ai/api/v1"
exec "$@"
