#!/bin/bash
# Assert that doctor.bash reports no FAIL in its Symlinks section.
#
# Called by .github/workflows/idempotency.yml after setup.bash --link-only so
# symlink regressions surface as CI failures rather than silent drift. Lives in
# bin/ (not inline in the workflow) so shellcheck/shfmt cover it automatically.
#
# Usage:
#   bash bin/check-doctor-symlinks.bash     # uses current $HOME
#   HOME=/tmp/fakehome bash bin/check-doctor-symlinks.bash

set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "$0")/.." && pwd)"

out=$(bash "$DOTFILES_DIR/bin/doctor.bash" --verbose || true)
printf '%s\n' "$out"

symlinks_section=$(printf '%s\n' "$out" |
    awk '/=== Symlinks ===/{flag=1;next} /^=== /{flag=0} flag')

if [ -z "$symlinks_section" ]; then
    printf '::error::doctor.bash --verbose produced no Symlinks section (section header changed?)\n' >&2
    exit 1
fi

if printf '%s\n' "$symlinks_section" | grep -q "FAIL"; then
    printf '::error::doctor.bash reported a symlink FAIL after setup.bash --link-only\n' >&2
    exit 1
fi
