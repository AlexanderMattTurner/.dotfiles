"""bin/lib/disk-space.sh + disk_space_check — free-space health.

Two layers, tested two ways:

  - The classifier runs against the real `df`/`du` on a real path, with the
    *thresholds* moved around the machine's actual free space to reach each
    state. Stubbing df wholesale would only prove the lib can parse a string
    this file wrote, and the parsing is the part that breaks across platforms.
    The stubbed cases are the ones no real df produces on demand.
  - The reporting lives in bin/lib/doctor-checks.sh as `disk_space_check`, so
    it is driven directly with stub pass/fail/skip recorders — the same trick
    that file already uses for check_symlink/check_command. That covers every
    arm in milliseconds; a single end-to-end run then proves doctor.bash
    actually calls it.
"""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
LIB_SH = REPO / "bin" / "lib" / "disk-space.sh"
CHECKS_SH = REPO / "bin" / "lib" / "doctor-checks.sh"

REAL_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def _bash(
    snippet: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", snippet],
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


def _run_lib(
    snippet: str, extra_env: dict[str, str] | None = None, expect_rc: int = 0
) -> str:
    proc = _bash(f'source "{LIB_SH}" && {snippet}', extra_env)
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


def _write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    stub.write_text(body)
    stub.chmod(0o755)
    return stub


# ── rounding ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kib, gib",
    [(0, 0), (524288, 0), (1048576, 1), (1572864, 1), (2097152, 2), (34 * 1048576, 34)],
)
def test_kib_to_gib_truncates(kib: int, gib: int) -> None:
    """Truncation, not rounding: 1.5GiB must read as 1, never 2.

    The reporter suppresses a sub-1GiB figure, so the 524288 case decides
    whether a note is printed at all.
    """
    assert _run_lib(f"disk_kib_to_gib {kib}") == str(gib)


@pytest.mark.parametrize("bad", ["", "abc", "12x", "-5", "1 2"])
def test_kib_to_gib_refuses_non_digits(bad: str) -> None:
    """Arithmetic on unvalidated input is both a `set -u` crash and code
    execution: bash evaluates array subscripts recursively, so `a[$(cmd)]` runs
    `cmd`. The readings come from df/du, i.e. outside this repo."""
    _run_lib(f"disk_kib_to_gib {shlex.quote(bad)}", expect_rc=2)


def test_kib_to_gib_does_not_execute_its_argument(tmp_path: Path) -> None:
    """The concrete exploit, pinned: a subscript payload must not run."""
    canary = tmp_path / "canary"
    payload = f"a[$(touch {canary})]"
    _run_lib(f"disk_kib_to_gib {shlex.quote(payload)}", expect_rc=2)
    assert not canary.exists(), "argument was evaluated as arithmetic"


def test_kib_to_gib_survives_no_argument() -> None:
    """Called with nothing under `set -u`, it must return, not abort."""
    proc = _bash(f'set -u; source "{LIB_SH}"; disk_kib_to_gib; echo "rc=$?"')
    assert "rc=2" in proc.stdout, f"{proc.stdout!r} {proc.stderr!r}"
    assert "unbound variable" not in proc.stderr


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


# A df that emits the POSIX single-line record only when -P is present, and the
# wrapped two-line form otherwise — which is what real df does with a long
# device name. Available is 39230464 KiB == 37 GiB after truncation.
_DF_STUB = """#!/bin/sh
echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'
case "$*" in
*-P*) echo '/dev/disk1s5 489350984 396135176 39230464 91% /' ;;
*)
    echo '/dev/mapper/a-very-long-device-name-that-wraps-the-record'
    echo '  489350984 396135176 39230464  91% /'
    ;;
esac
"""


def test_df_is_called_with_dash_P(tmp_path: Path) -> None:
    """Pins the -P flag rather than merely asserting a bad parse is caught.

    Without -P, df wraps a long device name onto a second line and every column
    shifts, so row 2 field 4 is a device name instead of a size. This stub
    reproduces exactly that, conditional on the flag — so dropping -P from the
    call flips the result to `unknown` and turns this red.
    """
    stub_bin = tmp_path / "bin"
    _write_stub(stub_bin, "df", _DF_STUB)
    assert (
        _run_lib(
            "disk_space_health",
            {
                "PATH": f"{stub_bin}:{REAL_PATH}",
                "DISK_SPACE_TARGET": str(tmp_path),
                "DISK_LOW_GIB": "0",
                "DISK_CRITICAL_GIB": "0",
            },
        )
        == "ok:37"
    )


@pytest.mark.parametrize("flavour", ["missing", "wrapped"])
def test_unreadable_df_is_unknown_and_exits_2(tmp_path: Path, flavour: str) -> None:
    """Headroom must read as unknowable, never as healthy — by either route.

    `missing` removes df from PATH; `wrapped` keeps it but shifts the columns.
    Both must reach `unknown`, and both must surface as the documented rc=2 from
    disk_free_gib rather than only as the string derived from it.
    """
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    if flavour == "wrapped":
        # Always wraps: a df that ignores -P, i.e. the parse guard itself.
        _write_stub(
            stub_bin,
            "df",
            "#!/bin/sh\necho 'Filesystem blocks'\necho '/dev/long-name-only'\n",
        )
        path = f"{stub_bin}:{REAL_PATH}"
    else:
        path = str(stub_bin)

    env = {"DISK_SPACE_TARGET": str(tmp_path)}
    # PATH is overridden inside the call, not in the environment: emptying it
    # outright would also hide `bash` from the launcher.
    assert _run_lib(f'PATH="{path}" disk_space_health', env) == "unknown"
    _run_lib(f'PATH="{path}" disk_free_gib', env, expect_rc=2)


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


