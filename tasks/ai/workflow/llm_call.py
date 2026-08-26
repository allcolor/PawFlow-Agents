"""Production LLM processor for isolated workflow-agent runs."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

import jsonschema

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.identifier import resolve_identifier
from core.llm_client import LLMMessage, LLMToolDefinition
from core.service_definition_revision import compute_service_definition_revision
from core.usage_ledger import UsageLedger, compute_cost
from core.workflow_run_store import WorkflowBudgetExceeded
from tasks.ai.workflow.turn_tasks import _WorkflowContextTask

_LLM_SERVICE_TYPES = frozenset(
    {
        "llmConnection",
        "llmAggregator",
        "llmRouter",
    }
)
_STRUCTURED_OUTPUT_TOOL = "submit_workflow_result"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class AgentLLMCallTask(_WorkflowContextTask):
    """One bounded, recoverable LLM call with strict output validation."""

    TYPE = "agentLLMCall"
    VERSION = "1.0.0"
    NAME = "Agent LLM Call"
    DESCRIPTION = "Run one idempotent LLM processor inside an agent workflow."
    ICON = "ai"
    RELATIONSHIPS: ClassVar = ["success", "failure"]
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.RUN_CACHED
    AUTHORIZATION_TARGET_KIND = "service"

    def workflow_authorization_target(self, _flowfile: FlowFile) -> dict[str, Any]:
        canonical, entry = self._snapshot_service_entry(self._context())
        return {
            "service_id": str(entry.get("service_id") or canonical),
            "scope": str(entry.get("scope") or ""),
            "scope_id": str(entry.get("scope_id") or ""),
        }

    def _snapshot_service_entry(self, context) -> tuple[str, dict[str, Any]]:
        snapshot = dict(context.service_snapshot or {})
        services = dict(snapshot.get("services") or {})
        bindings = dict(snapshot.get("bindings") or {})
        requested = str(self.config.get("service") or "").strip()
        requested = bindings.get(requested, requested)
        canonical = resolve_identifier(services, requested)
        if not canonical:
            raise ValueError(
                f"workflow LLM service is outside the run snapshot: {requested}")
        entry = dict(services[canonical])
        resolved = str(entry.get("resolved_llm_service") or "").strip()
        if resolved:
            canonical = resolve_identifier(services, resolved)
            if not canonical:
                raise ValueError(
                    "workflow resolved LLM service is outside the run snapshot")
            entry = dict(services[canonical])
        return canonical, entry

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "service": {
                "type": "string",
                "required": True,
                "description": "Service id or service_ref parameter binding.",
            },
            "model": {"type": "string", "required": False, "default": ""},
            "system_prompt": {"type": "string", "required": False, "default": ""},
            "messages": {"type": "json", "required": False, "default": []},
            "input": {"type": "string", "required": False, "default": ""},
            "input_attribute": {"type": "string", "required": False, "default": ""},
            "response_format": {
                "type": "select",
                "required": False,
                "default": "text",
                "options": ["text", "json", "json_schema"],
            },
            "json_schema": {"type": "json", "required": False, "default": {}},
            "temperature": {"type": "number", "required": False, "default": 0.7},
            "max_tokens": {"type": "integer", "required": False, "default": 4096},
            "thinking_budget": {"type": "integer", "required": False, "default": 0},
            "output_target": {
                "type": "select",
                "required": False,
                "default": "content",
                "options": ["content", "attribute"],
            },
            "output_attribute": {
                "type": "string",
                "required": False,
                "default": "llm.response",
            },
            "progress_label": {"type": "string", "required": False, "default": ""},
            "cache_policy": {
                "type": "select",
                "required": False,
                "default": "run_idempotent",
                "options": ["none", "run_idempotent"],
            },
            "retry_attempts": {"type": "integer", "required": False, "default": 1},
            "timeout": {"type": "integer", "required": False, "default": 60},
            "visibility": {
                "type": "select",
                "required": False,
                "default": "hidden",
                "options": ["hidden", "final_candidate"],
            },
        }

    def workflow_retry_attempts(self, default: int) -> int:
        requested = max(1, int(self.config.get("retry_attempts", 1) or 1))
        if (
            requested > 1
            and self.config.get("cache_policy", "run_idempotent") != "run_idempotent"
        ):
            raise ValueError("agentLLMCall retries require run_idempotent caching")
        return min(max(1, int(default)), requested)

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        run_store = getattr(self, "_workflow_run_store", None)
        if run_store is None:
            raise RuntimeError("workflow run store was not injected")
        cancel_event = getattr(self, "_workflow_cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("workflow LLM call was cancelled")

        service_id, service_snapshot, service = self._resolve_service(context)
        messages = self._messages(flowfile, context)
        input_hash = self._input_hash(messages, service_snapshot)
        if self.config.get("cache_policy", "run_idempotent") == "none":
            input_hash = hashlib.sha256(
                f"{input_hash}:{uuid.uuid4()}".encode()
            ).hexdigest()
        task_id = self.get_task_id()
        try:
            cached = run_store.begin_llm_step(context.run_id, task_id, input_hash)
        except WorkflowBudgetExceeded as exc:
            self._stop_for_budget(context, str(exc))
            raise
        if cached is not None:
            self._record_usage_once(
                context,
                task_id,
                input_hash,
                service_id,
                service_snapshot,
                cached["usage"],
            )
            self._apply_result(flowfile, cached["result"])
            self._check_committed_cost(context, cached["usage"])
            return [flowfile]

        committed = False
        try:
            self._emit(context, "started", service_id)
            client = service.get_client()
            self._bind_client(client, context, service_id, task_id)
            timeout_seconds = self._bound_timeout(context)
            response = self._complete_with_cancellation(
                client, messages, cancel_event, timeout_seconds)
            if str(response.content or "").strip():
                self._emit_execution(
                    context,
                    "agent_message",
                    role="assistant",
                    model=str(response.model or ""),
                    **self._observable_agent_message_values(
                        context, str(response.content or "")),
                )
            for call in response.tool_calls or []:
                self._emit_execution(
                    context,
                    "tool_call",
                    tool_call_id=str(call.id),
                    tool_name=str(call.name),
                    arguments=self._observable_event_value(
                        context, dict(call.arguments or {}),
                        max_string=800, max_items=32),
                )
            track_tokens = getattr(service, "_track_tokens", None)
            if callable(track_tokens):
                track_tokens(response, messages)
            result = self._validated_result(response)
            usage = self._usage(response, service, service_id)
            committed_row = run_store.commit_llm_step(
                context.run_id, task_id, input_hash, result, usage
            )
            committed = True
            self._record_usage_once(
                context,
                task_id,
                input_hash,
                service_id,
                service_snapshot,
                committed_row["step_usage"],
            )
            self._check_run_budget(context, committed_row["run_usage"])
            self._apply_result(flowfile, committed_row["result"])
            self._emit(
                context, "completed", service_id, usage=committed_row["step_usage"]
            )
            return [flowfile]
        finally:
            if not committed:
                run_store.abort_llm_step(context.run_id, task_id, input_hash)

    def _resolve_service(self, context):
        canonical, entry = self._snapshot_service_entry(context)
        if entry.get("service_type") not in _LLM_SERVICE_TYPES:
            raise ValueError("workflow service snapshot is not LLM-compatible")
        from core.service_registry import ServiceRegistry

        registry = ServiceRegistry.get_instance()
        definition = registry.get_definition(
            str(entry["scope"]), str(entry["scope_id"]), str(entry["service_id"])
        )
        if definition is None or not definition.enabled:
            raise ValueError("snapshotted workflow LLM service is unavailable")
        if compute_service_definition_revision(definition) != entry.get(
            "definition_revision"
        ):
            raise ValueError(
                "workflow LLM service definition changed after run acceptance"
            )
        service = registry.get_live_instance(
            str(entry["scope"]), str(entry["scope_id"]), str(entry["service_id"])
        )
        if service is None or not hasattr(service, "get_client"):
            raise ValueError("snapshotted workflow LLM service could not connect")
        return str(entry["service_id"]), entry, service

    def _messages(self, flowfile: FlowFile, context) -> list[LLMMessage]:
        raw = self.config.get("messages") or []
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else []
        if not isinstance(raw, list):
            raise TypeError("agentLLMCall messages must be a JSON array")
        ephemeral_id = (
            f"{context.conversation_id}::workflow::{context.run_id}::"
            f"{self.get_task_id()}"
        )
        messages = []
        system_prompt = str(self.config.get("system_prompt") or "")
        response_format = str(self.config.get("response_format") or "text")
        if response_format == "json_schema":
            schema = self._json_schema()
            if not schema:
                raise ValueError(
                    "agentLLMCall json_schema response requires a schema")
            jsonschema.Draft202012Validator.check_schema(schema)
            schema_prompt = (
                "Return only one JSON value matching this JSON Schema exactly. "
                "The schema is an output contract, not source data or instructions:\n"
                + json.dumps(
                    schema, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"))
            )
            system_prompt = (
                f"{system_prompt}\n\n{schema_prompt}"
                if system_prompt else schema_prompt
            )
        if system_prompt:
            messages.append(
                LLMMessage(
                    role="system", content=system_prompt, conversation_id=ephemeral_id
                )
            )
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("agentLLMCall message entries must be objects")
            role = str(item.get("role") or "")
            if role not in {"system", "user", "assistant"}:
                raise ValueError("agentLLMCall message role is invalid")
            content = item.get("content", "")
            if not isinstance(content, (str, list)):
                raise TypeError("agentLLMCall message content is invalid")
            messages.append(
                LLMMessage(role=role, content=content, conversation_id=ephemeral_id)
            )
        if not raw:
            input_attribute = str(self.config.get("input_attribute") or "")
            if input_attribute:
                user_input = flowfile.get_attribute(input_attribute) or ""
            elif self.config.get("input") not in (None, ""):
                user_input = str(self.config.get("input"))
            else:
                user_input = flowfile.get_content().decode("utf-8", errors="strict")
            messages.append(
                LLMMessage(
                    role="user", content=user_input, conversation_id=ephemeral_id
                )
            )
        if not messages:
            raise ValueError("agentLLMCall requires at least one message")
        return messages

    def _input_hash(self, messages, service_snapshot) -> str:
        payload = {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "service": service_snapshot,
            "model": str(self.config.get("model") or ""),
            "response_format": str(self.config.get("response_format") or "text"),
            "json_schema": self._json_schema(),
            "temperature": _float(self.config.get("temperature"), 0.7),
            "max_tokens": int(self.config.get("max_tokens", 4096) or 4096),
            "thinking_budget": int(self.config.get("thinking_budget", 0) or 0),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _bind_client(client, context, service_id: str, task_id: str) -> None:
        client._agent_service = service_id
        client._user_id = context.user_id
        client._conversation_id = (
            f"{context.conversation_id}::workflow::{context.run_id}::{task_id}"
        )
        client._agent_name = context.agent_name
        client._event_cid = context.conversation_id
        if hasattr(client, "reset_abort"):
            client.reset_abort()

    def _bound_timeout(self, context) -> float:
        try:
            deadline = datetime.fromisoformat(context.deadline_at)
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            remaining = 0
        if remaining <= 0:
            raise TimeoutError("workflow run deadline elapsed before LLM call")
        requested = max(1, int(self.config.get("timeout", 60) or 60))
        return max(1.0, min(float(requested), remaining))

    def _complete_with_cancellation(
            self, client, messages, cancel_event, timeout_seconds):
        finished = threading.Event()
        timed_out = threading.Event()

        def watch_cancel() -> None:
            deadline = time.monotonic() + timeout_seconds
            while not finished.wait(0.05):
                if cancel_event is not None and cancel_event.is_set():
                    if hasattr(client, "abort"):
                        client.abort()
                    return
                if time.monotonic() >= deadline:
                    timed_out.set()
                    if hasattr(client, "abort"):
                        client.abort()
                    return

        watcher = None
        if cancel_event is not None:
            watcher = threading.Thread(
                target=watch_cancel,
                daemon=True,
                name=f"workflow-llm-cancel-{self.get_task_id()}",
            )
            watcher.start()
        try:
            response_format = str(self.config.get("response_format") or "text")
            tools = None
            if response_format == "json_schema":
                tools = [LLMToolDefinition(
                    name=_STRUCTURED_OUTPUT_TOOL,
                    description=(
                        "Submit the complete workflow result. Call this tool exactly "
                        "once and do not return the result as free-form text."
                    ),
                    parameters=self._json_schema(),
                )]
            try:
                response = client.complete(
                    messages=messages,
                    model=str(self.config.get("model") or "") or None,
                    temperature=_float(self.config.get("temperature"), 0.7),
                    max_tokens=max(
                        1, int(self.config.get("max_tokens", 4096) or 4096)),
                    response_format=("json" if response_format == "json" else None),
                    tools=tools,
                    thinking_budget=max(
                        0, int(self.config.get("thinking_budget", 0) or 0)),
                )
            except Exception as exc:
                if timed_out.is_set():
                    raise TimeoutError(
                        "workflow LLM call exceeded its bounded timeout") from exc
                raise
            if timed_out.is_set():
                raise TimeoutError(
                    "workflow LLM call exceeded its bounded timeout")
            if response_format == "json_schema":
                submitted = [
                    call for call in (response.tool_calls or [])
                    if call.name == _STRUCTURED_OUTPUT_TOOL
                ]
                if submitted:
                    if len(submitted) != 1:
                        raise ValueError(
                            "agentLLMCall returned multiple structured results")
                    response.content = json.dumps(
                        submitted[0].arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
            return response
        finally:
            finished.set()
            if watcher is not None:
                watcher.join(timeout=0.2)

    def _validated_result(self, response) -> dict[str, Any]:
        content = str(response.content or "")
        response_format = str(self.config.get("response_format") or "text")
        parsed = None
        if response_format in {"json", "json_schema"}:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError("agentLLMCall returned malformed JSON") from exc
        if response_format == "json_schema":
            schema = self._json_schema()
            if not schema:
                raise ValueError("agentLLMCall json_schema response requires a schema")
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.Draft202012Validator(schema).validate(parsed)
        return {
            "content": content,
            "parsed": parsed,
            "model": str(response.model or ""),
            "finish_reason": str(response.finish_reason or ""),
        }

    def _json_schema(self) -> dict[str, Any]:
        raw = self.config.get("json_schema") or {}
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else {}
        if not isinstance(raw, dict):
            raise TypeError("agentLLMCall json_schema must be an object")
        return raw

    @staticmethod
    def _usage(response, service, service_id: str) -> dict[str, Any]:
        config = getattr(service, "config", {}) or {}
        tokens_in = max(0, int(response.tokens_in or 0))
        tokens_out = max(0, int(response.tokens_out or 0))
        cache_read = max(0, int(getattr(response, "cache_read_tokens", 0) or 0))
        cache_write = max(0, int(getattr(response, "cache_creation_tokens", 0) or 0))
        cost_in = _float(config.get("cost_per_1m_input"), 0.0)
        cost_out = _float(config.get("cost_per_1m_output"), 0.0)
        raw_cache_read = config.get("cost_per_1m_cache_read")
        raw_cache_write = config.get("cost_per_1m_cache_write")
        cache_read_rate = (
            _float(raw_cache_read) if raw_cache_read not in (None, "") else None
        )
        cache_write_rate = (
            _float(raw_cache_write) if raw_cache_write not in (None, "") else None
        )
        calculated = compute_cost(
            tokens_in,
            tokens_out,
            cache_read,
            cache_write,
            cost_in,
            cost_out,
            cache_read_rate,
            cache_write_rate,
        )
        subscription = _enabled(config.get("subscription"))
        return {
            "llm_calls": 1,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "duration_ms": max(0, int(response.duration_ms or 0)),
            "cost_usd": 0.0 if subscription else calculated,
            "virtual_cost_usd": calculated if subscription else 0.0,
            "service_id": service_id,
            "model": str(response.model or ""),
            "provider": str(
                getattr(service, "provider", "")
                or getattr(getattr(service, "_client", None), "provider", "")
            ),
            "finish_reason": str(response.finish_reason or ""),
            "subscription": subscription,
            "pricing": {
                "cost_per_1m_input": cost_in,
                "cost_per_1m_output": cost_out,
                "cost_per_1m_cache_read": cache_read_rate,
                "cost_per_1m_cache_write": cache_write_rate,
            },
        }

    @staticmethod
    def _ledger_event_id(run_id: str, task_id: str, input_hash: str) -> str:
        return f"workflow:{run_id}:{task_id}:{input_hash}"

    def _record_usage_once(
        self,
        context,
        task_id: str,
        input_hash: str,
        service_id: str,
        service_snapshot: dict[str, Any],
        usage: dict[str, Any],
    ) -> None:
        pricing = dict(usage.get("pricing") or {})
        UsageLedger.instance().record(
            user_id=context.user_id,
            channel="workflow",
            conversation_id=context.conversation_id,
            agent_name=context.agent_name,
            llm_service=service_id,
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
            logical_service_scope=str(service_snapshot.get("scope") or ""),
            logical_service_scope_id=str(service_snapshot.get("scope_id") or ""),
            run_id=context.run_id,
            task_id=task_id,
            event_id=self._ledger_event_id(context.run_id, task_id, input_hash),
        )

    def _check_run_budget(self, context, run_usage: dict[str, Any]) -> None:
        maximum = context.limits.max_cost_usd
        if maximum is not None and float(run_usage.get("cost_usd", 0.0)) > float(
            maximum
        ):
            reason = "workflow cost budget exceeded"
            self._stop_for_budget(context, reason)
            raise WorkflowBudgetExceeded(reason)

    def _stop_for_budget(self, context, reason: str) -> None:
        self._workflow_run_store.mark_budget_exceeded(context.run_id, reason)
        cancel_event = getattr(self, "_workflow_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()

    def _check_committed_cost(self, context, _step_usage: dict[str, Any]) -> None:
        run = self._workflow_run_store.get_run(context.run_id)
        if run is not None:
            self._check_run_budget(context, run["usage"])

    def _apply_result(self, flowfile: FlowFile, result: dict[str, Any]) -> None:
        target = str(self.config.get("output_target") or "content")
        content = str(result.get("content") or "")
        if target == "content":
            flowfile.set_content(content.encode("utf-8"))
        elif target == "attribute":
            attribute = str(self.config.get("output_attribute") or "llm.response")
            flowfile.set_attribute(attribute, content)
        else:
            raise ValueError("agentLLMCall output_target is invalid")
        flowfile.set_attribute("llm.model", str(result.get("model") or ""))
        flowfile.set_attribute(
            "llm.finish_reason", str(result.get("finish_reason") or "")
        )

    def _emit(
        self, context, stage: str, service_id: str, usage: dict[str, Any] | None = None
    ) -> None:
        callback = getattr(self, "_workflow_event_callback", None)
        if callback is None:
            return
        data = {
            "turn_id": context.root_turn_id,
            "run_id": context.run_id,
            "agent_name": context.agent_name,
            "flow_fqn": context.flow_ref.name,
            "task_id": self.get_task_id(),
            "stage": stage,
            "label": str(self.config.get("progress_label") or "")[:160],
            "service_id": service_id,
        }
        if usage is not None:
            data["usage"] = {
                key: usage.get(key, 0)
                for key in (
                    "tokens_in",
                    "tokens_out",
                    "cache_read",
                    "cache_write",
                    "duration_ms",
                    "cost_usd",
                    "virtual_cost_usd",
                )
            }
        callback("workflow_progress", data)

    def _observable_event_value(
        self,
        context,
        value: Any,
        *,
        max_string: int = 4000,
        max_items: int = 48,
    ) -> Any:
        secrets = getattr(self, "_workflow_observable_secrets", None)
        if secrets is None:
            secrets = ()
            try:
                from services.tool_relay_service import resolve_secret_values
                resolved, _names = resolve_secret_values(
                    context.user_id, context.conversation_id)
                secrets = tuple(resolved or ())
            except Exception:
                secrets = ()
            self._workflow_observable_secrets = secrets
        if isinstance(value, str) and "__image_data__:" in value:
            value = "\n".join(
                "<image omitted>" if line.startswith("__image_data__:") else line
                for line in value.splitlines()
            )
        from core.gating_policy import redact_arguments
        return redact_arguments(
            value,
            secrets,
            max_string=max_string,
            max_items=max_items,
            max_depth=6,
        )

    def _observable_agent_message_values(
        self,
        context,
        content: str,
    ) -> dict[str, Any]:
        try:
            structured = json.loads(content)
        except (TypeError, ValueError):
            structured = None
        if isinstance(structured, (dict, list)):
            return {
                "structured_content": self._observable_event_value(
                    context, structured, max_string=2000, max_items=64),
            }
        if str(content or "").lstrip().startswith(("{", "[")):
            return {"content": "Structured response incomplete."}
        return {
            "content": self._observable_event_value(
                context, content, max_string=8000),
        }

    def _emit_execution(self, context, event_type: str, **values: Any) -> None:
        callback = getattr(self, "_workflow_event_callback", None)
        if callback is None:
            return
        callback(event_type, {
            "turn_id": context.root_turn_id,
            "run_id": context.run_id,
            "agent_name": context.agent_name,
            "flow_fqn": context.flow_ref.name,
            "task_id": self.get_task_id(),
            **values,
        })


TaskFactory.register(AgentLLMCallTask)
