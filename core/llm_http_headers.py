"""Outbound identity and operator-configured headers for direct LLM HTTP calls.

PawFlow always identifies itself with a versioned ``User-Agent``. Everything
else a gateway may ask for (OpenCode Go's ``x-opencode-session``, a routing
hint, a tenant id) is operator configuration: the ``extra_headers`` field of
an ``llmConnection`` service holds a JSON object whose values go through the
expression language with a ``request.*`` scope, so no provider name or host is
hard-coded here.

Request scope keys (flow scope, first in the cascade):

- ``request.session_id``: the PawFlow conversation id, or a stable id generated
  by the client when a call runs outside any conversation.
- ``request.conversation_id``: the conversation id, empty outside one.
- ``request.user_id`` / ``request.agent_name``: call identity, may be empty.
- ``request.request_id``: unique per HTTP request.
- ``pawflow.version``: the running PawFlow version.

The cascade continues to conversation, user and global parameters and secrets,
so ``${my_gateway_token}`` works as well.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: Headers the transport or the credential layer owns; configuration may not
#: override them (a stray ``Authorization`` would silently replace the key).
PROTECTED_HEADERS = frozenset({
    "authorization", "x-api-key", "api-key", "content-length",
    "content-type", "host", "transfer-encoding", "connection",
})
_CONTROL_CHARS = re.compile(r"[\r\n\x00]")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def pawflow_user_agent() -> str:
    """Return the versioned identity used for every direct provider request."""
    from core import __version__

    return f"PawFlow/{__version__}"


def request_scope(*, session_id: str, conversation_id: str = "",
                  user_id: str = "", agent_name: str = "",
                  request_id: str = "") -> Dict[str, str]:
    """Build the ``request.*`` parameters one HTTP request exposes to headers."""
    from core import __version__

    if not session_id:
        raise ValueError("session_id is required for the request scope")
    return {
        "request.session_id": str(session_id),
        "request.conversation_id": str(conversation_id or ""),
        "request.user_id": str(user_id or ""),
        "request.agent_name": str(agent_name or ""),
        "request.request_id": str(request_id or uuid.uuid4().hex),
        "pawflow.version": str(__version__),
    }


def render_extra_headers(extra_headers: Optional[Mapping[str, Any]],
                         scope: Mapping[str, str], *,
                         owner: str = "", conversation_id: str = "",
                         ) -> Dict[str, str]:
    """Resolve configured header templates into concrete header values.

    Invalid names, protected names, unresolved templates and empty values are
    dropped with a warning: a header is either exactly what the operator
    meant or absent, never a raw ``${...}`` sent to a provider.
    """
    from core.expression import resolve_expression

    rendered: Dict[str, str] = {}
    for raw_name, raw_value in (extra_headers or {}).items():
        name = str(raw_name or "").strip()
        if not _HEADER_NAME.match(name):
            logger.warning("Ignoring invalid llm extra_headers name: %r", raw_name)
            continue
        if name.lower() in PROTECTED_HEADERS:
            logger.warning("Ignoring protected llm extra_headers name: %s", name)
            continue
        if raw_value is None or isinstance(raw_value, (dict, list)):
            logger.warning("Ignoring non-scalar llm extra_headers value for %s", name)
            continue
        template = str(raw_value)
        try:
            value = resolve_expression(
                template, parameters=dict(scope),
                owner=owner or None, conversation_id=conversation_id or None)
        except Exception:
            logger.warning("llm extra_headers %s failed to resolve", name, exc_info=True)
            continue
        if "${" in value:
            logger.warning("Ignoring unresolved llm extra_headers template for %s", name)
            continue
        value = _CONTROL_CHARS.sub("", value).strip()
        if not value:
            continue
        if name.lower() == "user-agent":
            name = "User-Agent"
        rendered[name] = value
    return rendered


def llm_api_headers(extra_headers: Optional[Mapping[str, Any]] = None,
                    scope: Optional[Mapping[str, str]] = None, *,
                    owner: str = "", conversation_id: str = "") -> Dict[str, str]:
    """PawFlow identity plus the operator's rendered ``extra_headers``.

    Configuration may replace ``User-Agent``: some gateways want a precise
    client identity rather than PawFlow's, and that choice belongs to the
    operator of the service.
    """
    headers = {"User-Agent": pawflow_user_agent()}
    if extra_headers:
        if scope is None:
            raise ValueError("a request scope is required to render extra_headers")
        headers.update(render_extra_headers(
            extra_headers, scope, owner=owner, conversation_id=conversation_id))
    return headers
