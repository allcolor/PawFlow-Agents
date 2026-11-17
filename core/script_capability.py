"""Per-execution capabilities for scripts that call PawFlow tools.

A script started by the server on a relay (``execute_script``, a PFP package
runner) reaches PawFlow tools through its relay's own authenticated
connection (``tool.*`` relay requests, see ``services/relay_tool_bridge.py``).
The script never states who it is: it presents an opaque capability minted
here for that one execution, and the server derives the whole identity from
it -- user, conversation, agent, run handle and fence -- after checking that
the request arrived on the relay the capability was issued for.

Capabilities live in memory only, expire, and are revoked when the execution
ends, so a leaked value stops working with the script that held it.
"""

import secrets
import threading
import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 6 * 3600
# A script's tool call gets its result in full up to this size (instead of
# the agent's per-result cap): the script filters it, the agent only sees
# what it prints. Larger results still become a FileStore reference.
SCRIPT_RESULT_MAX_CHARS = 2_000_000


@dataclass(frozen=True)
class ScriptCapability:
    relay_service_id: str
    user_id: str
    conversation_id: str
    agent_name: str
    run_identity: tuple | None
    parent_call_id: str
    expires_at: float


_caps: dict[str, ScriptCapability] = {}
_lock = threading.Lock()


def _required(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required for a script capability")
    return text


def mint(*, relay_service_id: str, user_id: str, conversation_id: str,
         agent_name: str, run_identity: tuple | None = None,
         parent_call_id: str = "",
         ttl_seconds: float = DEFAULT_TTL_SECONDS) -> str:
    """Create a capability bound to one relay and one agent run."""
    cap = ScriptCapability(
        relay_service_id=_required(relay_service_id, "relay_service_id"),
        user_id=_required(user_id, "user_id"),
        conversation_id=_required(conversation_id, "conversation_id"),
        agent_name=_required(agent_name, "agent_name"),
        run_identity=tuple(run_identity) if run_identity else None,
        parent_call_id=str(parent_call_id or ""),
        expires_at=time.time() + float(ttl_seconds),
    )
    token = secrets.token_urlsafe(32)
    with _lock:
        _purge_expired_locked()
        _caps[token] = cap
    return token


def resolve(token: str, *, relay_service_id: str,
            user_id: str) -> ScriptCapability:
    """Return the capability, or raise PermissionError.

    The relay connection that carried the request must be the one the
    capability was minted for, and belong to the same user.
    """
    if not token:
        raise PermissionError("missing script capability")
    with _lock:
        cap = _caps.get(token)
        if cap is not None and cap.expires_at <= time.time():
            _caps.pop(token, None)
            cap = None
    if cap is None:
        raise PermissionError("unknown, expired or revoked script capability")
    if cap.relay_service_id != str(relay_service_id or ""):
        raise PermissionError("script capability was issued for another relay")
    if cap.user_id != str(user_id or ""):
        raise PermissionError("script capability belongs to another user")
    return cap


CAP_ENV = "PAWFLOW_TOOL_CAP"


def current_call_id() -> str:
    """UI id of the tool call running on this thread, or "".

    Both dispatch paths (agent loop, relay MCP) register the call in
    ``ToolRelayService._inflight`` with the cancel event they also bind to
    the thread; that event identifies the entry.
    """
    from services._tool_relay_base import current_cancel_event
    cancel = current_cancel_event()
    if cancel is None:
        return ""
    from services.tool_relay_service import ToolRelayService
    with ToolRelayService._inflight_lock:
        for info in ToolRelayService._inflight.values():
            if info.get("cancel") is cancel:
                return str(info.get("cc_tc_id") or info.get("bg_tc_id") or "")
    return ""


def mint_for_relay(relay, *, user_id: str, conversation_id: str,
                   agent_name: str):
    """Mint a capability for a script about to run on ``relay``.

    Returns ``(env, token)``: the environment the script needs to reach
    PawFlow tools through the relay tool bridge, and the token to revoke
    once it ends. Returns ``({}, "")`` when the relay or the identity is
    unknown: the script still runs, and the SDK reports that tools are
    unavailable instead of guessing an identity.
    """
    relay_service_id = str(getattr(relay, "_service_id", "") or "")
    from services._tool_relay_base import current_run_identity
    run_identity = current_run_identity()
    if run_identity and len(run_identity) >= 4:
        conversation_id = conversation_id or str(run_identity[2] or "")
        agent_name = agent_name or str(run_identity[3] or "")
    if not all((relay_service_id, user_id, conversation_id, agent_name)):
        return {}, ""
    token = mint(
        relay_service_id=relay_service_id, user_id=user_id,
        conversation_id=conversation_id, agent_name=agent_name,
        run_identity=run_identity,
        parent_call_id=current_call_id())
    return {
        CAP_ENV: token,
        "PAWFLOW_USER_ID": user_id,
        "PAWFLOW_CONVERSATION_ID": conversation_id,
        "PAWFLOW_AGENT_NAME": agent_name,
    }, token


def revoke(token: str) -> None:
    """Invalidate a capability. Idempotent."""
    if not token:
        return
    with _lock:
        _caps.pop(token, None)


def active_count() -> int:
    with _lock:
        _purge_expired_locked()
        return len(_caps)


def _purge_expired_locked() -> None:
    now = time.time()
    for token in [t for t, c in _caps.items() if c.expires_at <= now]:
        _caps.pop(token, None)
