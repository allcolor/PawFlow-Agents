"""Durable typed user interaction, notification, timer, and signal tasks.

Unlike ``waitForSignal``/``notify`` (in-memory SignalRegistry, seconds-scale
timeouts), these tasks are DURABLE: a parked FlowFile survives restarts and
resumes when its signal fires — after minutes, days, months, or years
(``timeout`` accepts ``"90s"``, ``"12h"``, ``"30d"``, ``"6mo"``, ``"2y"``;
absent/0 = wait forever). Backed by ``core.confirmation_store``.

Canonical confirmation pattern::

    requestConfirmation ──> durableWait(signal from attribute) ──> routeOnAttribute

``requestConfirmation`` publishes a confirmation into the conversation (the
user answers from the webchat pending panel, whenever), stamps
``confirmation.request_id`` / ``confirmation.signal_id``, and passes the
FlowFile on. ``durableWait`` parks it; the answer fires the signal
``confirmation:<request_id>`` and the FlowFile resumes at the wait task with
``durable.wait.status`` = ``signaled`` (or ``timeout``) and
``durable.wait.value`` = the JSON resolution.

Durable waits require a DEPLOYED continuous flow (the parked FlowFile is
re-injected through the ExecutorRegistry); a batch run cannot resume after
its process returned.
"""

import time
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List

from core import FlowFile, TaskError, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.base_task import BaseTask


def _workflow_interaction_identity(
        task: BaseTask, flowfile: FlowFile | None = None,
) -> tuple[str, str, int]:
    context = getattr(task, "_workflow_run_context", None)
    if context is None:
        return "", "", 0
    task_id = str(getattr(task, "_workflow_task_id", "") or "").strip()
    if not task_id:
        from core.confirmation_store import find_own_flow_ids
        ids = find_own_flow_ids(task)
        task_id = str((ids or {}).get("task_id") or "").strip()
    if not task_id:
        raise TaskError(
            "workflow interaction requires a deployed task identity")
    base_key = f"{context.run_id}:{task_id}"
    if flowfile is None:
        return base_key, "", 1
    sequence_attribute = f"workflow.interaction.sequence.{task_id}"
    try:
        completed = max(
            0, int(flowfile.get_attribute(sequence_attribute, "0") or 0))
    except (TypeError, ValueError):
        completed = 0
    occurrence = completed + 1
    key = base_key if occurrence == 1 else f"{base_key}:{occurrence}"
    return key, sequence_attribute, occurrence


