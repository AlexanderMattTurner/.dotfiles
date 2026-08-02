"""Contract test for the skill-structure validator wired into pre-commit.

`.hooks/lint-skills.sh` is template-synced but wired locally as the
`lint-skills` hook in `.pre-commit-config.yaml`, so its behaviour is
load-bearing for this repo's CI. These tests pin the contract — a valid
SKILL.md passes; missing `name:`, a too-short description, and the flat-file
layout each fail — so a future template sync that silently regresses the
validator (or a broken rewire) is caught here rather than by a skill slipping
through unvalidated.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
LINT_SKILLS = REPO_ROOT / ".hooks" / "lint-skills.sh"

VALID_SKILL = """\
---
name: sample
description: A valid skill. It has two sentences so the period check passes.
---
## Examples
some example body
"""


def run_validator(*files: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LINT_SKILLS), *[str(f) for f in files]],
        capture_output=True,
        text=True,
    )


def write_skill(root: Path, name: str, body: str) -> Path:
    skill = root / ".claude" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body)
    return skill


def test_validator_exists_and_wired():
    """The validator ships and is referenced by the pre-commit hook."""
    assert LINT_SKILLS.is_file(), f"{LINT_SKILLS} missing"
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    assert ".hooks/lint-skills.sh" in config, "lint-skills not wired into pre-commit"


def test_validator_is_executable():
    """The pre-commit hook uses `language: script`, which execs the file
    directly — so the exec bit is load-bearing. `.hooks/` is template-synced,
    and a sync that drops +x would red `pre-commit run` while leaving the
    logic tests above green. Guard the mode here so pytest catches it too."""
    assert os.access(LINT_SKILLS, os.X_OK), (
        f"{LINT_SKILLS} is not executable; pre-commit `language: script` will fail"
    )


def test_valid_skill_passes(tmp_path):
    skill = write_skill(tmp_path, "good", VALID_SKILL)
    result = run_validator(skill)
    assert result.returncode == 0, result.stderr


def test_missing_name_fails(tmp_path):
    body = VALID_SKILL.replace("name: sample\n", "")
    skill = write_skill(tmp_path, "noname", body)
    result = run_validator(skill)
    assert result.returncode == 1
    assert "missing 'name:'" in result.stderr


def test_short_description_fails(tmp_path):
    body = "---\nname: terse\ndescription: one sentence only\n---\n## Examples\nx\n"
    skill = write_skill(tmp_path, "terse", body)
    result = run_validator(skill)
    assert result.returncode == 1
    assert "description too short" in result.stderr


def test_flat_file_layout_fails(tmp_path):
    """A .md directly under .claude/skills/ (not <name>/SKILL.md) is rejected."""
    flat = tmp_path / ".claude" / "skills" / "flat.md"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(VALID_SKILL)
    result = run_validator(flat)
    assert result.returncode == 1
    assert "flat file format" in result.stderr


def test_zero_period_description_does_not_crash(tmp_path):
    """Regression guard: a period-less description must fail cleanly, not error
    out under `set -o pipefail` (the bug the template `.sh` fixed vs the old
    grep|wc form)."""
    body = "---\nname: nop\ndescription: no periods at all here\n---\n"
    skill = write_skill(tmp_path, "nop", body)
    result = run_validator(skill)
    # Fails the description check, but as a clean lint failure (1), not a
    # bash/pipefail crash (would surface as a different code / no ERROR line).
    assert result.returncode == 1
    assert "description too short" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
