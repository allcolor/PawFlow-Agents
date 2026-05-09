"""Private Gateway service - pre-authentication access gate.

When enabled, every HTTP request must first pass a secret challenge
before reaching the login page. Challenge secrets are referenced
explicitly from the ``privateGateway`` service configuration.

IP-based rate-limiting and banning:
- Exponential cooldown on failed attempts (1s, 3s, 10s, 30s).
- After 5 consecutive failures the IP is banned for 24 h.
- All requests from banned IPs are rejected immediately.

The "passed" state is tracked via an HMAC-signed cookie
that survives logout/login cycles.

HTTP listeners opt in by referencing a ``privateGateway`` service.
"""

import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any, Dict, List

from core.base_service import BaseService

logger = logging.getLogger(__name__)

_COOKIE_NAME = "_pf_gw"
_COOKIE_MAX_AGE = 30 * 86400  # 30 days


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _split_refs(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _signing_key() -> bytes:
    from core.secrets import get_secrets_manager
    return get_secrets_manager().derive_subkey(b"private-gateway-cookie")


def _make_cookie_value(ip: str) -> str:
    ts = str(int(time.time()))
    payload = f"{ts}:{ip}"
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{sig}"


def _verify_cookie(value: str, ip: str, max_age: int = _COOKIE_MAX_AGE) -> bool:
    try:
        ts_str, sig = value.split(".", 1)
        ts = int(ts_str)
        if time.time() - ts > max_age:
            return False
        payload = f"{ts_str}:{ip}"
        expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


_ip_state: Dict[str, dict] = {}
_lock = threading.Lock()
_COOLDOWNS = [0, 1, 3, 10, 30]
_MAX_FAILURES = 5
_BAN_DURATION = 24 * 3600
import core.paths as _paths


def _save_bans():
    """Persist banned IPs to disk. Call with _lock held."""
    now = time.time()
    bans = {ip: st for ip, st in _ip_state.items() if st.get("banned_until", 0) > now}
    try:
        _paths.GATEWAY_BANS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _paths.GATEWAY_BANS_FILE.write_text(json.dumps(bans), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save gateway bans: %s", e)


def _load_bans():
    """Load banned IPs from disk on startup.

    Skips entries for IPs that should never have been banned in the first
    place (loopback / RFC1918 / docker bridge — see _is_local_or_private).
    Pre-fix releases banned the user's LAN IP after the codex MCP bridge
    cookie-auth cascade; clean those up at boot so the rule kicks in
    even for already-persisted bans.
    """
    if not _paths.GATEWAY_BANS_FILE.exists():
        return
    try:
        data = json.loads(_paths.GATEWAY_BANS_FILE.read_text(encoding="utf-8"))
        now = time.time()
        skipped_local = 0
        with _lock:
            for ip, st in data.items():
                if st.get("banned_until", 0) <= now:
                    continue
                if _is_local_or_private(ip):
                    skipped_local += 1
                    continue
                _ip_state[ip] = st
        if skipped_local:
            logger.info(
                "Discarded %d stale local/docker-IP ban(s) at boot "
                "(local IPs are no longer ban-eligible).", skipped_local)
            # Rewrite the file so the discarded entries don't reappear.
            _save_bans()
        logger.info("Loaded %d gateway ban(s) from disk", len(_ip_state))
    except Exception as e:
        logger.warning("Failed to load gateway bans: %s", e)


# `_is_local_or_private` is defined below — forward-declare via a stub so
# the boot-time `_load_bans()` call can reach it before the real impl.
def _is_local_or_private(ip: str) -> bool:  # noqa: F811  (real impl below)
    if not ip:
        return True
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_reserved or addr.is_unspecified)
    except (ValueError, TypeError):
        return True


_load_bans()


def _is_local_or_private(ip: str) -> bool:
    """True for IPs that PawFlow's gateway must NEVER ban: loopback,
    RFC1918 private ranges (10/8, 172.16/12, 192.168/16), CGNAT
    (100.64/10), link-local (169.254/16), IPv6 loopback / link-local /
    ULA. Server-spawned components — the CC / codex / gemini Docker
    containers, the user's relay running on the LAN, anything in the
    docker bridge subnet — all live on these ranges and a failed auth
    attempt from one of them must not lock out everything else on the
    same source IP.

    Public IPs (failed attempts from the open internet) still get
    banned per the original 5-failures → 24h policy.
    """
    if not ip:
        return True  # missing addr — treat as local, don't ban anything
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_unspecified
        )
    except (ValueError, TypeError):
        # Unparseable — be safe and DON'T ban (better to miss a ban than
        # to lock out a legit user with a weird X-Forwarded-For header).
        return True


