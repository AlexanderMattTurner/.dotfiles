"""bin/lib/retry.sh — attempt count, backoff, and final-rc contract.

Every network operation in setup.bash/setup_llm.bash goes through `retry`
per CLAUDE.md, so a regression here (e.g. swallowing the real exit code,
or retrying forever) would quietly break the "warn and keep going" pattern
those callers rely on.
"""

import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
RETRY_SH = REPO / "bin" / "lib" / "retry.sh"


def _run_retry(tmp_path: Path, attempts: int, base: int, body: str):
    """Source retry.sh and run `retry <attempts> <base> cmd` against a
    throwaway `cmd` function defined by `body`; return the CompletedProcess.
    """
    script = f"""
source "{RETRY_SH}"
cmd() {{
{body}
}}
retry {attempts} {base} cmd
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )


def test_success_on_first_attempt_returns_zero_without_sleep(tmp_path: Path) -> None:
    result = _run_retry(tmp_path, attempts=3, base=1, body="return 0")
    assert result.returncode == 0
    assert "retry:" not in result.stderr


def test_succeeds_after_transient_failures(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    body = f"""
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" >"{counter}"
[ "$n" -ge 3 ] && return 0
return 1
"""
    result = _run_retry(tmp_path, attempts=3, base=1, body=body)
    assert result.returncode == 0
    assert counter.read_text().strip() == "3"
    assert result.stderr.count("retry:") == 2


def test_persistent_failure_returns_last_exit_code(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    body = f"""
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" >"{counter}"
return 7
"""
    result = _run_retry(tmp_path, attempts=3, base=1, body=body)
    assert result.returncode == 7
    assert counter.read_text().strip() == "3"
