# CLAUDE.md

Guidance for Claude Code when working in this dotfiles repo. Optimized for
keeping `setup.bash`, `doctor.bash`, and CI honest with each other.

## Layout

- `setup.bash` — top-level installer; idempotent, supports `--link-only`.
  Always finishes by running `bin/doctor.bash` so the user sees a green
  health summary (or knows exactly what's still broken).
- `agent-glovebox/` — the sandboxed-Claude subrepo
  (`AlexanderMattTurner/agent-glovebox`), `.gitignore`d and
  **user-managed**: this repo neither clones nor pins it. Clone it
  yourself and run its own `setup.bash`, which installs
  `~/.local/bin/glovebox` and owns Claude Code version updates and PATH
  precedence from then on. `doctor.bash` only checks that the checkout
  is present and that `glovebox` on PATH resolves into it.
  `apps/fish/functions/claude.fish` execs `agent-glovebox/bin/glovebox`,
  so an interactive `claude` starts a sandboxed session; `command claude`
  is the unsandboxed escape hatch glovebox deliberately leaves alone.
  Nothing in `.claude/` symlinks into it.
- `bin/setup_llm.bash` — AI tooling installer invoked from `setup.bash`:
  claude-code (pnpm), aider/llm/wut (uv), VSCodium + extensions,
  llm-based commit-msg template hook. claude-code is pinned to the
  version in `agent-glovebox/package.json` (the canonical pin glovebox's
  own setup + `test_claude_code_version.py` enforce, read via
  `bin/lib/pnpm-pin.sh`), not installed as unpinned `latest`.
  The uv tools are pinned too (`AIDER_PIN`/`WUT_PIN`/`LLM_PIN` at the
  top of the script — bump there).
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
- `.claude/` — all real tracked content, no symlinks. `.claude/skills/`
  is populated by `template-sync` from the upstream template.
- `Brewfile` — package manifest, gated by `if OS.mac?` for cask blocks.
- `launchagents/`, `etc/sudoers.d/` — `__USERNAME__` templates rendered
  during install.
- `.github/workflows/lint.yml` — shellcheck + shfmt + stylua + yamllint
  + actionlint + ruff + gitleaks. Auto-fixes and pushes a `style:` commit.
  (`actionlint` is GitHub-Actions-aware where yamllint is generic YAML — it
  catches `if:` expression-type bugs, unknown action inputs, and shell
  issues in `run:` blocks; built from a pinned rev via `language: golang`.)
- `.github/workflows/idempotency.yml` — runs `setup.bash --link-only` twice
  on both `ubuntu-latest` and `macos-latest`, asserts identical symlink
  set + clean doctor output. The macOS leg covers the `if [ "$(uname)"
  = "Darwin" ]` branches that Ubuntu can't.
- `.github/workflows/uninstall.yml` — runs `tests/test_uninstall_roundtrip.py`
  on push/PR; enforces the "Uninstall upkeep" contract below.
- `.github/workflows/security-vulnerability-scan.yaml` — weekly; collects
  open Dependabot/code-scanning/secret-scanning/pnpm-audit/Socket.dev
  alerts and hands them to Claude to fix or roll up into one PR. Pairs
  with `dependabot-auto-merge.yaml`.

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
`~/.config/nvim`, `~/.claude/hooks`) would, under a
plain `ln -sf`, be dereferenced so the new link lands *inside* the old
directory while the symlink itself stays pointed at the stale target. Never
drop the `-n`.

Removal is the inverse: `setup.bash` runs `bin/lib/stale-symlinks.sh --prune`
right after the link loops, so a rename that orphans a link under
`$DOTFILES_DIR` (e.g. `~/.local/bin/claude-account` after mid-session
account rotation was removed) is cleaned up on the next `--link-only` run rather than
lingering until someone answers `doctor.bash`'s interactive "Refresh symlinks
now?" prompt. Prune only ever removes already-dangling symlinks, so unlike
`safe_link` it needs no backup. That prompt only fires for standalone `doctor`
runs — `setup.bash` invokes doctor with `--no-refresh` so the success path
never blocks on input.

### Secrets

- Bitwarden vault is the cross-machine source of truth; envchain is the
  per-machine runtime cache (auto-unlocked via macOS Keychain at GUI
  login). Don't add a third secrets layer.
