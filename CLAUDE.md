# CLAUDE.md

Guidance for Claude Code when working in this dotfiles repo. Optimized for
keeping `setup.bash`, `doctor.bash`, and CI honest with each other.

## Layout

- `setup.bash` — top-level installer; idempotent, supports `--link-only`.
  Always finishes by running `bin/doctor.bash` so the user sees a green
  health summary (or knows exactly what's still broken).
- `claude-guard/` — cloned repo
  (`AlexanderMattTurner/agent-glovebox`), `.gitignore`d.
  `setup.bash` clones or pulls it on every run. Contains all
  Claude Code configuration: hooks, skills, project/global settings,
  wrapper scripts, Venice/ccr routing, and the ccr LaunchAgent plist.
  `.claude/` in this repo is symlinks into this directory.
  - `hooks/monitor.bash` — AI safety "trusted monitor" PreToolUse hook.
    Sends each tool call to a cheap/OSS model for review before
    execution (the "AI control" pattern). Auto-detects provider from
    available API keys: Anthropic (Haiku) or Venice (qwen3-coder-480b /
    OSS). Skips Read by default. Logs decisions to
    `~/.cache/claude-monitor/monitor.jsonl`. Disable with
    `MONITOR_DISABLED=1`.
  - `hooks/notify.bash` — cross-platform desktop notification for the
    Notification lifecycle hook.
  - `bin/claude-private`, `bin/claude-paranoid` — claude-code wrappers
    that route through ccr to Venice. `claude-private` defaults to
    Venice's `default_code` model and escalates to `claude-opus-4-7`
    when `CLAUDE_PRIVATE_THINK=1`; `claude-paranoid` always uses
    `default_code` with no escalation. Both source
    `bin/lib/venice-resolve.bash` for the cached model id with a
    hardcoded fallback.
  - `launchagents/com.turntrout.ccr.plist` — ccr LaunchAgent.
  - `user-config/` — global (user-level) Claude Code config symlinked
    to `~/.claude/` by `symlinks.sh`.
- `bin/setup_llm.bash` — AI tooling installer invoked from `setup.bash`:
  claude-code + ccr (pnpm), aider/llm/wut (uv), VSCodium + extensions,
  llm-based commit-msg template hook. claude-code + ccr are pinned to the
  versions in `claude-guard/package.json` (the canonical pin
  `claude-guard`'s own setup + `test_claude_code_version.py` enforce), not
  installed as unpinned `latest`. Also refreshes the Venice
  `default_code` model cache via the subrepo's
  `bin/lib/venice-resolve.bash`. The ccr binary it installs is what the
  `com.turntrout.ccr` LaunchAgent starts; without this script, that
  LaunchAgent KeepAlive-respawns a missing binary.
- `bin/lib/safe_link.sh` — the only place that creates user-facing symlinks.
  Backs up real files to `~/.dotfiles-backup/<UTC-timestamp>/` before
  overwriting.
- `bin/doctor.bash` — read-only health check; mirrors what `setup.bash` builds.
- `bin/uninstall.bash` — reverses `setup.bash`'s symlink creation, restoring
  the most recent backup when one exists.
- `apps/fish/config.fish`, `.bashrc` — interactive shell config. `.bashrc`
  hands off to fish for interactive use.
- `apps/fish/conf.d/*.fish` — auto-sourced activation snippets (mise,
  carapace).
- `apps/mods/mods.yml` — Charm `mods` config; routes through Venice
  (E2EE) only. Wrapped by the `mods` fish function.
- `AGENTS.md` — symlink to `CLAUDE.md`. Lets Cursor / Aider / OpenCode
  pick up the same project context Claude Code uses.
- `.mcp.json` — Claude Code MCP server config; currently registers the
  filesystem MCP scoped to `~/.dotfiles`.
- `.claude/` — mostly symlinks into `claude-guard/`:
  `settings.json`, `hooks/`, `README.md`. `.claude/skills/` is a real
  tracked directory populated by `template-sync` from the upstream template;
  any private skills from `claude-guard/skills/` must be
  individually symlinked in by `setup.bash` if needed.
- `Brewfile` — package manifest, gated by `if OS.mac?` for cask blocks.
- `launchagents/`, `etc/sudoers.d/` — `__USERNAME__` templates rendered
  during install.
- `.github/workflows/lint.yml` — shellcheck + shfmt + stylua + yamllint
  + ruff + gitleaks. Auto-fixes and pushes a `style:` commit.
- `.github/workflows/idempotency.yml` — runs `setup.bash --link-only` twice
  on both `ubuntu-latest` and `macos-latest`, asserts identical symlink
  set + clean doctor output. The macOS leg covers the `if [ "$(uname)"
  = "Darwin" ]` branches that Ubuntu can't.

## Maintenance invariants

Whenever you add anything to `setup.bash`, `safe_link`, or related scripts,
update the matching observer.

### Doctor upkeep

`doctor.bash` is the contract for "this dotfiles install is healthy."
Every new symlink, daemon, or required external dependency added to
`setup.bash` must get a corresponding check in `doctor.bash`. Concretely:

- New `safe_link` call in `setup.bash` → new `check_symlink` line in
  `doctor.bash`'s "Symlinks" section.
- New `brew install` of a tool that's expected at runtime (i.e. used by
  `config.fish` or shell wrappers) → new `check_command` entry.
- New `launchctl load` / launchd plist symlink → new check in the
  "launchd agents" section, including a `launchctl list | grep` for the
  loaded label.
- New `envchain` namespace consumed by a wrapper in `config.fish` → list
  it in the "Secrets" section comment so users know to seed it.

A doctor check that requires an optional tool should `skip` (not `fail`)
when the tool isn't installed — `doctor.bash` is meant to be safe to run on
a partially-bootstrapped machine.

### Uninstall upkeep

`bin/uninstall.bash` is the inverse of `setup.bash` for symlinks in `$HOME`.
Every new `safe_link` in `setup.bash` whose target lives in `$HOME` must
get a matching `remove_dotfile_symlink` call in `uninstall.bash`. The
mirror set is:

- `safe_link "$DOTFILES_DIR/foo" "$HOME/.foo"` in `setup.bash` →
  `remove_dotfile_symlink "$HOME/.foo" "$DOTFILES_DIR/foo"` in
  `uninstall.bash`.
- macOS-only links go inside `if $IS_MAC` in `uninstall.bash`, same as the
  `if [ "$(uname)" = "Darwin" ]` block in `setup.bash`.
- Loops over a directory (e.g. `for aider_file in ...`) should iterate
  the same source list in both files — don't hard-code the names.

`uninstall.bash` MUST NOT touch the entries from `repo_hook_symlinks`
(`.hooks/pre-push`, `.hooks/prepare-commit-msg`, …). Those are repo
plumbing, not user dotfiles — they leave with the repo itself.

### Idempotency upkeep

`setup.bash --link-only` MUST be safe to run repeatedly. CI enforces this.
When editing the `--link-only` path:

- Don't add `read -rp` prompts on the success path — use `safe_link`,
  which only prompts when clobbering a non-symlink real file.
- Don't add commands that produce side effects on every run (e.g.
  unconditional `cp`, appending to a file). Guard with an existence or
  content check.
