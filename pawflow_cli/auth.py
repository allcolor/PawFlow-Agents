"""OAuth browser authentication for PawCode."""
import logging

import json
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event, Thread
from urllib.parse import urlparse, parse_qs, quote

from pawflow_cli.config import load_session, save_session, clear_session


def check_session(server_url: str, gateway_cookie: str = "") -> dict:
    """Check for a valid cached session. Validates with server, returns {} if invalid.

    Always tries the server even if the local session is expired — the server
    may silently refresh using stored OAuth refresh tokens.
    """
    # Load session INCLUDING expired ones — server may still refresh them
    cached = load_session(include_expired=True)
    if not cached or cached.get("server_url") != server_url:
        return {}
    token = cached.get("token", "")
    if not token:
        return {}
    # Validate token with a lightweight server call (ping action)
    try:
        import http.client
        import ssl
        from urllib.parse import urlparse
        parsed = urlparse(server_url)
        use_ssl = parsed.scheme == "https"
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if use_ssl else 80)
        if use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=5)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=5)
        _headers = {"Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"}
        if gateway_cookie:
            _headers["Cookie"] = f"_pf_gw={gateway_cookie}"
        _body = json.dumps({"action": "ping"})
        conn.request("POST", "/api/ui", body=_body, headers=_headers)
        resp = conn.getresponse()
        # Follow 301/302 (HTTP→HTTPS redirect)
        if resp.status == 301:
            resp.read()
            location = resp.getheader("Location", "")
            conn.close()
            if location:
                from urllib.parse import urlparse as _rparse
                _rp = _rparse(location)
                _rssl = _rp.scheme == "https"
                _rh = _rp.hostname or host
                _rpt = _rp.port or (443 if _rssl else 80)
                if _rssl:
                    _ctx2 = ssl.create_default_context()
                    _ctx2.check_hostname = False
                    _ctx2.verify_mode = ssl.CERT_NONE
                    conn = http.client.HTTPSConnection(_rh, _rpt, context=_ctx2, timeout=5)
                else:
                    conn = http.client.HTTPConnection(_rh, _rpt, timeout=5)
                conn.request("POST", _rp.path or "/api/ui", body=_body, headers=_headers)
                resp = conn.getresponse()
        _resp_body = resp.read().decode("utf-8", errors="replace")
        # Server may send a refreshed token in header (silent OAuth refresh)
        new_token = resp.getheader("X-Session-Token")
        conn.close()
        if resp.status == 401 or resp.status == 403:
            clear_session()
            return {}
        # Private Gateway challenge page (HTTP 200 + HTML): the ping never
        # reached the server, so this proves nothing about the session —
        # and every later request will be blocked the same way. Surface it
        # instead of mistaking the page for a valid ping response.
        from pawflow_cli.api import looks_like_gateway_challenge, GATEWAY_BLOCKED_HINT
        if looks_like_gateway_challenge(_resp_body):
            sys.stderr.write(f"[PawCode] {GATEWAY_BLOCKED_HINT}\n")
            return {}
        if new_token:
            token = new_token
        save_session(token, cached.get("username", ""), server_url,
                     time.time() + 8 * 3600)
        return {"token": token, "username": cached.get("username", ""),
                "server_url": server_url}
    except Exception:
        # Server unreachable — trust local cache if not expired
        expires = cached.get("expires_at", 0)
        if expires and time.time() < expires:
            return cached
        clear_session()
        return {}


def parse_pasted_credential(pasted: str) -> tuple:
    """Extract (token, username) from a pasted login redirect URL or token.

    After login the browser is redirected to
    ``http://127.0.0.1:<port>/callback?token=...&username=...``. On a
    headless/remote machine that loopback address is unreachable, but the
    token still sits in the browser's address bar — the user copies either
    the whole URL or just the token and pastes it back here.
    """
    pasted = (pasted or "").strip()
    if not pasted:
        return "", ""
    if "token=" in pasted or pasted.lower().startswith("http"):
        try:
            q = urlparse(pasted).query or pasted.split("?", 1)[-1]
            params = parse_qs(q)
            return (params.get("token", [""])[0],
                    params.get("username", [""])[0])
        except Exception:
            return "", ""
    # A bare token: no whitespace, looks like an opaque id.
    if " " not in pasted and "/" not in pasted:
        return pasted, ""
    return "", ""