class RequestConfirmationTask(BaseTask):
    """Publish a durable confirmation request into a conversation."""

    TYPE = "requestConfirmation"
    VERSION = "1.0.0"
    NAME = "Request User Confirmation"
    DESCRIPTION = ("Creates a durable confirmation request (yes/no, single "
                   "or multi choice) the user answers from the webchat — "
                   "whenever they want. Chain a durableWait on the stamped "
                   "signal to suspend the flow until the answer.")
    ICON = "question"
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (
        CapabilityEffect.RESOURCE_WRITE,
        CapabilityEffect.MESSAGING_SEND,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "conversation.interaction"

    def set_workflow_run_context(self, context, *, task_id: str = "", **_kwargs):
        self._workflow_run_context = context
        if task_id:
            self._workflow_task_id = str(task_id)

    def workflow_authorization_target(self, _flowfile: FlowFile) -> Dict[str, Any]:
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            return {}
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "scope": "conversation",
            "scope_id": context.conversation_id,
        }

    def _idempotency_key(self, flowfile: FlowFile | None = None) -> str:
        return _workflow_interaction_identity(self, flowfile)[0]

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import ConfirmationStore, parse_timeout_seconds
        conversation_id = (self.config.get("conversation_id", "")
                           or self.config.get("_conversation_id", "")
                           or flowfile.get_attribute("conversation_id", ""))
        user_id = (self.config.get("user_id", "")
                   or self.config.get("_user_id", "")
                   or getattr(self, "_runtime_user_id", ""))
        if not conversation_id or not user_id:
            raise TaskError(
                "requestConfirmation needs a conversation_id and user_id "
                "(deploy the flow with a conversation runtime context or "
                "set the parameters explicitly)")
        message = self.config.get("message", "")
        if not message:
            raise TaskError("The 'message' parameter is required")
        options_raw = self.config.get("options", "")
        if isinstance(options_raw, str):
            options = [o.strip() for o in options_raw.split(",") if o.strip()]
        else:
            options = options_raw
        idempotency_key, sequence_attribute, occurrence = (
            _workflow_interaction_identity(self, flowfile))
        record = ConfirmationStore.instance().create_confirmation(
            conversation_id=conversation_id,
            user_id=user_id,
            requester_kind="flow",
            requester=self.config.get("requester_label", "") or self.TYPE,
            message=message,
            title=self.config.get("title", ""),
            mode=self.config.get("mode", "confirm"),
            options=options,
            expires_in_seconds=parse_timeout_seconds(
                self.config.get("expires_in", "")),
            idempotency_key=idempotency_key,
        )
        if sequence_attribute:
            flowfile.set_attribute(sequence_attribute, str(occurrence))
        flowfile.set_attribute("confirmation.request_id", record["request_id"])
        flowfile.set_attribute("confirmation.signal_id",
                               f"confirmation:{record['request_id']}")
        return [flowfile]

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        self._runtime_user_id = user_id
        if conversation_id and not self.config.get("conversation_id"):
            self.config["_conversation_id"] = conversation_id
        if user_id and not self.config.get("user_id"):
            self.config["_user_id"] = user_id

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "message": {"type": "string", "required": True,
                        "description": "Question shown to the user"},
            "title": {"type": "string", "required": False,
                      "description": "Short title for the pending panel"},
            "mode": {"type": "string", "required": False,
                     "default": "confirm",
                     "description": "confirm | choice | multi"},
            "options": {"type": "string", "required": False,
                        "description": "Comma-separated choices for "
                                       "choice/multi modes"},
            "expires_in": {"type": "string", "required": False,
                           "description": "Optional expiry: '2h', '3d', "
                                          "'1mo'... absent = none"},
            "conversation_id": {"type": "string", "required": False,
                                "description": "Target conversation "
                                               "(defaults to the deploy "
                                               "runtime context)"},
            "user_id": {"type": "string", "required": False,
                        "description": "Owner user (defaults to the deploy "
                                       "runtime context)"},
            "requester_label": {"type": "string", "required": False,
                                "description": "Label shown as the requester"},
        }


