"""Setup → uninstall round-trip.

Enforces CLAUDE.md's "Uninstall upkeep" invariant: every safe_link in setup.bash
whose target lives in $HOME has a matching remove in uninstall.bash (typically
via bin/lib/symlinks.sh). Scope matches uninstall.bash — symlinks only, not the
touch-files setup.bash creates.
"""

import os
import subprocess
from pathlib import Path

DOTFILES = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)


def _repo_symlinks(home: Path) -> list[Path]:
    """Symlinks under `home` that point into the dotfiles repo."""
    found: list[Path] = []
    for root, dirs, files in os.walk(home, followlinks=False):
        for name in (*dirs, *files):
            p = Path(root) / name
            if p.is_symlink() and os.readlink(p).startswith(f"{DOTFILES}{os.sep}"):
                found.append(p)
    return sorted(found)


def _run(script: str, *args: str, home: Path) -> None:
    subprocess.run(
        ["bash", str(DOTFILES / script), *args],
        env={**os.environ, "HOME": str(home)},
        check=True,
    )


def test_uninstall_removes_every_setup_symlink(tmp_path: Path) -> None:
    _run("setup.bash", "--link-only", home=tmp_path)
    installed = _repo_symlinks(tmp_path)
    assert installed, "setup.bash --link-only produced zero repo symlinks — test is no longer testing anything"

    _run("bin/uninstall.bash", "--yes", home=tmp_path)
    leftover = _repo_symlinks(tmp_path)
    assert not leftover, (
        "uninstall.bash left repo-pointing symlinks in $HOME. Likely cause: a "
        "safe_link in setup.bash has no matching entry in bin/lib/symlinks.sh. "
        f"Leftover: {leftover}"
    )


def test_uninstall_restores_most_recent_backup(tmp_path: Path) -> None:
    """CLAUDE.md: uninstall.bash "restor[es] the most recent backup when one
    exists." Simulates the post-safe_link-backup state directly (a symlink at
    the target plus a same-named file under the latest backup stamp dir)
    rather than driving safe_link's interactive clobber prompt.
    """
    home = tmp_path / "home"
    home.mkdir()
    backup_root = home / ".dotfiles-backup"

    older = backup_root / "20260101T000000Z"
    older.mkdir(parents=True)
    (older / ".gitconfig").write_text("stale backup — must not be restored\n")

    latest = backup_root / "20260102T000000Z"
    latest.mkdir(parents=True)
    original_content = "[user]\n\tname = Original User\n"
    (latest / ".gitconfig").write_text(original_content)

    target = home / ".gitconfig"
    target.symlink_to(DOTFILES / ".gitconfig")

    subprocess.run(
        ["bash", str(DOTFILES / "bin" / "uninstall.bash"), "--yes"],
        env={**os.environ, "HOME": str(home)},
        check=True,
    )

    assert not target.is_symlink()
    assert target.read_text() == original_content
