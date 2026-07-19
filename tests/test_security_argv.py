"""Regression test: macOS Keychain secrets must not transit argv.

`security add-generic-password -w "$value"` and
`security unlock-keychain -p "$pw"` used to put plaintext secrets on the
process argv (visible to any local user via `ps -eo args`). The fix
routes both calls through `security -i` (interactive mode reading from
stdin). These tests stub `security` to record its argv and stdin and
assert the secret stays on the stdin side of the divide.
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


def _make_security_stub(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path]:
    """Install a stub `security` on PATH that records argv and stdin separately.

    argv is recorded one arg per line so substring assertions can't
    accidentally match across arg boundaries.
    """
    argv_log = tmp_path / "argv.log"
    stdin_log = tmp_path / "stdin.log"
    stub = tmp_path / "security"
    stub.write_text(
        '#!/bin/bash\n'
        f'printf "%s\\n" "$@" >> "{argv_log}"\n'
        f'cat >> "{stdin_log}"\n'
        f'exit {exit_code}\n'
    )
    stub.chmod(0o755)
    return argv_log, stdin_log


def _bash(script: str, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        check=check,
        cwd=REPO,
    )


def _argv_tokens(argv_log: Path) -> list[str]:
    return argv_log.read_text().splitlines()


def test_secret_set_security_backend_does_not_leak_value_to_argv(tmp_path: Path) -> None:
    argv_log, stdin_log = _make_security_stub(tmp_path)
    secret = "s3cret-tokn-DO-NOT-LEAK-9f8e7d"

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "DOTFILES_SECRET_BACKEND": "security",
        "USER": "tester",
    }
    _bash(
        f'source "{REPO}/bin/lib/secret-store.sh"\n'
        f'secret_set my-service {secret!r}\n',
        env,
    )

    argv = _argv_tokens(argv_log)
    stdin_text = stdin_log.read_text()
    assert all(secret not in tok for tok in argv), f"secret leaked to argv: {argv!r}"
    assert argv == ["-i"], f"expected exactly `security -i`, got: {argv!r}"
    assert secret in stdin_text, f"secret missing from stdin: {stdin_text!r}"


def test_keychain_unlock_does_not_leak_password_to_argv(tmp_path: Path) -> None:
    argv_log, stdin_log = _make_security_stub(tmp_path)
    password = "p@ssw0rd-DO-NOT-LEAK-1a2b3c"

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    }
    _bash(
        f'source "{REPO}/bin/lib/keychain.sh"\n'
        f'_keychain_unlock {password!r} /fake/login.keychain-db\n',
        env,
    )

    argv = _argv_tokens(argv_log)
    stdin_text = stdin_log.read_text()
    assert all(password not in tok for tok in argv), f"password leaked to argv: {argv!r}"
    assert argv == ["-i"], f"expected exactly `security -i`, got: {argv!r}"
    assert password in stdin_text, f"password missing from stdin: {stdin_text!r}"


def test_keychain_unlock_propagates_failure_exit_code(tmp_path: Path) -> None:
    """`security -i` exits with the result of its last (only) command,
    so _keychain_unlock must surface unlock failure to its caller."""
    _make_security_stub(tmp_path, exit_code=42)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    }
    result = _bash(
        f'source "{REPO}/bin/lib/keychain.sh"\n'
        '_keychain_unlock wrong-pw /fake/login.keychain-db\n',
        env,
        check=False,
    )
    # The stub exits 42; _keychain_unlock pipes into it, so the pipeline's
    # final exit code is 42 (pipefail isn't required — security is the last
    # stage). Caller can branch on it the same way the real code does.
    assert result.returncode == 42, f"expected rc=42, got rc={result.returncode}"


def test_keychain_unlock_rejects_newline_password(tmp_path: Path) -> None:
    """Same line-oriented-parser hazard as secret_set: a raw newline in the
    password can't reach `security -i` safely (it would either truncate the
    command or be read back as a separate one). _keychain_unlock must refuse
    it rather than pass it through — assert security is never even invoked."""
    argv_log, stdin_log = _make_security_stub(tmp_path)
    # Passed via env var, not interpolated into the bash source string: see
    # test_secret_set_security_backend_rejects_newline_value for why.
    smuggled = "legit-part\ndelete-generic-password -s bw-master-password -a tester"

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "SMUGGLED_PW": smuggled,
    }
    result = _bash(
        f'source "{REPO}/bin/lib/keychain.sh"\n'
        '_keychain_unlock "$SMUGGLED_PW" /fake/login.keychain-db\n',
        env,
        check=False,
    )

    assert result.returncode != 0, "_keychain_unlock must fail on a newline-containing password"
    assert not argv_log.exists(), "security must never be invoked with a truncated/injected value"
    assert not stdin_log.exists(), "security must never be invoked with a truncated/injected value"


def test_bw_session_never_on_argv() -> None:
    """BW_SESSION is a live vault-decryption key; it must reach bw only via
    the exported environment variable (which the bw CLI reads natively),
    never as `--session <token>` argv — argv is visible to every same-user
    process via `ps -eo args`, including the monitored model the trusted
    monitor is supposed to contain."""
    scripts = [
        *(REPO / "bin").glob("*.bash"),
        *(REPO / "bin").glob("*.sh"),
        *(REPO / "bin" / "lib").glob("*.sh"),
    ]
    assert scripts, "glob found no shell scripts under bin/"
    # Match `--session <value>` usages (flag plus an argument), not prose
    # mentions of the flag in comments.
    pattern = re.compile(r'--session[= ]+["\']?\$')
    offenders = [
        str(p.relative_to(REPO)) for p in scripts if pattern.search(p.read_text())
    ]
    assert not offenders, (
        f"--session argv found in {offenders}; rely on the exported "
        "BW_SESSION env var instead (see bw_ensure_session in bw-common.sh)"
    )


def test_security_quote_escapes_backslash_and_double_quote(tmp_path: Path) -> None:
    """Values containing " or \\ must round-trip through `security -i`'s parser."""
    argv_log, stdin_log = _make_security_stub(tmp_path)
    # A value the naive `printf '"%s"'` would mis-quote without escaping.
    tricky = 'has "quote" and \\backslash inside'

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "DOTFILES_SECRET_BACKEND": "security",
        "USER": "tester",
    }
    _bash(
        f'source "{REPO}/bin/lib/secret-store.sh"\n'
        f'secret_set my-service {tricky!r}\n',
        env,
    )

    stdin_text = stdin_log.read_text()
    # Stdin should contain the escaped form: \" and \\ inside double quotes.
    assert r'\"quote\"' in stdin_text, f"unexpected stdin: {stdin_text!r}"
    assert r'\\backslash' in stdin_text, f"unexpected stdin: {stdin_text!r}"