class RequestUserInputTask(BaseTask):
    """Create one versioned typed interaction using injected runtime scope."""

    TYPE = "requestUserInput"
    VERSION = "1.0.0"
    NAME = "Request Typed User Input"
    DESCRIPTION = (
        "Creates a durable typed request for text, multiline text, a choice, "
        "multiple choices, a number, date/datetime, file reference, or form. "
        "Chain durableWait on interaction.signal_id to suspend until answered.")
    ICON = "question"
    RELATIONSHIPS: ClassVar = ["success", "failure"]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (
        CapabilityEffect.RESOURCE_WRITE,
        CapabilityEffect.MESSAGING_SEND,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "conversation.interaction"

    def set_workflow_run_context(self, context, **_kwargs):
        self._workflow_run_context = context

    def workflow_authorization_target(self, _flowfile: FlowFile) -> Dict[str, Any]:
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            return {}
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "scope": "conversation",
            "scope_id": context.conversation_id,
        }

    def _idempotency_key(self, flowfile: FlowFile | None = None) -> str:
        return _workflow_interaction_identity(self, flowfile)[0]

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        import json

        from core.confirmation_store import (
            UserInteractionStore,
            parse_timeout_seconds,
        )

        conversation_id = getattr(self, "_runtime_conversation_id", "")
        user_id = getattr(self, "_runtime_user_id", "")
        if not conversation_id or not user_id:
            raise TaskError(
                "requestUserInput requires injected conversation and user runtime context")
        interaction = {
            "message": self.config.get("message"),
            "title": self.config.get("title", ""),
            "kind": self.config.get("kind", "text"),
            "options": self.config.get("options", []),
            "response_schema": self.config.get("response_schema", {}),
        }
        payload_attribute = str(
            self.config.get("payload_attribute") or "").strip()
        if payload_attribute:
            raw_payload = flowfile.get_attribute(payload_attribute, "")
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError) as exc:
                raise TaskError(
                    "requestUserInput payload attribute must contain valid JSON") from exc
            if not isinstance(payload, dict):
                raise TaskError(
                    "requestUserInput payload attribute must contain an object")
            allowed = {"message", "title", "kind", "options", "response_schema"}
            unknown = set(payload) - allowed
            if unknown:
                raise TaskError(
                    "requestUserInput payload attribute contains unsupported fields: "
                    + ", ".join(sorted(unknown)))
            interaction.update(payload)
        message = str(interaction.get("message") or "").strip()
        if not message:
            raise TaskError("The 'message' parameter is required")
        options_raw = interaction.get("options", [])
        if isinstance(options_raw, str):
            options = [item.strip() for item in options_raw.split(",") if item.strip()]
        else:
            options = options_raw
        response_schema = interaction.get("response_schema", {})
        if isinstance(response_schema, str):
            try:
                response_schema = json.loads(response_schema or "{}")
            except ValueError as exc:
                raise TaskError("response_schema must be valid JSON") from exc
        idempotency_key, sequence_attribute, occurrence = (
            _workflow_interaction_identity(self, flowfile))
        try:
            record = UserInteractionStore.instance().create_interaction(
                conversation_id=conversation_id,
                user_id=user_id,
                requester_kind="flow",
                requester=str(self.config.get("requester_label") or self.TYPE),
                message=message,
                title=str(interaction.get("title") or ""),
                kind=str(interaction.get("kind") or "text"),
                options=options,
                response_schema=response_schema,
                expires_in_seconds=parse_timeout_seconds(
                    self.config.get("expires_in", "")),
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            raise TaskError(f"Invalid user interaction: {exc}") from exc
        if sequence_attribute:
            flowfile.set_attribute(sequence_attribute, str(occurrence))
        flowfile.set_attribute("interaction.request_id", record["request_id"])
        flowfile.set_attribute("interaction.signal_id", record["signal_id"])
        flowfile.set_attribute("interaction.kind", record["kind"])
        return [flowfile]

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        self._runtime_user_id = user_id
        self._runtime_conversation_id = conversation_id

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "message": {"type": "string", "required": True,
                        "description": "Prompt shown to the user"},
            "title": {"type": "string", "required": False,
                      "description": "Short pending-inbox title"},
            "kind": {"type": "string", "required": True, "default": "text",
                     "description": "confirm | choice | multi | text | multiline | "
                                    "integer | decimal | date | datetime | file | form"},
            "options": {"type": "array", "required": False,
                        "description": "Choice values or value/label objects"},
            "response_schema": {"type": "object", "required": False,
                                "description": "Validation bounds or form fields"},
            "expires_in": {"type": "string", "required": False,
                           "description": "Optional durable expiry"},
            "requester_label": {"type": "string", "required": False,
                                "description": "Label shown in the pending inbox"},
            "payload_attribute": {
                "type": "string", "required": False,
                "description": (
                    "Optional FlowFile attribute containing a bounded JSON "
                    "interaction payload."),
            },
        }


class NotifyUserTask(BaseTask):
    """Publish a non-blocking user notification and route by delivery state."""

    TYPE = "notifyUser"
    VERSION = "1.0.0"
    NAME = "Notify User"
    DESCRIPTION = (
        "Publishes a notification to the injected conversation without parking "
        "the FlowFile. Routes to sent when a live client exists, queued when the "
        "event is buffered for replay, and failure on delivery errors.")
    ICON = "bell"
    RELATIONSHIPS: ClassVar = ["sent", "queued", "failure"]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (
        CapabilityEffect.RESOURCE_WRITE,
        CapabilityEffect.MESSAGING_SEND,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "conversation.notification"

    def set_workflow_run_context(self, context, **_kwargs):
        self._workflow_run_context = context

    def workflow_authorization_target(self, _flowfile: FlowFile) -> Dict[str, Any]:
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            return {}
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "scope": "conversation",
            "scope_id": context.conversation_id,
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.conversation_event_bus import ConversationEventBus

        conversation_id = getattr(self, "_runtime_conversation_id", "")
        user_id = getattr(self, "_runtime_user_id", "")
        if not conversation_id or not user_id:
            raise TaskError("notifyUser requires injected conversation and user runtime context")
        message = str(self.config.get("message") or "").strip()
        if not message:
            raise TaskError("The 'message' parameter is required")
        urgency = str(self.config.get("urgency") or "normal").strip().lower()
        if urgency not in {"low", "normal", "high"}:
            raise TaskError("urgency must be low, normal, or high")
        bus = ConversationEventBus.instance()
        relationship = "sent" if bus.has_subscribers(conversation_id) else "queued"
        bus.publish_event(conversation_id, "notification", {
            "message": message,
            "urgency": urgency,
            "user_id": user_id,
            "source": "flow",
        })
        flowfile.set_attribute("notification.status", relationship)
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        self._runtime_user_id = user_id
        self._runtime_conversation_id = conversation_id

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "message": {"type": "string", "required": True,
                        "description": "Notification message"},
            "urgency": {"type": "string", "required": False,
                        "default": "normal", "description": "low | normal | high"},
        }


