# shellcheck shell=bash
# pnpm_pin_spec — resolve a pnpm install spec from a package.json pin.
#
#   pnpm_pin_spec <package.json> <package-name>
#
# Echoes "<pkg>@<version>" using the devDependencies entry in the given
# package.json (claude-guard/package.json is the canonical pin source).
# Falls back to the bare "<pkg>" (i.e. latest) and returns 1 when the
# file is missing, jq is unavailable, or the pin isn't present — callers
# decide whether that deserves a WARN. Covered by tests/test_pnpm_pin.py.
pnpm_pin_spec() {
    local pkg_json="$1" pkg="$2" ver
    if [[ -f "$pkg_json" ]] && command -v jq >/dev/null 2>&1; then
        if ver="$(jq -re --arg p "$pkg" '.devDependencies[$p]' "$pkg_json" 2>/dev/null)"; then
            printf '%s@%s\n' "$pkg" "$ver"
            return 0
        fi
    fi
    printf '%s\n' "$pkg"
    return 1
}
