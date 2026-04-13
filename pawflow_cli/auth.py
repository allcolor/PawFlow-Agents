"""OAuth browser authentication for PawCode."""

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
    import os as _os, time as _t
    _trace_path = _os.path.join(_os.path.expanduser("~"),
                                 ".pawflow", "pawcode_start.log")
    def _trace(msg):
        try:
            with open(_trace_path, "a", encoding="utf-8") as f:
                f.write(f"{_t.strftime('%H:%M:%S')} [check_session] {msg}\n")
        except Exception:
            pass
    _trace("entered")
    # Load session INCLUDING expired ones — server may still refresh them
    cached = load_session(include_expired=True)
    _trace(f"cached session exists={bool(cached)} "
           f"server_url_match={(cached or {}).get('server_url') == server_url}")
    if not cached or cached.get("server_url") != server_url:
        _trace("no matching cached session — returning {}")
        return {}
    token = cached.get("token", "")
    if not token:
        _trace("no token in cached session — returning {}")
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
        _trace(f"connecting {host}:{port} ssl={use_ssl}")
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
        _trace("about to POST /api/agent")
        conn.request("POST", "/api/agent", body=_body, headers=_headers)
        _trace("request sent, awaiting response")
        resp = conn.getresponse()
        _trace(f"got response status={resp.status}")
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
                conn.request("POST", _rp.path or "/api/agent", body=_body, headers=_headers)
                resp = conn.getresponse()
        resp.read()
        # Server may send a refreshed token in header (silent OAuth refresh)
        new_token = resp.getheader("X-Session-Token")
        conn.close()
        if resp.status == 401 or resp.status == 403:
            clear_session()
            return {}
        if new_token:
            token = new_token
        save_session(token, cached.get("username", ""), server_url,
                     time.time() + 8 * 3600)
        return {"token": token, "username": cached.get("username", ""),
                "server_url": server_url}
    except Exception as _ce:
        _trace(f"exception: {type(_ce).__name__}: {_ce}")
        # Server unreachable — trust local cache if not expired
        expires = cached.get("expires_at", 0)
        if expires and time.time() < expires:
            _trace("returning cached (not expired)")
            return cached
        _trace("clearing session and returning {}")
        clear_session()
        return {}


def authenticate(server_url: str, force: bool = False,
                 gateway_cookie: str = "") -> dict:
    """Authenticate with PawFlow server. Returns {token, username, server_url}.

    Tries cached session first. Opens browser if needed.
    """
    if not force:
        cached = check_session(server_url, gateway_cookie=gateway_cookie)
        if cached:
            return cached

    # Start local callback server
    result = {"token": None, "username": None}
    ready = Event()

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
    port = server.server_address[1]
    Thread(target=server.handle_request, daemon=True).start()

    callback_url = f"http://127.0.0.1:{port}/callback"
    auth_url = f"{server_url}/auth/login?relay_callback={quote(callback_url)}"

    sys.stderr.write(f"Opening browser for login...\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    # Always print URL — browser may fail in headless/WSL/SSH environments
    sys.stderr.write(f"\nIf the browser didn't open, visit this URL:\n{auth_url}\n\n")

    if not ready.wait(timeout=120):
        server.server_close()
        raise TimeoutError("Authentication timed out (120s)")

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
