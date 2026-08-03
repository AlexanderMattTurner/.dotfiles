"""Tests for the Claude subscription rotation: bin/claude-account.bash and the
loopback proxy bin/claude-rotate-proxy.py.

claude-account walks the envchain namespaces holding a CLAUDE_CODE_OAUTH_TOKEN,
probes each against api.anthropic.com, and either execs the caller's command under
the first usable account (launcher mode) or prints that namespace for the proxy
(--pick). Most tests drive the real script with stub `envchain`/`curl` binaries on
PATH and observe the cooldown records and exec'd environment. The final test drives
the REAL proxy with a real curl against a fake Anthropic and a stub envchain, so the
rotation is proven on the wire, not asserted.
"""

import contextlib
import json
import os
import subprocess
import time
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
api_key=""
[ "$ns" = "ai" ] && api_key="${AI_KEY:-}"
exec env CLAUDE_CODE_OAUTH_TOKEN="$token" ANTHROPIC_API_KEY="$api_key" "$@"
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
        "GLOVEBOX_LOG": str(tmp_path / "glovebox.log"),
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


# ── --pick and --cooldown (the rotation proxy's interface) ──────────────────
#
# bin/claude-rotate-proxy.py asks --pick which namespace to serve, and calls
# --cooldown on a 429 it read off the response. --pick prints the NAMESPACE, never
# a token: the proxy issues the upstream request as `envchain <ns> curl`, so the
# token stays inside the envchain child. These assert on that namespace and on the
# shared cooldown/stamp state both --pick and a launch read.

_GLOVEBOX_STUB = r"""#!/bin/bash
printf 'argv: %s token: %s\n' "$*" "${CLAUDE_CODE_OAUTH_TOKEN:-}" >>"${GLOVEBOX_LOG:?}"
"""


def _glovebox_calls(tmp_path: Path, expect: int) -> list[str]:
    """Lines the glovebox stub logged; polls because the convergence is detached."""
    log = tmp_path / "glovebox.log"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        if len(lines) >= expect:
            return lines
        time.sleep(0.05)
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_pick_prints_the_namespace_to_serve(tmp_path: Path) -> None:
    r = _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "one"


