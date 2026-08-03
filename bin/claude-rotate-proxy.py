#!/usr/bin/env python3
"""claude-rotate-proxy — a loopback proxy that rotates Claude subscription accounts.

Claude Code decides HOW to present a credential once, at launch, from the slot the
token arrived in. A launch-slot ``CLAUDE_CODE_OAUTH_TOKEN`` gets the subscription
presentation: ``Authorization: Bearer <token>`` plus the ``anthropic-beta:
oauth-2025-04-20`` header that makes the request bill a subscription. So the
``claude`` wrapper launches the client with a SENTINEL in that slot — the client
fixes the subscription presentation without ever holding a real token — and points
``ANTHROPIC_BASE_URL`` at this proxy. This proxy replaces only the ``Authorization``
VALUE on the way through, so the presentation the client fixed survives however many
times the account changes.

The token never enters THIS process. The upstream request is issued by
``envchain <ns> curl``: ``$CLAUDE_CODE_OAUTH_TOKEN`` expands inside that child and
reaches curl over a pipe (``-H @-``), exactly the discipline
``claude-account-lib.sh``'s ``_probe`` uses, so the credential lands on no argv, in
no file, and in no long-lived process. This proxy only chooses the account (via
``claude-account --pick``), relays bytes, and on a usage-limit 429 records the
cooldown (``claude-account --cooldown``) and replays the request on the next account
before the client ever sees a failure.

Detection needs no transcript grep and no poll: the proxy reads the 429 straight off
the response. It binds 127.0.0.1 only, is a per-machine singleton (a second start
fails to bind and exits), and self-exits after an idle period, so there is nothing to
install and nothing for doctor or uninstall to manage.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CLAUDE_ROTATE_PROXY_PORT", "8789"))
IDLE_EXIT_SECONDS = int(os.environ.get("CLAUDE_ROTATE_PROXY_IDLE_EXIT", "1800"))
UPSTREAM = os.environ.get("CLAUDE_ROTATE_PROXY_UPSTREAM", "https://api.anthropic.com")
# How many distinct accounts one client request may be replayed across before the
# proxy returns the last upstream verdict. Bounds a rotation storm.
MAX_ROTATIONS = 8

_HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_ACCOUNT = os.environ.get(
    "CLAUDE_ROTATE_PROXY_ACCOUNT_CLI", os.path.join(_HERE, "claude-account.bash")
)

# The one line the child runs: print the Authorization header (token from the env,
# never an argv) into curl's -H @-, then exec the curl argv the proxy built. The
# token reaches curl only over that pipe — on no argv (printf is a builtin), in no
# file. Kept as a constant so the token-handling contract is read in one place.
_CHILD_SCRIPT = 'printf "Authorization: Bearer %s\\n" "$CLAUDE_CODE_OAUTH_TOKEN" | exec "$@"'

_last_request = time.monotonic()
_last_request_lock = threading.Lock()


def _mark_request() -> None:
    global _last_request
    with _last_request_lock:
        _last_request = time.monotonic()


def _idle_seconds() -> float:
    with _last_request_lock:
        return time.monotonic() - _last_request


def _pick_namespace() -> str | None:
    """The subscription namespace to serve right now, or None when none is usable.

    Delegates to the tested selection engine; the proxy never re-implements it.
    """
    proc = subprocess.run(
        [CLAUDE_ACCOUNT, "--pick"], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or None


def _record_cooldown(namespace: str, reset_epoch: str) -> None:
    """Record NAMESPACE as rate-limited until RESET_EPOCH (best-effort)."""
    subprocess.run(
        [CLAUDE_ACCOUNT, "--cooldown", namespace, reset_epoch],
        capture_output=True,
        check=False,
    )


def _is_usage_limit(status: int, headers: list[tuple[str, str]]) -> bool:
    """A usage-limit denial — the one signal that must trigger a rotation.

    Read from the response itself, so detection needs no transcript and no poll. A
    429 without the unified-status header is still a rate limit, so rotate on it too.
    """
    if status != 429:
        return False
    for name, value in headers:
        if name.lower() == "anthropic-ratelimit-unified-status":
            return value.strip() == "rejected"
    return True


def _reset_epoch(headers: list[tuple[str, str]]) -> str:
    for name, value in headers:
        if name.lower() == "anthropic-ratelimit-unified-reset":
            return value.strip()
    return ""


class _Upstream:
    """One upstream response with its body stream still open for relay."""

    def __init__(self, status, headers, proc):
        self.status = status
        self.headers = headers
        self.proc = proc


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 keep-alive: a real claude client reuses one connection for many
    # requests, so the server must be threaded (ThreadingHTTPServer below) or a
    # held-open connection would serialize every other one.
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object, **kwargs: object) -> None:
        pass

    # Anthropic's API is POST for /v1/messages, but the client may GET other paths
    # through the base URL; a method with no handler would 501 and break the session,
    # so both route through one handler.
    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        _mark_request()
        # A chunked request body carries no content-length, so reading by
        # content-length would silently forward an EMPTY body. Fail loud instead of
        # corrupting the request; the client always sends content-length for JSON.
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            self._reply_plain(
                400, b"claude-rotate-proxy: chunked request bodies are not supported.\n"
            )
            return
        try:
            length = int(self.headers.get("content-length", 0))
        except ValueError:
            self._reply_plain(400, b"claude-rotate-proxy: invalid content-length header.\n")
            return
        if length < 0:
            self._reply_plain(400, b"claude-rotate-proxy: invalid content-length header.\n")
            return
        body = self.rfile.read(length) if length else b""
        # Drop hop-by-hop and the client's own credential headers: the sentinel
        # Authorization it sent is replaced downstream, and any x-api-key would
        # double-authenticate.
        forwarded = [
            f"{name}: {value}"
            for name, value in self.headers.items()
            if name.lower()
            not in ("host", "content-length", "authorization", "x-api-key", "connection")
        ]

        last_status = None
        for _ in range(MAX_ROTATIONS):
            namespace = _pick_namespace()
            if namespace is None:
                self._reply_plain(
                    503,
                    b"claude-rotate-proxy: no usable Claude subscription account "
                    b"(every account is at its usage limit).\n",
                )
                return
            upstream = self._forward(namespace, forwarded, body)
            if upstream is None:
                self._reply_plain(502, b"claude-rotate-proxy: upstream request failed.\n")
                return
            if _is_usage_limit(upstream.status, upstream.headers):
                upstream.proc.stdout.read()
                upstream.proc.wait()
                _record_cooldown(namespace, _reset_epoch(upstream.headers))
                last_status = upstream.status
                continue
            self._relay(upstream)
            return

        self._reply_plain(
            last_status or 502, b"claude-rotate-proxy: all accounts rate-limited.\n"
        )

    def _forward(self, namespace, forwarded, body) -> "_Upstream | None":
        """Issue the request as ``envchain <ns> curl``; the token expands only in
        that child. The body and forwarded headers ride short-lived non-secret files
        so curl's single stdin is free for the Authorization header."""
        body_file = tempfile.NamedTemporaryFile(delete=False)
        hdr_file = tempfile.NamedTemporaryFile(delete=False, mode="w")
        proc: "subprocess.Popen | None" = None
        try:
            body_file.write(body)
            body_file.close()
            hdr_file.write("\n".join(forwarded) + ("\n" if forwarded else ""))
            hdr_file.close()
            curl = ["curl", "-sS", "-N", "-D", "-", "-o", "-", "-X", self.command]
            if body:
                curl += ["--data-binary", "@" + body_file.name]
            curl += ["-H", "@" + hdr_file.name, "-H", "@-", f"{UPSTREAM}{self.path}"]
            argv = ["envchain", namespace, "bash", "-c", _CHILD_SCRIPT, "_", *curl]
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE)
            except OSError:
                return None
            parsed = self._read_response(proc.stdout)
            if parsed is None:
                proc.wait()
                proc = None
                return None
            status, headers = parsed
            result = _Upstream(status, headers, proc)
            proc = None  # ownership passes to the caller, which reaps it
            return result
        except Exception:
            # A parse failure (malformed status line) must not leak the curl child.
            if proc is not None and proc.poll() is None:
                proc.kill()
            raise
        finally:
            if proc is not None:
                proc.wait()
            # Safe to unlink now: _read_response only returns once curl has produced
            # response headers, which means it already sent the request and therefore
            # already read the body (--data-binary @file) and header (-H @file) files.
            # On the early-return paths curl never started or is dead, so the files
            # are equally free to remove.
            for path in (body_file.name, hdr_file.name):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _read_response(self, stream) -> "tuple[int, list[tuple[str, str]]] | None":
        """The final response's (status, headers), skipping any interim 1xx blocks
        curl may emit; None if the stream ends or the status line will not parse (so
        a malformed upstream reply becomes a clean 502, not an unhandled exception)."""
        for _ in range(8):
            blob = self._read_header_block(stream)
            if blob is None:
                return None
            try:
                status, headers = self._parse_headers(blob)
            except (ValueError, IndexError):
                return None
            if status >= 200:
                return status, headers
        return None

    @staticmethod
    def _read_header_block(stream) -> "bytes | None":
        blob = b""
        while b"\r\n\r\n" not in blob:
            chunk = stream.read(1)
            if not chunk:
                return None
            blob += chunk
        return blob

    @staticmethod
    def _parse_headers(blob: bytes) -> "tuple[int, list[tuple[str, str]]]":
        head = blob.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = head.split("\r\n")
        status = int(lines[0].split()[1])
        headers = [
            (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
            for line in lines[1:]
            if ":" in line
        ]
        return status, headers

    def _relay(self, upstream: _Upstream) -> None:
        """Stream a non-rotated upstream response back to the client."""
        self.send_response_only(upstream.status)
        for name, value in upstream.headers:
            if name.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(name, value)
        # The body length is unknown as it streams, so close the connection to
        # delimit it.
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        while True:
            chunk = upstream.proc.stdout.read(65536)
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
        upstream.proc.stdout.close()
        upstream.proc.wait()

    def _reply_plain(self, status: int, message: bytes) -> None:
        self.send_response_only(status)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(message)))
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(message)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _idle_watch(server: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(min(60, IDLE_EXIT_SECONDS))
        if _idle_seconds() >= IDLE_EXIT_SECONDS:
            server.shutdown()
            return


def main() -> int:
    if shutil.which("envchain") is None:
        sys.stderr.write("claude-rotate-proxy: envchain is not installed.\n")
        return 2
    if not os.path.exists(CLAUDE_ACCOUNT):
        sys.stderr.write(f"claude-rotate-proxy: {CLAUDE_ACCOUNT} not found.\n")
        return 2
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Almost always EADDRINUSE: another proxy already owns the port. A
        # per-machine singleton is the whole point, so exit quietly.
        return 0
    # server.shutdown() blocks until serve_forever()'s loop notices the
    # shutdown flag and signals back — but a signal handler runs synchronously
    # on the main thread, which is the same thread serve_forever() runs on.
    # Calling shutdown() directly from the handler deadlocks: the loop can't
    # observe the flag until the handler returns, and the handler is blocked
    # waiting for the loop. Run it on a separate thread instead, same as the
    # idle-watch shutdown below.
    signal.signal(
        signal.SIGTERM,
        lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
    )
    threading.Thread(target=_idle_watch, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
