"""bin/lib/bw-common.sh::bw_ensure_session — the vault unlock never comes from storage.

A stored master password is readable by every process running as the user,
and beside the vault file bw keeps on disk it is the whole vault. So the
unlock asks at the terminal, and a caller that has no terminal fails instead
of reaching for a cached copy. These tests drive the function against stub
`bw-node` and `security` binaries and assert neither is ever consulted when
nobody can type the password.
"""

import os
import re
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
LIB = REPO / "bin" / "lib" / "bw-common.sh"

# Both stubs only record that they ran; a real answer is never needed because
# every case here must stop before calling them.
STUB = '#!/bin/bash\nprintf \'%s\\n\' "$(basename "$0") $*" >> "$CALL_LOG"\n'


def _ensure_session(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess, str]:
    """Run bw_ensure_session with no controlling terminal; return (result, calls)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("bw-node", "security", "secret-tool"):
        stub = bindir / name
        stub.write_text(STUB)
        stub.chmod(0o755)
    call_log = tmp_path / "calls.log"
    call_log.touch()
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; bw_ensure_session; echo "session=${{BW_SESSION:-}}"'],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        # A new session has no controlling terminal, so /dev/tty cannot be
        # opened — the same state as a backgrounded shell-startup job or CI.
        start_new_session=True,
        env={
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "USER": "tester",
            "CALL_LOG": str(call_log),
            **env,
        },
    )
    return result, call_log.read_text()


def test_exported_session_is_reused(tmp_path: Path) -> None:
    result, calls = _ensure_session(tmp_path, BW_SESSION="already-unlocked")
    assert "session=already-unlocked" in result.stdout
    assert calls == "", f"an existing session must not call anything, got: {calls}"


def test_caller_without_a_terminal_fails_and_reads_no_stored_secret(tmp_path: Path) -> None:
    result, calls = _ensure_session(tmp_path)
    assert "session=\n" in result.stdout, "no session may be invented"
    assert "vault is locked" in result.stderr
    assert calls == "", f"a locked vault with no terminal must not consult bw or a secret store, got: {calls}"


def test_unattended_caller_fails_and_reads_no_stored_secret(tmp_path: Path) -> None:
    result, calls = _ensure_session(tmp_path, BW_NONINTERACTIVE="1")
    assert "session=\n" in result.stdout
    assert "vault is locked" in result.stderr
    assert calls == "", f"an unattended caller must not consult bw or a secret store, got: {calls}"


def test_no_script_stores_a_secret() -> None:
    """`secret_set` is how the master password and API key reached the Keychain."""
    scripts = [
        REPO / "setup.bash",
        *(REPO / "bin").glob("*.bash"),
        *(REPO / "bin" / "lib").glob("*.sh"),
    ]
    assert scripts, "glob found no shell scripts under bin/"
    call = re.compile(r"^\s*secret_set\s+\S", re.MULTILINE)
    offenders = [
        str(p.relative_to(REPO))
        for p in scripts
        if p.name != "secret-store.sh" and call.search(p.read_text())
    ]
    assert not offenders, f"secret_set call in {offenders}; nothing may cache a Bitwarden unlock secret"


def test_fish_config_runs_no_background_seed() -> None:
    """A background seed can only work with a stored master password."""
    config = (REPO / "apps" / "fish" / "config.fish").read_text()
    seed_lines = [
        line for line in config.splitlines()
        if "bw-seed-envchain.bash" in line and not line.lstrip().startswith("#")
    ]
    assert seed_lines, "config.fish no longer references the seed script; update this test"
    backgrounded = [line for line in seed_lines if "&" in line or "disown" in line or "seed_cmd" in line]
    assert not backgrounded, f"config.fish backgrounds the seed: {backgrounded}"
