"""Relay proxy URL helpers.

PawFlow uses URLs shaped like `http://<relay_id>:<host>:<port>/path` to
route HTTP calls through a user's relay. The public URL minted here points
back to PawFlow's `/relay-proxy/...` route with an ephemeral token.
"""

import logging
import re
from typing import Optional

from core.docker_utils import get_host_ip

logger = logging.getLogger(__name__)


def maybe_transform_relay_proxy_url(url: str, user_id: str = "") -> Optional[str]:
    """Return a PawFlow relay-proxy URL, or None when `url` is not proxy-shaped.

    Input format:  http(s)://<relay_id>:<host>:<port>/path
    Output format: <pawflow>/relay-proxy/<relay_id>/<token>/[s/]<host>:<port>/path
    """
    m = re.match(
        r'^(https?)://([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+):(\d+)(/.*)?$',
        url or "")
    if not m:
        return None
    target_scheme = m.group(1)
    relay_id = m.group(2)
    target_host = m.group(3)
    target_port = int(m.group(4))
    target_path = m.group(5) or '/'

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
        _token = issue_token(user_id, relay_id)
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
    _target = f"{target_host}:{target_port}"
    _s_prefix = "s/" if target_scheme == "https" else ""
    return f"{_scheme}://{_host}:{_port}/relay-proxy/{relay_id}/{_token}/{_s_prefix}{_target}{target_path}"