def _get_ip_state(ip: str) -> dict:
    with _lock:
        if ip not in _ip_state:
            _ip_state[ip] = {"failures": 0, "last_attempt": 0.0, "banned_until": 0.0}
        return _ip_state[ip]


def is_banned(ip: str) -> bool:
    # Local / docker-bridge / RFC1918 IPs are never banned — see
    # `_is_local_or_private` for the policy rationale.
    if _is_local_or_private(ip):
        return False
    with _lock:
        st = _ip_state.get(ip)
        if not st:
            return False
        if st["banned_until"] > time.time():
            return True
        if st["banned_until"] > 0:
            st["failures"] = 0
            st["banned_until"] = 0.0
        return False


def get_cooldown_remaining(ip: str) -> float:
    if _is_local_or_private(ip):
        return 0.0
    st = _get_ip_state(ip)
    if st["failures"] <= 0:
        return 0.0
    idx = min(st["failures"], len(_COOLDOWNS)) - 1
    cooldown = _COOLDOWNS[idx]
    remaining = (st["last_attempt"] + cooldown) - time.time()
    return max(0.0, remaining)


def record_failure(ip: str):
    # Local / docker-bridge / RFC1918 IPs are never recorded as failures.
    # A failed auth from a server-spawned MCP bridge or the user's
    # LAN relay must not pollute the ban counter — doing so would
    # eventually lock out every legitimate component sharing that IP.
    if _is_local_or_private(ip):
        return
    with _lock:
        st = _ip_state.setdefault(ip, {"failures": 0, "last_attempt": 0.0, "banned_until": 0.0})
        st["failures"] += 1
        st["last_attempt"] = time.time()
        if st["failures"] >= _MAX_FAILURES:
            st["banned_until"] = time.time() + _BAN_DURATION
            logger.warning("Private gateway: banned IP %s for 24h after %d failures",
                           ip, st["failures"])
            _save_bans()


def record_success(ip: str):
    with _lock:
        was_banned = _ip_state.pop(ip, {}).get("banned_until", 0) > time.time()
        if was_banned:
            _save_bans()


def list_bans() -> list:
    now = time.time()
    with _lock:
        return [
            {"ip": ip, "banned_until": st["banned_until"],
             "failures": st["failures"]}
            for ip, st in _ip_state.items()
            if st["banned_until"] > now
        ]


def unban_ip(ip: str) -> bool:
    with _lock:
        st = _ip_state.pop(ip, None)
        was_banned = st is not None and st.get("banned_until", 0) > time.time()
        if was_banned:
            _save_bans()
        return was_banned


def _load_gateway_secrets(secret_refs: Any = None) -> Dict[str, str]:
    refs = _split_refs(secret_refs)
    if not refs:
        return {}
    from core.expression import _load_global_secrets
    all_secrets = _load_global_secrets()
    return {ref: str(all_secrets[ref]) for ref in refs if ref in all_secrets}


def verify_secret(submitted: str, secret_refs: Any = None) -> bool:
    gw_secrets = _load_gateway_secrets(secret_refs)
    if not gw_secrets:
        logger.warning("Private gateway enabled but no explicit secret_refs resolved")
        return False
    for _name, value in gw_secrets.items():
        if hmac.compare_digest(submitted.strip().encode('utf-8'), value.strip().encode('utf-8')):
            return True
    return False


def is_enabled() -> bool:
    """Legacy module helper: standalone module state is never enabled."""
    return False



def render_challenge(error="", cooldown=0, next_url="/", skin="matrix"):
    try:
        from core.private_gateway_skins import render_skin
        result = render_skin(skin, error=error, cooldown=cooldown, next_url=next_url)
    except Exception as exc:
        logger.error("Private gateway skin %r failed: %s", skin, exc, exc_info=True)
        import html as _html
        result = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Private Gateway</title></head><body>'
            '<form method="POST" action="/_gateway">'
            f'<input type="hidden" name="next" value="{_html.escape(next_url, quote=True)}">'
            '<input type="password" name="secret" autocomplete="off" autofocus>'
            f'<div>{_html.escape(str(error or ""), quote=False)}</div>'
            '<button type="submit">Enter</button></form></body></html>'
        )
    return result.encode("utf-8")


