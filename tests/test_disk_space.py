"""bin/lib/disk-space.sh — free-space classification and lima image sizing.

Drives the lib against the real `df` and `du` on a real path rather than
stubbing their replies, and moves the *thresholds* around the machine's actual
free space to reach each state. Stubbing df would only test that the lib can
parse a string this file wrote; the parsing is the part that broke on other
platforms, so it stays real. The one stubbed case is the unparseable-df path,
which no real df produces on demand.

The states must track their consumer, bin/doctor.bash's "Disk space" section.
The distinction that matters is the one a percent-used figure hides: absolute
headroom, because a kata VM wants tens of GiB to grow into regardless of how
large the volume is.
"""

import subprocess
from pathlib import Path


DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
LIB_SH = DOTFILES / "bin" / "lib" / "disk-space.sh"

REAL_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def _run_lib(snippet: str, extra_env: dict[str, str] | None = None) -> str:
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
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
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


# ── free-space classification ───────────────────────────────────────────────


def test_free_gib_is_plausible(tmp_path: Path) -> None:
    """Non-vacuity: a real df on a real path yields a real, bounded number.

    Guards the failure this lib exists to catch being masked by a parse that
    silently yields 0 — which would read as `critical` forever.
    """
    free = _free_gib(tmp_path)
    assert 0 <= free < 1_000_000


def test_ample_headroom_is_ok(tmp_path: Path) -> None:
    free = _free_gib(tmp_path)
    assert _health(tmp_path, low=max(free - 1, 0), critical=0) == f"ok:{free}"


def test_below_low_threshold_is_low(tmp_path: Path) -> None:
    free = _free_gib(tmp_path)
    assert _health(tmp_path, low=free + 1, critical=0) == f"low:{free}"


def test_below_critical_threshold_is_critical(tmp_path: Path) -> None:
    """critical must win over low — both predicates are true at this point."""
    free = _free_gib(tmp_path)
    assert _health(tmp_path, low=free + 10, critical=free + 1) == f"critical:{free}"


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


def test_unparseable_df_output_is_unknown(tmp_path: Path) -> None:
    """A df whose columns shift must not be read as a GiB count."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "df"
    stub.write_text("#!/bin/sh\necho 'Filesystem blocks'\necho 'weird output here'\n")
    stub.chmod(0o755)
    assert (
        _run_lib(
            "disk_space_health",
            {
                "PATH": f"{stub_bin}:{REAL_PATH}",
                "DISK_SPACE_TARGET": str(tmp_path),
            },
        )
        == "unknown"
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


def test_underscore_entries_are_not_instances(tmp_path: Path) -> None:
    """lima's own `_config` / `_disks` are not VMs and must not be counted."""
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)
    only_instance = int(_lima_kib(lima))

    _make_instance(lima, "_disks", 512)
    _make_instance(lima, "_config", 512)
    assert int(_lima_kib(lima)) == only_instance


def test_files_in_lima_home_are_ignored(tmp_path: Path) -> None:
    """Only directories are instances; a stray file must not be summed in."""
    lima = tmp_path / "lima"
    _make_instance(lima, "gb-kata", 512)
    only_instance = int(_lima_kib(lima))

    (lima / "stray.log").write_bytes(b"\0" * (256 * 1024))
    assert int(_lima_kib(lima)) == only_instance


# ── consumer contract ───────────────────────────────────────────────────────


def test_doctor_handles_every_state() -> None:
    """Every state the classifier can print needs an arm in doctor's case.

    CLAUDE.md's rule is that adding a failure mode means a new state here plus
    a case in bin/doctor.bash; this is what makes skipping the second half a
    test failure rather than a silent `unhandled state` at runtime.
    """
    doctor = (DOTFILES / "bin" / "doctor.bash").read_text()
    section = doctor.split('section "Disk space"', 1)[1].split("# ── cron", 1)[0]
    for state in ("ok:*", "low:*", "critical:*", "unknown"):
        assert f"{state})" in section, f"doctor.bash has no arm for {state}"


def test_sourcing_emits_nothing() -> None:
    """Sourcing must be silent — doctor prints one line per check, not per lib."""
    assert _run_lib("true") == ""