- Namespaces consumed by wrappers, so you know what to seed: `ai`
  (`VENICE_INFERENCE_KEY`, `ANTHROPIC_API_KEY`), `npm`, `cloudflare`,
  `pypi`, `duplicati` (the settings-encryption key that decrypts the
  `enc-v1:` target URL, and with it the remote credentials, in Duplicati's
  server database — the LaunchAgent runs the server under `envchain
  duplicati` for exactly this).
- **There is no multi-account rotation.** `bin/claude-account.bash`,
  `bin/lib/claude-account-lib.sh`, the loopback proxy
  `bin/claude-rotate-proxy.py`, and their doctor checks were deleted —
  don't resurrect them. Claude Code signs in on its own, and glovebox
  owns credentials for sandboxed sessions. The `claude` fish function
  (`apps/fish/functions/claude.fish`) is now a thin wrapper that execs
  `agent-glovebox/bin/glovebox`; `command claude` remains the
  unsandboxed escape hatch. An exported `ANTHROPIC_API_KEY` still
  outranks subscription credentials and silently bills per token, which
  is why the wrapper warns about one rather than exporting it.
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

The AI safety monitor lives in `agent-glovebox/` and is that repo's
concern, not this one — it runs inside the sandbox glovebox starts, with
its policy, audit log and keys outside the monitored model's reach. This
repo ships no monitor hook and no hardening script; do not add one here.
On a plain `command claude` session (the unsandboxed escape hatch) there
is no monitor at all, and protection is Claude Code's own permission
prompts — never `--dangerously-skip-permissions`.

### AI provider routing

- Inference flows through Venice only — Venice provides end-to-end
  encryption between client and inference, so prompts/outputs are not
  visible to the provider. Redpill (a weaker TEE guarantee) was fully
  removed; do not add new providers.
- OpenAI-compatible CLIs (aider, llm) don't read `VENICE_INFERENCE_KEY`
  directly, so the `aider_venice` and `llm` fish functions remap it onto
  `OPENAI_API_KEY`/`OPENAI_API_BASE` inline via `envchain ai bash -c
  '...'` — no separate shim script for a three-var remap; the
  assignment happens inside the `bash -c` string so the key is read at
  runtime from envchain's environment, never expanded by fish or placed
  on argv. `bin/setup_llm.bash` writes llm's `extra-openai-models.yaml`
  (default `venice-sonnet`).
- `apps/mods/mods.yml` routes through Venice only (default
  `qwen3-coder-480b-a35b-instruct-turbo`, plus `claude-sonnet-4-6`,
  `claude-opus-4-7`, `deepseek-v4-pro`, `mistral-small-2603` — model ids
  match Venice's `/v1/models` and rotate, so treat these as examples, not
  a frozen list). The `mods` fish function wraps invocations in
  `envchain ai` so `VENICE_INFERENCE_KEY` is populated from the Keychain.

### Backups

Duplicati is the only offsite copy of this machine, and it is the one
subsystem here whose failures are all silent. Nothing about a dead backup
looks different from a live one: the LaunchAgent stays `running`,
`Schedule.LastRun` advances on every *trigger* whether or not the run
succeeded, and a run that skips thousands of files still ends `Success`.
This went unmanaged for months — the plist existed only in
`~/Library/LaunchAgents`, `doctor.bash` checked nothing, and "are we backing
up?" could only be answered by opening the web UI.

- `launchagents/com.duplicati.server.plist` is the tracked plist,
  symlinked by `setup.bash` and removed by `uninstall.bash`. It has no
  user-specific paths, so it is a plain plist, not a `__USERNAME__`
  template. `setup.bash` bootstraps it **only when it isn't already
  loaded**: a bootout/bootstrap cycle on every run would abort a backup
  that happened to be mid-flight.
- `bin/lib/duplicati-status.sh` is the single classifier
  (`ok:<days>` / `stale:<days>` / `never` / `no-jobs` / `no-db` /
  `no-sqlite`). Freshness comes from the newest `Fileset` row in each job's
  own database, because that is the only record that proves a backup
  *landed*. Every read uses `immutable=1` so doctor takes no sqlite locks
  on a live multi-GB database — it can under-report freshness, never
  over-report it. Adding a failure mode = new state here + a case in
  `bin/doctor.bash` + a case in `tests/test_duplicati_status.py`.