def test_secret_set_security_backend_rejects_newline_value(tmp_path: Path) -> None:
    """`security -i` parses one command per line, so a raw newline in the
    value can't be embedded safely — it would either truncate the command or
    be read back as a separate one. secret_set must refuse rather than pass a
    newline-containing value through to `security -i` — assert the value never
    reaches security at all (neither argv nor stdin log is written)."""
    argv_log, stdin_log = _make_security_stub(tmp_path)
    # Passed via env var, not interpolated into the bash source string:
    # Python's repr() of an embedded "\n" prints the two-character escape
    # "\n" (harmless inside bash single-quotes), not a real newline byte —
    # only an env var reliably carries the actual byte through to bash.
    smuggled = "legit-part\ndelete-generic-password -s my-service -a tester"

    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "DOTFILES_SECRET_BACKEND": "security",
        "USER": "tester",
        "SMUGGLED_VALUE": smuggled,
    }
    result = _bash(
        f'source "{REPO}/bin/lib/secret-store.sh"\n'
        'secret_set my-service "$SMUGGLED_VALUE"\n',
        env,
        check=False,
    )

    assert result.returncode != 0, "secret_set must fail on a newline-containing value"
    assert not argv_log.exists(), "security must never be invoked with a truncated/injected value"
    assert not stdin_log.exists(), "security must never be invoked with a truncated/injected value"


# ── Shared `security -i` line-builder (bin/lib/security-cmd.sh, issue #126) ──
#
# The quoting + newline-rejection logic used to be copy-pasted into
# secret-store.sh and keychain.sh (that duplication is what let the newline
# gap re-open in #125). It now lives in one shared helper; these exercise it
# directly and assert the duplication is gone. secret_set's own newline refusal
# is covered by test_secret_set_security_backend_rejects_newline_value above.

SECURITY_CMD_SH = REPO / "bin" / "lib" / "security-cmd.sh"


def _capture(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_security_build_line_quotes_and_escapes_every_token() -> None:
    result = _capture(
        f'source "{SECURITY_CMD_SH}"\n'
        r'security_build_line add-generic-password -s "my svc" -w '
        r'''"has \"quote\" and \\slash"'''
        "\n"
    )
    assert result.returncode == 0, result.stderr
    # Each token is double-quoted; the value's " and \ are backslash-escaped.
    assert result.stdout == (
        r'"add-generic-password" "-s" "my svc" "-w" '
        r'"has \"quote\" and \\slash"'
    ), f"unexpected line: {result.stdout!r}"


def test_security_build_line_rejects_newline_before_emitting() -> None:
    """The guard must run before any quoting/emission: a newline in ANY
    argument fails the whole line with nothing on stdout, so a caller doing
    `line=$(security_build_line ...)` can branch on the exit code."""
    result = _capture(
        f'source "{SECURITY_CMD_SH}"\n'
        # A newline embedded in the value argument.
        "security_build_line add-generic-password -w $'line1\\nline2'\n"
    )
    assert result.returncode != 0, "newline argument must fail the build"
    assert result.stdout == "", f"nothing should be emitted, got: {result.stdout!r}"
    assert "newline" in result.stderr, f"expected a diagnostic, got: {result.stderr!r}"


def test_security_quote_is_not_duplicated_across_libs() -> None:
    """Regression guard for the whole point of #126: `_security_quote` must be
    defined in exactly one place. A future third `security -i` caller sourcing
    the shared helper then can't re-open the newline gap by copying the quoter
    without the guard."""
    definers = [
        p.relative_to(REPO)
        for p in (REPO / "bin").rglob("*.sh")
        if re.search(r"^_security_quote\(\)", p.read_text(), re.MULTILINE)
    ]
    assert definers == [Path("bin/lib/security-cmd.sh")], (
        f"_security_quote should be defined only in security-cmd.sh, found: {definers}"
    )
