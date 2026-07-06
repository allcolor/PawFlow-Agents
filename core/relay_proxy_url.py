"""Relay-aware provider URL helpers.

PawFlow uses URLs shaped like `http(s)://<relay_id>/<host>:<port>/path` to
route HTTP calls through a user's relay. The URL scheme is the target protocol
used by the relay. The public URL minted here points back to PawFlow's
`/relay-proxy/...` route with an ephemeral token.
"""

from dataclasses import dataclass
import ipaddress
import logging
import re
import socket
from typing import Optional
import urllib.parse

from core.docker_utils import get_host_ip

logger = logging.getLogger(__name__)

_TARGET_SEGMENT_RE = re.compile(r"^(?:\[([^\]]+)\]|([^/:?#]+)):(\d{1,5})$")
_EXPR_OPEN = "$" + "{"


@dataclass(frozen=True)
class RelayProxyUrl:
    """Parsed PawFlow relay URL."""

    target_scheme: str
    relay_id: str
    target_host: str
    target_port: int
    target_path: str
    query: str = ""
    relay_local: Optional[bool] = None


def _service_error(message: str):
    from core import ServiceError
    return ServiceError(message)


def _is_private_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_url_template(raw_url: str, *, user_id: str = "",
                          conversation_id: str = "",
                          agent_name: str = "") -> str:
    value = str(raw_url or "").strip()
    if _EXPR_OPEN not in value:
        return value
    try:
        from core.expression import resolve_expression
        return resolve_expression(
            value,
            parameters={"agent_name": agent_name or ""},
            owner=user_id or None,
            conversation_id=conversation_id or None,
        )
    except Exception:
        logger.debug("Relay URL expression resolution failed", exc_info=True)
        return value