- **iCloud-evicted files are excluded from every backup.** Duplicati cannot
  materialize a dataless placeholder, so it logs `Excluding path due to
  file locked ... Resource deadlock avoided` and the file lands in no
  backup version — iCloud becomes its only copy. This is why doctor FAILs
  on a non-zero `duplicati_dataless_count`; as of 2026-08-09 there were
  6,291 such files under `~/Documents` and `~/Desktop`, which is what the
  ~4,214 warnings on each daily run were. The fix is to stop evicting
  (turn off iCloud Drive's "Optimize Mac Storage"), not to silence the
  warning.
- Duplicati's data lives in `~/Library/Application Support/Duplicati`.
  Do **not** resurrect the old `sudo duplicati-server` pattern (removed in
  `a877cef`): it left `~/.duplicati` and `/Users/Shared/Duplicati/data`
  owned by `root:wheel 700`, which the user-owned server can't read, and
  `~/.duplicati` sits inside the `%HOME%` backup source so it warns on
  every run.

### Cross-platform

- `IS_MAC=false; [[ "$(uname)" == "Darwin" ]] && IS_MAC=true` is the
  canonical detector (see `bin/doctor.bash`, `bin/uninstall.bash`,
  `bin/setup_llm.bash`). `setup.bash` is the exception — it never defines
  `IS_MAC` and inlines `[ "$(uname)" = "Darwin" ]` checks directly. Linux
  is everything else; we don't separately branch for distros, and we
  don't support WSL.
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
traffic — via DNS, not routing.** While the exit node is engaged
`tailscaled` points *itself* at Mullvad's resolver
(`dns: Set: {DefaultResolvers:[194.242.2.2] ...}` in
`/var/log/tailscaled.stderr.log`) and points macOS at `tailscaled`
(`/etc/resolv.conf` + `State:/Network/Global/DNS` → `100.100.100.100`).
`194.242.2.2` is reachable **only through the tunnel**, so when
`tailscale set --exit-node=` (the SwiftBar "Disconnect") tears the
tunnel down and that pref survives, every lookup dies at a resolver with
no path to it. The symptom reads as "no internet"; the giveaway is
`dns: resolver: forward: sendTCP: response code indicating server
failure: 2` on a loop while the physical default route is *perfectly
healthy*. That is why a Wi-Fi bounce can't fix it (`tailscaled` just
re-`Set`s the same DNS) and a reboot can (fresh daemon, macOS reverts to
the DHCP servers still held in the service's DNS key).

`bin/tailscale-set-exit-node.bash` self-heals it on the disconnect path:
`restore_dns` gives the DNS manager a grace window (it re-applies a beat
*after* `tailscale set` returns), then forces a teardown + re-apply by
toggling `--accept-dns` off and back on — the only lever that works
without sudo, which is required because SwiftBar runs the applier
detached and cannot prompt. With the exit node already cleared, the
re-apply derives resolvers from the netmap alone and drops the Mullvad
entry. It restores `--accept-dns` to its prior value and no-ops when
`CorpDNS` was already false (DNS never hijacked ⇒ not this bug).

**Historical note — do not re-chase this.** This was long attributed to
a *route* drop: `tailscaled` failing to re-elect the physical default
route, leaving `State:/Network/Global/IPv4` with no
`Router`/`PrimaryInterface`. `restore_default_route` + the Wi-Fi bounce
were built for that theory and are retained (cheap, and the state was
apparently seen once), but they are **not** what fires: across 17
`→ off` disconnects in `menu.log` the recovery path logged *zero*
times, because `sc_default_router` reads the router — which never drops.
A teardown that checks only routing will always call this blackhole
clean. Both halves are now verified, and both run even if the first
fails.

A second trigger reaches the same stale-resolver state with **no
disconnect to hook**: sleep/wake churn, where an exit node stops routing
(no `0.0.0.0/1`+`128.0.0.0/1` via `utun0`, peer goes `idle`) while DNS
stays pointed through it — and the menubar still shows a flag, so
traffic egresses in the clear. Nothing on the disconnect path can catch
that, so `doctor.bash` checks `tailscale_dns_healthy` directly and is
the only backstop for it.

The probe is `dig` against the system's *configured* resolvers, because
the rewritten `/etc/resolv.conf` is exactly the path that breaks. `dig
+short` exits 0 on SERVFAIL, so an **empty answer, not exit status**, is
the signal. `tailscale_dns_healthy` deliberately answers "healthy" when
no `dig` exists (never notify on a guess), which is why `doctor.bash`
asks `tailscale_dns_probe_available` first and `skip`s rather than
`pass`ing a check it never ran. Coverage lives in
`tests/test_tailscale_health.py` (the lib functions) and the macOS-gated
cases in `tests/test_set_exit_node.py` — the latter must keep `dig` in
its stub set, or `restore_dns` silently no-ops and the path goes
uncovered with nothing turning red.

A separate, milder hazard: `brew upgrade tailscale` swaps the CLI binary
but leaves the *old* `tailscaled` running (version skew). This is *not*
the blackhole cause but is real drift. `tailscale_version_skew` in
`bin/lib/tailscale-resolve.sh` compares `tailscale version` against the
daemon's `status --json` Version; `setup.bash` kickstarts the daemon on
skew (self-heals every run), `doctor.bash` FAILs on it, and both
`tailscale-set-exit-node.bash` and `vpn.10s.bash` surface a *non-blocking*
warning (they must not refuse to disconnect — that would only strand you
on the exit node, and teardown recovery covers any fallout). It
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

### tmux session restore

`bin/tmux-bootstrap.bash` owns starting the tmux server and replaying the
tmux-resurrect snapshot. **`@continuum-restore` is `off` and must stay
off** — leaving it on races the bootstrap for the same snapshot.

Continuum cannot be trusted to restore. Both its restore hook and its
*save* hook are gated on `another_tmux_server_running_on_startup`, which
is (`scripts/helpers.sh`):

```sh
ps -u $uid -o "command pid" | grep "^tmux" | grep -v "^tmux source"
```

counted against 1. That pattern matches tmux **clients** exactly as
readily as tmux **servers**. Every interactive fish runs `tmux
new-session …`, and iTerm2 relaunches its saved windows in parallel at
login, so two or three such processes exist in the instant the server
starts. Continuum concludes a rival server is running and returns
without restoring.

The failure is **totally silent, and invisible until the next reboot**:
no message, no log line, the plugin loaded, the status bar normal, and a
snapshot on disk that still looks perfectly healthy. On 2026-08-23 a
reboot came up with every session gone while
`~/.local/share/tmux/resurrect/last` held all of them. Restore itself
was never broken — running `restore.sh` by hand replayed the snapshot
fine. Only the trigger failed. **Do not re-chase this as a resurrect or
a tmux-version bug.**

A second consequence of the same predicate: it also gates
`add_resurrect_save_interpolation`, so the race can silently stop
*saving* too. Serializing the server start fixes both — when continuum
loads there is exactly one client and one server, so its count is 1.

Invariants, all enforced by `doctor.bash`:

- `@continuum-restore 'off'` in `.tmux.conf`.
- `apps/fish/config.fish` calls `dotfiles tmux-bootstrap` and attaches to
  `main` only on `primary`. Doing the `tmux new-session` decision inline
  is what caused the loss.
- A snapshot newer than the current tmux server's `#{start_time}` (past
  two save intervals of grace). This is the only honest test that saving
  still works, and the only check that would have caught the outage
  before a reboot did.