def render_failure_redirect(submitted: str, skin="matrix") -> str:
    """Return a redirect URL for invalid key (skin-dependent).

    Returns empty string if no redirect (show error on same page).
    """
    try:
        from core.private_gateway_skins import failure_redirect
        return failure_redirect(skin, submitted)
    except Exception as exc:
        logger.debug("Private gateway failure redirect failed: %s", exc, exc_info=True)
        return ""


_EXEMPT_PATHS = frozenset(["/health", "/favicon.ico"])


def check_request(handler) -> bool:
    """Check an incoming HTTP request against the private gateway.

    Called from _RequestHandler._handle() BEFORE route matching.
    Returns True if the request was handled (blocked/challenged).
    Returns False if the request should proceed normally.
    """
    try:
        return _check_request_inner(handler, {})
    except Exception as e:
        logger.error("Private gateway error: %s", e, exc_info=True)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "text/plain")
            handler.end_headers()
            handler.wfile.write(b"Internal Server Error")
            handler.wfile.flush()
        except Exception:
            pass
        return True


def _check_request_inner(handler, config: Dict[str, Any]) -> bool:
    if not _truthy(config.get("enabled", False)):
        return False

    ip = handler.client_address[0] if handler.client_address else "0.0.0.0"
    path = handler.path.split('?', 1)[0]
    cookie_name = str(config.get("cookie_name") or _COOKIE_NAME)
    cookie_max_age = int(config.get("cookie_max_age") or _COOKIE_MAX_AGE)
    skin = str(config.get("skin") or "matrix").strip().lower()
    secret_refs = config.get("secret_refs", "")

    if path in _EXEMPT_PATHS:
        return False

    # Routes flagged `public=True AND private_only=True` carry their own
    # credential (usually a URL-embedded ephemeral token) AND restrict
    # themselves to RFC1918 source IPs. They must bypass this human-
    # oriented challenge page — otherwise automated LAN-only clients
    # (CC container hitting /relay-proxy/, service-to-service callbacks,
    # …) get the HTML challenge instead of their actual response and
    # can't parse it. Repro: CC surfaced
    #   "API returned an empty or malformed response (HTTP 200) —
    #    check for a proxy or gateway intercepting the request"
    # while the Matrix-themed challenge page was what actually flew
    # back (container has no _gw cookie). The private_only flag is
    # the guarantee that this bypass can't be abused from the public
    # internet.
    try:
        _server = getattr(handler, "server", None)
        _registry = getattr(_server, "_route_registry", None)
        if _registry is not None:
            _match = _registry.match(handler.command, path)
            _entry = _match[0] if _match else None
            if (_entry is not None
                    and getattr(_entry, "public", False)
                    and getattr(_entry, "private_only", False)):
                return False
    except Exception:
        logger.debug(
            "gateway public+private_only exempt check failed",
            exc_info=True)

    # /files/{file_id} — check if public or gateway_key access
    if path.startswith("/files/"):
        file_id = path.split("/")[2] if len(path.split("/")) >= 3 else ""
        if file_id:
            try:
                from core.file_store import FileStore, ACCESS_PUBLIC, ACCESS_GATEWAY_KEY
                level = FileStore.instance().get_access_level(file_id)
                if level == ACCESS_PUBLIC:
                    return False  # bypass gateway
                if level == ACCESS_GATEWAY_KEY:
                    # Check ?k= param
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(handler.path).query)
                    key = qs.get("k", [""])[0]
                    if key and FileStore.instance().check_access(
                            file_id, gateway_key=key):
                        return False  # bypass gateway
            except Exception:
                pass

    if is_banned(ip):
        _send_page(handler, 403, b"Forbidden", "text/plain")
        return True

    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(cookie_name + "="):
            cookie_val = part[len(cookie_name) + 1:]
            if _verify_cookie(cookie_val, ip, max_age=cookie_max_age):
                return False

    if handler.command == "POST" and path == "/_gateway":
        content_length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(content_length) if content_length > 0 else b""
        return _handle_submit(handler, ip, body, secret_refs, cookie_name,
                              cookie_max_age, skin)

    # Show challenge page, preserving original URL for post-auth redirect
    original_url = handler.path  # includes query string
    cooldown = get_cooldown_remaining(ip)
    page = render_challenge(cooldown=cooldown, next_url=original_url, skin=skin)
    _send_page(handler, 200, page, "text/html; charset=utf-8")
    return True


