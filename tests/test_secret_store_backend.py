"""Backend selection for bin/lib/secret-store.sh.

secret_store_init picks security / secret-tool / file, and the secret-tool
branch hinges on a D-Bus probe heuristic that is easy to get subtly wrong:
`secret-tool lookup` returns rc=1 for BOTH "item not found" (healthy daemon,
silent) and "D-Bus broken" (prints an error), so the code treats rc=1 as
healthy only when nothing was printed. These drive the function directly with
stubbed `uname` / `secret-tool` / `timeout` so the branching is unit-tested
rather than left to whatever daemon the CI box happens to have.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
SECRET_STORE_SH = REPO / "bin" / "lib" / "secret-store.sh"


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)


def _run_init(
    tmp_path: Path,
    *,
    uname: str = "Linux",
    secret_tool: bool = False,
    probe_rc: int = 1,
    probe_out: str = "",
    env_backend: str | None = None,
) -> str:
    """Return the backend secret_store_init picks under the given conditions.

    `uname` fakes the platform; `secret_tool` toggles whether secret-tool is on
    PATH; `probe_rc`/`probe_out` are what the probe `secret-tool lookup` yields.
    `timeout` is stubbed to drop its duration arg and exec the rest so the real
    3s wrapper doesn't need coreutils' timeout on PATH.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir, "uname", f'printf "%s\\n" {uname!r}\n')
    _stub(bindir, "timeout", 'shift\nexec "$@"\n')
    if secret_tool:
        _stub(
            bindir,
            "secret-tool",
            f'printf "%s" {probe_out!r}\nexit {probe_rc}\n',
        )

    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "USER": "tester",
    }
    if env_backend is not None:
        env["DOTFILES_SECRET_BACKEND"] = env_backend
    else:
        env.pop("DOTFILES_SECRET_BACKEND", None)

    result = subprocess.run(
        ["bash", "-c", f'source "{SECRET_STORE_SH}"\nsecret_store_init\nprintf "%s" "$SECRET_STORE_BACKEND"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_env_override_wins_over_everything(tmp_path: Path) -> None:
    # An explicit override is honored even on a Linux box with no secret-tool.
    assert _run_init(tmp_path, uname="Linux", env_backend="file") == "file"
    assert _run_init(tmp_path, uname="Darwin", env_backend="secret-tool") == "secret-tool"


def test_darwin_uses_security(tmp_path: Path) -> None:
    assert _run_init(tmp_path, uname="Darwin") == "security"


def test_linux_without_secret_tool_falls_back_to_file(tmp_path: Path) -> None:
    assert _run_init(tmp_path, uname="Linux", secret_tool=False) == "file"


def test_probe_success_selects_secret_tool(tmp_path: Path) -> None:
    # rc=0 with a value on stdout: daemon healthy, item present.
    assert (
        _run_init(tmp_path, secret_tool=True, probe_rc=0, probe_out="somevalue")
        == "secret-tool"
    )


def test_probe_silent_rc1_selects_secret_tool(tmp_path: Path) -> None:
    # rc=1 and NO output == "item not found" from a healthy daemon.
    assert (
        _run_init(tmp_path, secret_tool=True, probe_rc=1, probe_out="")
        == "secret-tool"
    )


def test_probe_rc1_with_error_output_falls_back_to_file(tmp_path: Path) -> None:
    # rc=1 WITH output == D-Bus broken. Must NOT pick secret-tool, or every
    # secret op would fail against a dead daemon instead of using the file store.
    assert (
        _run_init(
            tmp_path,
            secret_tool=True,
            probe_rc=1,
            probe_out="Cannot autolaunch D-Bus without X11 $DISPLAY",
        )
        == "file"
    )


@pytest.mark.parametrize(
    "backend,expected",
    [("security", "security"), ("secret-tool", "secret-tool"), ("file", "")],
)
def test_required_cmd_maps_backend_to_command(
    tmp_path: Path, backend: str, expected: str
) -> None:
    """secret_store_required_cmd feeds bw_require_cmds; the file backend needs
    no external command, so it must print an empty string (not 'file')."""
    result = subprocess.run(
        ["bash", "-c", f'source "{SECRET_STORE_SH}"\nsecret_store_required_cmd'],
        env={
            **os.environ,
            "DOTFILES_SECRET_BACKEND": backend,
            "USER": "tester",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == expected