# ── the reporter (bin/lib/doctor-checks.sh) ─────────────────────────────────

# Stub reporters standing in for doctor's counters, exactly as
# tests/test_doctor_checks.py does for check_symlink/check_command.
_RECORDERS = """
pass() { printf 'PASS %s\\n' "$1"; }
fail() { printf 'FAIL %s\\n' "$1"; [ -n "${2:-}" ] && printf '%s\\n' "$2"; return 0; }
skip() { printf 'SKIP %s (%s)\\n' "$1" "$2"; }
"""


def _check(state: str, lima_kib: str = "", low: str = "25", crit: str = "10") -> str:
    """Drive disk_space_check with the classifier stubbed to `state`."""
    snippet = (
        f'source "{LIB_SH}"\n'
        f'source "{CHECKS_SH}"\n'
        f"{_RECORDERS}\n"
        f"disk_space_health() {{ printf '%s\\n' {shlex.quote(state)}; }}\n"
        f"disk_lima_image_kib() {{ printf '%s' {shlex.quote(lima_kib)}; }}\n"
        "disk_space_check\n"
    )
    proc = _bash(snippet, {"DISK_LOW_GIB": low, "DISK_CRITICAL_GIB": crit})
    assert proc.returncode == 0, f"{proc.stdout!r} {proc.stderr!r}"
    return proc.stdout


@pytest.mark.parametrize(
    "state, marker, detail",
    [
        ("ok:56", "PASS free space (56GiB)", ""),
        ("low:20", "FAIL free space", "20GiB free, under 25GiB"),
        ("critical:5", "FAIL free space", "5GiB free, under 10GiB"),
        ("unknown", "SKIP free space", ""),
        ("warn:3", "FAIL free space", "unhandled disk_space_health state: warn:3"),
    ],
    ids=["ok", "low", "critical", "unknown", "unrecognised"],
)
def test_every_state_reaches_an_arm(state: str, marker: str, detail: str) -> None:
    """Each state gets its own arm, and each arm reports the right figure.

    The low and critical cases carry *different* thresholds (25 vs 10) and pin
    the number printed, so swapping DISK_LOW_GIB for DISK_CRITICAL_GIB in either
    arm turns this red — with identical thresholds the two are indistinguishable.

    The `warn:3` case is the point of the catch-all: renaming a state in the
    classifier without adding its arm must be loud, not silent.
    """
    out = _check(state)
    assert marker in out, out
    if detail:
        assert detail in out, out


def test_critical_names_its_consequence() -> None:
    assert "writes will start failing" in _check("critical:5")


def test_low_does_not_claim_writes_are_failing() -> None:
    """The escalation has to mean something — low must not borrow the warning."""
    assert "writes will start failing" not in _check("low:20")


@pytest.mark.parametrize(
    "lima_kib, expected",
    [
        ("", None),  # no instances at all
        ("512", None),  # under 1GiB: suppressed as noise
        ("1048576", "lima VM images hold 1GiB"),
        ("35651584", "lima VM images hold 34GiB"),
    ],
    ids=["none", "sub-gib", "one-gib", "many-gib"],
)
def test_lima_note_is_sized_and_suppressed(lima_kib: str, expected: str | None) -> None:
    """The rounding the reporter owns, including the sub-1GiB suppression that
    the classifier's KiB return type exists to make testable."""
    out = _check("low:20", lima_kib=lima_kib)
    if expected is None:
        assert "lima VM images" not in out, out
    else:
        assert expected in out, out


def test_remedies_are_offered_only_when_unhealthy() -> None:
    assert "reclaim:" in _check("low:20")
    assert "reclaim:" not in _check("ok:56")


def test_remedy_block_aligns_under_the_detail_column() -> None:
    """fail() indents only the first line of its detail, so the continuation
    lines carry their own 7 spaces. Without them the block breaks the report's
    left margin."""
    detail = [ln for ln in _check("low:20").splitlines() if "reclaim:" in ln]
    assert detail and detail[0].startswith(" " * 7), detail


# ── wiring, end to end ──────────────────────────────────────────────────────


def test_doctor_runs_the_disk_check() -> None:
    """One real doctor.bash run, to prove the section is actually wired in.

    Everything above drives disk_space_check directly; this is the only claim
    that needs the whole script, so it is the only place that pays for it.
    """
    proc = subprocess.run(
        ["bash", str(REPO / "bin" / "doctor.bash"), "--no-refresh", "--verbose"],
        env={**os.environ, "DISK_LOW_GIB": "0", "DISK_CRITICAL_GIB": "0"},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
        cwd=REPO,
    )
    # doctor exits non-zero if any unrelated check fails, so parse, don't assert
    # on status.
    labels = [ln for ln in proc.stdout.splitlines() if "free space" in ln]
    assert len(labels) == 1, proc.stdout
    assert "PASS" in labels[0], labels[0]
    # Pins the measured figure reaching the label, not just the word "free space".
    assert "GiB)" in labels[0], labels[0]
    assert "unhandled" not in proc.stdout


def test_sourcing_emits_nothing() -> None:
    """Silent on both streams — doctor prints one line per check, not per lib."""
    proc = _bash(f'source "{LIB_SH}"; source "{CHECKS_SH}"')
    assert proc.stdout == ""
    assert proc.stderr == ""