def _send_page(handler, status, body, content_type):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def _handle_submit(handler, ip, body, secret_refs=None,
                   cookie_name=_COOKIE_NAME, cookie_max_age=_COOKIE_MAX_AGE,
                   skin="matrix"):
    from urllib.parse import parse_qs
    params = parse_qs(body.decode("utf-8", errors="replace"))
    submitted = params.get("secret", [""])[0]
    next_url = params.get("next", ["/"])[0] or "/"
    # Ensure redirect is relative (prevent open redirect)
    if not next_url.startswith("/"):
        next_url = "/"

    cooldown = get_cooldown_remaining(ip)
    if cooldown > 0:
        record_failure(ip)
        page = render_challenge(error="Too many attempts.", cooldown=get_cooldown_remaining(ip), next_url=next_url, skin=skin)
        _send_page(handler, 429, page, "text/html; charset=utf-8")
        return True

    if not submitted or not verify_secret(submitted, secret_refs):
        record_failure(ip)
        if is_banned(ip):
            _send_page(handler, 403, b"Forbidden", "text/plain")
            return True
        # Skin-dependent failure redirect (e.g. Google → real google search)
        redirect_url = render_failure_redirect(submitted, skin=skin)
        if redirect_url:
            handler.send_response(302)
            handler.send_header("Location", redirect_url)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            handler.wfile.flush()
            return True
        cooldown = get_cooldown_remaining(ip)
        page = render_challenge(error="Invalid key.", cooldown=cooldown, next_url=next_url, skin=skin)
        _send_page(handler, 200, page, "text/html; charset=utf-8")
        return True

    # Success — set cookie and redirect to original URL
    record_success(ip)
    cookie_val = _make_cookie_value(ip)
    cookie = f"{cookie_name}={cookie_val}; Path=/; Max-Age={cookie_max_age}; HttpOnly; SameSite=Lax"
    handler.send_response(302)
    handler.send_header("Location", next_url)
    handler.send_header("Set-Cookie", cookie)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    handler.wfile.flush()
    return True


class PrivateGateway(BaseService):
    """Configurable private pre-authentication gateway."""

    TYPE = "privateGateway"
    VERSION = "1.0.0"
    NAME = "Private Gateway"
    DESCRIPTION = "Pre-authentication challenge gate for HTTP listeners"
    CATEGORY = "security"

    def _create_connection(self):
        return {"ready": True}

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "enabled": {
                "type": "boolean", "required": False, "default": False,
                "description": "Enable the private gateway challenge",
            },
            "secret_refs": {
                "type": "string", "required": True, "default": "",
                "description": "Comma-separated global secret names accepted by the challenge",
            },
            "skin": {
                "type": "string", "required": False, "default": "matrix",
                "description": "Private gateway skin resource name",
            },
            "cookie_name": {
                "type": "string", "required": False, "default": _COOKIE_NAME,
                "description": "Challenge pass cookie name",
            },
            "cookie_max_age": {
                "type": "integer", "required": False, "default": _COOKIE_MAX_AGE,
                "description": "Challenge pass cookie lifetime in seconds",
            },
        }

    def is_enabled(self) -> bool:
        return _truthy(self.config.get("enabled", False))

    @staticmethod
    def is_enabled_static() -> bool:
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for service_id, sdef in reg.get_all("global", "").items():
                if sdef.service_type != PrivateGateway.TYPE:
                    continue
                svc = reg.resolve(service_id)
                if svc and svc.is_enabled():
                    return True
        except Exception:
            logger.debug("PrivateGateway.is_enabled_static failed", exc_info=True)
        return False

    def check_request(self, handler) -> bool:
        return _check_request_inner(handler, self.config)

    def check_ws(self, path: str, headers: Dict[str, str], client_address,
                 internal_ok: bool = False) -> bool:
        if not self.is_enabled() or internal_ok:
            return False
        ip = client_address[0] if client_address else "0.0.0.0"
        if is_banned(ip):
            return True
        cookie_name = str(self.config.get("cookie_name") or _COOKIE_NAME)
        cookie_max_age = int(self.config.get("cookie_max_age") or _COOKIE_MAX_AGE)
        cookie_header = headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(cookie_name + "="):
                cookie_val = part[len(cookie_name) + 1:]
                if _verify_cookie(cookie_val, ip, max_age=cookie_max_age):
                    return False
        return True

    def is_banned(self, ip: str) -> bool:
        return is_banned(ip)

    def list_bans(self) -> list:
        return list_bans()

    def unban_ip(self, ip: str) -> bool:
        return unban_ip(ip)


from core import ServiceFactory
ServiceFactory.register(PrivateGateway)
