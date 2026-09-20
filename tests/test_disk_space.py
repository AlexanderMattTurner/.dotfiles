"""bin/lib/disk-space.sh — free-space classification and lima image sizing.

Drives the lib against the real `df` and `du` on a real path rather than
stubbing their replies, and moves the *thresholds* around the machine's actual
free space to reach each state. Stubbing df wholesale would only test that the
lib can parse a string this file wrote; the parsing is the part that breaks
across platforms, so it stays real. The stubbed cases are the two no real df
produces on demand: a missing binary, and a record wrapped onto a second line.

The states must track their consumer, bin/doctor.bash's "Disk space" section.
That link is tested end-to-end through the real script rather than by grepping
its source: a source grep still passes after the lib renames a state, while
doctor silently falls through to its unhandled-state arm at runtime.

The distinction that matters is the one a percent-used figure hides: absolute
headroom, because a kata VM wants tens of GiB to grow into regardless of how
large the volume is.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
LIB_SH = REPO / "bin" / "lib" / "disk-space.sh"

REAL_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def _run_lib(
    snippet: str, extra_env: dict[str, str] | None = None, expect_rc: int = 0
) -> str:
    proc = subprocess.run(
        ["bash", "-c", f'source "{LIB_SH}" && {snippet}'],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        env={
            "PATH": REAL_PATH,
            "HOME": "/nonexistent-home-so-the-default-paths-cannot-resolve",
            **(extra_env or {}),
        },
    )
    assert proc.returncode == expect_rc, (
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return proc.stdout.strip()


def _free_gib(target: Path) -> int:
    out = _run_lib("disk_free_gib", {"DISK_SPACE_TARGET": str(target)})
    assert out.isdigit(), f"disk_free_gib printed {out!r}"
    return int(out)


def _health(target: Path, low: int, critical: int) -> str:
    return _run_lib(
        "disk_space_health",
        {
            "DISK_SPACE_TARGET": str(target),
            "DISK_LOW_GIB": str(low),
            "DISK_CRITICAL_GIB": str(critical),
        },
    )


# ── rounding ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kib, gib",
    [(0, 0), (524288, 0), (1048576, 1), (1572864, 1), (2097152, 2), (34 * 1048576, 34)],
)
def test_kib_to_gib_truncates(kib: int, gib: int) -> None:
    """Truncation, not rounding: 1.5GiB must read as 1, never 2.

    doctor suppresses a sub-1GiB figure as noise, so the 524288 case is the one
    that decides whether a note is printed at all.
    """
    assert _run_lib(f"disk_kib_to_gib {kib}") == str(gib)


# ── free-space classification ───────────────────────────────────────────────


def test_free_gib_is_plausible(tmp_path: Path) -> None:
    """Non-vacuity: a real df on a real path yields a real, bounded number.

    Strictly greater than zero — a parse that silently yielded 0 would read as
    `critical` forever, which is the exact failure this guards.
    """
    assert 0 < _free_gib(tmp_path) < 1_000_000


@pytest.mark.parametrize(
    "low_offset, critical_offset, expected",
    [
        (-1, 0, "ok"),  # free is above low
        (0, 0, "ok"),  # boundary: free == low is still ok (comparison is -lt)
        (+1, 0, "low"),  # boundary: one GiB under low
        (+1, +1, "critical"),  # boundary: one GiB under critical
        (+10, +5, "critical"),  # critical wins when both predicates hold
    ],
)
def test_thresholds_classify(
    tmp_path: Path, low_offset: int, critical_offset: int, expected: str
) -> None:
    free = _free_gib(tmp_path)
    state = _health(
        tmp_path, low=max(free + low_offset, 0), critical=max(free + critical_offset, 0)
    )
    assert state == f"{expected}:{free}"


def test_missing_df_is_unknown(tmp_path: Path) -> None:
    """Headroom must read as unknowable, never as healthy, when df is absent."""
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    # The override is scoped to the call, not the process environment: emptying
    # PATH outright would also hide `bash` from the launcher.
    assert (
        _run_lib(
            f'PATH="{empty_bin}" disk_space_health',
            {"DISK_SPACE_TARGET": str(tmp_path)},
        )
        == "unknown"
    )


def test_wrapped_df_record_is_unknown(tmp_path: Path) -> None:
    """The failure `df -P` exists to prevent: a long device name wrapping.

    Without -P, df breaks the record across two lines, so row 2 holds only the
    filesystem name and the numbers land on row 3 — every column shifts. Reading
    row 2 field 4 then yields a filesystem name or nothing, never a size. This
    stub reproduces that shape, so dropping -P would turn this red.
    """
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "df"
    stub.write_text(
        "#!/bin/sh\n"
        "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        "echo '/dev/mapper/a-very-long-device-name-that-wraps-the-record'\n"
        "echo '  489350984 396135176 39230464  91% /'\n"
    )
    stub.chmod(0o755)
    assert (
        _run_lib(
            "disk_space_health",
            {"PATH": f"{stub_bin}:{REAL_PATH}", "DISK_SPACE_TARGET": str(tmp_path)},
        )
        == "unknown"
    )


def test_unparseable_df_exits_2(tmp_path: Path) -> None:
    """The documented exit-code contract, asserted directly rather than only
    through the `unknown` string it produces."""
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    _run_lib(
        f'PATH="{empty_bin}" disk_free_gib',
        {"DISK_SPACE_TARGET": str(tmp_path)},
        expect_rc=2,
    )


def test_nonexistent_target_is_unknown(tmp_path: Path) -> None:
    assert _health(tmp_path / "absent", low=40, critical=15) == "unknown"


# ── lima image sizing ───────────────────────────────────────────────────────


def _lima_kib(lima_home: Path) -> str:
    return _run_lib("disk_lima_image_kib", {"LIMA_HOME": str(lima_home)})


def _make_instance(lima_home: Path, name: str, kib: int) -> None:
    inst = lima_home / name
    inst.mkdir(parents=True)
    (inst / "disk").write_bytes(b"\0" * (kib * 1024))


def test_no_lima_home_prints_nothing(tmp_path: Path) -> None:
    assert _lima_kib(tmp_path / "absent") == ""


def test_empty_lima_home_prints_nothing(tmp_path: Path) -> None:
    lima = tmp_path / "lima"
    lima.mkdir()
    assert _lima_kib(lima) == ""


def test_sums_across_instances(tmp_path: Path) -> None:
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)
    _make_instance(lima, "gb-kata-lab", 256)

    total = _lima_kib(lima)
    assert total.isdigit(), f"printed {total!r}"
    # du reports allocated blocks, so the sum is at least the bytes written and
    # not wildly more; an exact figure would encode the filesystem's block size.
    assert 768 <= int(total) <= 768 + 128


@pytest.mark.parametrize("noise", ["_disks", "_config"])
def test_underscore_entries_are_not_instances(tmp_path: Path, noise: str) -> None:
    """lima's own `_config` / `_disks` are not VMs and must not be counted."""
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)
    only_instance = int(_lima_kib(lima))

    _make_instance(lima, noise, 512)
    assert int(_lima_kib(lima)) == only_instance


