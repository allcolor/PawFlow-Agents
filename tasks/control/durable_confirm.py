"""Durable confirmation and wait/notify tasks.

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

from typing import Any, Dict, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


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

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import (
            ConfirmationStore, parse_timeout_seconds)
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
        )
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

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from core.confirmation_store import (
            ConfirmationStore, find_own_flow_ids, parse_timeout_seconds)
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
        ids = find_own_flow_ids(self)
        if not ids:
            raise TaskError(
                "durableWait requires a DEPLOYED continuous flow: the parked "
                "FlowFile is re-injected through the executor registry, which "
                "a one-shot batch run cannot receive")
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
TaskFactory.register(DurableWaitTask)
TaskFactory.register(DurableNotifyTask)
