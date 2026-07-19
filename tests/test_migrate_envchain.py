"""bin/migrate-envchain-to-bitwarden.bash — the skip / fill / create decisions.

This is exactly the "silently corrupts a vault if wrong" logic #126 flags:
an existing populated item must be skipped, an empty one skipped unless
--fill-empty is passed, and a brand-new (namespace, var) created. The script
is driven end-to-end against stub `bw-node` / `envchain` (real jq + awk) so
the branch it takes is observed from stdout and from what it would have
written to the vault — no real Bitwarden or envchain involved.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
SCRIPT = REPO / "bin" / "migrate-envchain-to-bitwarden.bash"

FOLDER_ID = "folder-envchain-id"

# Stub bw-node: answer the handful of subcommands the script uses, sourcing the
# item list from $BW_ITEMS and logging create/edit payloads to $BW_LOG.
BW_STUB = r"""#!/bin/bash
log() { printf '%s\n' "$*" >> "$BW_LOG"; }
case "$1 $2" in
"status --raw") printf '{"status":"unlocked"}' ;;
"sync ") exit 0 ;;
"list folders") printf '[{"name":"envchain","id":"%s"}]' "$BW_FOLDER_ID" ;;
"list items") cat "$BW_ITEMS" ;;
"get template") printf '{}' ;;   # `get template item` / `get template folder`
"get item")
    # $3 is the id; echo the matching item from the list.
    jq -c --arg id "$3" '.[] | select(.id==$id)' "$BW_ITEMS"
    ;;
"encode ") cat ;;                # passthrough (real bw base64s; stub is transparent)
"create item") log "CREATE $(cat)" ;;
"create folder") cat >/dev/null; printf '{"id":"%s"}' "$BW_FOLDER_ID" ;;
"edit item") log "EDIT $3 $(cat)" ;;
*) exit 0 ;;
esac
"""

# Stub envchain: one namespace `myns` with one var `TOKEN` whose value is $SECRET_VALUE.
ENVCHAIN_STUB = r"""#!/bin/bash
if [[ "$1" == "--list" && -z "${2:-}" ]]; then
    printf 'myns\n'
elif [[ "$1" == "--list" && "$2" == "myns" ]]; then
    printf 'TOKEN\n'
elif [[ "$1" == "myns" && "$2" == "printenv" && "$3" == "TOKEN" ]]; then
    printf '%s' "$SECRET_VALUE"
fi
"""


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text(body)
    p.chmod(0o755)


def _run(tmp_path: Path, items: list, *, fill_empty: bool, secret: str = "s3cret"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "bw-node", BW_STUB)
    _stub(bindir, "envchain", ENVCHAIN_STUB)

    items_file = tmp_path / "items.json"
    items_file.write_text(json.dumps(items))
    log_file = tmp_path / "bw.log"

    args = ["bash", str(SCRIPT)]
    if fill_empty:
        args.append("--fill-empty")

    result = subprocess.run(
        args,
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "DOTFILES_DIR": str(REPO),
            "DOTFILES_SECRET_BACKEND": "file",  # keeps secret_store_required_cmd empty
            "BW_SESSION": "fake-session-so-unlock-is-skipped",
            "BW_ITEMS": str(items_file),
            "BW_FOLDER_ID": FOLDER_ID,
            "BW_LOG": str(log_file),
            "SECRET_VALUE": secret,
            "USER": os.environ.get("USER", "tester"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    log = log_file.read_text() if log_file.exists() else ""
    return result.stdout, log


def _item(name: str, password: str) -> dict:
    return {"name": name, "id": "item-1", "login": {"password": password}}


def test_populated_item_is_skipped(tmp_path: Path) -> None:
    out, log = _run(tmp_path, [_item("envchain/myns/TOKEN", "already-here")], fill_empty=False)
    assert "skip" in out and "already populated" in out
    assert log == "", "a populated item must not be edited or recreated"


def test_empty_item_skipped_without_fill_flag(tmp_path: Path) -> None:
    out, log = _run(tmp_path, [_item("envchain/myns/TOKEN", "")], fill_empty=False)
    assert "skip" in out and "rerun with --fill-empty" in out
    assert log == "", "without --fill-empty an empty item must be left untouched"


def test_empty_item_filled_with_flag(tmp_path: Path) -> None:
    out, log = _run(
        tmp_path, [_item("envchain/myns/TOKEN", "")], fill_empty=True, secret="filled-value"
    )
    assert "fill" in out
    # The edit payload carries the secret pulled from envchain.
    assert "EDIT item-1" in log
    assert "filled-value" in log


def test_new_item_is_created(tmp_path: Path) -> None:
    out, log = _run(tmp_path, [], fill_empty=False, secret="brand-new-value")
    assert "ok" in out
    assert "CREATE" in log
    payload = log.split("CREATE ", 1)[1]
    doc = json.loads(payload)
    assert doc["name"] == "envchain/myns/TOKEN"
    assert doc["login"]["password"] == "brand-new-value"
    assert doc["folderId"] == FOLDER_ID


def test_brew_sudo_namespace_is_skipped(tmp_path: Path) -> None:
    """The brew-sudo namespace is intentionally not migrated (replaced by a
    NOPASSWD sudoers fragment). Drive an envchain that only lists brew-sudo."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "bw-node", BW_STUB)
    _stub(
        bindir,
        "envchain",
        '#!/bin/bash\n[[ "$1" == "--list" && -z "${2:-}" ]] && printf "brew-sudo\\n"\n',
    )
    items_file = tmp_path / "items.json"
    items_file.write_text("[]")
    log_file = tmp_path / "bw.log"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "DOTFILES_DIR": str(REPO),
            "DOTFILES_SECRET_BACKEND": "file",
            "BW_SESSION": "fake",
            "BW_ITEMS": str(items_file),
            "BW_FOLDER_ID": FOLDER_ID,
            "BW_LOG": str(log_file),
            "USER": os.environ.get("USER", "tester"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Skipping brew-sudo" in result.stdout
    assert not log_file.exists() or log_file.read_text() == ""


@pytest.mark.parametrize("fill", [False, True])
def test_envchain_empty_value_warns_not_creates(tmp_path: Path, fill: bool) -> None:
    """If envchain hands back an empty value the item must NOT be created with a
    blank password — the script WARNs and moves on."""
    out, log = _run(tmp_path, [], fill_empty=fill, secret="")
    assert "WARN" in out
    assert log == "", "must not create/edit an item from an empty envchain value"
