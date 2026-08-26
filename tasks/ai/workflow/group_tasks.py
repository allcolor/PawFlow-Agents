"""Workflow-safe primitives for private-context-free group deliberation."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.agent_group_contracts import AgentGroupDefinition, ParticipantPost
from core.llm_client import LLMMessage
from core.service_definition_revision import compute_service_definition_revision
from core.usage_ledger import UsageLedger
from core.workflow_agent_contracts import AgentWorkflowRequest, AgentWorkflowResult
from core.workflow_run_store import WorkflowBudgetExceeded
from tasks.ai.workflow.llm_call import AgentLLMCallTask
from tasks.ai.workflow.turn_tasks import _WorkflowContextTask

_GROUP_FLOW_FQN = "pawflow.agents.group-deliberation:1.0.0"


def _load(flowfile: FlowFile) -> dict[str, Any]:
    value = json.loads(flowfile.get_content().decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("group workflow state must be an object")
    return value


def _store(flowfile: FlowFile, value: dict[str, Any]) -> list[FlowFile]:
    flowfile.set_content(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    return [flowfile]


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _group_run_id(run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pawflow:agent-group:{run_id}"))


def _created_at(context, round_number: int, ordinal: int) -> str:
    try:
        base = datetime.fromisoformat(context.turn_identity.created_at)
    except (AttributeError, TypeError, ValueError):
        base = datetime.now(timezone.utc)
    return (base + timedelta(
        microseconds=round_number * 1000 + ordinal
    )).astimezone(timezone.utc).isoformat()


def _usage_tokens(usage: dict[str, Any]) -> int:
    return sum(max(0, int(usage.get(key, 0) or 0)) for key in (
        "tokens_in", "tokens_out", "cache_read", "cache_write"
    ))


def _normalize_post(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip().casefold()


class _GroupTask(_WorkflowContextTask):
    GROUP_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    AUTHORIZATION_TARGET_KIND = "agent_group"

    def workflow_authorization_target(self, _flowfile: FlowFile) -> dict[str, Any]:
        context = self._context()
        group = dict((context.service_snapshot or {}).get("agent_group") or {})
        return {
            "conversation_id": context.conversation_id,
            "target_fingerprint": str(group.get("run_snapshot_digest") or ""),
        }

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        callback = getattr(self, "_workflow_event_callback", None)
        if callback is None:
            return
        context = self._context()
        callback(event_type, {
            "turn_id": context.root_turn_id,
            "run_id": context.run_id,
            "agent_name": context.agent_name,
            "flow_fqn": context.flow_ref.name,
            **data,
        })

    def _cancelled(self) -> bool:
        event = getattr(self, "_workflow_cancel_event", None)
        return bool(event is not None and event.is_set())


class GroupDeliberationInputTask(_GroupTask):
    TYPE = "groupDeliberationInput"
    NAME = "Group Deliberation Input"
    DESCRIPTION = "Validate a server-owned request for the bound group workflow."

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        if context.flow_ref.name != _GROUP_FLOW_FQN:
            raise ValueError("group input requires the first-party group workflow")
        request = AgentWorkflowRequest.from_dict(json.loads(
            flowfile.get_content().decode("utf-8")
        ))
        if request.conversation.id != context.conversation_id:
            raise ValueError("group request conversation does not match")
        if request.turn.root_turn_id != context.root_turn_id:
            raise ValueError("group request turn does not match")
        if self._cancelled():
            raise RuntimeError("group run was cancelled")
        return _store(flowfile, {"request": request.to_dict()})


class ResolveGroupSnapshotTask(_GroupTask):
    TYPE = "resolveGroupSnapshot"
    NAME = "Resolve Group Snapshot"
    DESCRIPTION = "Attach the immutable run-start group and member snapshot."

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load(flowfile)
        context = self._context()
        snapshot = dict((context.service_snapshot or {}).get("agent_group") or {})
        group = AgentGroupDefinition.from_dict(snapshot.get("definition") or {})
        expected = str((state["request"].get("parameters") or {}).get(
            "group_name") or "")
        if expected != group.name:
            raise ValueError("workflow group_name differs from the run snapshot")
        members = {
            str(row.get("member_id") or ""): row
            for row in snapshot.get("member_snapshots") or ()
        }
        if set(members) != {member.member_id for member in group.members}:
            raise ValueError("group run snapshot member roster is incomplete")
        state["group"] = snapshot
        self._emit("group_run_started", {
            "group_run_id": _group_run_id(context.run_id),
            "group_name": group.name,
            "member_count": len(group.members),
            "max_rounds": group.deliberation.max_rounds,
            "max_tokens": group.budgets.max_tokens,
        })
        return _store(flowfile, state)


class SelectGroupRespondersTask(_GroupTask):
    TYPE = "selectGroupResponders"
    NAME = "Select Group Responders"
    DESCRIPTION = "Select immutable member ids without permitting roster expansion."

    @staticmethod
    def _mentioned(group: AgentGroupDefinition, message: str) -> list[str]:
        tokens = {
            token.casefold()
            for token in re.findall(r"(?<![\w@])@([A-Za-z0-9_.:+-]+)", message)
        }
        if not tokens:
            return []
        aliases: dict[str, str] = {}
        collisions: set[str] = set()
        for member in group.members:
            values = {
                member.member_id,
                member.instance_name,
                member.display_name or "",
            }
            for raw in values:
                alias = raw.strip().casefold()
                if not alias:
                    continue
                if alias in aliases and aliases[alias] != member.member_id:
                    collisions.add(alias)
                aliases[alias] = member.member_id
        selected = []
        for token in tokens:
            if token in collisions:
                raise ValueError(f"ambiguous group mention: @{token}")
            member_id = aliases.get(token)
            if member_id and member_id not in selected:
                selected.append(member_id)
        return selected

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load(flowfile)
        group = AgentGroupDefinition.from_dict(state["group"]["definition"])
        message = str(state["request"]["request"].get("message") or "")
        if group.selection.mode == "all":
            selected = [member.member_id for member in group.members]
        elif group.selection.mode == "mentioned":
            selected = self._mentioned(group, message)
            if not selected:
                selected = [member.member_id for member in group.members]
        else:
            selected = self._classify(state, group, message)
        allowed = {member.member_id for member in group.members}
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= allowed:
            raise ValueError("group responder selection is invalid")
        state["selected_member_ids"] = selected
        return _store(flowfile, state)

    def _classify(
        self,
        state: dict[str, Any],
        group: AgentGroupDefinition,
        message: str,
    ) -> list[str]:
        service_id = str((state["group"].get("group_services") or {}).get(
            "classifier") or "")
        if not service_id:
            raise ValueError("classifier group selection has no snapshotted service")
        roster = [
            {
                "member_id": member.member_id,
                "display_name": member.display_name,
                "role": member.role,
            }
            for member in group.members
        ]
        result, _usage = self._llm_json(
            task_key="classifier",
            service_id=service_id,
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "Select only member_id values from the supplied roster. "
                        "Return JSON: {\"member_ids\":[...]}."),
                    conversation_id=self._ephemeral("classifier"),
                ),
                LLMMessage(
                    role="user",
                    content=_canonical({"request": message, "roster": roster}),
                    conversation_id=self._ephemeral("classifier"),
                ),
            ],
            max_tokens=group.budgets.max_tokens,
            token_budget=(
                None if group.budgets.max_tokens <= 0 else
                group.budgets.max_tokens
                - int((state.get("budget") or {}).get("tokens", 0))),
            cost_budget=(
                None if group.budgets.max_cost is None else
                group.budgets.max_cost
                - float((state.get("budget") or {}).get("cost", 0.0))),
        )
        budget = state.setdefault("budget", {})
        budget["tokens"] = int(budget.get("tokens", 0)) + _usage_tokens(_usage)
        budget["cost"] = float(budget.get("cost", 0.0)) + float(
            _usage.get("cost_usd", 0.0) or 0.0)
        budget["llm_calls"] = int(budget.get("llm_calls", 0)) + 1
        selected = result.get("member_ids")
        if not isinstance(selected, list):
            raise ValueError("group classifier output is malformed")
        return [str(item) for item in selected]

    def _ephemeral(self, suffix: str) -> str:
        context = self._context()
        return f"{context.conversation_id}::group::{context.run_id}::{suffix}"

    def _llm_json(
        self, *, task_key, service_id, messages, max_tokens,
        token_budget, cost_budget,
    ):
        caller = _GroupLLMCaller(self)
        return caller.call_json(
            task_key, service_id, messages, max_tokens,
            token_budget=token_budget, cost_budget=cost_budget)


class InitializeSharedRoomTask(_GroupTask):
    TYPE = "initializeSharedRoom"
    NAME = "Initialize Shared Room"
    DESCRIPTION = "Create the shared room from explicit request data only."

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load(flowfile)
        request = state["request"]
        body = request["request"]
        group = AgentGroupDefinition.from_dict(state["group"]["definition"])
        state["shared_room"] = {
            "request": str(body.get("message") or ""),
            "attachments": list(body.get("attachments") or ()),
            "policy": {
                "private_context": group.context_policy.private_context,
                "attachments": group.context_policy.attachments,
                "tool_mode": group.tool_policy.mode,
            },
            "posts": [],
        }
        state.setdefault("budget", {
            "participant_calls": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tokens": 0,
            "cost": 0.0,
            "rounds": 0,
        })
        return _store(flowfile, state)


class _GroupLLMCaller:
    def __init__(self, task: _GroupTask):
        self.task = task
        self.context = task._context()
        self.run_store = getattr(task, "_workflow_run_store", None)
        if self.run_store is None:
            raise RuntimeError("workflow run store was not injected")

    def _service(self, service_id: str):
        services = dict((self.context.service_snapshot or {}).get("services") or {})
        snapshot = dict(services.get(service_id) or {})
        if not snapshot:
            raise ValueError("group LLM service is outside the run snapshot")
        from core.service_registry import ServiceRegistry

        registry = ServiceRegistry.get_instance()
        definition = registry.get_definition(
            str(snapshot["scope"]),
            str(snapshot["scope_id"]),
            str(snapshot["service_id"]),
        )
        if definition is None or not definition.enabled:
            raise ValueError("group LLM service is unavailable")
        if compute_service_definition_revision(definition) != snapshot.get(
            "definition_revision"
        ):
            raise ValueError("group LLM service changed after run acceptance")
        service = registry.get_live_instance(
            str(snapshot["scope"]),
            str(snapshot["scope_id"]),
            str(snapshot["service_id"]),
        )
        if service is None or not hasattr(service, "get_client"):
            raise ValueError("group LLM service could not connect")
        return snapshot, service

    def call_json(
        self,
        task_key: str,
        service_id: str,
        messages: list[LLMMessage],
        max_tokens: int,
        *,
        model: str = "",
        token_budget: int | None = None,
        cost_budget: float | None = None,
        tool_runtime=None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.task._cancelled():
            raise RuntimeError("group run was cancelled")
        snapshot, service = self._service(service_id)
        input_hash = _hash({
            "messages": [
                {"role": item.role, "content": item.content} for item in messages
            ],
            "service": snapshot,
            "model": model,
            "max_tokens": max_tokens,
            "tools": list(tool_runtime.allowed_names) if tool_runtime else [],
        })
        cache_key = f"{self.task.get_task_id()}:{task_key}"
        cached = self.run_store.begin_llm_step(
            self.context.run_id, cache_key, input_hash
        )
        if cached is not None:
            self._enforce_budget(
                cached["usage"], token_budget=token_budget,
                cost_budget=cost_budget)
            parsed = cached["result"].get("parsed")
            if not isinstance(parsed, dict):
                raise ValueError("cached group LLM output is malformed")
            return parsed, dict(cached["usage"])
        committed = False
        try:
            client = service.get_client()
            client._agent_service = service_id
            client._user_id = self.context.user_id
            client._conversation_id = (
                f"{self.context.conversation_id}::group::"
                f"{self.context.run_id}::{task_key}"
            )
            client._agent_name = self.context.agent_name
            client._event_cid = self.context.conversation_id
            if hasattr(client, "reset_abort"):
                client.reset_abort()
            response, usage = self._complete_json(
                client,
                messages,
                service,
                str(snapshot["service_id"]),
                max_tokens=max_tokens,
                model=model,
                token_budget=token_budget,
                cost_budget=cost_budget,
                tool_runtime=tool_runtime,
            )
            try:
                parsed = json.loads(str(response.content or ""))
            except json.JSONDecodeError as exc:
                raise ValueError("group LLM returned malformed JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError("group LLM output must be an object")
            self._enforce_budget(
                usage, token_budget=token_budget, cost_budget=cost_budget)
            row = self.run_store.commit_llm_step(
                self.context.run_id,
                cache_key,
                input_hash,
                {
                    "content": str(response.content or ""),
                    "parsed": parsed,
                    "model": str(response.model or ""),
                    "finish_reason": str(response.finish_reason or ""),
                },
                usage,
            )
            committed = True
            self._record_usage(cache_key, input_hash, snapshot, row["step_usage"])
            return parsed, dict(row["step_usage"])
        finally:
            if not committed:
                self.run_store.abort_llm_step(
                    self.context.run_id, cache_key, input_hash
                )

    @staticmethod
    def _enforce_budget(usage, *, token_budget, cost_budget):
        if token_budget is not None and _usage_tokens(usage) > max(
            0, int(token_budget)
        ):
            raise WorkflowBudgetExceeded("group token allocation exceeded")
        if cost_budget is not None and float(
            usage.get("cost_usd", 0.0) or 0.0
        ) > max(0.0, float(cost_budget)):
            raise WorkflowBudgetExceeded("group cost allocation exceeded")

    def _complete_json(
        self, client, messages, service, service_id, *, max_tokens, model,
        token_budget, cost_budget, tool_runtime,
    ):
        active_messages = list(messages)
        aggregate: dict[str, Any] = {"tool_calls": 0}
        while True:
            remaining = (
                0 if max_tokens <= 0 else
                max_tokens - _usage_tokens(aggregate)
            )
            if max_tokens > 0 and remaining <= 0:
                raise WorkflowBudgetExceeded("group token allocation exhausted by tool loop")
            response = self._complete(
                client,
                active_messages,
                max_tokens=remaining,
                model=model,
                tools=(tool_runtime.definitions() if tool_runtime else None),
            )
            usage = AgentLLMCallTask._usage(response, service, service_id)
            for key in (
                "llm_calls", "tokens_in", "tokens_out", "cache_read", "cache_write",
                "duration_ms", "cost_usd", "virtual_cost_usd",
            ):
                aggregate[key] = aggregate.get(key, 0) + (usage.get(key, 0) or 0)
            for key in ("model", "provider", "pricing", "subscription"):
                if usage.get(key) not in (None, "", {}):
                    aggregate[key] = usage[key]
            self._enforce_budget(
                aggregate, token_budget=token_budget, cost_budget=cost_budget)
            calls = list(response.tool_calls or ())
            if not calls:
                return response, aggregate
            if tool_runtime is None:
                raise ValueError("group LLM requested a tool while tool mode is none")
            aggregate["tool_calls"] = int(aggregate["tool_calls"]) + len(calls)
            active_messages.append(LLMMessage(
                role="assistant",
                content=str(response.content or ""),
                tool_calls=calls,
                conversation_id=active_messages[-1].conversation_id,
            ))
            for call in calls:
                active_messages.append(LLMMessage(
                    role="tool",
                    content=tool_runtime.execute(call),
                    tool_call_id=call.id,
                    conversation_id=active_messages[-1].conversation_id,
                ))
            if self.task._cancelled():
                raise RuntimeError("group run was cancelled")
    def _complete(self, client, messages, *, max_tokens: int, model: str, tools=None):
        finished = threading.Event()
        cancel_event = getattr(self.task, "_workflow_cancel_event", None)

        def watch() -> None:
            while not finished.wait(0.05):
                if cancel_event is not None and cancel_event.is_set():
                    if hasattr(client, "abort"):
                        client.abort()
                    return

        watcher = threading.Thread(
            target=watch,
            daemon=True,
            name=f"group-llm-cancel-{self.context.run_id[-8:]}",
        )
        watcher.start()
        try:
            return client.complete(
                messages=messages,
                model=model or None,
                temperature=0.2,
                max_tokens=max(0, int(max_tokens)),
                response_format="json",
                tools=tools,
                thinking_budget=0,
            )
        finally:
            finished.set()
            watcher.join(timeout=0.2)

    def _record_usage(self, task_id, input_hash, snapshot, usage):
        pricing = dict(usage.get("pricing") or {})
        UsageLedger.instance().record(
            user_id=self.context.user_id,
            channel="group",
            conversation_id=self.context.conversation_id,
            agent_name=self.context.agent_name,
            llm_service=str(snapshot["service_id"]),
            model=str(usage.get("model") or ""),
            provider=str(usage.get("provider") or ""),
            tokens_in=int(usage.get("tokens_in", 0) or 0),
            tokens_out=int(usage.get("tokens_out", 0) or 0),
            cache_read=int(usage.get("cache_read", 0) or 0),
            cache_write=int(usage.get("cache_write", 0) or 0),
            duration_ms=int(usage.get("duration_ms", 0) or 0),
            cost_per_1m_input=pricing.get("cost_per_1m_input"),
            cost_per_1m_output=pricing.get("cost_per_1m_output"),
            cost_per_1m_cache_read=pricing.get("cost_per_1m_cache_read"),
            cost_per_1m_cache_write=pricing.get("cost_per_1m_cache_write"),
            subscription=bool(usage.get("subscription")),
            logical_service_scope=str(snapshot.get("scope") or ""),
            logical_service_scope_id=str(snapshot.get("scope_id") or ""),
            run_id=self.context.run_id,
            task_id=task_id,
            event_id=f"group:{self.context.run_id}:{task_id}:{input_hash}",
        )


class AgentParticipantCallTask(_GroupTask):
    TYPE = "agentParticipantCall"
    NAME = "Agent Participant Call"
    DESCRIPTION = "Run structured participant calls without private context or mutating tools."
    IDEMPOTENCY = IdempotencyClass.RUN_CACHED
    RELATIONSHIPS: ClassVar = ["success", "failure"]

    def workflow_retry_attempts(self, default: int) -> int:
        return 0

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load(flowfile)
        group = AgentGroupDefinition.from_dict(state["group"]["definition"])
        if group.tool_policy.mode not in {"none", "read_only"}:
            raise ValueError("group participant tools must be none or read_only")
        selected = list(state["selected_member_ids"])
        snapshots = {
            str(row["member_id"]): dict(row)
            for row in state["group"]["member_snapshots"]
        }
        posts: list[dict[str, Any]] = []
        budget = dict(state["budget"])
        previous_hashes: set[str] | None = None
        stop_reason = "completed"
        caller = _GroupLLMCaller(self)
        group_id = _group_run_id(self._context().run_id)
        round_number = 0

        while (
            group.deliberation.max_rounds <= 0
            or round_number < group.deliberation.max_rounds
        ):
            round_number += 1
            if self._cancelled():
                raise RuntimeError("group run was cancelled")
            call_limit = group.deliberation.max_total_participant_calls
            remaining_calls = (
                None if call_limit <= 0 else
                call_limit - int(budget["participant_calls"])
            )
            if remaining_calls is not None and remaining_calls <= 0:
                stop_reason = "participant_call_budget"
                break
            ordered = self._ordered(
                selected,
                group_id,
                round_number,
                rotate=group.deliberation.rotate_first_speaker,
            )
            if remaining_calls is not None:
                ordered = ordered[:remaining_calls]
            remaining_tokens = (
                None if group.budgets.max_tokens <= 0 else
                group.budgets.max_tokens - int(budget["tokens"])
            )
            if remaining_tokens is not None and remaining_tokens <= 0:
                stop_reason = "token_budget"
                break
            allocations = self._allocations(remaining_tokens, len(ordered))
            remaining_cost = (
                None if not group.budgets.max_cost else
                group.budgets.max_cost - float(budget["cost"])
            )
            cost_allocations = self._cost_allocations(
                remaining_cost, len(ordered))
            outcomes = self._round_calls(
                caller,
                state,
                group,
                snapshots,
                ordered,
                round_number,
                allocations,
                cost_allocations,
            )
            for member_id in ordered:
                reason = outcomes[member_id].get("budget_error")
                if reason:
                    self._budget_stop(str(reason))
                    raise WorkflowBudgetExceeded(str(reason))
            round_posts = []
            for ordinal, member_id in enumerate(ordered):
                outcome = outcomes[member_id]
                member = next(item for item in group.members if item.member_id == member_id)
                if "error" in outcome:
                    if member.required:
                        raise RuntimeError(
                            f"required group member failed: {member_id}: "
                            f"{outcome['error']}"
                        )
                    self._emit("group_participant_failed", {
                        "group_run_id": group_id,
                        "round": round_number,
                        "member_id": member_id,
                        "required": False,
                    })
                    continue
                usage = dict(outcome["usage"])
                new_tokens = int(budget["tokens"]) + _usage_tokens(usage)
                new_cost = float(budget["cost"]) + float(
                    usage.get("cost_usd", 0.0) or 0.0
                )
                if (
                    group.budgets.max_tokens > 0
                    and new_tokens > group.budgets.max_tokens
                ) or (
                    bool(group.budgets.max_cost)
                    and new_cost > group.budgets.max_cost
                ):
                    self._budget_stop("group budget exhausted")
                    raise WorkflowBudgetExceeded("group budget exhausted")
                budget["tokens"] = new_tokens
                budget["cost"] = new_cost
                budget["participant_calls"] = int(budget["participant_calls"]) + 1
                budget["llm_calls"] = int(budget["llm_calls"]) + int(
                    usage.get("llm_calls", 1) or 1
                )
                budget["tool_calls"] = int(budget.get("tool_calls", 0)) + int(
                    usage.get("tool_calls", 0) or 0
                )
                parsed = dict(outcome["parsed"])
                disposition = str(parsed.get("disposition") or "")
                content = str(parsed.get("content") or "")
                post = ParticipantPost(
                    schema_version=1,
                    group_run_id=group_id,
                    round=round_number,
                    member_id=member_id,
                    member_snapshot_digest=str(
                        snapshots[member_id]["snapshot_digest"]
                    ),
                    disposition=disposition,
                    content=content,
                    citations=tuple(parsed.get("citations") or ()),
                    confidence=parsed.get("confidence"),
                    token_usage=usage,
                    created_at=_created_at(self._context(), round_number, ordinal),
                )
                if post.disposition == "pass" and not group.deliberation.allow_pass:
                    raise ValueError("participant pass is disabled by group policy")
                row = post.to_dict()
                round_posts.append(row)
                posts.append(row)
                self._emit("group_participant_post", {
                    "group_run_id": group_id,
                    "round": round_number,
                    "member_id": member_id,
                    "disposition": post.disposition,
                    "content": post.content,
                    "citations": list(post.citations),
                    "confidence": post.confidence,
                    "token_usage": post.token_usage,
                })
            budget["rounds"] = round_number
            history_limit = group.context_policy.shared_history_limit
            state["shared_room"]["posts"] = (
                posts if history_limit <= 0 else posts[-history_limit:]
            )
            if round_posts and all(
                row["disposition"] == "pass" for row in round_posts
            ):
                stop_reason = "all_passed"
                break
            hashes = {
                _hash(_normalize_post(row["content"]))
                for row in round_posts if row["disposition"] == "post"
            }
            if previous_hashes is not None and hashes <= previous_hashes:
                stop_reason = "no_new_contributions"
                break
            previous_hashes = hashes
        else:
            stop_reason = "max_rounds"
        history_limit = group.context_policy.shared_history_limit
        state["shared_room"]["posts"] = (
            posts if history_limit <= 0 else posts[-history_limit:]
        )
        state["budget"] = budget
        state["stop_reason"] = stop_reason
        self._emit("group_rounds_completed", {
            "group_run_id": group_id,
            "rounds": int(budget["rounds"]),
            "participant_calls": int(budget["participant_calls"]),
            "llm_calls": int(budget["llm_calls"]),
            "tool_calls": int(budget.get("tool_calls", 0)),
            "stop_reason": stop_reason,
            "tokens": int(budget["tokens"]),
            "cost": float(budget["cost"]),
        })
        return _store(flowfile, state)

    @staticmethod
    def _ordered(
        selected: list[str],
        group_id: str,
        round_number: int,
        *,
        rotate: bool,
    ) -> list[str]:
        if not selected or not rotate:
            return list(selected)
        if not selected:
            return []
        seed = int(hashlib.sha256(group_id.encode("utf-8")).hexdigest(), 16)
        offset = (seed + round_number) % len(selected)
        return selected[offset:] + selected[:offset]

    @staticmethod
    def _allocations(remaining_tokens: int | None, count: int) -> list[int]:
        if count <= 0:
            return []
        if remaining_tokens is None:
            return [0] * count
        per_call = max(1, remaining_tokens // count)
        return [per_call] * count

    @staticmethod
    def _cost_allocations(
        remaining_cost: float | None, count: int
    ) -> list[float | None]:
        if count <= 0:
            return []
        if remaining_cost is None:
            return [None] * count
        per_call = max(0.0, float(remaining_cost)) / count
        return [per_call] * count

    def _round_calls(
        self,
        caller,
        state,
        group,
        snapshots,
        ordered,
        round_number,
        allocations,
        cost_allocations,
    ):
        results = {}
        parallelism = group.deliberation.max_parallelism
        workers = min(len(ordered), parallelism) if parallelism > 0 else len(ordered)
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"group-{self._context().run_id[-6:]}-r{round_number}",
        ) as pool:
            futures = {}
            for ordinal, member_id in enumerate(ordered):
                future = pool.submit(
                    self._call_member,
                    caller,
                    state,
                    group,
                    snapshots[member_id],
                    round_number,
                    allocations[ordinal],
                    cost_allocations[ordinal],
                )
                futures[future] = member_id
            for future in as_completed(futures):
                member_id = futures[future]
                try:
                    results[member_id] = future.result()
                except WorkflowBudgetExceeded as exc:
                    results[member_id] = {"budget_error": str(exc)}
                except Exception as exc:
                    results[member_id] = {"error": str(exc)}
        return results

    def _call_member(
        self,
        caller,
        state,
        group,
        snapshot,
        round_number,
        max_tokens,
        cost_budget,
    ):
        member_id = str(snapshot["member_id"])
        member = next(item for item in group.members if item.member_id == member_id)
        public_definition = dict(snapshot.get("agent_definition") or {})
        prompt = str(public_definition.get("prompt") or "")
        tool_instruction = (
            "Do not request tools, delegate, message the user, or assume private "
            "conversation history. "
            if group.tool_policy.mode == "none" else
            "You may use only the explicitly exposed read-only observation tools. "
            "Never request mutation, delegation, messaging, memory, diary, or "
            "conversation-history tools. "
        )
        system = (
            f"{prompt}\n\n"
            "You are participating in a PawFlow group deliberation. "
            f"Your group role is: {member.role or member.display_name or member_id}. "
            "You receive only the explicit shared room below. "
            f"{tool_instruction}"
            "Return exactly one JSON object with disposition ('post' or 'pass'), "
            "content, citations (array), and optional confidence (0..1)."
        )
        room = {
            "request": state["shared_room"]["request"],
            "attachments": state["shared_room"]["attachments"],
            "prior_posts": state["shared_room"]["posts"],
            "current_posts": [
                post for post in state.get("_current_round_posts", [])
            ],
            "round": round_number,
            "parameters": snapshot.get("params") or {},
        }
        ephemeral = (
            f"{self._context().conversation_id}::group::"
            f"{self._context().run_id}::r{round_number}::{member_id}"
        )
        tool_runtime = None
        if group.tool_policy.mode == "read_only":
            from core.agent_group_tools import GroupReadOnlyToolRuntime

            tool_runtime = GroupReadOnlyToolRuntime(
                context=self._context(),
                group=group,
                member_snapshot=snapshot,
                round_number=round_number,
                group_run_id=_group_run_id(self._context().run_id),
                emit=self._emit,
                cancelled=self._cancelled,
                cancel_event=getattr(self, "_workflow_cancel_event", None),
            )
        parsed, usage = caller.call_json(
            task_key=f"r{round_number}:{member_id}",
            service_id=str(snapshot["service"]["service_id"]),
            messages=[
                LLMMessage(role="system", content=system, conversation_id=ephemeral),
                LLMMessage(
                    role="user",
                    content=_canonical(room),
                    conversation_id=ephemeral,
                ),
            ],
            max_tokens=max_tokens,
            model=str(snapshot.get("model") or ""),
            token_budget=None if max_tokens <= 0 else max_tokens,
            cost_budget=cost_budget,
            tool_runtime=tool_runtime,
        )
        return {"parsed": parsed, "usage": usage}

    def _budget_stop(self, reason: str):
        run_store = getattr(self, "_workflow_run_store", None)
        if run_store is not None:
            run_store.mark_budget_exceeded(self._context().run_id, reason)
        event = getattr(self, "_workflow_cancel_event", None)
        if event is not None:
            event.set()


class SynthesizeGroupResultTask(_GroupTask):
    TYPE = "synthesizeGroupResult"
    NAME = "Synthesize Group Result"
    DESCRIPTION = "Produce one terminal candidate from the shared room."
    IDEMPOTENCY = IdempotencyClass.RUN_CACHED

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load(flowfile)
        group = AgentGroupDefinition.from_dict(state["group"]["definition"])
        posts = list(state["shared_room"]["posts"])
        budget = dict(state["budget"])
        response = self._synthesize(state, group, posts, budget)
        result = AgentWorkflowResult(
            schema_version=1,
            status="completed",
            response=response,
            metrics={
                "group_rounds": int(budget["rounds"]),
                "group_participant_calls": int(budget["participant_calls"]),
                "group_llm_calls": int(budget["llm_calls"]),
                "group_tool_calls": int(budget.get("tool_calls", 0)),
                "group_tokens": int(budget["tokens"]),
                "group_posts": sum(
                    1 for post in posts if post["disposition"] == "post"
                ),
                "group_passes": sum(
                    1 for post in posts if post["disposition"] == "pass"
                ),
            },
            answered_turn_ids=(self._context().root_turn_id,),
        )
        self._emit("group_synthesis_completed", {
            "group_run_id": _group_run_id(self._context().run_id),
            "stop_reason": state.get("stop_reason"),
            "rounds": int(budget["rounds"]),
            "participant_calls": int(budget["participant_calls"]),
            "llm_calls": int(budget["llm_calls"]),
            "tool_calls": int(budget.get("tool_calls", 0)),
            "tokens": int(budget["tokens"]),
            "cost": float(budget["cost"]),
        })
        flowfile.set_content(json.dumps(
            result.to_dict(), ensure_ascii=False
        ).encode("utf-8"))
        return [flowfile]

    def _synthesize(self, state, group, posts, budget) -> str:
        contributions = [
            post for post in posts if post["disposition"] == "post"
        ]
        if group.synthesis.mode == "deterministic_concat":
            if not contributions:
                return "The group reached no additional finding."
            lines = []
            for post in contributions:
                prefix = (
                    f"[{post['member_id']}] "
                    if group.output.include_attributions else ""
                )
                lines.append(prefix + str(post["content"]))
            return "\n\n".join(lines)

        if group.synthesis.mode == "designated_member":
            member_id = str(group.synthesis.member_id or "")
            snapshot = next(
                row for row in state["group"]["member_snapshots"]
                if row["member_id"] == member_id
            )
            service_id = str(snapshot["service"]["service_id"])
            model = str(snapshot.get("model") or "")
        else:
            service_id = str((state["group"].get("group_services") or {}).get(
                "synthesis") or "")
            model = ""
        if not service_id:
            raise ValueError("group synthesis service is unavailable")
        token_limit = group.budgets.max_tokens
        remaining = (
            None if token_limit <= 0
            else token_limit - int(budget["tokens"])
        )
        if remaining is not None and remaining <= 0:
            raise WorkflowBudgetExceeded("no token budget remains for synthesis")
        ephemeral = (
            f"{self._context().conversation_id}::group::"
            f"{self._context().run_id}::synthesis"
        )
        parsed, usage = _GroupLLMCaller(self).call_json(
            "synthesis",
            service_id,
            [
                LLMMessage(
                    role="system",
                    content=(
                        "Synthesize the group room into one final answer. "
                        "Return JSON with a non-empty response string. Preserve "
                        "material dissent when present."),
                    conversation_id=ephemeral,
                ),
                LLMMessage(
                    role="user",
                    content=_canonical({
                        "request": state["shared_room"]["request"],
                        "posts": contributions,
                        "include_attributions": group.output.include_attributions,
                        "include_dissent": group.output.include_dissent,
                    }),
                    conversation_id=ephemeral,
                ),
            ],
            0 if remaining is None else remaining,
            model=model,
            token_budget=remaining,
            cost_budget=(
                None if group.budgets.max_cost is None else
                group.budgets.max_cost - float(budget["cost"])),
        )
        total_tokens = int(budget["tokens"]) + _usage_tokens(usage)
        total_cost = float(budget["cost"]) + float(
            usage.get("cost_usd", 0.0) or 0.0
        )
        if (group.budgets.max_tokens > 0
            and total_tokens > group.budgets.max_tokens) or (
            group.budgets.max_cost is not None
            and total_cost > group.budgets.max_cost
        ):
            raise WorkflowBudgetExceeded("group synthesis exceeded its budget")
        budget["tokens"] = total_tokens
        budget["cost"] = total_cost
        budget["llm_calls"] = int(budget["llm_calls"]) + 1
        response = str(parsed.get("response") or "").strip()
        if not response:
            raise ValueError("group synthesis returned no response")
        return response


for _task in (
    GroupDeliberationInputTask,
    ResolveGroupSnapshotTask,
    SelectGroupRespondersTask,
    InitializeSharedRoomTask,
    AgentParticipantCallTask,
    SynthesizeGroupResultTask,
):
    TaskFactory.register(_task)


__all__ = [
    "AgentParticipantCallTask",
    "GroupDeliberationInputTask",
    "InitializeSharedRoomTask",
    "ResolveGroupSnapshotTask",
    "SelectGroupRespondersTask",
    "SynthesizeGroupResultTask",
]