def _manual_login(server_url: str, auth_url: str, input_fn) -> dict:
    """No-browser login: print the URL, let the user authenticate on ANY
    machine, then paste the redirected URL (or token) back here."""
    sys.stderr.write(
        "\nHeadless login — open this URL in a browser on any machine:\n"
        f"  {auth_url}\n\n"
        "After signing in, your browser is redirected to a "
        "127.0.0.1/callback URL.\nThat address may not load (normal on a "
        "remote machine) — copy the full URL from the address bar (or just "
        "the token=... value) and paste it here.\n\n")
    for _ in range(5):
        try:
            pasted = input_fn("Paste the redirected URL or token (blank to cancel): ")
        except (EOFError, KeyboardInterrupt):
            raise RuntimeError("Login cancelled")
        if not pasted.strip():
            raise RuntimeError("Login cancelled")
        token, username = parse_pasted_credential(pasted)
        if token:
            return {"token": token, "username": username}
        sys.stderr.write("Couldn't find a token in that input — try again.\n")
    raise RuntimeError("No valid token pasted")


def authenticate(server_url: str, force: bool = False,
                 gateway_cookie: str = "", no_browser: bool = False,
                 input_fn=input) -> dict:
    """Authenticate with PawFlow server. Returns {token, username, server_url}.

    Tries cached session first. Opens a browser and listens on a loopback
    callback by default; with ``no_browser`` (or when no browser is
    available) it falls back to a copy/paste flow that works over SSH and
    on headless machines.
    """
    if not force:
        cached = check_session(server_url, gateway_cookie=gateway_cookie)
        if cached:
            return cached

    # A loopback relay_callback makes the server embed the token in the
    # post-login redirect (the only place it surfaces). Even in paste mode
    # we keep it loopback so the token lands in the browser's address bar.
    callback_port = 0
    result = {"token": None, "username": None}  # nosec B105
    ready = Event()
    server = None

    if not no_browser:
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                result["token"] = params.get("token", [None])[0]
                result["username"] = params.get("username", [None])[0]
                html = (
                    '<!DOCTYPE html><html><body style="font-family:sans-serif;'
                    'text-align:center;padding:60px;background:#1a1a2e;color:#e0e0e0">'
                    '<h2>&#10004; PawCode authenticated</h2>'
                    '<p>You can close this window.</p></body></html>'
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
                ready.set()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        callback_port = server.server_address[1]
        Thread(target=server.handle_request, daemon=True).start()
    else:
        # Manual mode still needs a loopback callback in the URL so the
        # server includes the token in its redirect; the port need not be
        # listening since the user pastes the URL back instead.
        callback_port = 1

    callback_url = f"http://127.0.0.1:{callback_port}/callback"
    auth_url = f"{server_url}/auth/login?relay_callback={quote(callback_url)}"

    # Manual / headless path: no loopback race, just paste the result.
    if no_browser:
        creds = _manual_login(server_url, auth_url, input_fn)
        result["token"] = creds["token"]
        result["username"] = creds["username"]
    else:
        sys.stderr.write("Opening browser for login...\n")
        opened = False
        try:
            opened = webbrowser.open(auth_url)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        # Always print URL — browser may fail in headless/WSL/SSH environments
        sys.stderr.write(f"\nIf the browser didn't open, visit this URL:\n{auth_url}\n\n")
        sys.stderr.write(
            "Waiting for the browser callback... (on a headless/remote "
            "machine, press Ctrl+C and re-run `/login paste`)\n")
        if not ready.wait(timeout=120):
            if server:
                server.server_close()
            raise TimeoutError(
                "Authentication timed out (120s). On a headless/remote "
                "machine use the paste flow: `/login paste` (or "
                "`pawcode auth login --no-browser`).")
        if server:
            server.server_close()

    if not result["token"]:
        raise RuntimeError("No token received from login")

    # Cache session (8h default expiry)
    expires_at = time.time() + 8 * 3600
    save_session(result["token"], result["username"], server_url, expires_at)

    return {
        "token": result["token"],
        "username": result["username"],
        "server_url": server_url,
    }
