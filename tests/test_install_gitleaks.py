"""bin/install-gitleaks.bash — checksum verification is the whole point.

The script's header says a tampered/replaced release asset should "fail
loudly instead of being untarred as root." This locks that contract: a
mismatched GITLEAKS_SHA256 must abort before `sudo tar` ever runs, and a
matching checksum must let the install proceed.
"""

import hashlib
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = REPO / "bin" / "install-gitleaks.bash"

FAKE_TARBALL_CONTENT = b"not a real gitleaks release, just test fixture bytes"


def _stub_bin(tmp_path: Path) -> Path:
    """PATH dir with a `curl` that ignores the URL and writes fixture bytes
    to the `-o` target, plus no-op `sudo`/`gitleaks` stubs that record
    whether they were invoked.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()

    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/bash\n"
        "out=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-o" ]; then out="$2"; shift; fi\n'
        "  shift\n"
        "done\n"
        f'printf %s "{FAKE_TARBALL_CONTENT.decode()}" >"$out"\n'
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    sudo_marker = tmp_path / "sudo-was-called"
    sudo = bin_dir / "sudo"
    sudo.write_text(f'#!/bin/bash\ntouch "{sudo_marker}"\nexit 0\n')
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)

    gitleaks = bin_dir / "gitleaks"
    gitleaks.write_text("#!/bin/bash\necho stub-gitleaks 1.0.0\n")
    gitleaks.chmod(gitleaks.stat().st_mode | stat.S_IXUSR)

    return bin_dir


def _run(tmp_path: Path, sha256: str) -> subprocess.CompletedProcess:
    bin_dir = _stub_bin(tmp_path)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITLEAKS_SHA256": sha256,
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_mismatched_checksum_aborts_before_sudo(tmp_path: Path) -> None:
    result = _run(tmp_path, sha256="0" * 64)
    assert result.returncode != 0
    assert not (tmp_path / "sudo-was-called").exists()


def test_matching_checksum_proceeds_to_install(tmp_path: Path) -> None:
    real_sha256 = hashlib.sha256(FAKE_TARBALL_CONTENT).hexdigest()
    result = _run(tmp_path, sha256=real_sha256)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sudo-was-called").exists()
