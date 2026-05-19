"""PawFlow relay — server-side auto-registration helpers.

All HTTP goes through pawflow_relay.utils.api_call so there is exactly one
stdlib HTTP client in the package. Functions here are pure (no argparse
coupling) so both the monolithic tools/pawflow_relay.py worker and any
future CLI can share them.

Exported:
    acquire_gateway_cookie(api_url, gateway_key)
    agent_api_call(api_url, session_token, action_body, gateway_cookie="")
    create_service(api_url, session_token, service_id, relay_path, token,
                   gateway_cookie="")
    delete_service(api_url, session_token, service_id, gateway_cookie="")
    start_callback_server() -> (port, result_holder, ready_event, server)
    auto_register(login_url, directory, relay_id="", relay_path="/ws/relay",
                  gateway_cookie="", timeout=120)
        -> (ws_url, ws_token, session_token, resolved_relay_id, login_url)
"""
import logging

import http.client
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .utils import api_call, generate_relay_id


def acquire_gateway_cookie(api_url, gateway_key):
    """POST /_gateway with the access key; return the _pf_gw cookie value.

    Respects PAWFLOW_RELAY_INSECURE=1 for self-signed certs in dev.
    """
    parsed = urlparse(api_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        import ssl
        ctx = ssl.create_default_context()
        if os.environ.get("PAWFLOW_RELAY_INSECURE") == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port)

    body = urlencode({"secret": gateway_key, "next": "/"})
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    conn.request("POST", "/_gateway", body=body, headers=headers)
    resp = conn.getresponse()
    resp.read()

    cookie_val = ""
    for hdr in resp.msg.get_all("Set-Cookie") or []:
        for part in hdr.split(";"):
            part = part.strip()
            if part.startswith("_pf_gw="):
                cookie_val = part[len("_pf_gw="):]
                break
        if cookie_val:
            break
    conn.close()
    return cookie_val


def agent_api_call(api_url, session_token, action_body, gateway_cookie=""):
    """Dispatch a UI action on /api/ui.

    UI actions (service_install, service_uninstall, relay_list_available, ...)
    use /api/ui — not /api/agent, which is reserved for user↔agent messages.
    """
    return api_call(api_url, "POST", "/api/ui", body=action_body,
                    session_token=session_token,
                    gateway_cookie=gateway_cookie)


def create_service(api_url, session_token, service_id, relay_path, token,
                   gateway_cookie=""):
    """Create a user filesystem (relay) service.

    `port` from the legacy config schema is omitted — the server listens on
    its own main HTTP listener and does not use the value.
    """
    config_str = f"path={relay_path},token={token},mode=readwrite"
    return agent_api_call(api_url, session_token, {
        "action": "service_install",
        "service_type": "relay",
        "service_name": service_id,
        "config_str": config_str,
    }, gateway_cookie=gateway_cookie)


def delete_service(api_url, session_token, service_id, gateway_cookie=""):
    """Delete a user filesystem service. Swallows the 404 case."""
    try:
        agent_api_call(api_url, session_token, {
            "action": "service_uninstall",
            "service_id": service_id,
        }, gateway_cookie=gateway_cookie)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def start_callback_server():
    """Loopback HTTP server that collects the OAuth callback token.

    Returns (port, result_dict, ready_event, server). The caller waits on
    `ready_event.wait(timeout)` then reads result_dict[{token,username}].
    """
    result = {"token": None, "username": None}  # nosec B105
    ready = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            result["token"] = params.get("token", [None])[0]
            result["username"] = params.get("username", [None])[0]
            html = (
                '<!DOCTYPE html><html><body style="font-family:sans-serif;'
                'text-align:center;padding:60px;background:#1a1a2e;color:#e0e0e0">'
                '<h2>&#10004; Relay authenticated</h2>'
                '<p>You can close this window. The relay is now connected.</p>'
                '</body></html>'
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
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return port, result, ready, server


def auto_register(login_url, directory, relay_id="", relay_path="/ws/relay",
                  gateway_cookie="", timeout=120, log=None):
    """Browser-based auto-registration.

    Opens the user's browser at login_url, waits for the OAuth callback,
    (re)creates the relay service on the server, and returns everything the
    caller needs to open the WS reverse tunnel:

        (ws_url, ws_token, session_token, resolved_relay_id, login_url)

    `log` is an optional callable(str) for progress messages; defaults to
    stderr writes so the function is usable from a bare script.
    """
    if log is None:
        def log(msg):
            sys.stderr.write(msg + "\n")

    login_url = login_url.rstrip("/")
    cb_port, cb_result, cb_ready, cb_server = start_callback_server()
    callback_url = f"http://127.0.0.1:{cb_port}/callback"
    auth_url = f"{login_url}/auth/login?relay_callback={quote(callback_url)}"
    log(f"[FSRelay] Opening browser for login: {auth_url}")
    log("[FSRelay] Waiting for authentication...")
    webbrowser.open(auth_url)

    if not cb_ready.wait(timeout=timeout):
        cb_server.server_close()
        raise TimeoutError(f"authentication timed out ({timeout}s)")
    cb_server.server_close()

    session_token = cb_result.get("token")
    username = cb_result.get("username") or "?"
    if not session_token:
        raise RuntimeError("no token received from login callback")

    log(f"[FSRelay] Authenticated as '{username}'.")

    root_dir = str(Path(directory).resolve())
    if not relay_id:
        relay_id = generate_relay_id(username, root_dir)
        log(f"[FSRelay] Auto-generated relay ID: {relay_id}")

    ws_token = secrets.token_urlsafe(32)

    log(f"[FSRelay] Cleaning up previous service '{relay_id}' ...")
    delete_service(login_url, session_token, relay_id,
                   gateway_cookie=gateway_cookie)

    log(f"[FSRelay] Creating service '{relay_id}' ...")
    create_service(login_url, session_token, relay_id, relay_path, ws_token,
                   gateway_cookie=gateway_cookie)
    log("[FSRelay] Service created.")

    parsed = urlparse(login_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ws_url = f"{scheme}://{host}:{port}/ws/relay/{relay_id}"

    return ws_url, ws_token, session_token, relay_id, login_url
