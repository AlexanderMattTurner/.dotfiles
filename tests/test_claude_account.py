"""Tests for bin/claude-account.bash — the per-launch Claude subscription picker.

It walks the envchain namespaces holding a CLAUDE_CODE_OAUTH_TOKEN, probes each
against api.anthropic.com, and execs the caller's command under the first account
that is not at its usage limit. These drive the real script with stub `envchain`
and `curl` binaries on PATH, so the cooldown records and the exec'd environment
are observed rather than asserted about.
"""

import os
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()
)
SCRIPT = DOTFILES / "bin" / "claude-account.bash"

# One distinct token per namespace, so an assertion can tell WHICH account was picked.
TOKENS = {"one": "tok-one", "two": "tok-two", "three": "tok-three"}
FAR_FUTURE = "1893456000"  # 2030-01-01T00:00:00Z

# `envchain --list` prints the namespaces; `envchain <ns> <cmd...>` runs <cmd> with that
# namespace's token exported. A namespace absent from $TOKS holds no Claude token — the
# shape discovery must skip. One listed in $BROKEN fails outright, the shape of a
# keychain entry that will not open.
_ENVCHAIN_STUB = r"""#!/bin/bash
if [ "$1" = "--list" ]; then printf '%s\n' ${NS:-}; exit 0; fi
ns="$1"; shift
case " ${BROKEN:-} " in *" $ns "*) exit 3 ;; esac
token=""
for pair in ${TOKS:-}; do
    [ "${pair%%=*}" = "$ns" ] && token="${pair#*=}"
done
exec env CLAUDE_CODE_OAUTH_TOKEN="$token" "$@"
"""

# A curl answering from the token handed to THIS invocation on stdin: $FX maps each
# namespace to "<http-code>|<unified-status>|<reset>". Matching on this request's stdin
# rather than an accumulated log keeps the harness order-independent. Header names are
# emitted in HTTP/1.1 mixed case on purpose — HTTP/2 forbids uppercase field names, so
# a real client sees either spelling depending on the negotiated protocol, and a
# case-sensitive parser would silently report every account as unknown over HTTP/1.1.
# $BODY replaces the response body when the verdict lives there (the drained balance).
_CURL_STUB = r"""#!/bin/bash
stdin="$(cat)"
printf 'argv: %s\nstdin: %s\n' "$*" "$stdin" >>"${CURL_LOG:?}"
fixture=""
for pair in ${FX:?}; do
    case "$stdin" in *"tok-${pair%%=*}"*) fixture="${pair#*=}" ;; esac
done
[ -n "$fixture" ] || { printf '\n000'; exit 0; }
IFS='|' read -r code status reset <<<"$fixture"
printf 'HTTP/2 %s\r\n' "$code"
[ -n "$status" ] && printf 'Anthropic-RateLimit-Unified-Status: %s\r\n' "$status"
[ -n "$reset" ] && printf 'anthropic-ratelimit-unified-reset: %s\r\n' "$reset"
body='{"ok":1}'
[ -z "${BODY:-}" ] || body="$BODY"
printf '\r\n%s\n%s' "$body" "$code"
"""


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    *args: str,
    fx: dict[str, str],
    namespaces: str = "one two three",
    tokens: dict[str, str] | None = None,
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    _stub(bin_dir / "envchain", _ENVCHAIN_STUB)
    _stub(bin_dir / "curl", _CURL_STUB)
    held = TOKENS if tokens is None else tokens
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "NS": namespaces,
        "TOKS": " ".join(f"{k}={v}" for k, v in held.items()),
        "FX": " ".join(f"{k}={v}" for k, v in fx.items()),
        **extra,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _until_file(tmp_path: Path, ns: str) -> Path:
    return tmp_path / "state" / "claude-accounts" / f"{ns}.until"


def _requests(tmp_path: Path) -> list[str]:
    log = tmp_path / "curl.log"
    if not log.exists():
        return []
    return [
        ln for ln in log.read_text(encoding="utf-8").splitlines() if ln[:6] == "argv: "
    ]


def _exec_env(r: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """The environment `env` was exec'd with — how the chosen account is observed."""
    return dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)


