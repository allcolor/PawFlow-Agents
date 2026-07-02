"""Voice-session tool bridge — provider function calls → PawFlow tools.

A realtime session exposes a restricted `tool_profile` (comma-separated
tool names from the default registry). Approval is SILENT by design: the
gate is probed with `allow_prompt=False` — a tool that is approval-exempt
or already carries a session/always permission runs, anything that would
require the interactive dialog is refused with a spoken-friendly message
(a live voice session has no approval UX and a Telegram voice turn has
none at all). `permission_mode=auto` approves everything, `read_only`
falls back to the same allowlist the relay uses.

Long tools never block the session: execution runs on its own thread and
past `soft_timeout_s` the provider immediately receives an interim "still
running" result so the agent can keep talking; the real result is handed
to `announce` when it lands (live bridge → inject into the session, or
persist as a system message once the session is gone).
"""

import json
import logging
import threading

logger = logging.getLogger(__name__)

_RESULT_MAX_CHARS = 4000   # realtime models have small contexts
_SOFT_TIMEOUT_S = 15.0     # keep the spoken exchange snappy
_HARD_TIMEOUT_S = 600.0    # give up announcing a detached tool after this


class RealtimeToolBridge:
    """Executes provider tool calls for one voice session/turn."""

    def __init__(self, tool_profile: str, conversation_id: str,
                 agent_name: str, user_id: str, registry=None):
        self._names = [n.strip() for n in (tool_profile or "").split(",")
                       if n.strip()]
        self._cid = conversation_id
        self._agent = agent_name
        self._user_id = user_id
        self._registry = registry if registry is not None \
            else self._build_registry()

    # -- registry ---------------------------------------------------------

    def _build_registry(self):
        from core.tool_registry import ToolRegistry, create_default_registry
        base = create_default_registry()
        registry = ToolRegistry()
        available = {h.name for h in base.list_tools()}
        for name in self._names:
            if name not in available:
                logger.warning("[realtime] tool_profile entry '%s' is not a "
                               "registered tool — skipped", name)
        for h in base.list_tools():
            if h.name not in self._names:
                continue
            # Provider-invariant runtime context (same contract as
            # AgentToolConfigMixin's generic pass).
            for setter, value in (("set_user_id", self._user_id),
                                  ("set_conversation_id", self._cid),
                                  ("set_agent_name", self._agent)):
                if value and hasattr(h, setter):
                    try:
                        getattr(h, setter)(value)
                    except Exception:
                        logger.debug("[realtime] %s.%s failed", h.name,
                                     setter, exc_info=True)
            registry.register(h)
        return registry

    def tool_definitions(self) -> list:
        """Provider function-tool definitions (OpenAI realtime flat shape)."""
        return [{
            "type": "function",
            "name": d["name"],
            "description": d["description"],
            "parameters": d["parameters"],
        } for d in self._registry.get_tool_definitions()]

    # -- authorization ------------------------------------------------------

    def _authorize(self, name: str, args: dict) -> str:
        """Return 'approved' or a refusal message for the model to speak."""
        mode = "default"
        try:
            from core.conversation_store import ConversationStore
            mode = ConversationStore.instance().get_extra(
                self._cid, "permission_mode") or "default"
        except Exception:
            logger.debug("[realtime] permission_mode lookup failed",
                         exc_info=True)
        from core.tool_approval import ToolApprovalGate
        if mode == "read_only":
            if ToolApprovalGate.is_read_only_allowed(name, args):
                return "approved"
            return (f"The '{name}' tool is blocked: this conversation is in "
                    "read-only mode. Tell the user to change the permission "
                    "mode if they want this to run.")
        if mode == "auto":
            return "approved"
        status = ToolApprovalGate.check(
            name, f"{name} (voice session)", self._cid, self._user_id,
            arguments=args, agent_name=self._agent, allow_prompt=False)
        if status == "approved":
            return "approved"
        return (f"The '{name}' tool requires interactive user approval, "
                "which a voice session cannot show. Tell the user to run it "
                "from the text chat, or grant the tool 'always allow' "
                "there first.")

    # -- execution ------------------------------------------------------------

    @staticmethod
    def _parse_args(arguments) -> dict:
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _clip(text: str) -> str:
        text = str(text or "")
        if len(text) <= _RESULT_MAX_CHARS:
            return text
        return text[:_RESULT_MAX_CHARS] + "\n[... result truncated for the voice session]"

    def handle_call(self, call_id: str, name: str, arguments, *,
                    send_result, announce=None,
                    soft_timeout_s: float = _SOFT_TIMEOUT_S) -> str:
        """Run one provider tool call.

        Blocks up to `soft_timeout_s`, then detaches: the provider gets an
        interim result immediately and `announce(text)` receives the real
        one when it lands. Returns 'done' | 'background' | 'denied' |
        'unavailable' | 'error' for UI status events.
        """
        args = self._parse_args(arguments)
        if not any(h.name == name for h in self._registry.list_tools()):
            send_result(call_id, f"Error: tool '{name}' is not available in "
                                 "this voice session.")
            return "unavailable"
        verdict = self._authorize(name, args)
        if verdict != "approved":
            logger.info("[realtime] tool '%s' refused (silent approval) "
                        "conv=%s", name, self._cid[:8])
            send_result(call_id, verdict)
            return "denied"

        box = {}
        done = threading.Event()

        def _run():
            try:
                box["result"] = self._registry.execute(name, args)
            except Exception as exc:
                logger.warning("[realtime] tool '%s' raised: %s", name, exc,
                               exc_info=True)
                box["result"] = f"Error: {exc}"
            done.set()

        worker = threading.Thread(target=_run, name=f"voice-tool-{name}",
                                  daemon=True)
        worker.start()
        if done.wait(timeout=soft_timeout_s):
            send_result(call_id, self._clip(box.get("result", "")))
            return "done"

        # Long tool → delegate: unblock the model now, report later.
        send_result(call_id, f"The '{name}' tool is taking a while and now "
                    "runs in the background. Tell the user you started it; "
                    "the result will be announced when it is ready.")

        def _late():
            if not done.wait(timeout=_HARD_TIMEOUT_S):
                logger.warning("[realtime] background tool '%s' never "
                               "finished (conv=%s)", name, self._cid[:8])
                return
            if announce is not None:
                try:
                    announce(f"Background tool '{name}' finished. Result:\n"
                             f"{self._clip(box.get('result', ''))}")
                except Exception:
                    logger.warning("[realtime] background tool announce "
                                   "failed for '%s'", name, exc_info=True)

        threading.Thread(target=_late, name=f"voice-tool-late-{name}",
                         daemon=True).start()
        return "background"