def test_pick_rotates_past_an_exhausted_account(tmp_path: Path) -> None:
    """The rotation the proxy relies on: an account at its limit is skipped and its
    cooldown recorded, and --pick names the next usable one."""
    r = _run(
        tmp_path,
        "--pick",
        fx={"one": f"429|rejected|{FAR_FUTURE}", "two": "200|allowed|"},
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "two"
    assert _until_file(tmp_path, "one").read_text(encoding="utf-8").strip() == FAR_FUTURE


def test_pick_trusts_a_fresh_stamp_without_a_probe(tmp_path: Path) -> None:
    """The proxy calls --pick per request, so a healthy stamp is what stops a live
    probe from being spent on every request."""
    _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    r = _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    assert r.stdout.strip() == "one"
    assert len(_requests(tmp_path)) == 1


def test_pick_reprobes_a_stale_stamp(tmp_path: Path) -> None:
    _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    ok = tmp_path / "state" / "claude-accounts" / "one.ok"
    stale = time.time() - 400  # past the 300s default probe interval
    os.utime(ok, (stale, stale))
    r = _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    assert r.stdout.strip() == "one"
    assert len(_requests(tmp_path)) == 2


def test_pick_fails_when_every_account_is_exhausted(tmp_path: Path) -> None:
    """All accounts down is exit 1 with empty stdout — the proxy then returns 503
    to the client rather than a namespace it cannot serve."""
    r = _run(
        tmp_path,
        "--pick",
        fx={"one": "429|rejected|", "two": "429|rejected|", "three": "401||"},
    )
    assert r.returncode == 1
    assert r.stdout.strip() == ""


def test_pick_never_names_a_namespace_whose_token_was_emptied(tmp_path: Path) -> None:
    """A fresh stamp can outlive the keychain entry (or CLAUDE_ACCOUNT_NAMESPACES
    lists one unverified); naming it would make the proxy send an empty Bearer.
    --pick verifies the token, clears the stale stamp, and fails."""
    state = tmp_path / "state" / "claude-accounts"
    state.mkdir(parents=True)
    (state / "one.ok").touch()
    r = _run(
        tmp_path,
        "--pick",
        fx={},
        tokens={},
        namespaces="one",
        CLAUDE_ACCOUNT_NAMESPACES="one",
    )
    assert r.returncode == 1
    assert r.stdout.strip() == ""
    assert not (state / "one.ok").exists()


def test_pick_keeps_the_token_off_every_argv(tmp_path: Path) -> None:
    r = _run(tmp_path, "--pick", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    argv_lines = [ln for ln in log.splitlines() if ln.startswith("argv: ")]
    assert argv_lines
    for token in TOKENS.values():
        assert all(token not in ln for ln in argv_lines), argv_lines


def test_pick_converges_glovebox_once_on_a_change(tmp_path: Path) -> None:
    """A selection change pushes the new account to running glovebox sandboxes
    ('glovebox login-sync'), exactly once per change."""
    _stub(tmp_path / "bin" / "glovebox", _GLOVEBOX_STUB)
    r = _run(
        tmp_path,
        "--pick",
        fx={"one": f"429|rejected|{FAR_FUTURE}", "two": "200|allowed|"},
    )
    assert r.stdout.strip() == "two"
    calls = _glovebox_calls(tmp_path, expect=1)
    assert len(calls) == 1
    assert "login-sync" in calls[0]
    assert TOKENS["two"] in calls[0]  # ran under the NEW account's envchain
    r = _run(tmp_path, "--pick", fx={"two": "200|allowed|"})
    assert r.stdout.strip() == "two"
    time.sleep(0.3)
    assert len(_glovebox_calls(tmp_path, expect=1)) == 1


def test_pick_no_converge_knob_suppresses_the_glovebox_push(tmp_path: Path) -> None:
    """doctor's --pick self-test is read-only; the knob keeps it from re-pointing
    live sandboxes a user pinned to another account on purpose."""
    _stub(tmp_path / "bin" / "glovebox", _GLOVEBOX_STUB)
    r = _run(
        tmp_path, "--pick", fx={"one": "200|allowed|"}, CLAUDE_ACCOUNT_NO_CONVERGE="1"
    )
    assert r.stdout.strip() == "one"
    time.sleep(0.3)
    assert not (tmp_path / "glovebox.log").exists()
    assert not (tmp_path / "state" / "claude-accounts" / "current").exists()


def test_cooldown_records_the_reset(tmp_path: Path) -> None:
    r = _run(tmp_path, "--cooldown", "one", FAR_FUTURE, fx={})
    assert r.returncode == 0
    assert _until_file(tmp_path, "one").read_text(encoding="utf-8").strip() == FAR_FUTURE


def test_cooldown_backs_off_an_hour_for_a_past_reset(tmp_path: Path) -> None:
    """A reset in the past cannot cool the account down; --cooldown falls back to an
    hour so the next --pick does not re-probe straight into the same 429."""
    before = int(time.time())
    r = _run(tmp_path, "--cooldown", "one", "1", fx={})
    assert r.returncode == 0
    recorded = int(_until_file(tmp_path, "one").read_text(encoding="utf-8").strip())
    assert before + 3500 <= recorded <= before + 3700


def test_cooldown_clears_a_healthy_stamp(tmp_path: Path) -> None:
    state = tmp_path / "state" / "claude-accounts"
    state.mkdir(parents=True)
    (state / "one.ok").touch()
    _run(tmp_path, "--cooldown", "one", FAR_FUTURE, fx={})
    assert not (state / "one.ok").exists()


def test_a_proxy_cooldown_is_honored_by_a_later_launch(tmp_path: Path) -> None:
    """One shared state dir lets the proxy and a launch rotate in concert — a
    cooldown the proxy recorded must not be re-derived by the next launch."""
    _run(tmp_path, "--cooldown", "one", FAR_FUTURE, fx={})
    r = _run(tmp_path, "env", fx={"two": "200|allowed|"})
    assert r.returncode == 0
    assert _exec_env(r)["CLAUDE_CODE_OAUTH_TOKEN"] == TOKENS["two"]
    assert "one is at its usage limit" in r.stderr


def test_cooldown_requires_a_namespace(tmp_path: Path) -> None:
    r = _run(tmp_path, "--cooldown", fx={})
    assert r.returncode == 2


def test_a_launch_probes_even_when_a_stamp_is_fresh(tmp_path: Path) -> None:
    """Launcher mode never trusts the stamp — a fresh launch spends one real probe
    rather than inherit a --pick verdict."""
    state = tmp_path / "state" / "claude-accounts"
    state.mkdir(parents=True)
    (state / "one.ok").touch()
    r = _run(tmp_path, "env", fx={"one": "200|allowed|"})
    assert r.returncode == 0
    assert len(_requests(tmp_path)) == 1


def test_settings_no_longer_wire_an_apikeyhelper(tmp_path: Path) -> None:
    """Mid-session rotation is the loopback proxy now; a lingering apiKeyHelper
    would point at a --helper mode that no longer exists and break every session."""
    settings = json.loads(
        (DOTFILES / "apps" / "claude-user" / "settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert "apiKeyHelper" not in settings
    assert "CLAUDE_CODE_API_KEY_HELPER_TTL_MS" not in settings.get("env", {})


# ── end-to-end: the real rotation proxy against a fake Anthropic ────────────
#
# Drives bin/claude-rotate-proxy.py as a real process with a real curl and a real
# bash child, faking only the far side (Anthropic) and the credential store
# (envchain — there is no real keychain in CI). A POST the fake rejects for the
# first account must come back 200 after the proxy rotates to the next one, with
# the first account recorded on cooldown: the whole rotation proven, not asserted.

PROXY = DOTFILES / "bin" / "claude-rotate-proxy.py"


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 5.0) -> bool:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def _fake_anthropic(port: int, seen: list[str]):
    """A fake api.anthropic.com: 429-rejected for tok-one, 200 for tok-two, keyed on
    the Bearer token the proxy's curl child actually sent. Records each token seen."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def _respond(self) -> None:
            auth = self.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            seen.append(token)
            if token == "tok-two":
                body = b'{"ok":"served-by-two"}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b'{"type":"error","error":{"type":"rate_limit_error"}}'
            self.send_response(429)
            self.send_header("anthropic-ratelimit-unified-status", "rejected")
            self.send_header("anthropic-ratelimit-unified-reset", FAR_FUTURE)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self._respond()

        def do_GET(self) -> None:
            self._respond()

    server = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _start_proxy(
    tmp_path: Path, fake_port: int, proxy_port: int, seed: tuple[str, ...]
) -> subprocess.Popen[bytes]:
    """Stub envchain (real curl, real bash — only the store is faked), seed a fresh
    healthy stamp per SEED account so --pick serves without a probe, and launch the
    real proxy pointed at the fake upstream."""
    bin_dir = tmp_path / "bin"
    _stub(bin_dir / "envchain", _ENVCHAIN_STUB)
    state = tmp_path / "state" / "claude-accounts"
    state.mkdir(parents=True, exist_ok=True)
    for ns in seed:
        (state / f"{ns}.ok").touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "NS": "one two",
        "TOKS": "one=tok-one two=tok-two",
        "CLAUDE_ACCOUNT_NAMESPACES": " ".join(seed),
        "CLAUDE_ACCOUNT_PROBE_URL": f"http://127.0.0.1:{fake_port}/v1/messages",
        "CLAUDE_ROTATE_PROXY_PORT": str(proxy_port),
        "CLAUDE_ROTATE_PROXY_UPSTREAM": f"http://127.0.0.1:{fake_port}",
        "CLAUDE_ROTATE_PROXY_ACCOUNT_CLI": str(SCRIPT),
    }
    return subprocess.Popen(
        ["python3", str(PROXY)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _kill_proxy(proxy: subprocess.Popen, server) -> None:
    proxy.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proxy.wait(timeout=3)
    if proxy.poll() is None:
        proxy.kill()
    server.shutdown()


def test_the_proxy_rotates_a_live_request_past_an_exhausted_account(
    tmp_path: Path,
) -> None:
    fake_port = _free_port()
    proxy_port = _free_port()
    seen: list[str] = []
    server = _fake_anthropic(fake_port, seen)
    # Fresh stamps in preference order 'one' then 'two', so --pick serves 'one'
    # first without a probe; the proxy's 429 on 'one' drives the rotation to 'two'.
    proxy = _start_proxy(tmp_path, fake_port, proxy_port, seed=("one", "two"))
    try:
        assert _wait_port(proxy_port), "proxy never started listening"
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=b'{"model":"claude-haiku-4-5","messages":[]}',
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            payload = resp.read()

        assert status == 200, payload
        assert b"served-by-two" in payload
        # The rotation happened on the wire: the fake saw the exhausted account
        # first, then the account the proxy rotated to.
        assert seen == ["tok-one", "tok-two"], seen
        # And the exhausted account was recorded on cooldown for the next request.
        assert _until_file(tmp_path, "one").exists()
        assert (
            int(_until_file(tmp_path, "one").read_text(encoding="utf-8").strip())
            >= int(time.time())
        )
    finally:
        _kill_proxy(proxy, server)


def test_the_proxy_passes_a_non_post_request_through(tmp_path: Path) -> None:
    """The client may GET a path through ANTHROPIC_BASE_URL; a method with no
    handler would 501 and break the session. The proxy rewrites the token and
    forwards it like any other request."""
    fake_port = _free_port()
    proxy_port = _free_port()
    seen: list[str] = []
    server = _fake_anthropic(fake_port, seen)
    proxy = _start_proxy(tmp_path, fake_port, proxy_port, seed=("two",))
    try:
        assert _wait_port(proxy_port), "proxy never started listening"
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/models", method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert b"served-by-two" in resp.read()
        assert seen == ["tok-two"]
    finally:
        _kill_proxy(proxy, server)


@pytest.mark.parametrize(
    "content_length",
    [
        pytest.param(
            b"not-a-number",
            id="non-numeric — used to raise an uncaught ValueError (no per-request "
            "try/except in BaseHTTPRequestHandler), aborting the connection instead "
            "of a clean 400",
        ),
        pytest.param(
            b"-1",
            id="negative — truthy in Python, so `self.rfile.read(length)` used to "
            "read until EOF on a keep-alive connection the client never closes, "
            "hanging the request thread forever instead of rejecting it",
        ),
    ],
)
def test_a_malformed_content_length_gets_a_clean_400(
    tmp_path: Path, content_length: bytes
) -> None:
    import socket

    fake_port = _free_port()
    proxy_port = _free_port()
    server = _fake_anthropic(fake_port, [])
    proxy = _start_proxy(tmp_path, fake_port, proxy_port, seed=("two",))
    try:
        assert _wait_port(proxy_port), "proxy never started listening"
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as sock:
            sock.sendall(
                b"POST /v1/messages HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: " + content_length + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        assert response.startswith(b"HTTP/1.0 400") or response.startswith(b"HTTP/1.1 400")
    finally:
        _kill_proxy(proxy, server)


def test_sigterm_shuts_the_proxy_down_instead_of_deadlocking(tmp_path: Path) -> None:
    """server.shutdown() blocks until serve_forever()'s loop notices the shutdown
    flag — but a signal handler runs synchronously on the same main thread
    serve_forever() runs on, so calling shutdown() directly from the handler used
    to deadlock: a plain SIGTERM (session logout, `pkill`) left the singleton wedged,
    unresponsive, and still holding the port. This asserts a clean SIGTERM exit
    without falling back to SIGKILL."""
    import signal

    fake_port = _free_port()
    proxy_port = _free_port()
    server = _fake_anthropic(fake_port, [])
    proxy = _start_proxy(tmp_path, fake_port, proxy_port, seed=("two",))
    try:
        assert _wait_port(proxy_port), "proxy never started listening"
        proxy.send_signal(signal.SIGTERM)
        try:
            returncode = proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()
            proxy.wait(timeout=5)
            raise AssertionError("proxy did not exit within 10s of SIGTERM — deadlocked")
        assert returncode == 0
    finally:
        server.shutdown()