- Anything that writes outside `$HOME` (e.g. entries from
  `repo_hook_symlinks`, sudoers fragments) must also be a no-op on re-run.

### Linker upkeep

`safe_link` is the sole entry point for symlink creation. `setup.bash`
must not contain any direct `ln -s` — route every link through `safe_link`
(directly or via the `managed_symlinks` / `repo_hook_symlinks` iteration
loops) so the backup-on-clobber behaviour is uniform. `safe_link` handles
directory targets the same way it handles files: real-path collisions
prompt, then move to `~/.dotfiles-backup/<stamp>/` before linking.

Where a new symlink belongs:

- Target under `$HOME` → add to `managed_symlinks` in `bin/lib/symlinks.sh`
  and add a matching `remove_dotfile_symlink` in `bin/uninstall.bash`.
- Target under `$DOTFILES_DIR/.hooks/` (repo-internal git hook) → add to
  `repo_hook_symlinks` in `bin/lib/symlinks.sh`. `uninstall.bash` ignores
  these by design.
- Genuinely bespoke (launchd plists that also need bootstrap/bootout) →
  inline `safe_link` in `setup.bash`.

`safe_link` always repoints with `ln -sfn` — the `-n` is load-bearing.
A symlink whose current target resolves to a *directory* (e.g.
`~/.claude/commands`, `~/.config/nvim`, `~/.devcontainer`) would, under a
plain `ln -sf`, be dereferenced so the new link lands *inside* the old
directory while the symlink itself stays pointed at the stale target. Never
drop the `-n`.