def parse_relay_proxy_url(url: str) -> Optional[RelayProxyUrl]:
    """Parse the standard PawFlow relay URL format, if present.

    Standard format: `http(s)://<relay_id>/<host>:<port>/<path>`.
    The first path segment containing `host:port` is the discriminator between
    a normal HTTP URL and a PawFlow relay URL.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    first_segment = (parsed.path or "").lstrip("/").split("/", 1)[0]
    m = _TARGET_SEGMENT_RE.match(first_segment)
    if not m:
        return None
    if parsed.username or parsed.password or parsed.port is not None:
        raise ValueError("relay URL authority must be only the relay id")
    port = int(m.group(3))
    if port < 1 or port > 65535:
        raise ValueError("relay URL target port must be between 1 and 65535")
    target_host = m.group(1) or m.group(2) or ""
    relay_id = parsed.hostname or ""
    if not relay_id:
        raise ValueError("relay URL requires a relay id host")
    stripped = (parsed.path or "").lstrip("/")
    _first, sep, rest = stripped.partition("/")
    target_path = "/" + rest if sep else "/"
    return RelayProxyUrl(
        target_scheme=parsed.scheme,
        relay_id=relay_id,
        target_host=target_host,
        target_port=port,
        target_path=target_path,
        query=parsed.query or "",
    )


def is_relay_proxy_url(url: str) -> bool:
    """Return True when `url` has the standard PawFlow relay URL shape."""
    try:
        return parse_relay_proxy_url(url) is not None
    except ValueError:
        return True


def _format_target_hostport(target_host: str, target_port: int) -> str:
    host = target_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{target_port}"


def maybe_transform_relay_proxy_url(url: str, user_id: str = "",
                                    conv_id: str = "",
                                    relay_local: Optional[bool] = None) -> Optional[str]:
    """Return a PawFlow relay-proxy URL, or None when `url` is not proxy-shaped.

    Input format:  http(s)://<relay_id>/<host>:<port>/path
    Output format: <pawflow>/relay-proxy/<relay_id>/<token>/[l|c/][s/]<host>:<port>/path
    """
    try:
        parts = parse_relay_proxy_url(url)
    except ValueError as exc:
        logger.warning("Invalid relay-proxy URL %r: %s", url, exc)
        return None
    if not parts:
        return None

    try:
        from services import http_listener_service as _hl_mod
        instances = getattr(_hl_mod, "_instances", None) or {}
        if not instances:
            logger.warning("No HTTP listener running — cannot build relay-proxy URL")
            return None
        _public = [(p, lst) for p, lst in instances.items() if p != 19895]
        if _public:
            _port, _listener = _public[0]
        else:
            _port, _listener = next(iter(instances.items()))
        _is_ssl = bool(getattr(_listener, "is_ssl", False))
        _scheme = "https" if _is_ssl else "http"
    except Exception as e:
        logger.warning("HTTP listener lookup failed: %s", e)
        return None

    if not user_id:
        logger.warning("Cannot issue proxy token without user_id")
        return None
    try:
        from core.relay_proxy_auth import issue_token
        _token = issue_token(user_id, parts.relay_id, conv_id=conv_id)
    except Exception as e:
        logger.warning("Proxy token issue failed: %s", e)
        return None

    if _is_ssl:
        _host = (getattr(_listener, "public_hostname", "") or "").strip()
        if not _host:
            _host = get_host_ip()
            logger.warning(
                "relay-proxy URL using LAN IP %s with HTTPS: cert CN "
                "validation will be skipped in the container. Configure "
                "`public_hostname` on the HTTP listener (or a matching "
                "SNI cert) so the container can verify normally.",
                _host)
    else:
        _host = get_host_ip()
    _target = _format_target_hostport(parts.target_host, parts.target_port)
    _local = parts.relay_local if relay_local is None else bool(relay_local)
    _local_prefix = "" if _local is None else ("l/" if _local else "c/")
    _s_prefix = "s/" if parts.target_scheme == "https" else ""
    _url = f"{_scheme}://{_host}:{_port}/relay-proxy/{parts.relay_id}/{_token}/{_local_prefix}{_s_prefix}{_target}{parts.target_path}"
    if parts.query:
        _url += "?" + parts.query
    logger.info(
        "Relay proxy URL resolved relay=%s target=%s mode=%s scheme=%s listener=%s://%s:%s user=%s conv=%s path=%s",
        parts.relay_id,
        _target,
        "local" if _local else "container" if _local is False else "default",
        parts.target_scheme,
        _scheme,
        _host,
        _port,
        user_id,
        conv_id,
        parts.target_path,
    )
    return _url


def resolve_relay_aware_url(raw_url: str, *, user_id: str = "",
                            conversation_id: str = "",
                            agent_name: str = "",
                            allow_private: bool = False,
                            service_name: str = "provider endpoint",
                            transform_relay: bool = True) -> str:
    """Resolve and validate a provider URL, including PawFlow relay URLs.

    Relay-shaped URLs are always treated as relay URLs. Normal URLs go through
    direct SSRF validation and require `allow_private=True` for private targets.
    """
    resolved = _resolve_url_template(
        raw_url,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_name=agent_name,
    )
    parsed = urllib.parse.urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _service_error(f"invalid {service_name} base_url: {raw_url!r}")
    try:
        relay_url = parse_relay_proxy_url(resolved)
    except ValueError as exc:
        raise _service_error(f"invalid {service_name} relay URL: {exc}") from exc
    if relay_url:
        if not transform_relay:
            return resolved.rstrip("/")
        proxy = maybe_transform_relay_proxy_url(
            resolved, user_id=user_id, conv_id=conversation_id)
        if not proxy:
            raise _service_error(
                f"could not create relay-proxy route for {service_name}; user_id and HTTP listener are required")
        return proxy.rstrip("/")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise _service_error(f"invalid {service_name} base_url: {raw_url!r}")
    if allow_private:
        return resolved.rstrip("/")
    if host == "localhost" or host.endswith(".localhost") or _is_private_address(host):
        raise _service_error(
            f"{service_name} base_url targets a private/local network address. "
            "Use a PawFlow relay URL such as https://${conv.relay}/localhost:1234, "
            "or set allow_private_base_url=true only for a trusted endpoint."
        )
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _service_error(f"{service_name} base_url host could not be resolved: {host}") from exc
    for info in infos:
        address = info[4][0]
        if _is_private_address(address):
            raise _service_error(
                f"{service_name} base_url resolves to a private/local network address. "
                "Use a PawFlow relay URL such as https://${conv.relay}/localhost:1234, "
                "or set allow_private_base_url=true only for a trusted endpoint."
            )
    return resolved.rstrip("/")
