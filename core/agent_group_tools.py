"""Fail-closed read-only tool runtime for bounded agent-group participants."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from core.agent_contracts import CapabilityEffect
from core.llm_client import LLMToolCall, LLMToolDefinition

_GROUP_READ_ONLY_TOOLS = {
    "read": CapabilityEffect.FILESYSTEM_READ,
    "list_dir": CapabilityEffect.FILESYSTEM_READ,
    "stat": CapabilityEffect.FILESYSTEM_READ,
    "exists": CapabilityEffect.FILESYSTEM_READ,
    "glob": CapabilityEffect.FILESYSTEM_READ,
    "grep": CapabilityEffect.FILESYSTEM_READ,
    "search": CapabilityEffect.FILESYSTEM_READ,
    "web_search": CapabilityEffect.NETWORK_READ,
}


def _configured_tool_names(value: Any) -> set[str]:
    result = set()
    for item in value if isinstance(value, (list, tuple)) else ():
        name = item.get("name") if isinstance(item, dict) else item
        text = str(name or "").strip()
        if text:
            result.add(text)
    return result


def group_read_only_tool_names(group, member_snapshot: dict[str, Any]) -> tuple[str, ...]:
    """Return the reviewed intersection for one immutable member snapshot."""
    if group.tool_policy.mode != "read_only":
        return ()
    effects = set(group.tool_policy.allowed_effects)
    configured = _configured_tool_names(member_snapshot.get("tools"))
    return tuple(sorted(
        name for name, effect in _GROUP_READ_ONLY_TOOLS.items()
        if name in configured and effect in effects
    ))


@dataclass(frozen=True)
class GroupToolAttribution:
    group_run_id: str
    run_id: str
    round: int
    member_id: str
    turn_id: str

    @property
    def approval_agent_name(self) -> str:
        return (
            f"group:{self.group_run_id}:run:{self.run_id}:round:{self.round}:"
            f"member:{self.member_id}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_run_id": self.group_run_id,
            "run_id": self.run_id,
            "round": self.round,
            "member_id": self.member_id,
            "turn_id": self.turn_id,
        }


class GroupReadOnlyToolRuntime:
    """Execute only prepared calls from the group-safe capability intersection."""

    def __init__(
        self,
        *,
        context,
        group,
        member_snapshot: dict[str, Any],
        round_number: int,
        group_run_id: str,
        emit: Callable[[str, dict[str, Any]], None],
        cancelled: Callable[[], bool],
        cancel_event=None,
        registry=None,
    ):
        self.context = context
        self.emit = emit
        self.cancelled = cancelled
        self.cancel_event = cancel_event
        self.attribution = GroupToolAttribution(
            group_run_id=group_run_id,
            run_id=context.run_id,
            round=round_number,
            member_id=str(member_snapshot["member_id"]),
            turn_id=context.root_turn_id,
        )
        self.allowed_names = group_read_only_tool_names(group, member_snapshot)
        self.allowed_effects = set(group.tool_policy.allowed_effects)
        self.registry = self._registry(registry)

    def _registry(self, registry):
        if registry is None:
            from core.tool_registry import create_default_registry
            registry = create_default_registry()
        registry = registry.fork() if hasattr(registry, "fork") else registry
        for handler in list(registry.list_tools()):
            if handler.name not in self.allowed_names:
                registry.unregister(handler.name)
        self._configure_handlers(registry)
        return registry

    def _configure_handlers(self, registry) -> None:
        from core.handlers._fs_base import BaseFsHandler

        for handler in registry.list_tools():
            for setter, value in (
                ("set_user_id", self.context.user_id),
                ("set_conversation_id", self.context.conversation_id),
                ("set_agent_name", self.context.agent_name),
            ):
                method = getattr(handler, setter, None)
                if value and callable(method):
                    method(value)
            if isinstance(handler, BaseFsHandler):
                service = self._default_relay_service()
                if service is not None:
                    handler.set_fs_service(service)
                linked = self._linked_relays()
                if linked and hasattr(handler, "set_available_services"):
                    handler.set_available_services([
                        {"id": item, "type": "relay", "root": "?"}
                        for item in linked
                    ])
            elif handler.name == "web_search" and hasattr(handler, "set_fs_resolver"):
                handler.set_fs_resolver(self._resolve_scoped_service)

    def _linked_relays(self) -> tuple[str, ...]:
        from core.relay_bindings import get_default, get_linked

        agent = self.context.agent_name
        linked = list(get_linked(self.context.conversation_id, agent=agent) or ())
        default = get_default(self.context.conversation_id, agent=agent) or ""
        if default and default not in linked:
            linked.append(default)
        return tuple(linked)

    def _default_relay_service(self):
        from core.relay_bindings import get_default
        from core.service_registry import ServiceRegistry

        service_id = get_default(
            self.context.conversation_id, agent=self.context.agent_name
        ) or ""
        if not service_id:
            return None
        return ServiceRegistry.get_instance().resolve(
            service_id,
            user_id=self.context.user_id,
            conv_id=self.context.conversation_id,
        )

    def _resolve_scoped_service(self, service_id: str):
        from core.service_registry import ServiceRegistry

        return ServiceRegistry.get_instance().resolve(
            service_id,
            user_id=self.context.user_id,
            conv_id=self.context.conversation_id,
        )

    def definitions(self) -> list[LLMToolDefinition]:
        return [
            LLMToolDefinition(
                name=row["name"],
                description=row["description"],
                parameters=row["parameters"],
            )
            for row in self.registry.get_tool_definitions()
        ]

    def execute(self, call: LLMToolCall) -> str:
        self._lifecycle(call, "announced")
        if self.cancelled():
            self._lifecycle(call, "retired", reason="group run cancelled")
            return "Error: group run was cancelled before tool dispatch."
        effect = _GROUP_READ_ONLY_TOOLS.get(str(call.name or ""))
        if call.name not in self.allowed_names or effect not in self.allowed_effects:
            self._lifecycle(call, "denied", reason="outside group-safe intersection")
            return f"Error: Tool '{call.name}' is not allowed for this group participant."
        prepared = self.registry.prepare(call.name, call.arguments)
        if isinstance(prepared, str):
            self._lifecycle(call, "denied", reason=prepared)
            return prepared
        scope_error = self._scope_error(prepared.arguments, effect)
        if scope_error:
            self._lifecycle(call, "denied", reason=scope_error)
            return f"Error: {scope_error}"
        from core.tool_authorization import authorize_tool_call

        decision = authorize_tool_call(
            tool_name=prepared.name,
            arguments=prepared.arguments,
            user_id=self.context.user_id,
            conversation_id=self.context.conversation_id,
            agent_name=self.context.agent_name,
            turn_id=self.context.root_turn_id,
            call_id=str(call.id or ""),
            permission_mode=self.context.permission_mode,
            read_only_override=True,
            capability_effects=(effect,),
            attribution=self.attribution.to_dict(),
        )
        if decision.decision == "deny":
            self._lifecycle(call, "denied", reason=decision.reason)
            return f"Error: Tool '{prepared.name}' was denied: {decision.reason}"
        if decision.decision == "ask":
            self._lifecycle(call, "approval_requested", reason=decision.reason)
            from core.tool_approval import ToolApprovalGate

            approval = ToolApprovalGate.check(
                prepared.name,
                f"[group policy] {decision.reason[:160]} — {prepared.name}",
                self.context.conversation_id,
                self.context.user_id,
                arguments=prepared.arguments,
                agent_name=self.context.agent_name,
                attribution=self.attribution.to_dict(),
                cancel_event=self.cancel_event,
                force_prompt=True,
            )
            if approval != "approved":
                phase = "retired" if approval == "cancelled" else "denied"
                self._lifecycle(call, phase, reason=f"approval {approval}")
                return f"Error: Tool '{prepared.name}' approval was {approval}."
            self._lifecycle(call, "approved")
        self._lifecycle(call, "dispatched")
        finished = threading.Event()
        kill_hooks: list[Callable[[], None]] = []
        watcher = None
        if self.cancel_event is not None:
            from services.tool_relay_service import (
                _set_current_cancel_event,
                _set_current_kill_hooks,
            )

            _set_current_cancel_event(self.cancel_event)
            _set_current_kill_hooks(kill_hooks)

            def watch_cancel() -> None:
                while not finished.wait(0.05):
                    if self.cancel_event.is_set():
                        for hook in list(kill_hooks):
                            try:
                                hook()
                            except Exception:
                                pass
                        return

            watcher = threading.Thread(
                target=watch_cancel,
                daemon=True,
                name=f"group-tool-cancel-{str(call.id or '')[-8:]}",
            )
            watcher.start()
        try:
            result = self.registry.execute_prepared(prepared)
        except Exception as exc:
            self._lifecycle(call, "failed", reason=str(exc))
            return f"Error: group tool failed: {exc}"
        finally:
            finished.set()
            if watcher is not None:
                watcher.join(timeout=0.2)
                _set_current_cancel_event(None)
                _set_current_kill_hooks(None)
        if self.cancelled():
            self._lifecycle(call, "retired", reason="group run cancelled")
            return "Error: group run was cancelled during tool execution."
        self._lifecycle(call, "completed")
        return str(result)

    def _scope_error(self, arguments: dict[str, Any], effect: CapabilityEffect) -> str:
        for key, expected in (
            ("conversation_id", self.context.conversation_id),
            ("user_id", self.context.user_id),
        ):
            if arguments.get(key) not in (None, "", expected):
                return f"group tool cannot target another {key}"
        if effect == CapabilityEffect.FILESYSTEM_READ:
            requested = str(
                arguments.get("relay") or arguments.get("source")
                or arguments.get("service") or ""
            )
            linked = set(self._linked_relays())
            if not linked:
                return "group filesystem tools require a conversation-bound relay"
            if requested and requested not in linked:
                return "group tool cannot escape conversation relay scope"
        return ""

    def _lifecycle(self, call: LLMToolCall, phase: str, *, reason: str = "") -> None:
        data = {
            **self.attribution.to_dict(),
            "tool_call_id": str(call.id or ""),
            "tool_name": str(call.name or ""),
            "phase": phase,
        }
        if reason:
            data["reason"] = str(reason)[:240]
        self.emit("group_tool_lifecycle", data)


__all__ = [
    "GroupReadOnlyToolRuntime",
    "GroupToolAttribution",
    "group_read_only_tool_names",
]
