"""bin/lib/doctor-checks.sh — the core check_symlink / check_command contract.

CLAUDE.md calls doctor.bash "the contract for 'this dotfiles install is
healthy'", but before this only the stale-symlink-prune slice had a direct
test. These drive check_symlink / check_command with test-owned pass/fail
recorders so every PASS/FAIL branch is asserted without a full doctor run.
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
DOCTOR_CHECKS_SH = REPO / "bin" / "lib" / "doctor-checks.sh"

# Harness: define pass/fail as recorders that print PASS/FAIL <label>, source
# the lib, run the check, and echo the resulting MANAGED_LINK_FAIL counter.
HARNESS = f"""
pass() {{ printf 'PASS %s\\n' "$1"; }}
fail() {{ printf 'FAIL %s | %s\\n' "$1" "${{2:-}}"; }}
MANAGED_LINK_FAIL=0
source "{DOCTOR_CHECKS_SH}"
"""


def _run(snippet: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["bash", "-c", HARNESS + snippet],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_correct_symlink_passes(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.write_text("x")
    tgt = tmp_path / "link"
    tgt.symlink_to(src)
    out = _run(
        f'check_symlink "{tgt}" "{src}" mylabel\n'
        'printf "count=%s\\n" "$MANAGED_LINK_FAIL"'
    )
    assert "PASS mylabel" in out
    assert "count=0" in out


def test_missing_target_fails_and_bumps_counter(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.write_text("x")
    tgt = tmp_path / "nope"
    out = _run(
        f'check_symlink "{tgt}" "{src}" mylabel\n'
        'printf "count=%s\\n" "$MANAGED_LINK_FAIL"'
    )
    assert "FAIL mylabel" in out
    assert "missing" in out
    assert "count=1" in out


def test_real_file_at_target_fails(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.write_text("x")
    tgt = tmp_path / "realfile"
    tgt.write_text("i am not a symlink")
    out = _run(f'check_symlink "{tgt}" "{src}" mylabel')
    assert "FAIL mylabel" in out
    assert "not a symlink" in out


def test_wrong_source_fails_with_actual_vs_expected(tmp_path: Path) -> None:
    src = tmp_path / "source"
    other = tmp_path / "other"
    src.write_text("x")
    other.write_text("y")
    tgt = tmp_path / "link"
    tgt.symlink_to(other)
    out = _run(f'check_symlink "{tgt}" "{src}" mylabel')
    assert "FAIL mylabel" in out
    assert str(other) in out and str(src) in out


def test_dangling_symlink_fails(tmp_path: Path) -> None:
    src = tmp_path / "gone"  # never created
    tgt = tmp_path / "link"
    tgt.symlink_to(src)
    out = _run(f'check_symlink "{tgt}" "{src}" mylabel')
    assert "FAIL mylabel" in out
    assert "dangling" in out


def test_check_command_present(tmp_path: Path) -> None:
    stub = tmp_path / "mytool"
    stub.write_text("#!/bin/bash\n")
    stub.chmod(0o755)
    out = _run(
        "check_command mytool",
        env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
    )
    assert "PASS mytool" in out


def test_check_command_absent(tmp_path: Path) -> None:
    out = _run("check_command definitely-not-a-real-command-xyz")
    assert "FAIL definitely-not-a-real-command-xyz" in out
    assert "not on PATH" in out


@pytest.mark.parametrize("flag", ["check_symlink", "check_command"])
def test_doctor_still_sources_the_extracted_lib(flag: str) -> None:
    """Guard against the refactor drifting: doctor.bash must source the lib and
    not redefine these itself, or the unit tests would cover dead code."""
    doctor = (REPO / "bin" / "doctor.bash").read_text()
    assert "doctor-checks.sh" in doctor
    assert f"{flag}() {{" not in doctor, f"{flag} should be defined in the lib, not doctor.bash"
