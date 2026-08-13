"""bin/lib/duplicati-status.sh — backup freshness classification.

Drives the lib against real (tiny) Duplicati databases built with sqlite3, so
it is exercised through the schema it actually reads rather than a stub of
sqlite's replies. The live job database is multi-GB and is written by a
running duplicati-server, so these build their own.

The states must track their consumer, bin/doctor.bash's "Backups" section.
The distinctions that matter are the ones a running server hides: a job that
has never completed, a job configured on a server that lost it, and a job
whose last success is weeks old all present as "the agent is running".
"""

import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
STATUS_SH = DOTFILES / "bin" / "lib" / "duplicati-status.sh"

DAY = 86400


def _make_job_db(path: Path, timestamps: list[int]) -> None:
    """Write a job database holding one Fileset row per completed backup."""
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE "Fileset" ("ID" INTEGER PRIMARY KEY, "Timestamp" INTEGER NOT NULL)'
    )
    conn.executemany(
        "INSERT INTO Fileset (Timestamp) VALUES (?)", [(t,) for t in timestamps]
    )
    conn.commit()
    conn.close()


def _make_server_db(path: Path, job_db_paths: list[Path]) -> None:
    """Write a server database mapping each configured job to its own database."""
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE "Backup" ("ID" INTEGER PRIMARY KEY AUTOINCREMENT, '
        '"Name" TEXT NOT NULL, "Description" TEXT NOT NULL DEFAULT \'\', '
        '"Tags" TEXT NOT NULL, "TargetURL" TEXT NOT NULL, "DBPath" TEXT NOT NULL)'
    )
    conn.executemany(
        "INSERT INTO Backup (Name, Tags, TargetURL, DBPath) VALUES (?, '', 'enc-v1:xx', ?)",
        [(f"Job {i}", str(p)) for i, p in enumerate(job_db_paths)],
    )
    conn.commit()
    conn.close()


def _run_lib(
    snippet: str,
    extra_env: dict[str, str] | None = None,
    args: list[str] | None = None,
) -> str:
    proc = subprocess.run(
        ["bash", "-c", f'source "{STATUS_SH}" && {snippet}', "_", *(args or [])],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": "/nonexistent-home-so-the-default-db-path-cannot-resolve",
            **(extra_env or {}),
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.stdout.strip()


def _health(server_db: Path, stale_days: int = 3) -> str:
    return _run_lib(
        "duplicati_health",
        {"DUPLICATI_SERVER_DB": str(server_db), "DUPLICATI_STALE_DAYS": str(stale_days)},
    )


def test_missing_server_db_is_no_db(tmp_path: Path) -> None:
    assert _health(tmp_path / "absent.sqlite") == "no-db"


def test_server_db_without_jobs_is_no_jobs(tmp_path: Path) -> None:
    """A running server that backs nothing up — the failure with no symptom."""
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [])
    assert _health(server) == "no-jobs"


def test_job_that_never_completed_is_never(tmp_path: Path) -> None:
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server) == "never"


def test_job_db_missing_from_disk_is_never(tmp_path: Path) -> None:
    """A job row pointing at a deleted database must not read as healthy."""
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [tmp_path / "deleted.sqlite"])
    assert _health(server) == "never"


def test_unreadable_job_db_is_never(tmp_path: Path) -> None:
    """A truncated/corrupt job database must not read as healthy either."""
    job = tmp_path / "JOB.sqlite"
    job.write_bytes(b"this is not a sqlite database")
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server) == "never"


@pytest.mark.parametrize("age_days", [0, 1, 3])
def test_recent_backup_is_ok(tmp_path: Path, age_days: int) -> None:
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [int(time.time()) - age_days * DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server) == f"ok:{age_days}"


@pytest.mark.parametrize("age_days", [4, 30])
def test_old_backup_is_stale(tmp_path: Path, age_days: int) -> None:
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [int(time.time()) - age_days * DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server) == f"stale:{age_days}"


def test_stale_threshold_binds_at_its_boundary(tmp_path: Path) -> None:
    """The threshold is read, not hard-coded: the same backup flips either way."""
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [int(time.time()) - 5 * DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server, stale_days=5) == "ok:5"
    assert _health(server, stale_days=4) == "stale:5"


def test_freshness_uses_newest_fileset_not_last_row(tmp_path: Path) -> None:
    """Filesets are not stored in timestamp order after a repair or a restore."""
    now = int(time.time())
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [now - 40 * DAY, now - 1 * DAY, now - 20 * DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])
    assert _health(server) == "ok:1"


def test_freshness_spans_every_configured_job(tmp_path: Path) -> None:
    """Health is the newest backup across jobs, whichever job row comes first."""
    now = int(time.time())
    stale_job = tmp_path / "STALE.sqlite"
    fresh_job = tmp_path / "FRESH.sqlite"
    _make_job_db(stale_job, [now - 90 * DAY])
    _make_job_db(fresh_job, [now - 2 * DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [stale_job, fresh_job])
    assert _health(server) == "ok:2"


def test_health_reads_a_database_a_running_backup_holds(tmp_path: Path) -> None:
    """A backup in flight must not make doctor block or report a false failure.

    This is what the immutable=1 open buys: it takes no locks, so a writer
    holding the job database cannot turn a healthy backup into a reported
    fault. Duplicati writes this database for the whole length of a run —
    14 minutes on this machine — so a locking read would collide daily.
    """
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [int(time.time()) - DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])

    writer = sqlite3.connect(job, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO Fileset (Timestamp) VALUES (1)")
    try:
        # The uncommitted row must not be reported either: an in-flight
        # backup reads as its previous state, never as a newer one.
        assert _health(server) == "ok:1"
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_reading_health_writes_nothing(tmp_path: Path) -> None:
    """doctor is read-only: no content change, no mtime bump, no side files."""
    job = tmp_path / "JOB.sqlite"
    _make_job_db(job, [int(time.time()) - DAY])
    server = tmp_path / "Duplicati-server.sqlite"
    _make_server_db(server, [job])

    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in (server, job)}
    names_before = {p.name for p in tmp_path.iterdir()}
    assert _health(server) == "ok:1"

    for path in (server, job):
        mtime, content = before[path.name]
        assert path.stat().st_mtime_ns == mtime, f"{path.name} mtime changed"
        assert path.read_bytes() == content, f"{path.name} content changed"
    created = {p.name for p in tmp_path.iterdir()} - names_before
    assert created == set(), f"left sqlite side files behind: {sorted(created)}"


def _dataless_count(roots: list[Path]) -> str:
    return _run_lib('duplicati_dataless_count "$@"', args=[str(r) for r in roots])


def test_dataless_count_ignores_ordinary_local_files(tmp_path: Path) -> None:
    """Materialized files are backed up fine, so they must not be reported."""
    (tmp_path / "a.txt").write_text("local file, fully materialized")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("also local")
    assert _dataless_count([tmp_path]) == "0"


def test_dataless_count_is_zero_when_roots_are_absent(tmp_path: Path) -> None:
    """A machine without ~/Documents reports 0, not a find error."""
    assert _dataless_count([tmp_path / "nope"]) == "0"


def test_dataless_count_survives_an_unreadable_subdirectory(tmp_path: Path) -> None:
    """find's permission errors must not leak into the count or to stderr."""
    (tmp_path / "a.txt").write_text("local")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.txt").write_text("unreachable")
    locked.chmod(0o000)
    try:
        assert _dataless_count([tmp_path]) == "0"
    finally:
        locked.chmod(0o700)