- The wait loop in the bootstrap must not run `tmux`. A probe there lands
  in the very `ps` snapshot continuum samples, recreating the miscount.

`tmux_snapshot_health` in `bin/lib/tmux-snapshot.sh` is the single
classifier for snapshot freshness (`ok:<min>` / `stale:<min>` /
`warming:<sec>` / `no-snapshot` / `no-server` / `no-tmux`). It only ever
*under*-reports freshness — a pending save reads as `warming`, never as
`ok` — because it exists to catch saving that stopped and must not
invent a save that never landed. Adding a failure mode = new state there
+ an arm in `bin/doctor.bash`'s case + a case in
`tests/test_tmux_snapshot.py`.

Behaviour is locked by `tests/test_tmux_bootstrap.py` and
`tests/test_tmux_snapshot.py`, which drive the scripts against a stubbed
tmux — they never touch a real server.

## Conventions

- Bash scripts: `set -euo pipefail` at the top. Use `command_exists` for
  tool detection. Use `status_msg "..."` (defined in `setup.bash`) for
  visible progress lines so output stays consistent.
- Script extensions: `.bash` for scripts with a `#!/bin/bash` or
  `#!/usr/bin/env bash` shebang (the macOS `/bin/sh` is bash 3.2 in
  POSIX mode, so a bash-shebang script under a `.sh` name silently lies
  about what it needs). `.sh` is reserved for `#!/bin/sh` POSIX scripts
  and for sourced libraries under `bin/lib/` that declare
  `# shellcheck shell=bash` instead of carrying a shebang. The
  `sh-extension` pre-commit hook
  (`.pre-commit-config.yaml` → `bin/check-sh-extension.bash`) enforces
  this in CI — adding a new `.sh` file with a bash shebang fails the
  lint job.
