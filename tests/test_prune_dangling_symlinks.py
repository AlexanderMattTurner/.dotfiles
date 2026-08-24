"""bin/prune-dangling-symlinks.bash — what it removes, and what it must not.

This runs in CI right before the Claude reviewer starts, against a checkout of
the default branch. Over-deleting there would silently strip real repo content
from the tree the reviewer reads; under-deleting leaves the dangling
`.claude/hooks` that makes Claude Code die with `statx` ENOENT and the workflow
mislabel it "every configured Claude credential errored".
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
SCRIPT = REPO / "bin" / "prune-dangling-symlinks.bash"


def _run(*args, cwd=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def tree(tmp_path):
    """A .claude mirroring the real one: dangling links, live links, real files."""
    claude = tmp_path / ".claude"
    (claude / "hooks-real").mkdir(parents=True)
    (claude / "hooks-real" / "hook.sh").write_text("#!/bin/sh\n")
    (claude / "settings.json").write_text("{}\n")

    # The two the repo actually tracks, both pointing into gitignored claude-guard.
    (claude / "hooks").symlink_to("../claude-guard/hooks")
    (claude / "README.md").symlink_to("../claude-guard/README.md")
    # A symlink that resolves — must survive.
    (claude / "live").symlink_to(claude / "hooks-real")
    # A dangling link one level down — must also be found.
    (claude / "hooks-real" / "nested-dangling").symlink_to("../../nowhere")
    return tmp_path


def test_removes_only_the_dangling_links(tree):
    claude = tree / ".claude"

    result = _run(str(claude))

    assert result.returncode == 0, result.stderr
    assert not (claude / "hooks").exists(follow_symlinks=False)
    assert not (claude / "README.md").exists(follow_symlinks=False)
    assert not (claude / "hooks-real" / "nested-dangling").exists(follow_symlinks=False)
    # Everything that resolves is untouched.
    assert (claude / "live").is_symlink()
    assert (claude / "settings.json").read_text() == "{}\n"
    assert (claude / "hooks-real" / "hook.sh").read_text() == "#!/bin/sh\n"


def test_reports_what_it_removed(tree):
    result = _run(str(tree / ".claude"))

    assert "removed 3 dangling symlink(s)" in result.stdout
    assert ".claude/hooks -> ../claude-guard/hooks" in result.stdout


def test_is_idempotent(tree):
    claude = tree / ".claude"
    _run(str(claude))

    result = _run(str(claude))

    assert result.returncode == 0
    assert "removed 0 dangling symlink(s)" in result.stdout


def test_defaults_to_dot_claude_relative_to_cwd(tree):
    result = _run(cwd=tree)

    assert result.returncode == 0
    assert not (tree / ".claude" / "hooks").exists(follow_symlinks=False)


def test_missing_directory_is_not_an_error(tmp_path):
    """A repo without .claude, or a machine where claude-guard is cloned."""
    result = _run(str(tmp_path / "absent"))

    assert result.returncode == 0
    assert "nothing to do" in result.stdout


def test_never_follows_a_link_out_of_the_tree(tmp_path):
    """A symlink to a real file elsewhere resolves, so it must survive untouched
    — and the file it points at must not be deleted."""
    outside = tmp_path / "outside.txt"
    outside.write_text("precious\n")
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "escape").symlink_to(outside)

    _run(str(claude))

    assert (claude / "escape").is_symlink()
    assert outside.read_text() == "precious\n"


def test_rejects_unknown_option(tree):
    result = _run("--force", cwd=tree)

    assert result.returncode == 2
    assert "unexpected option" in result.stderr
    # Nothing was touched before the argument was rejected.
    assert (tree / ".claude" / "hooks").is_symlink()


def test_help_exits_clean_without_pruning(tree):
    result = _run("--help", cwd=tree)

    assert result.returncode == 0
    assert (tree / ".claude" / "hooks").is_symlink()


WORKFLOW = REPO / ".github" / "workflows" / "claude-review.yaml"


def _job_steps() -> dict[str, list[str]]:
    """Map job id -> ordered step names in claude-review.yaml.

    Hand-rolled rather than PyYAML because PyYAML is not a declared dependency
    of this repo (no pyproject/requirements, no other test imports it), so an
    `importorskip` here would skip in CI too — silently retiring the guard in
    the one place it needs to hold. `actionlint` already fails the lint job on
    malformed YAML, so this only has to handle well-formed input.
    """
    jobs: dict[str, list[str]] = {}
    current = None
    in_jobs = False
    for raw in WORKFLOW.read_text().splitlines():
        if raw.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs or not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  ") and not raw.startswith("   ") and raw.rstrip().endswith(":"):
            current = raw.strip().rstrip(":")
            jobs[current] = []
        elif current and raw.startswith("      - name: "):
            jobs[current].append(raw.split("- name: ", 1)[1].strip())
    return jobs


PRUNE = "Prune dangling .claude symlinks"
SANITIZER = "Install the input sanitizer"


def test_every_claude_job_prunes_before_it_runs():
    """The reviewer, the merge-delta review, and the thread resolver all check
    out the same tree and all start Claude Code, so all three need the prune.

    This is a static contract, not an execution: a GitHub Actions job cannot be
    driven in-process. The real end-to-end proof is the "Claude PR review" check
    on this PR going green — this exists so a later edit cannot silently drop
    the step and take that check red again with the same misleading
    "every configured Claude credential errored" message.
    """
    jobs = _job_steps()
    claude_jobs = {name: steps for name, steps in jobs.items() if SANITIZER in steps}

    assert claude_jobs, f"parsed no Claude jobs from {WORKFLOW}"
    offenders = [
        name
        for name, steps in claude_jobs.items()
        if PRUNE not in steps or steps.index(PRUNE) > steps.index(SANITIZER)
    ]

    assert offenders == []


def test_prune_runs_after_a_checkout():
    """Pruning before the checkout would delete nothing and leave the links."""
    for name, steps in _job_steps().items():
        if PRUNE not in steps:
            continue
        checkouts = [i for i, s in enumerate(steps) if s.startswith("Checkout ")]
        assert checkouts, f"{name}: prune step with no checkout"
        assert steps.index(PRUNE) > min(checkouts), name


def test_parser_sees_the_jobs_that_exist():
    """Non-vacuity for _job_steps: a parser returning {} would pass the guards."""
    jobs = _job_steps()

    assert "review" in jobs
    assert "Checkout base (trusted) branch" in jobs["review"]
    assert jobs["review"].count(PRUNE) == 1


def test_script_is_at_the_path_the_workflow_invokes():
    invoked = REPO / "bin" / "prune-dangling-symlinks.bash"

    assert invoked.is_file()
    assert os.access(invoked, os.R_OK)
