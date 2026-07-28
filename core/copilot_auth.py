"""GitHub Copilot authentication: device flow, then short-lived token exchange.

Copilot is a two-token provider. The device flow yields a long-lived GitHub
OAuth token, which is what PawFlow stores as the service's ``api_key``. That
token cannot call the chat endpoint: it is exchanged for a Copilot token that
lives well under an hour, so the exchange has to be redone as sessions run
long. Both steps are here, the second one memoised per GitHub token.

The device flow is chosen over a redirect flow on purpose: it needs no callback
URL and no browser on this machine. The user opens the URL wherever they
already have a browser and types the code.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GITHUB_HOST = "github.com"
GITHUB_API_HOST = "api.github.com"
DEVICE_CODE_PATH = "/login/device/code"
ACCESS_TOKEN_PATH = "/login/oauth/access_token"  # nosec B105 -- URL path, not a secret
COPILOT_TOKEN_PATH = "/copilot_internal/v2/token"  # nosec B105 -- URL path, not a secret

#: Public client id of the GitHub Copilot editor integration. Client ids are
#: not secrets; this one is what every editor plugin sends. Override with
#: ``PAWFLOW_COPILOT_CLIENT_ID`` to use your own registered OAuth app.
DEFAULT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

#: Copilot rejects requests that do not identify an editor.
DEFAULT_EDITOR_VERSION = "vscode/1.99.0"
DEFAULT_PLUGIN_VERSION = "copilot-chat/0.26.0"
INTEGRATION_ID = "vscode-chat"

#: Refresh this long before the token actually dies, so a turn that starts
#: valid does not expire mid-stream.
_REFRESH_MARGIN_SECONDS = 300

_token_cache: Dict[str, Tuple[str, float]] = {}
_cache_lock = threading.Lock()


def client_id() -> str:
    return (os.environ.get("PAWFLOW_COPILOT_CLIENT_ID") or "").strip() or DEFAULT_CLIENT_ID


def _post_form(host: str, path: str, fields: Dict[str, str],
               extra_headers: Dict[str, str] = None) -> Dict[str, Any]:
    body = urlencode(fields).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
        "User-Agent": "PawFlow",
    }
    headers.update(extra_headers or {})
    conn = http.client.HTTPSConnection(host, timeout=30)
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"GitHub returned {response.status}: {raw[:300]}")
        return json.loads(raw)
    finally:
        conn.close()


def _get_json(host: str, path: str, headers: Dict[str, str]) -> Dict[str, Any]:
    conn = http.client.HTTPSConnection(host, timeout=30)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"GitHub returned {response.status}: {raw[:300]}")
        return json.loads(raw)
    finally:
        conn.close()


def start_device_login() -> Dict[str, Any]:
    """Ask GitHub for a device code.

    Returns the user-facing code and URL, plus the device code the caller must
    hand back to :func:`poll_device_login`.
    """
    data = _post_form(GITHUB_HOST, DEVICE_CODE_PATH, {
        "client_id": client_id(),
        "scope": "read:user",
    })
    if not data.get("device_code"):
        raise RuntimeError(f"GitHub did not return a device code: {str(data)[:200]}")
    return {
        "device_code": data["device_code"],
        "user_code": data.get("user_code", ""),
        "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
        "interval": int(data.get("interval", 5) or 5),
        "expires_in": int(data.get("expires_in", 900) or 900),
    }


def poll_device_login(device_code: str) -> Dict[str, Any]:
    """Poll once for the device flow's outcome.

    One poll, not a loop: the caller drives the cadence, so a browser tab that
    is never opened costs nothing here. ``authorization_pending`` and
    ``slow_down`` are the two non-terminal answers GitHub defines.
    """
    if not device_code:
        raise ValueError("device_code is required")
    data = _post_form(GITHUB_HOST, ACCESS_TOKEN_PATH, {
        "client_id": client_id(),
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })
    token = data.get("access_token") or ""
    if token:
        return {"status": "ok", "access_token": token}
    error = data.get("error") or ""
    if error in ("authorization_pending", "slow_down"):
        return {"status": "pending", "error": error,
                "slow_down": error == "slow_down"}
    return {"status": "error",
            "error": data.get("error_description") or error or "unknown error"}


def _cache_key(github_token: str) -> str:
    return hashlib.sha256(github_token.encode("utf-8")).hexdigest()


def copilot_token(github_token: str, *, now: float = 0.0) -> str:
    """Exchange a GitHub token for a Copilot chat token, cached until it ages out.

    Every call would otherwise be a second network round trip; the token is
    valid for tens of minutes, so it is kept in memory (never on disk — the
    long-lived GitHub token is the thing worth persisting, and that lives in
    the service's encrypted config).
    """
    if not github_token:
        raise ValueError("A GitHub token is required for the copilot provider")
    stamp = now or time.time()
    key = _cache_key(github_token)
    with _cache_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] - _REFRESH_MARGIN_SECONDS > stamp:
            return cached[0]

    data = _get_json(GITHUB_API_HOST, COPILOT_TOKEN_PATH, {
        "Authorization": f"token {github_token}",
        "Accept": "application/json",
        "User-Agent": "PawFlow",
        "Editor-Version": editor_version(),
    })
    token = data.get("token") or ""
    if not token:
        raise RuntimeError(
            "GitHub accepted the credentials but returned no Copilot token — "
            "the account may not have an active Copilot subscription")
    # GitHub sends an absolute expiry; treat a missing one as short-lived
    # rather than assuming it never expires.
    expires_at = float(data.get("expires_at") or (stamp + 1800))
    with _cache_lock:
        _token_cache[key] = (token, expires_at)
    logger.info("[copilot] chat token obtained, expires in %ds", int(expires_at - stamp))
    return token


def editor_version() -> str:
    return (os.environ.get("PAWFLOW_COPILOT_EDITOR_VERSION") or "").strip() or DEFAULT_EDITOR_VERSION


def plugin_version() -> str:
    return (os.environ.get("PAWFLOW_COPILOT_PLUGIN_VERSION") or "").strip() or DEFAULT_PLUGIN_VERSION


def clear_token_cache() -> None:
    with _cache_lock:
        _token_cache.clear()
