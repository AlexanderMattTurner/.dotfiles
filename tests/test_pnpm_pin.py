"""pnpm_pin_spec (bin/lib/pnpm-pin.sh): pin resolution + fallback contract.

bin/setup_llm.bash uses this to pin claude-code/ccr to the versions in
claude-guard/package.json. The fallback ("bare package name, rc 1") is
what keeps a partial bootstrap (subrepo not cloned yet) installing
*something* — so both directions are locked here.
"""

import json
import subprocess
from pathlib import Path

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)


def _pin_spec(pkg_json: Path, pkg: str) -> subprocess.CompletedProcess:
    script = (
        f'source "{REPO}/bin/lib/pnpm-pin.sh"\n'
        f'pnpm_pin_spec "{pkg_json}" "{pkg}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=REPO
    )


def test_pinned_version_is_resolved(tmp_path: Path) -> None:
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps({"devDependencies": {"@anthropic-ai/claude-code": "2.1.7"}})
    )
    result = _pin_spec(pkg_json, "@anthropic-ai/claude-code")
    assert result.returncode == 0
    assert result.stdout.strip() == "@anthropic-ai/claude-code@2.1.7"


def test_missing_pin_falls_back_to_bare_name(tmp_path: Path) -> None:
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({"devDependencies": {"other-pkg": "1.0.0"}}))
    result = _pin_spec(pkg_json, "@anthropic-ai/claude-code")
    assert result.returncode == 1
    assert result.stdout.strip() == "@anthropic-ai/claude-code"


def test_missing_package_json_falls_back(tmp_path: Path) -> None:
    result = _pin_spec(tmp_path / "does-not-exist.json", "some-pkg")
    assert result.returncode == 1
    assert result.stdout.strip() == "some-pkg"


def test_malformed_package_json_falls_back(tmp_path: Path) -> None:
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text("{ not json")
    result = _pin_spec(pkg_json, "some-pkg")
    assert result.returncode == 1
    assert result.stdout.strip() == "some-pkg"
