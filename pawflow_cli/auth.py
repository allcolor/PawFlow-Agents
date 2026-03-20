"""OAuth browser authentication for PawCode."""

import json
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event, Thread
from urllib.parse import urlparse, parse_qs, quote

from pawflow_cli.config import load_session, save_session, clear_session


def authenticate(server_url: str, force: bool = False) -> dict:
    """Authenticate with PawFlow server. Returns {token, username, server_url}.

    Tries cached session first. Opens browser if needed.
    """
    if not force:
        cached = load_session()
        if cached and cached.get("server_url") == server_url:
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
    webbrowser.open(auth_url)

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