def test_skips_the_exhausted_account_and_runs_under_the_first_usable_one(
    tmp_path: Path,
) -> None:
    """The whole point: an account at its limit costs one probe, and the command
    still runs — under the NEXT account, carrying that account's token."""
    r = _run(
        tmp_path, "env", fx={"one": f"429|rejected|{FAR_FUTURE}", "two": "200|allowed|"}
    )
    assert r.returncode == 0, r.stderr
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert "using two" in r.stderr


def test_an_account_near_its_limit_is_still_used_but_says_so(tmp_path: Path) -> None:
    r = _run(tmp_path, "env", fx={"one": "200|allowed_warning|"})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["one"]
    assert "close to its usage limit" in r.stderr


def test_the_token_never_reaches_a_command_line(tmp_path: Path) -> None:
    """Every argv is world-readable through `ps`. The token must reach curl on stdin
    and the command through envchain's own environment; a token in any recorded argv
    is a leak to every other user on the host."""
    r = _run(tmp_path, "env", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    argv_lines = [ln for ln in log.splitlines() if ln.startswith("argv: ")]
    assert argv_lines
    for token in TOKENS.values():
        assert all(token not in ln for ln in argv_lines), argv_lines
    assert f"Authorization: Bearer {TOKENS['one']}" in log  # ...but it did authenticate


def test_the_probe_sends_the_shape_a_subscription_token_requires(
    tmp_path: Path,
) -> None:
    """A subscription token is refused with 401 on a request missing either the OAuth
    beta header or the Claude Code system line — which the loop would read as a
    revoked account and skip forever."""
    _run(tmp_path, "env", fx={"one": "200|allowed|"})
    log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "anthropic-beta: oauth-2025-04-20" in log
    assert "You are Claude Code" in log


def test_an_exhausted_account_records_when_it_frees_up(tmp_path: Path) -> None:
    r = _run(
        tmp_path, "env", fx={"one": f"429|rejected|{FAR_FUTURE}", "two": "200|allowed|"}
    )
    assert r.returncode == 0
    assert (
        _until_file(tmp_path, "one").read_text(encoding="utf-8").strip() == FAR_FUTURE
    )


def test_a_reset_time_arriving_as_iso8601_is_stored_as_an_epoch(tmp_path: Path) -> None:
    """The header's format is not contractual, so both spellings normalize — a
    caller comparing the record against `date +%s` must never see a timestamp."""
    r = _run(
        tmp_path,
        "env",
        fx={"one": "429|rejected|2030-01-01T00:00:00Z", "two": "200|allowed|"},
    )
    assert r.returncode == 0
    assert (
        _until_file(tmp_path, "one").read_text(encoding="utf-8").strip() == FAR_FUTURE
    )


@pytest.mark.parametrize(
    ("reset", "why"),
    [
        ("", "no reset header at all"),
        ("whenever", "an unparseable reset"),
        ("100", "an absolute epoch already in the past"),
        ("3600", "seconds-until-reset misread as an absolute epoch"),
    ],
)
def test_a_refusal_without_a_usable_reset_backs_off_an_hour(
    tmp_path: Path, reset: str, why: str
) -> None:
    """A reset that cannot cool the account down falls back to an hour. The past-epoch
    case is the subtle one: _on_cooldown reads it as expired, so the very next launch
    re-probes an account already known to be exhausted."""
    r = _run(
        tmp_path, "env", fx={"one": f"429|rejected|{reset}", "two": "200|allowed|"}
    )
    assert r.returncode == 0, why
    recorded = int(_until_file(tmp_path, "one").read_text(encoding="utf-8"))
    now = int(
        subprocess.run(
            ["date", "+%s"], capture_output=True, text=True, check=True
        ).stdout
    )
    assert 3000 < recorded - now <= 3600, why


def test_a_cooled_down_account_is_skipped_without_spending_a_request(
    tmp_path: Path,
) -> None:
    """The record exists to make the skip free. Probing anyway would cost one live
    request per exhausted account on every launch."""
    until = _until_file(tmp_path, "one")
    until.parent.mkdir(parents=True, exist_ok=True)
    until.write_text(f"{FAR_FUTURE}\n", encoding="utf-8")
    r = _run(tmp_path, "env", fx={"two": "200|allowed|"})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert len(_requests(tmp_path)) == 1


@pytest.mark.parametrize("corrupt", ["", "   ", "not-a-time", "12x34"])
def test_a_corrupt_cooldown_record_re_probes_rather_than_skipping_forever(
    tmp_path: Path, corrupt: str
) -> None:
    """An unreadable record is not evidence the account is exhausted; treating it as
    one would strand a healthy account behind a junk file."""
    until = _until_file(tmp_path, "one")
    until.parent.mkdir(parents=True, exist_ok=True)
    until.write_text(corrupt, encoding="utf-8")
    r = _run(tmp_path, "env", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["one"]


def test_a_recovered_account_clears_its_cooldown(tmp_path: Path) -> None:
    until = _until_file(tmp_path, "one")
    until.parent.mkdir(parents=True, exist_ok=True)
    until.write_text("100\n", encoding="utf-8")
    r = _run(tmp_path, "env", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    assert not until.exists()


def test_a_revoked_account_is_passed_over_with_the_command_that_fixes_it(
    tmp_path: Path,
) -> None:
    r = _run(tmp_path, "env", fx={"one": "401||", "two": "200|allowed|"})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert "claude setup-token" in r.stderr
    # A dead token is not a usage limit, so it gets no cooldown — waiting cannot fix it.
    assert not _until_file(tmp_path, "one").exists()


def test_a_drained_credit_balance_is_not_read_as_a_usage_limit(tmp_path: Path) -> None:
    """The verdict lives in the response body, which the usage headers do not model.
    Reading it as a limit would promise a reset that never arrives."""
    r = _run(
        tmp_path,
        "env",
        fx={"one": "400||", "two": "200|allowed|"},
        BODY='{"error":{"message":"Your credit balance is too low."}}',
    )
    assert r.returncode == 0
    assert "out of credits" in r.stderr
    assert not _until_file(tmp_path, "one").exists()


@pytest.mark.parametrize(
    ("broken", "fx", "why"),
    [
        ("", {"one": "000||"}, "a network fault"),
        ("one", {}, "a keychain entry that will not open"),
    ],
)
def test_an_unreachable_account_is_skipped_without_a_cooldown(
    tmp_path: Path, broken: str, fx: dict[str, str], why: str
) -> None:
    """Unknown is not refused: recording a cooldown would lock a healthy account out
    for an hour because the wifi dropped or the keychain was locked."""
    r = _run(tmp_path, "env", fx={**fx, "two": "200|allowed|"}, BROKEN=broken)
    assert r.returncode == 0, why
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert not _until_file(tmp_path, "one").exists(), why


def test_when_no_account_is_available_it_fails_naming_every_one_tried(
    tmp_path: Path,
) -> None:
    r = _run(
        tmp_path,
        "env",
        fx={"one": "429|rejected|", "two": "429|rejected|", "three": "401||"},
    )
    assert r.returncode == 1
    assert "none of these accounts is available" in r.stderr
    for ns in ("one", "two", "three"):
        assert ns in r.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN=" not in r.stdout  # nothing was exec'd


def test_the_namespace_list_sets_both_membership_and_order(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        "env",
        fx={"one": "200|allowed|", "three": "200|allowed|"},
        CLAUDE_ACCOUNT_NAMESPACES="three one",
    )
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["three"]
    assert len(_requests(tmp_path)) == 1  # stopped at the first usable one


def test_discovery_ignores_a_namespace_holding_no_claude_token(tmp_path: Path) -> None:
    """envchain namespaces hold all sorts of secrets. Probing one with no Claude token
    would send an unauthenticated request and read the 401 as a dead account."""
    r = _run(tmp_path, "env", fx={"two": "200|allowed|"}, tokens={"two": TOKENS["two"]})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert len(_requests(tmp_path)) == 1


def test_no_account_at_all_is_a_setup_error_not_a_usage_limit(tmp_path: Path) -> None:
    r = _run(tmp_path, "env", fx={}, namespaces="", tokens={})
    assert r.returncode == 2
    assert "claude setup-token" in r.stderr
    assert not _requests(tmp_path)


def test_no_command_is_an_error_rather_than_a_silent_success(tmp_path: Path) -> None:
    r = _run(tmp_path, fx={"one": "200|allowed|"})
    assert r.returncode == 2
    assert "needs a command" in r.stderr
    assert not _requests(tmp_path)


def test_help_describes_the_command_without_touching_any_account(
    tmp_path: Path,
) -> None:
    r = _run(tmp_path, "--help", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    assert "claude-account" in r.stdout
    assert not _requests(tmp_path)
