"""apps/fish/conf.d/carapace.fish: the CARAPACE_EXCLUDES contract.

carapace deletes fish's own completion for every command it claims, so
CARAPACE_EXCLUDES is the only thing keeping the richer completions for the
commands in apps/fish/carapace-excludes.txt. The failure mode is silent: a
parse that yields an empty list still loads carapace, still starts a working
shell, and only shows up as flags missing from the Tab menu. So the list has to
arrive non-empty and comment-free, and NO_CARAPACE has to actually suppress
carapace -- bin/gen-carapace-excludes.fish measures against it, and a switch
that does nothing would regenerate the list as empty.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
CONF_D = REPO / "apps/fish/conf.d/carapace.fish"
EXCLUDES = REPO / "apps/fish/carapace-excludes.txt"

pytestmark = pytest.mark.skipif(
    shutil.which("fish") is None, reason="fish is not installed"
)


@pytest.fixture
def stub_carapace(tmp_path: Path) -> Path:
    """A carapace that satisfies `command -q` and emits an empty init.

    The exclude parsing lives inside the `command -q carapace` guard, so
    without a carapace on PATH every assertion below would pass vacuously.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "carapace"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    return bindir


def _source(stub_bin: Path, script: str, **env: str) -> str:
    """Source the real conf.d snippet, then run `script`; return its stdout."""
    environ = dict(os.environ)
    environ["PATH"] = f"{stub_bin}:{environ['PATH']}"
    environ.pop("NO_CARAPACE", None)
    environ.pop("CARAPACE_EXCLUDES", None)
    environ.update(env)
    result = subprocess.run(
        ["fish", "--no-config", "-c", f"source {CONF_D}\n{script}"],
        capture_output=True,
        text=True,
        env=environ,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _listed_commands() -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in EXCLUDES.read_text().splitlines())
    return [line for line in lines if line]


def test_exclude_list_is_not_empty() -> None:
    assert len(_listed_commands()) > 0


def test_every_listed_command_reaches_carapace(stub_carapace: Path) -> None:
    got = _source(stub_carapace, "echo $CARAPACE_EXCLUDES").split(",")
    assert got == _listed_commands()


def test_comments_and_blank_lines_are_stripped(stub_carapace: Path) -> None:
    got = _source(stub_carapace, "echo $CARAPACE_EXCLUDES").split(",")
    assert got, "empty exclude list still loads carapace, so assert it is non-empty"
    assert all(entry and "#" not in entry and entry == entry.strip() for entry in got)


def test_no_carapace_suppresses_carapace(stub_carapace: Path) -> None:
    """The control switch bin/gen-carapace-excludes.fish measures against."""
    got = _source(
        stub_carapace, "set -q CARAPACE_EXCLUDES; and echo set; or echo unset",
        NO_CARAPACE="1",
    )
    assert got == "unset"


def test_inherited_excludes_win(stub_carapace: Path) -> None:
    """The generator asks for an unmodified carapace by exporting this empty."""
    got = _source(stub_carapace, "echo \"[$CARAPACE_EXCLUDES]\"", CARAPACE_EXCLUDES="")
    assert got == "[]"