class DurableWaitTask(BaseTask):
    """Park the FlowFile durably until a signal fires (or timeout)."""

    TYPE = "durableWait"
    VERSION = "1.0.0"
    NAME = "Durable Wait For Signal"
    DESCRIPTION = ("Parks the FlowFile on a durable signal — for minutes, "
                   "days, months, or years (configurable timeout, none by "
                   "default). Survives restarts. Resumes with "
                   "durable.wait.status = signaled|timeout and "
                   "durable.wait.value; route downstream with "
                   "routeOnAttribute.")
    ICON = "clock"
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_WRITE,)
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "workflow.wait"

    def set_workflow_run_context(self, context, **_kwargs):
        self._workflow_run_context = context

    def workflow_authorization_target(self, _flowfile: FlowFile) -> Dict[str, Any]:
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            return {}
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "scope": "conversation",
            "scope_id": context.conversation_id,
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import (
            ConfirmationStore,
            find_own_flow_ids,
            parse_timeout_seconds,
        )
        # Resumed FlowFile (re-injected at this very task): pass through.
        if flowfile.get_attribute("durable.wait.status", ""):
            return [flowfile]
        signal_id = self.config.get("signal_id", "")
        if not signal_id:
            attr = self.config.get("signal_id_attribute",
                                   "confirmation.signal_id")
            signal_id = flowfile.get_attribute(attr, "")
        if not signal_id:
            raise TaskError(
                "durableWait needs 'signal_id' (or a FlowFile attribute "
                "named by 'signal_id_attribute')")
        try:
            timeout_seconds = parse_timeout_seconds(
                self.config.get("timeout", ""))
        except ValueError as exc:
            raise TaskError(f"Invalid durableWait timeout: {exc}")
        context = getattr(self, "_workflow_run_context", None)
        task_id = str(getattr(self, "_workflow_task_id", "") or "")
        ids = (
            {"instance_id": f"workflow:{context.run_id}", "task_id": task_id}
            if context is not None and task_id
            else find_own_flow_ids(self)
        )
        if not ids:
            raise TaskError(
                "durableWait requires a DEPLOYED continuous flow or a durable "
                "Workflow Agent run context")
        wait_id = ConfirmationStore.instance().park_wait(
            signal_id=signal_id,
            instance_id=ids["instance_id"],
            task_id=ids["task_id"],
            flowfile=flowfile,
            timeout_seconds=timeout_seconds,
        )
        if wait_id is None:
            # The signal already carried a value: pass through immediately.
            return [flowfile]
        return []   # parked — the FlowFile resumes here when the signal fires

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "signal_id": {"type": "string", "required": False,
                          "description": "Durable signal to wait for "
                                         "(static or expression)"},
            "signal_id_attribute": {
                "type": "string", "required": False,
                "default": "confirmation.signal_id",
                "description": "FlowFile attribute holding the signal id "
                               "when 'signal_id' is empty"},
            "timeout": {"type": "string", "required": False,
                        "description": "Max wait: '3600', '90s', '12h', "
                                       "'30d', '6mo', '2y'... absent/0 = "
                                       "wait forever"},
        }


