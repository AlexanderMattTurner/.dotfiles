"""setup.bash's install_homebrew() — curl failure must propagate as a failure.

`bash -c "$(curl ...)"` discards curl's own exit code: a network failure that
yields empty stdout would otherwise run `bash -c ""`, which exits 0 — silently
"succeeding" without installing anything, and defeating the `retry` wrapper
around it in setup.bash. Extracts the function verbatim (rather than sourcing
the whole script, which has side effects) and runs it against a stub `curl`
that fails.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SETUP_BASH = REPO / "setup.bash"


def _extract_install_homebrew() -> str:
    src = subprocess.run(
        ["sed", "-n", "/^install_homebrew() {/,/^}/p", str(SETUP_BASH)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert src.strip(), "install_homebrew() not found in setup.bash"
    return src


def _run_with_stub_curl(tmp_path: Path, curl_body: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(f"#!/bin/sh\n{curl_body}\n")
    curl_stub.chmod(0o755)

    script = _extract_install_homebrew() + "\ninstall_homebrew\n"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=10
    )


def test_curl_failure_is_reported_as_failure(tmp_path: Path) -> None:
    # A network failure: curl exits non-zero and prints nothing.
    result = _run_with_stub_curl(tmp_path, "exit 1")
    assert result.returncode != 0


def test_curl_success_is_reported_as_success(tmp_path: Path) -> None:
    # A trivial but valid install script: curl exits 0 with a real body.
    result = _run_with_stub_curl(tmp_path, "echo 'exit 0'")
    assert result.returncode == 0