def test_files_in_lima_home_are_ignored(tmp_path: Path) -> None:
    """Only directories are instances; a stray file must not be summed in."""
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)
    only_instance = int(_lima_kib(lima))

    (lima / "stray.log").write_bytes(b"\0" * (256 * 1024))
    assert int(_lima_kib(lima)) == only_instance


# ── consumer contract, end to end ───────────────────────────────────────────


def _doctor_report(env_overrides: dict[str, str]) -> str:
    """Run the real doctor.bash and return its whole report.

    doctor exits non-zero whenever any check fails, which is expected here and
    unrelated to this section, so the status is ignored and the output parsed.
    """
    proc = subprocess.run(
        ["bash", str(REPO / "bin" / "doctor.bash"), "--no-refresh", "--verbose"],
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
        cwd=REPO,
    )
    return proc.stdout


@pytest.mark.parametrize(
    "overrides, marker, detail",
    [
        ({"DISK_LOW_GIB": "0", "DISK_CRITICAL_GIB": "0"}, "PASS", ""),
        ({"DISK_LOW_GIB": "999999", "DISK_CRITICAL_GIB": "0"}, "FAIL", "reclaim:"),
        (
            {"DISK_LOW_GIB": "999999", "DISK_CRITICAL_GIB": "999999"},
            "FAIL",
            "writes will start failing",
        ),
        ({"DISK_SPACE_TARGET": "/nonexistent-volume-path"}, "SKIP", ""),
    ],
    ids=["ok", "low", "critical", "unknown"],
)
def test_doctor_reports_every_state(
    overrides: dict[str, str], marker: str, detail: str
) -> None:
    """Every state the classifier emits must reach a real arm in doctor.

    Driven through the script, so renaming a state in the lib without updating
    doctor turns this red — where a grep over doctor's source would not, since
    the old literal is still present in the file. Each case also pins the detail
    that makes its arm actionable, so the arms cannot collapse into each other.
    """
    out = _doctor_report(overrides)
    labels = [ln for ln in out.splitlines() if "free space" in ln]
    assert len(labels) == 1, out
    assert marker in labels[0], labels[0]
    assert "unhandled" not in out
    if detail:
        assert detail in out


def test_doctor_suppresses_a_sub_gib_lima_note(tmp_path: Path) -> None:
    """The rounding doctor owns: under a whole GiB, print no note at all.

    The lib reports KiB precisely so this boundary is testable with a kilobyte
    fixture; without the suppression the report would read "hold 0GiB".
    """
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)

    out = _doctor_report(
        {"DISK_LOW_GIB": "999999", "DISK_CRITICAL_GIB": "0", "LIMA_HOME": str(lima)}
    )
    assert "free space" in out
    assert "lima VM images" not in out


def test_sourcing_emits_nothing() -> None:
    """Sourcing must be silent — doctor prints one line per check, not per lib."""
    assert _run_lib("true") == ""