- Prefer fish abbreviations (`abbr -a`) over functions when the only
  job is text expansion — abbrs preserve history readability.
- Network operations in setup scripts (clones, installers, package
  managers) go through `retry` from `bin/lib/retry.sh` — 3 attempts
  with linear backoff, then `|| status_msg "WARN: ..."` so setup still
  reaches its closing doctor summary instead of dying on a blip.
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

**Be careful editing template-synced workflows.** `template-sync.yaml`
3-way-merges each synced file against the last-synced template
version, so local edits persist across syncs — but if upstream later
touches the same lines, the sync opens a conflict PR with merge
markers for manual resolution. PR #112 deliberately removed
`.github/workflows/claude.yaml` (the `@claude` auto-resolve responder)
and `security-vulnerability-scan.yaml`; only the latter was added to
`template-sync.yaml`'s `EXCLUDE_PATHS`, so a later sync
(`bef5bfd`) silently resynced `claude.yaml` back in. It is live again
today — if that's unwanted, add it to `EXCLUDE_PATHS` too; if it's
wanted, this note can just be deleted. Synced files include:

- `.github/workflows/template-sync.yaml`
- `.github/workflows/dependabot-auto-merge.yaml`
- `.github/workflows/phone-home.yaml`
- `.github/workflows/claude.yaml`

The full list is in `template-sync.yaml`'s `SYNC_PATHS` env.
Substantive refactors of those scripts (e.g. for shellcheck coverage)
should still land upstream in
`alexander-turner/claude-automation-template` rather than accumulating
local drift that invites conflict PRs.

This repo symlinks `.hooks/{pre-push,prepare-commit-msg}` into `bin/`,
where a naive sync `cp` would write *through* the live link and corrupt
the target.
`template-sync.sh`'s `process_file()` now skips any synced path that
is, or sits under, a symlink generically (folded upstream into
`alexander-turner/claude-automation-template`), so this is handled by
the shared script, not a local patch.

Local customizations that still diverge from the template (fold
upstream when resolving the next sync conflict PR, then drop the
bullet):

- The dry-run input fix: `inputs.dry-run` is a boolean, so step
  conditions must compare `== true` / `!= true` — comparing to the
  string `'true'` never matches, which made "dry run" dispatches open
  real PRs. (The `actionlint` pre-commit hook now flags this class of
  expression-type mismatch.)
- The `sh-extension` pre-commit hook's `exclude` pattern additionally
  skips `.github/scripts/` and `.hooks/lint-skills.sh`: both are
  populated verbatim by `template-sync` from files the template itself
  names `*.sh` with a bash shebang, which this repo's own convention
  would otherwise flag. Renaming them locally would just have the next
  sync recreate the `.sh` originals alongside the renamed `.bash`
  copies — this is a permanent local exemption, not a to-fold bug.

**Known unresolved: several template-synced workflows still trigger on
`push: branches: [main]` (or `["main"]`) only, but this repo's default
branch is `master`** — so their `push` path silently never fires here
(`zizmor.yaml`, `hook-lifecycle.yaml`, `format-check.yaml`,
`auto-resolve-conflicts.yaml`, `pr-meta-privileged.yaml`,
`sync-required-checks.yaml`). Their `pull_request` triggers still work,
which is why this went unnoticed. Left unfixed here deliberately: three
of these back `# required-check: true` reporters
(`format-check-passed`, `hook-lifecycle-passed`, `zizmor-passed`) that
`sync-required-checks.yaml` uses as the *complete* source of truth for
branch-protection's required checks, and that annotation coverage is
thin (only those 3 in the whole tree) — flipping triggers live without
first confirming what the ruleset actually requires today risks
silently dropping a currently-required check. Before fixing: run
`sync-required-checks.yaml` via `workflow_dispatch` with
`check-only: true` to see the actual drift, annotate any reporter that
should be required but isn't, then add `master` to each trigger.

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
