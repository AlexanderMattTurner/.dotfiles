#!/bin/bash
# install-gitleaks.bash — download the pinned gitleaks release, verify its
# SHA-256 against the pinned checksum, and install to /usr/local/bin.
#
# Invoked from .github/workflows/lint.yml and phone-home.yaml. Extracted
# here (instead of an inline run: block) so shellcheck/shfmt cover it, per
# CLAUDE.md. The checksum pin means a replaced/tampered release asset
# fails loudly instead of being untarred as root.
#
# Bumping the version: update GITLEAKS_VERSION and GITLEAKS_SHA256 together
# — the values come from gitleaks_<ver>_checksums.txt on the release page.
set -euo pipefail

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"
# SHA-256 of gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz
GITLEAKS_SHA256="${GITLEAKS_SHA256:-551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb}"

tarball="$(mktemp)"
trap 'rm -f "$tarball"' EXIT

curl -fsSL --retry 6 --retry-all-errors --retry-delay 15 --connect-timeout 30 \
    -o "$tarball" \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"

echo "${GITLEAKS_SHA256}  ${tarball}" | sha256sum -c - >/dev/null

sudo tar xz -C /usr/local/bin -f "$tarball" gitleaks
gitleaks version
