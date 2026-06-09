"""Runtime access checks for deployed flows.

Deployment scope is an authorization boundary. User and conversation scoped
flows must stay inside their runtime owner, while global flows must either be
explicitly administrative or be bounded by a trusted requester user.
"""

from dataclasses import dataclass
from typing import Any


class FlowRuntimeAccessError(PermissionError):
    """Raised when a flow task targets data outside its runtime scope."""


@dataclass(frozen=True)
class FlowRuntimeContext:
    scope: str = ""
    user_id: str = ""
    conversation_id: str = ""
    agent_name: str = ""

    @property
    def allow_global_admin(self) -> bool:
        return self.scope == "global"


def make_runtime_context(scope: str = "", user_id: str = "",
                         conversation_id: str = "",
                         agent_name: str = "") -> FlowRuntimeContext:
    scope = str(scope or "").strip()
    user_id = str(user_id or "").strip()
    conversation_id = str(conversation_id or "").strip()
    agent_name = str(agent_name or "").strip()
    if not scope:
        scope = "conversation" if conversation_id else "user" if user_id else "global"
    return FlowRuntimeContext(scope, user_id, conversation_id, agent_name)


def set_runtime_context(target: Any, *, user_id: str = "",
                        conversation_id: str = "", scope: str = "",
                        agent_name: str = "") -> None:
    target._runtime_context = make_runtime_context(
        scope=scope, user_id=user_id, conversation_id=conversation_id,
        agent_name=agent_name)


def runtime_context_from_task(task: Any) -> FlowRuntimeContext:
    ctx = getattr(task, "_runtime_context", None)
    if isinstance(ctx, FlowRuntimeContext):
        return ctx
    config = getattr(task, "config", {}) or {}
    return make_runtime_context(
        scope=config.get("_scope") or config.get("_flow_scope") or "",
        user_id=config.get("_user_id") or config.get("user_id") or "",
        conversation_id=config.get("_conversation_id") or "",
        agent_name=config.get("_agent_name") or "",
    )


def trusted_requester_user_id(flowfile: Any) -> str:
    """Return a user identity set by trusted ingress/auth tasks.

    Body/config user IDs are intentionally ignored here. These attributes are
    produced by auth validation or by channel adapters after identity linking.
    """
    for key in ("http.auth.principal", "auth.user_id", "user_id"):
        try:
            value = str(flowfile.get_attribute(key) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def conversation_owner(conversation_id: str) -> str:
    if not conversation_id:
        return ""
    from core.conversation_store import ConversationStore
    meta = ConversationStore.instance().get_metadata(conversation_id) or {}
    return str(meta.get("user_id") or "").strip()


def _is_admin_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def authorize_user_target(ctx: FlowRuntimeContext, target_user_id: str,
                          *, requester_user_id: str = "",
                          allow_global_admin: Any = False) -> str:
    target = str(target_user_id or "").strip()
    requester = str(requester_user_id or "").strip()
    if ctx.scope == "conversation":
        expected = ctx.user_id or conversation_owner(ctx.conversation_id)
        if target and expected and target != expected:
            raise FlowRuntimeAccessError("Permission denied")
        return expected or target
    if ctx.scope == "user":
        if target and ctx.user_id and target != ctx.user_id:
            raise FlowRuntimeAccessError("Permission denied")
        return ctx.user_id or target
    if ctx.scope == "global":
        if requester:
            if target and target != requester:
                raise FlowRuntimeAccessError("Permission denied")
            return requester
        if _is_admin_enabled(allow_global_admin):
            if not target:
                raise FlowRuntimeAccessError("Missing target user_id")
            return target
        raise FlowRuntimeAccessError("Permission denied")
    raise FlowRuntimeAccessError("Permission denied")


def authorize_conversation_target(ctx: FlowRuntimeContext,
                                  target_conversation_id: str,
                                  *, requester_user_id: str = "",
                                  allow_global_admin: Any = False) -> str:
    target = str(target_conversation_id or "").strip()
    if not target:
        raise FlowRuntimeAccessError("Missing conversation_id")
    if ctx.scope == "conversation":
        if target != ctx.conversation_id:
            raise FlowRuntimeAccessError("Permission denied")
        return target
    owner = conversation_owner(target)
    if not owner:
        raise FlowRuntimeAccessError("Conversation not found")
    if ctx.scope == "user":
        if owner != ctx.user_id:
            raise FlowRuntimeAccessError("Permission denied")
        return target
    if ctx.scope == "global":
        requester = str(requester_user_id or "").strip()
        if requester:
            if owner != requester:
                raise FlowRuntimeAccessError("Permission denied")
            return target
        if _is_admin_enabled(allow_global_admin):
            return target
        raise FlowRuntimeAccessError("Permission denied")
    raise FlowRuntimeAccessError("Permission denied")


def authorize_filestore_target(ctx: FlowRuntimeContext, *, file_id: str = "",
                               target_user_id: str = "",
                               target_conversation_id: str = "",
                               requester_user_id: str = "",
                               allow_global_admin: Any = False) -> tuple[str, str]:
    user_id = str(target_user_id or "").strip()
    conv_id = str(target_conversation_id or "").strip()
    if file_id:
        from core.file_store import FileStore
        meta = FileStore.instance().get_metadata(file_id) or {}
        user_id = user_id or str(meta.get("user_id") or "").strip()
        conv_id = conv_id or str(meta.get("conversation_id") or "").strip()
    if conv_id:
        authorize_conversation_target(
            ctx, conv_id, requester_user_id=requester_user_id,
            allow_global_admin=allow_global_admin)
        return conversation_owner(conv_id) or user_id, conv_id
    if ctx.scope == "global" and _is_admin_enabled(allow_global_admin) and not user_id:
        return "", ""
    user_id = authorize_user_target(
        ctx, user_id, requester_user_id=requester_user_id,
        allow_global_admin=allow_global_admin)
    return user_id, conv_id

