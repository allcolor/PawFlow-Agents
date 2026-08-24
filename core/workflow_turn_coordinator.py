"""Recoverable terminal saga for workflow-agent turns."""

from __future__ import annotations

import logging
from typing import Any

from core.workflow_run_store import WorkflowRunStore, new_terminal_identities

logger = logging.getLogger(__name__)


class WorkflowTurnCoordinator:
    """Own the irreversible assistant/inbox/outbox commit sequence."""

    def __init__(self, run_store: WorkflowRunStore | None = None,
                 inbox_store=None) -> None:
        self.run_store = run_store or WorkflowRunStore.instance()
        if inbox_store is None:
            from core.agent_inbox_store import AgentInboxStore
            inbox_store = AgentInboxStore.instance()
        self.inbox_store = inbox_store

    def finalize(self, context, result) -> dict[str, Any] | None:
        """Stage a terminal once, then drive its idempotent commit saga."""
        existing = self.run_store.get_run(context.run_id)
        if existing is not None and existing["status"] == "completed":
            return existing["terminal_event"]
        if existing is not None and existing["status"] == "committing":
            return self.commit(context.run_id)
        if not self.run_store.is_current_generation(context.run_id):
            self.run_store.supersede(
                context.run_id, "stale generation reached finalization")
            self.inbox_store.release(
                context.conversation_id, context.agent_name, context.run_id)
            return None
        response = result.response
        if len(response.encode("utf-8")) > 256_000:
            raise ValueError("workflow terminal response is too large")
        assistant_msg_id, event_id = new_terminal_identities(context.run_id)
        from core.llm_client import stamp_message
        message = stamp_message({
            "role": "assistant",
            "content": response,
            "source": {
                "type": "agent", "name": context.agent_name,
                "runtime_kind": "workflow", "run_id": context.run_id,
                "flow_fqn": context.flow_ref.name,
            },
            "turn_id": context.root_turn_id,
            "channel": context.channel,
            "msg_id": assistant_msg_id,
        }, context.conversation_id)
        terminal = {
            "event_id": event_id,
            "turn_id": context.root_turn_id,
            "answered_turn_ids": list(result.answered_turn_ids),
            "run_id": context.run_id,
            "runtime_kind": "workflow",
            "flow_fqn": context.flow_ref.name,
            "agent_name": context.agent_name,
            "channel": context.channel,
            "response": response,
            "finish_reason": "workflow_complete",
        }
        self.run_store.stage_terminal(
            context.run_id, result=result, assistant_payload=message,
            terminal_event=terminal)
        return self.commit(context.run_id)

    def commit(self, run_id: str) -> dict[str, Any] | None:
        """Resume a staged commit from any durable saga boundary."""
        run = self.run_store.get_run(run_id)
        if run is None:
            raise KeyError("workflow run does not exist")
        if run["status"] == "completed":
            return run["terminal_event"]
        if run["status"] != "committing":
            raise RuntimeError(
                f"workflow terminal commit requires committing, got {run['status']}")
        if not self.run_store.is_current_generation(run_id):
            self.run_store.transition(
                run_id, "committing", "superseded",
                "stale generation during commit recovery")
            self.inbox_store.release(
                run["conversation_id"], run["agent_name"], run_id)
            return None

        silent = run["invocation_mode"] == "silent_maintenance"
        if not run["message_committed"] and silent:
            self.run_store.mark_message_committed(run_id)
        elif not run["message_committed"]:
            from core.conversation_writer import ConversationWriter
            ConversationWriter.for_conversation(
                run["conversation_id"]).enqueue_message_if_absent(
                    dict(run["assistant_payload"]),
                    agent_name=run["agent_name"], user_id=run["user_id"])
            self.run_store.mark_message_committed(run_id)

        run = self.run_store.get_run(run_id)
        if not run["inbox_acknowledged"]:
            self.inbox_store.acknowledge(
                run["conversation_id"], run["agent_name"], run_id,
                run["answered_turn_ids"])
            self.inbox_store.release(
                run["conversation_id"], run["agent_name"], run_id)
            self.run_store.mark_inbox_acknowledged(run_id)

        for outbox in self.run_store.pending_outbox(run_id):
            if silent:
                self.run_store.record_outbox_attempt(outbox["event_id"], True)
                continue
            delivered = False
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    run["conversation_id"], "done", dict(outbox["event"]))
                delivered = True
            except Exception:
                logger.exception(
                    "workflow terminal outbox delivery failed for %s", run_id)
            finally:
                self.run_store.record_outbox_attempt(
                    outbox["event_id"], delivered)
            if not delivered:
                return None

        if not self.run_store.complete(run_id):
            raise RuntimeError("workflow terminal saga is incomplete")
        return self.run_store.get_run(run_id)["terminal_event"]

    def recover_committing(self) -> dict[str, int]:
        completed = 0
        pending = 0
        for run in self.run_store.list_recoverable(("committing",)):
            try:
                event = self.commit(run["run_id"])
                if event is not None:
                    completed += 1
                else:
                    pending += 1
            except Exception:
                pending += 1
                logger.exception(
                    "workflow committing-state recovery remains pending for %s",
                    run["run_id"])
                # A terminal saga may already have crossed an irreversible
                # boundary. Keep it in committing so the same stable message
                # and event identities are retried on the next recovery pass.
        return {"completed": completed, "pending": pending}


__all__ = ["WorkflowTurnCoordinator"]
