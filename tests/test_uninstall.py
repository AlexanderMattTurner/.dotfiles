"""bin/uninstall.bash — symlink-removal safety + the invariants CLAUDE.md names.

Complements the full-roundtrip integration in test_uninstall_roundtrip.py with:
  * direct unit tests of the extracted remove_dotfile_symlink / latest_backup_root
    (bin/lib/uninstall-lib.sh): repo-pointing links are removed and restored from
    backup; real files and foreign symlinks are left untouched;
  * the macOS-only block is gated on $IS_MAC (skipped on Linux, runs under a
    faked Darwin uname);
  * the "never touch repo_hook_symlinks" invariant — repo plumbing must survive
    an uninstall.
"""

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
UNINSTALL_LIB_SH = REPO / "bin" / "lib" / "uninstall-lib.sh"


def _run_lib(snippet: str, home: Path, backup_root: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f'source "{UNINSTALL_LIB_SH}"\n{snippet}'],
        env={
            **os.environ,
            "HOME": str(home),
            "SAFE_LINK_BACKUP_ROOT": str(backup_root),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# ── remove_dotfile_symlink ───────────────────────────────────────────────────


def test_repo_symlink_removed_and_backup_restored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo_src = tmp_path / "dotfiles" / "foo"
    repo_src.parent.mkdir()
    repo_src.write_text("repo version")
    tgt = home / ".foo"
    tgt.symlink_to(repo_src)

    # A backup captured when setup.bash first clobbered the user's real ~/.foo.
    backup_root = tmp_path / "backups"
    stamp = backup_root / "20260506T143022Z"
    stamp.mkdir(parents=True)
    (stamp / ".foo").write_text("original user data")

    out = _run_lib(f'remove_dotfile_symlink "{tgt}" "{repo_src}"', home, backup_root)
    assert "removed" in out
    assert "restored backup" in out
    # The symlink is gone; the user's original file is back at the target.
    assert not tgt.is_symlink()
    assert tgt.read_text() == "original user data"


def test_real_file_is_left_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tgt = home / ".foo"
    tgt.write_text("i am a real file, not a symlink")
    out = _run_lib(
        f'remove_dotfile_symlink "{tgt}" "/some/repo/foo"', home, tmp_path / "b"
    )
    assert "skip" in out and "not a symlink" in out
    assert tgt.read_text() == "i am a real file, not a symlink"


def test_foreign_symlink_is_left_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("not ours")
    tgt = home / ".foo"
    tgt.symlink_to(elsewhere)
    out = _run_lib(
        f'remove_dotfile_symlink "{tgt}" "/some/repo/foo"', home, tmp_path / "b"
    )
    assert "not pointing into this dotfiles repo" in out
    assert tgt.is_symlink(), "a link we didn't create must survive"


def test_latest_backup_root_picks_lex_largest_stamp(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    for name in ("20260101T000000Z", "20260506T143022Z", "not-a-stamp", "20250101T000000Z"):
        (backup_root / name).mkdir(parents=True)
    out = _run_lib("latest_backup_root", tmp_path / "home", backup_root).strip()
    # Chronological max == lexical max among valid stamps; the junk dir is ignored
    # even though it lex-sorts after every stamp.
    assert out == str(backup_root / "20260506T143022Z")


# ── Full-script invariants (macOS gating + repo_hook_symlinks) ────────────────


def _fake_dotfiles(tmp_path: Path) -> Path:
    """A throwaway DOTFILES_DIR with just enough to run uninstall.bash."""
    dot = tmp_path / "dotfiles"
    (dot / "bin" / "lib").mkdir(parents=True)
    (dot / ".hooks").mkdir()
    shutil.copy(REPO / "bin" / "uninstall.bash", dot / "bin")
    for lib in ("symlinks.sh", "uninstall-lib.sh"):
        shutil.copy(REPO / "bin" / "lib" / lib, dot / "bin" / "lib")
    return dot


def _stub(bindir: Path, name: str, body: str = "exit 0\n") -> None:
    p = bindir / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)


def _run_uninstall(dot: Path, home: Path, bindir: Path, *, darwin: bool) -> str:
    # On the macOS leg we fake `uname` so the $IS_MAC block runs on a Linux CI
    # box, and stub the system tools it would otherwise call.
    if darwin:
        _stub(bindir, "uname", 'printf "Darwin\\n"\n')
    for tool in ("launchctl", "sudo", "crontab"):
        _stub(bindir, tool)
    result = subprocess.run(
        ["bash", str(dot / "bin" / "uninstall.bash"), "--yes"],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "USER": os.environ.get("USER", "tester"),
            "SAFE_LINK_BACKUP_ROOT": str(home / ".dotfiles-backup"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return result.stdout


def test_repo_hook_symlinks_are_never_touched(tmp_path: Path) -> None:
    """CLAUDE.md: uninstall.bash MUST NOT touch repo_hook_symlinks — they are
    repo plumbing, not user dotfiles, and leave with the repo itself."""
    dot = _fake_dotfiles(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()

    # Materialise the repo-internal hook symlink the way setup.bash would.
    hook = dot / ".hooks" / "pre-push"
    hook.symlink_to("../bin/pre-push")

    _run_uninstall(dot, home, bindir, darwin=False)
    assert hook.is_symlink(), "repo hook symlink must survive an uninstall"
    # And the script must not even iterate that list — ignore comment lines,
    # which legitimately mention the invariant.
    code_lines = [
        ln
        for ln in (dot / "bin" / "uninstall.bash").read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    assert not any("repo_hook_symlinks" in ln for ln in code_lines), (
        "uninstall.bash must never invoke repo_hook_symlinks"
    )


def test_macos_block_is_gated_on_is_mac(tmp_path: Path) -> None:
    dot = _fake_dotfiles(tmp_path)
    home = tmp_path / "home"
    la = home / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()

    # A ccr launch-agent symlink pointing into the repo.
    ccr = la / "com.turntrout.ccr.plist"
    ccr.symlink_to(dot / "claude-guard" / "launchagents" / "com.turntrout.ccr.plist")

    # Linux leg (real uname): the macOS block is skipped, so the plist survives.
    out_linux = _run_uninstall(dot, home, bindir, darwin=False)
    assert ccr.is_symlink(), "macOS block must not run on Linux"
    assert "ccr launch agent" not in out_linux

    # Darwin leg (faked uname): the block runs and removes the ccr symlink.
    out_mac = _run_uninstall(dot, home, bindir, darwin=True)
    assert not ccr.is_symlink(), "macOS block should remove the ccr symlink"
    assert "removed" in out_mac
