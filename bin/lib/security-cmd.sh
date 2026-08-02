# shellcheck shell=bash
# security-cmd.sh — the single source of truth for building `security -i`
# command lines.
#
# `security -i` (macOS SecurityTool interactive mode) reads one command per
# line and unquotes double-quoted arguments via its own split_line parser
# (backslash escapes `"` and `\` inside double quotes). Two hazards live here
# and BOTH must be handled at every call site:
#
#   1. Quoting — an argument with a space, `"`, or `\` corrupts the parse
#      unless double-quoted and escaped.
#   2. Newlines — the protocol is line-oriented, so an argument containing a
#      raw newline splits one command into two. Depending on how split_line
#      treats a newline inside `"..."`, that either truncates the value or is
#      read back as a separate command. Either way it must be refused, never
#      passed through.
#
# This helper used to be a `_security_quote` function copied verbatim into
# secret-store.sh and keychain.sh, with the newline guard bolted onto each
# call site after the fact (#125). A forgotten third copy is exactly how the
# gap re-opens, so both quoting and the guard now live here and nowhere else.
#
# Consumers call `security_build_line CMD ARG...` and pipe the result into
# `security -i`. The guard runs *before* any quoting or emission: the function
# validates every argument first and returns non-zero (emitting nothing) if
# any contains a newline. Assigning its output — `line=$(security_build_line
# ...)` — lets the caller branch on that exit code cleanly under `set -e`,
# instead of the awkward mid-`$(...)` failure that kept the guard out of the
# old stdout-returning `_security_quote`.

# Escape one string as a double-quoted `security -i` argument.
# Internal — callers should go through security_build_line so they can't quote
# an argument without also inheriting the newline guard.
_security_quote() {
    local s=${1//\\/\\\\}
    printf '"%s"' "${s//\"/\\\"}"
}

# security_build_line CMD ARG... — assemble one `security -i` command line with
# every token double-quoted and escaped. Validates all arguments for newlines
# up front; on the first offender it prints a diagnostic to stderr and returns
# 1 without writing anything to stdout. Otherwise prints the joined line (no
# trailing newline — the caller adds one when piping to `security -i`).
security_build_line() {
    local arg
    for arg in "$@"; do
        case "$arg" in
        *$'\n'*)
            echo "security_build_line: refusing an argument containing a newline" \
                "(security -i is line-oriented)" >&2
            return 1
            ;;
        esac
    done

    local out=""
    for arg in "$@"; do
        if [ -z "$out" ]; then
            out=$(_security_quote "$arg")
        else
            out="$out $(_security_quote "$arg")"
        fi
    done
    printf '%s' "$out"
}
