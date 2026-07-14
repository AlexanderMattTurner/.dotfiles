# .dotfiles

To install:

```bash
git clone https://github.com/alexander-turner/.dotfiles ~/.dotfiles && cd ~/.dotfiles && bash setup.bash
```

## Features

1. Installs `fish` shell and sets it as the default shell. `fish` has autocomplete, syntax highlighting, and indicates when a command is invalid.
   ![fish suggests command completions.](https://fishshell.com/assets/img/screenshots/autosuggestion.webp)

2. Also installs a cool `fish` theme, [`tide`](https://github.com/IlanCosman/tide):

   ![Compares tide theme configurations for the fish shell.](https://github.com/IlanCosman/tide/raw/assets/images/header.png)

3. Installs [`zoxide`](https://github.com/ajeetdsouza/zoxide) for quick directory navigation. Once you've been to directory `dir`, just hit `j dir` to go back there.
4. Installs `neovim` and sets it as default editor. Furthermore, sets up `LazyVim`, which is basically a full-fledged IDE.[^mini-pairs]
   ![Showing off the LazyVim CLI IDE.](https://user-images.githubusercontent.com/292349/213447056-92290767-ea16-430c-8727-ce994c93e9cc.png)

5. Installs a bunch of nice shortcuts, including `git` aliases (e.g. `git add` -> `ga`).
6. Overrides `rm` in favor of the reversible `tp` (`trash-put`) command. No more accidentally permanently deleting crucial files!
7. Installs `tmux` with the `tmux-restore` and `tmux-continuum` plugins. Basically, this means that your `tmux` sessions will be saved and restored automatically. No more losing your work when your computer crashes!
8. Uses `mosh` as the default for `ssh` connections. Mosh provides predictive local echo (no lag on keystrokes) and seamless roaming across network changes. The fish `ssh` wrapper automatically falls back to real ssh when you use flags mosh doesn't support (port forwarding, agent forwarding, jump hosts, etc.).
9. Installs `envchain` (OS Keychain at runtime) plus the [Bitwarden CLI](https://bitwarden.com/help/cli/) for cross-machine secret sync. API keys live encrypted in your Bitwarden vault; a background sync at shell startup pulls updates into envchain so wrappers like `npm`, `rclone`, `twine`, and `aider_venice` stay zero-prompt at runtime.
10. Configures open source AI-powered development tools, with inference routed through [Venice](https://venice.ai) for end-to-end encryption between client and inference:
    - Automatic commit message generation,
    - Aider for CLI coding,
    - VSCodium with Roo Cline extension for privacy-first AI pair programming,
    - `claude-code-router` (`ccr`) installed via pnpm and supervised by `claude-guard/launchagents/com.turntrout.ccr.plist`, so the private Claude wrappers route through [Venice](https://venice.ai) without the daemon dying across reboots. Store your Venice API key in Bitwarden as `envchain/ai/VENICE_INFERENCE_KEY` (the standard `envchain/<namespace>/<VAR>` convention) — `bwseed` then pulls it into envchain on every machine.
    - Three Claude Code wrappers in `~/.local/bin/` (source lives in the [`claude-guard`](https://github.com/AlexanderMattTurner/agent-glovebox) submodule), all sharing a per-session worktree helper:
      - `claude` — routes through the dotfiles devcontainer (`.devcontainer/`) and execs with `--dangerously-skip-permissions` inside it, where the firewall + bind-mount sandbox already contain the blast radius. If the container for this workspace isn't running, prints a warning and auto-starts it via `devcontainer up`. Before exec, snapshots `/home/node/.claude` (the `claude-code-config` named volume) to `~/.cache/claude-config-backups/<ts>.tar` and keeps the last 10 — restore with `docker exec -i <id> tar -xf - -C /home/node < <snap>`. Bypasses: `CLAUDE_NO_SANDBOX=1` skips the container, `CLAUDE_NO_WORKTREE=1` skips the worktree.
      - `claude-private` — routes through `ccr` to Venice's current `default_code` model (E2EE between client and Venice; Anthropic only ships the CLI). `venice-resolve.bash` queries Venice at install time and caches the resolved id in `~/.cache/claude-wrappers/default_code`, so the wrapper auto-tracks Venice's catalog as models rotate. Set `CLAUDE_PRIVATE_THINK=1` to escalate to `claude-opus-4-7` for heavy reasoning. Runs on the host (ccr listens on host loopback) but still gets a fresh worktree.
      - `claude-paranoid` — same `default_code` routing as `claude-private` but *never* escalates, even with `CLAUDE_PRIVATE_THINK=1`. Use when you want a hard guarantee that no request hops to a closed-lab flagship. Also gets a per-session worktree.

    By default each invocation lands in `<repo>/.worktrees/claude-<ts>` on a `claude/<ts>` branch — isolation per session, no fighting other Claude instances over the same files. Worktrees are listed in `~/.config/git/ignore`. Clean up with `git worktree remove <path> && git branch -D claude/<ts>`.
    - `wut` command to explain shell output.
    - `mods` (Charm) for piping shell output to an LLM, e.g. `<failing-cmd> 2>&1 | mods 'what broke?'`. Routes through Venice via `apps/mods/mods.yml`.
11. Modern Unix toolkit installed via `Brewfile`:
    - `eza` — drop-in `ls` replacement with git-aware listing and tree view; the fish `ls` function prefers it when present.
    
      ![eza directory listing with colored output and git status.](https://github.com/eza-community/eza/raw/main/docs/images/screenshots.png)
    - `ripgrep` (`rg`), `fd`, `bat` — faster grep / find / cat. In interactive fish, `grep` dispatches to `rg` and `cat` to `bat`; bash scripts and subshells still get the real binaries. (`find` is intentionally not shadowed — the argument grammars don't line up.) Use `command grep` or `\grep` to bypass the wrapper.
    
      ![bat displaying a source file with syntax highlighting and line numbers.](https://imgur.com/rGsdnDe.png)
    - `fzf` plus the [`PatrickF1/fzf.fish`](https://github.com/PatrickF1/fzf.fish) plugin: Ctrl-R history search, Ctrl-Alt-F file picker, etc., all with `bat` previews.
    - `git-delta` — paged, syntax-highlighted git diffs, wired up in `.gitconfig`.
   
      ![git-delta side-by-side diff with syntax highlighting and line numbers.](https://user-images.githubusercontent.com/52205/87230973-412eb900-c381-11ea-8aec-cc200290bd1b.png)
    - `mise` — single tool for Node, Python, Ruby, Go versions; auto-activated in fish via `apps/fish/conf.d/mise.fish`.
    - `tokei` (LOC), `dust` (`du` with bars), `bottom` (`btm`, htop-alike).
    - `carapace` — universal completion engine, auto-activated for fish.
    - `shfmt` — shell formatter, also enforced in CI.

12. Project plumbing for AI agents in this repo:
    - `AGENTS.md` symlinks to `CLAUDE.md` so Cursor/Aider/OpenCode pick up the same project context Claude Code uses.
    - `.mcp.json` configures the filesystem MCP server scoped to `~/.dotfiles` for Claude Code sessions in this repo.
    - `.claude/hooks/notify.bash` fires cross-platform desktop notifications when Claude Code needs input.
    - `.claude/hooks/monitor.bash` — **AI safety monitor** implementing the [AI control](https://arxiv.org/abs/2312.06942) pattern: a cheap, trusted model (qwen3-coder-480b via Venice E2EE, or Haiku) reviews every tool call before the primary model can execute it. Two escalation tiers: **deny** (block suspicious action, agent continues) and **ask** (halt agent, get the human — potential misalignment). Logs decisions to `~/.cache/claude-monitor/monitor.jsonl`. Overhead: ~250–700 tokens per monitored call (~$0.05 per 100 calls on Venice). Skips Read calls. Disable with `MONITOR_DISABLED=1`.
    - `.claude/hooks/statusline.bash` shows model, branch, context usage, and session cost in the Claude Code status line.
    - **Push notifications** for monitor alerts: run `bash bin/setup-ntfy.bash` to configure [ntfy.sh](https://ntfy.sh) — the monitor sends a push to your phone on ASK-tier escalations (potential misalignment). Session startup reminds you if not configured.

13. macOS keyboard-driven WM: `aerospace` for tiling.

14. Most importantly, the `goosesay` command. A variant on the classic `cowsay` (which renders text inside a cow's speech bubble), `goosesay` goosens up your terminal just the right amount. For example:

```plaintext
echo "Never gonna give you up" | goosesay
 _________________________
< Never gonna give you up >
 -------------------------
    \
     \
      \     ___
          .´   ""-⹁
      _.-´)  e  _  '⹁
     '-===.<_.-´ '⹁  \
                   \  \
                    ;  \
                    ;   \          _
                    |    '⹁__..--"" ""-._    _.´)
                   /                     ""-´  _>
                  :                          -´/
                  ;                  .__<   __)
                   \    '._      .__.-'   .-´
                    '⹁_    '-⹁__.-´      /
                       '-⹁__/    ⹁    _.´
                      ____< /'⹁__/_.""
                    .´.----´  | |
                  .´ /        | |
                 ´´-/      ___| ;
                          <_    /
                            `.'´
```


## The setup process

`setup.bash` will (with warning) **overwrite** your existing `.bashrc`, `.vimrc`, `.gitconfig`, `.npmrc`, `.tmux.conf`, `.config/nvim`, `.config/fish/config.fish`, `.config/mods/mods.yml`, and any `.aider*` files. On macOS it also links `.aerospace.toml` and `~/Library/com.googlecode.iterm2.plist`. Before clobbering, the previous file is moved to `~/.dotfiles-backup/<UTC-timestamp>/<rel-path>/` — so a misclick on the y/N prompt is recoverable. The new files are symlinked to the files in the repository, so edits to the originals are reflected immediately.

You can also run `bash setup.bash --link-only` to refresh symlinks without reinstalling packages. Both setup paths end by running `bin/doctor.bash`, which reports a green health summary (or tells you exactly what's still broken).

To verify health at any time: `bash bin/doctor.bash` (or `--quiet` for failures-only). To reverse the install — removing only symlinks that point into this repo and restoring the most recent backup — run `bash bin/uninstall.bash` (add `--yes` for non-interactive). The dotfiles repo itself is left untouched.

Once setup has run, those chores are also reachable through the `dotfiles` dispatcher symlinked at `~/.local/bin/dotfiles` (fish completions included): `dotfiles doctor | uninstall | link | lint`.

### Machine-local additions

`setup.bash` creates `~/.extras.fish` and `~/.extras.bash`, which are automatically sourced by `config.fish` and `.bashrc`. These files are not tracked by version control --- include commands you only want for the current machine, but use Bitwarden + envchain for secret management.


## Secret management: Bitwarden vault → envchain runtime

Two layers, both encrypted, complementary roles:

- **Bitwarden vault** (source of truth, cross-machine). End-to-end encrypted; syncs to every device logged into your Bitwarden account. We use the personal API key flow so WebAuthn-only accounts work without 2FA prompts on `bw login`.
- **envchain** (runtime cache, per-machine). Reads from the macOS Keychain, which is silently unlocked at GUI login — so wrappers like `npm`, `rclone`, and `aider_venice` are zero-prompt during normal use.

### GitHub CLI auth via Bitwarden

`setup.bash` authenticates `gh` non-interactively when a PAT is stored in the vault as `envchain/github/PAT`. Generate a token at <https://github.com/settings/tokens> (scopes: `repo`, `read:org` — add `admin:public_key` only if you want `gh` to upload your SSH key for you), then:

```bash
bin/bw-add-secret.bash github PAT
```

On the next `setup.bash` run (or directly via `bin/gh-auth-from-bw.bash`), `gh auth login --with-token` picks it up. No browser, no SSH-key-upload step — useful on headless boxes. 

### Threat model

- Vault decryption requires the master password. A stolen API key without it gets only the encrypted vault.
- The cached master password in macOS Keychain has the same protection envchain values themselves do — Keychain ACL + GUI-login-time unlock.
- Secret values never appear on any process's argv: every helper pipes values stdin→stdin between bw, envchain, and child commands.


## Defense against accidental leaks

Two-layer `gitleaks` gate, same `.gitleaks.toml`: pre-push scans only the commits the push adds (fast even on huge histories), CI scans full history on every PR. 

## Reinstalling programs using `brew`

`Brewfile` contains a list of programs which I like using on my personal Mac. From the repo root, `brew bundle` picks it up automatically.

## Claude Code security

Relatively good security. A firewalled container with minimal permissions, input sanitization, and independent AI monitor oversight. See the [`claude-guard`](https://github.com/AlexanderMattTurner/agent-glovebox) submodule.

[^mini-pairs]: To disable parenthesis matching, delete the `mini.pairs` plugin from `~/.local/share/nvim/lazy/LazyVim/lua/lazyvim/plugins/coding.lua`.