Removal is the inverse: `setup.bash` runs `bin/lib/stale-symlinks.sh --prune`
right after the link loops, so a rename that orphans a link under
`$DOTFILES_DIR` (e.g. `~/.local/bin/claude-private` after the wrapper became
`claude-guard`) is cleaned up on the next `--link-only` run rather than
lingering until someone answers `doctor.bash`'s interactive "Refresh symlinks
now?" prompt. Prune only ever removes already-dangling symlinks, so unlike
`safe_link` it needs no backup. That prompt only fires for standalone `doctor`
runs — `setup.bash` invokes doctor with `--no-refresh` so the success path
never blocks on input.

### Session-setup upkeep (Claude Code on the web)

`claude-guard/hooks/session-setup.bash` (symlinked via
`.claude/hooks/`) bootstraps fresh web/cloud sessions.
When a hook in `.pre-commit-config.yaml` or `bin/pre-push` gains a new
tool dependency, install it from `session-setup.bash` — otherwise the
next fresh session fails its first push on a missing-tool error
unrelated to the actual change.

Put new installers inside the `=== PROJECT CUSTOMIZATIONS ===` block so
`template-sync.yaml`'s 3-way merge preserves them. Helpers, in order
of preference: `webi_install_if_missing` (shfmt, gh, jq),
`uv_install_if_missing` (most uv tools; pre-commit needs an inline
`uv tool install pre-commit --with pre-commit-uv` for the plugin),
`apt-get` guarded by `is_root` (shellcheck, fish), direct release
tarball (gitleaks — webi doesn't ship it). The block currently
installs `pre-commit`, `fish` (the
`fish --no-execute` hook needs it even on machines that don't use fish
interactively), and `gitleaks` (required, not optional — `bin/pre-push`
sets `GITLEAKS_REQUIRED=1`, which flips `bin/lint.bash` from
skip-on-missing to fail-on-missing).

### Secrets

- Bitwarden vault is the cross-machine source of truth; envchain is the
  per-machine runtime cache (auto-unlocked via macOS Keychain at GUI
  login). Don't add a third secrets layer.
- Secrets must never appear on argv. Pipe stdin → stdin between `bw`,
  `envchain`, and child commands. See `bin/bw-add-secret.bash` for the
  pattern.
- `bin/bw-*.bash` scripts go through `bin/bw-node` (a wrapper around
  `@bitwarden/cli` from pnpm, pinned to Node 22) instead of the Rust
  bw CLI. The Rust 2026.x line silently ignores `--passwordenv`, writes
  ERROR lines to stdout on unlock failure, and doesn't honor session
  tokens passed by subcommands — all of which break automation.
  `bw-common.sh:BW_CMD` resolves the wrapper; doctor verifies it
  responds to `--version`.
- Public files must not contain credentials. The lint workflow runs
  `gitleaks` against the full git history on every PR — if it flags
  something, rotate the secret first, then fix the commit.

### Trusted infrastructure protection

The AI safety monitor (`monitor.bash`), its policy prompt, audit
log, and ntfy config must be unreadable and unmodifiable by the
monitored model. If the model can read the monitor, it can find
parsing vulnerabilities or learn which patterns are flagged and
tailor actions to evade detection.

**Defense layers (devcontainer):**

1. **Root ownership (kernel-enforced).**
   `harden-monitor.bash` sets `monitor.bash` to `root:root 700`
   (unreadable by the `node` user Claude runs as), the policy prompt
   at `/etc/claude-monitor/policy.txt` to `root:root 600`, and the
   log directory to `root:root 1733` (append-only for non-root).

2. **Dispatch wrapper.**  Bash scripts must be readable to execute,
   so `settings.json` points to `monitor-dispatch.bash` — a tiny
   readable wrapper that `sudo`s into the real script. The model can
   read the 8-line dispatch but learns nothing about detection logic.

3. **Deny rules (defense in depth).** `settings.json` denies
   `Read(.claude/hooks/monitor.bash)` and
   `Read(/etc/claude-monitor/**)`. These are glob patterns and
   trivially bypassable — they exist only to catch accidental reads,
   not as a security boundary.

4. **`IS_SANDBOX=no`** in `devcontainer.json` keeps the monitor
   active inside the container (the Anthropic cloud sets
   `IS_SANDBOX=yes`, which skips monitoring).

**On the host** (no devcontainer), root ownership isn't enforced
automatically — the user owns the repo. Protection comes from Claude
Code's permission prompts (no `--dangerously-skip-permissions`).

### AI provider routing

- Inference flows through Venice only for new tooling — Venice
  provides end-to-end encryption between client and inference, so
  prompts/outputs are not visible to the provider. Redpill's TEE is a
  weaker guarantee and is not used for new wrappers.
- `apps/mods/mods.yml` lists Venice models only (qwen-2.5-coder,
  llama-3.3, mistral, etc.). The `mods` fish function wraps invocations
  in `envchain ai` so `VENICE_INFERENCE_KEY` is populated from the
  Keychain.

### Cross-platform

- `IS_MAC=$([[ "$(uname)" == "Darwin" ]] && echo true || echo false)` is
  the canonical detector. Linux is everything else; we don't separately
  branch for distros, and we don't support WSL.
- Cask entries belong inside the `if OS.mac?` block in `Brewfile`. Brews
  that exist on both platforms go above it.
- macOS-only paths in `setup.bash` (launchd agents, defaults writes,
  iTerm2 integration) live inside `if [ "$(uname)" = "Darwin" ]`.

### Tailscale daemon

`com.$USER.tailscaled` is the sole tailscaled LaunchDaemon. `setup.bash`
boots out `homebrew.mxcl.tailscale` (the daemon homebrew installs when
`brew install tailscale` or `sudo brew services start tailscale` is
run) and removes its plist before bootstrapping ours. Two daemons
racing on `/var/run/tailscaled.socket` leave the socket carrying
provenance for whichever lost the race, after which the homebrew CLI
hits `connect: operation not permitted` even though `tailscaled` is
running. `doctor.bash` fails if `homebrew.mxcl.tailscale.plist` exists,
if `tailscale status` returns a socket-reachability error, or if the
daemon is logged out.

The race recurs only when something re-registers homebrew's service —
in practice `sudo brew services start tailscale` (plain `brew
install`/`upgrade` don't resurrect a booted-out plist). Defenses, in
order of when they fire: the `brew` fish wrapper in
`apps/fish/config.fish` refuses the non-sudo `brew services
start|run|restart|load tailscale` (the form brew makes you type before
it tells you to add sudo); `setup.bash` re-evicts the plist on every
run; and `doctor.bash` FAILs if it ever lands. The wrapper can't see
`sudo brew …` (sudo runs brew as root, bypassing fish functions), so
doctor is the real backstop, not the wrapper. We deliberately do *not*
`brew pin tailscale`: it wouldn't stop the trigger and would freeze
security updates on a VPN daemon.

The worst failure: **clearing a Mullvad exit node blackholes all
traffic.** `tailscale set --exit-node=` (the SwiftBar "Disconnect"
action) tears down the tunnel's split-default routes, but the
open-source `tailscaled` on macOS fails to re-elect the physical
default route — macOS's `State:/Network/Global/IPv4` is left with no
`Router`/`PrimaryInterface`, so every packet (v4 and v6) dies with
"network is unreachable". Confirmed in `/var/log/tailscaled.stderr.log`:
`link state: ...defaultRoute=` (empty) right after the disconnect,
restored only by the reboot's `DefaultRoute: ""->"enX"` rebind. A
`tailscaled` kickstart does **not** fix it — the daemon doesn't own the
missing route; macOS SystemConfiguration does. The App Store Tailscale
(NetworkExtension) tears down cleanly; this repo uses brew `tailscaled`,
so we self-heal instead: `bin/tailscale-set-exit-node.bash`, on the
disconnect path only, captures the primary interface *before* clearing
(it's unreadable once blackholed), polls `sc_default_router` for a
returning route, and if none comes back bounces the interface — Wi-Fi
via `networksetup -setairportpower` (no sudo), the minimal equivalent of
the reboot's rebind. It verifies recovery and, if it still fails, emits
a notification + non-zero exit rather than pretending success. The
detector reads SC state, *not* `route get default`, which reports
success off stale utun / OrbStack `!` reject routes while offline.

A separate, milder hazard: `brew upgrade tailscale` swaps the CLI binary
but leaves the *old* `tailscaled` running (version skew). This is *not*
the blackhole cause but is real drift. `tailscale_version_skew` in
`bin/lib/tailscale-resolve.sh` compares `tailscale version` against the
daemon's `status --json` Version; `setup.bash` kickstarts the daemon on
skew (self-heals every run), `doctor.bash` FAILs on it, and both
`tailscale-set-exit-node.bash` and `vpn.10s.bash` surface a *non-blocking*
warning (they must not refuse to disconnect — that would only strand you
on the exit node, and route recovery covers any teardown fallout). It
stays silent when either side is unreadable (EPERM/boot transients must
not false-alarm). Tested in `tests/test_tailscale_health.py`.

`tailscale_health` in `bin/lib/tailscale-resolve.sh` is the single
classifier for CLI↔daemon health (`ok` / `stopped` / `no-daemon` /
`eperm` / `logged-out` / `error`). Its consumers must stay in sync:

- `apps/swiftbar/vpn.10s.bash` shows a distinct glyph + repair menu
  item per state — "🔴 off" strictly means "daemon healthy, exit node
  deliberately off". (Logged-out matters: the node key expiring while
  a Mullvad exit node is engaged blackholes all traffic, and `pkill
  tailscaled` can't fix it because the LaunchDaemon's KeepAlive
  respawns it with the persisted exit-node pref.)
- `bin/tailscale-set-exit-node.bash` pre-flights the daemon and logs a
  remediation hint instead of relaying `tailscale set`'s misleading
  errors (a logged-out daemon yields `invalid value ... must be IP or
  hostname` because the netmap is gone). Failures hit both `menu.log`
  and stderr.
- `bin/tailscale-apply-exit-node.bash` (login agent) retries through
  boot-time `no-daemon`/`error` states but bails immediately on
  `logged-out` — interactive browser re-auth can't be retried into
  existence.
- `bin/doctor.bash` maps each unhealthy state to a FAIL with the exact
  recovery command; logged-out is a FAIL, not "reachable".

Adding a failure mode = new state in `tailscale_health` + handling in
every consumer + a case in `tests/test_tailscale_health.py`. The
set-exit-node exit-code/stderr/menu.log contract is locked by
`tests/test_set_exit_node.py` (which self-skips when a real tailscale
CLI is installed, so it can never drive an actual VPN).

## Conventions

- Bash scripts: `set -euo pipefail` at the top. Use `command_exists` for
  tool detection. Use `status_msg "..."` (defined in `setup.bash`) for
  visible progress lines so output stays consistent.
- Script extensions: `.bash` for scripts with a `#!/bin/bash` or
  `#!/usr/bin/env bash` shebang (the macOS `/bin/sh` is bash 3.2 in
  POSIX mode, so a bash-shebang script under a `.sh` name silently lies
  about what it needs). `.sh` is reserved for `#!/bin/sh` POSIX scripts
  (e.g. `bin/aider-redpill-shim.sh`) and for sourced libraries under
  `bin/lib/` that declare `# shellcheck shell=bash` instead of carrying
  a shebang. The `sh-extension` pre-commit hook
  (`.pre-commit-config.yaml` → `bin/check-sh-extension.bash`) enforces
  this in CI — adding a new `.sh` file with a bash shebang fails the
  lint job.
- Prefer fish abbreviations (`abbr -a`) over functions when the only
  job is text expansion — abbrs preserve history readability.
- Use `command <name>` to bypass fish/bash function shadowing
  (e.g. `command rm`, `command npm`) rather than removing the wrapper.
- Recording a "lesson learned" **always** means landing a change via
  PR — either a new commit on an open PR (use the `update-pr` skill)
  or a fresh PR if none exists. The durable record is the PR
  description plus any CLAUDE.md or code edits the lesson motivates.
  Lessons that live only in chat history are vapor; they don't survive
  the session.
- Tests of repo behavior go in `tests/test_*.py` and run via `pytest` —
  cleaner assertions than shell, ruff already lints `.py`, and
  `tmp_path` handles isolation. Pattern:
  `tests/test_uninstall_roundtrip.py` invoked from
  `.github/workflows/uninstall.yml`. Avoid non-trivial shell in workflow
  `run:` blocks for the same reason: it skips static analysis. A `run:`
  block of more than a handful of meaningful lines is the smell.
- **No `from __future__` imports.** Python here is 3.13+; PEP 563
  lazy annotations, `Path | None` union syntax, and generic builtins
  (`list[int]`) all work natively, so the import is dead weight that
  also subtly changes `inspect.get_annotations` behavior. The
  `no-future-import` pre-commit hook (`.pre-commit-config.yaml`)
  fails the lint job on any `from __future__` line in `*.py`.

## Workflow shell scripts live in `bin/`, not inline in `.yml`

Inline `run: |` blocks in `.github/workflows/*.yml` are invisible to
shellcheck/shfmt and skip the auto-fix pipeline. Anything beyond a
trivial 1-3 line install incantation should be extracted to
`bin/<name>.bash` and invoked from the workflow as `run: bash
bin/<name>.bash` — the shellcheck pre-commit hook
(`.pre-commit-config.yaml`) then covers it automatically via its
`bin/[^/]*\.(bash|sh)` files pattern.

Reference: `bin/check-idempotency.bash` is invoked from
`.github/workflows/idempotency.yml` with `TEST_HOME` / `SCRATCH` passed
via `env:`. The script defaults both to `mktemp` so it's runnable
locally too.

**Don't extract from template-synced workflows.** `template-sync.yaml`
copies these files verbatim from the upstream template on every daily
run, so any local edits get clobbered:

- `.github/workflows/template-sync.yaml`
- `.github/workflows/dependabot-auto-merge.yaml`
- `.github/workflows/claude.yaml`
- `.github/workflows/phone-home.yaml`
- `.github/workflows/security-vulnerability-scan.yaml`

The full list is in `template-sync.yaml`'s `SYNC_PATHS` env. Any
refactor for shellcheck coverage of those scripts has to land upstream
in `alexander-turner/claude-automation-template`.

## When fixing CI failures

- `lint.yml` runs `pre-commit run --all-files` (orchestrated by
  `.pre-commit-config.yaml`), then commits any auto-fixes back. If
  your push is followed by a `style: auto-fix` commit, pull before
  continuing. The verify-after-fix step re-runs `pre-commit` so
  non-auto-fixable issues (shellcheck warnings, gitleaks hits, fish
  syntax errors) still fail CI.
- `gitleaks` failures are not auto-fixed. Treat any hit as a real
  incident: rotate the leaked credential, then either rewrite the
  commit (if local) or add an allowlist entry justifying the false
  positive.
- `idempotency.yml` failure usually means a new step in `setup.bash` is
  not safely re-runnable. The diff between `links1.txt` and `links2.txt`
  in the run log shows which symlink mutated.

## Local quick reference

```bash
bash setup.bash --link-only       # refresh symlinks only (also runs doctor)
bash bin/doctor.bash              # health check
bash bin/doctor.bash --quiet      # show only failures/skips
bash bin/uninstall.bash           # remove $HOME symlinks, restore backups
bash bin/uninstall.bash --yes     # ... non-interactive
bash bin/lint.bash                # run all linters (pre-commit run --all-files)
pre-commit run shellcheck --all-files  # run one hook by id
pre-commit autoupdate             # bump pinned hook versions
bwseed                          # force Bitwarden → envchain refresh

# After setup.bash has run once, the same chores are reachable via the
# dispatcher symlinked at ~/.local/bin/dotfiles (with fish completions):
dotfiles doctor                 # → bin/doctor.bash
dotfiles uninstall --yes        # → bin/uninstall.bash
dotfiles link                   # → setup.bash --link-only
dotfiles lint                   # → bin/lint.bash → pre-commit run --all-files
```