class DurableTimerTask(BaseTask):
    """Park a FlowFile durably until a duration or absolute UTC time elapses."""

    TYPE = "durableTimer"
    VERSION = "1.0.0"
    NAME = "Durable Timer"
    DESCRIPTION = ("Parks a FlowFile without blocking a worker until either a "
                   "duration or an absolute timezone-aware UTC time. Survives "
                   "restarts and routes to elapsed or cancelled.")
    ICON = "clock"
    RELATIONSHIPS: ClassVar = ["elapsed", "cancelled", "failure"]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_WRITE,)
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "workflow.wait"

    def set_workflow_run_context(self, context, **_kwargs):
        self._workflow_run_context = context

    def workflow_authorization_target(self, _flowfile: FlowFile) -> Dict[str, Any]:
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            return {}
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "run_id": context.run_id,
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import (
            ConfirmationStore,
            find_own_flow_ids,
            parse_timeout_seconds,
            parse_utc_deadline,
        )
        status = flowfile.get_attribute("durable.timer.status", "")
        if status:
            flowfile.set_attribute("route.relationship", status)
            return [flowfile]
        duration = self.config.get("duration")
        until = self.config.get("until")
        has_duration = duration is not None and str(duration).strip() != ""
        has_until = until is not None and str(until).strip() != ""
        if has_duration == has_until:
            raise TaskError(
                "durableTimer requires exactly one of 'duration' or 'until'")
        try:
            if has_duration:
                seconds = parse_timeout_seconds(duration)
                if seconds <= 0:
                    raise ValueError("duration must be > 0")
                deadline_at = time.time() + seconds
            else:
                deadline_at = parse_utc_deadline(until)
        except ValueError as exc:
            raise TaskError(f"Invalid durableTimer deadline: {exc}") from exc
        flowfile.set_attribute(
            "durable.timer.scheduled_at",
            datetime.fromtimestamp(deadline_at, timezone.utc).isoformat())
        ids = find_own_flow_ids(self)
        if not ids:
            raise TaskError(
                "durableTimer requires a DEPLOYED continuous flow: the parked "
                "FlowFile must be re-injected after the deadline")
        wait_id = ConfirmationStore.instance().park_timer(
            instance_id=ids["instance_id"], task_id=ids["task_id"],
            flowfile=flowfile, deadline_at=deadline_at)
        if wait_id is None:
            return [flowfile]
        flowfile.set_attribute("durable.timer.id", wait_id)
        return []

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "duration": {"type": "string", "required": False,
                         "description": "Relative duration such as 30s, 5m, or 2h"},
            "until": {"type": "string", "required": False,
                      "description": "Absolute timezone-aware ISO-8601 timestamp"},
        }


class DurableNotifyTask(BaseTask):
    """Fire a durable signal (resumes parked durableWait FlowFiles)."""

    TYPE = "durableNotify"
    VERSION = "1.0.0"
    NAME = "Durable Notify Signal"
    DESCRIPTION = ("Fires a durable signal: every FlowFile parked on it by "
                   "durableWait resumes (across flows and restarts). With no "
                   "waiter, the value is remembered so the next durableWait "
                   "passes through immediately.")
    ICON = "bell"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import ConfirmationStore
        signal_id = self.config.get("signal_id", "")
        if not signal_id:
            attr = self.config.get("signal_id_attribute", "")
            if attr:
                signal_id = flowfile.get_attribute(attr, "")
        if not signal_id:
            raise TaskError("durableNotify needs 'signal_id'")
        value: Any = self.config.get("value", "")
        value_attr = self.config.get("value_attribute", "")
        if value_attr:
            value = flowfile.get_attribute(value_attr, "")
        resolved = ConfirmationStore.instance().notify_signal(signal_id, value)
        flowfile.set_attribute("durable.notify.resolved", str(resolved))
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "signal_id": {"type": "string", "required": False,
                          "description": "Durable signal to fire"},
            "signal_id_attribute": {"type": "string", "required": False,
                                    "description": "FlowFile attribute "
                                                   "holding the signal id"},
            "value": {"type": "string", "required": False,
                      "description": "Value delivered to the waiters"},
            "value_attribute": {"type": "string", "required": False,
                                "description": "FlowFile attribute whose "
                                               "value is delivered instead"},
        }


TaskFactory.register(RequestConfirmationTask)
TaskFactory.register(RequestUserInputTask)
TaskFactory.register(NotifyUserTask)
TaskFactory.register(DurableWaitTask)
TaskFactory.register(DurableTimerTask)
TaskFactory.register(DurableNotifyTask)
