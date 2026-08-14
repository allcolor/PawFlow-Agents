"""FlashAgentHandler — extracted from resource_agent.py (<=800 lines).

Subclasses SpawnAgentsHandler. Re-exported from core.handlers.resource_agent.
"""

import json
import logging
import re
from typing import Any, Dict

from core.handlers.spawn_agents import SpawnAgentsHandler

logger = logging.getLogger(__name__)


class FlashAgentHandler(SpawnAgentsHandler):
    """Create temporary task-specific agents and delegate to them."""

    @property
    def name(self) -> str:
        return "flash_delegate"

    @property
    def description(self) -> str:
        return (
            "Create temporary flash agents for independent parallel work. "
            "Use this for separable research/audit/checking tasks while you "
            "continue the main work: inspect a different file, search tests, "
            "verify documentation, compare approaches, or gather evidence. "
            "Do NOT use it for tightly coupled edits where one agent must "
            "preserve a single invariant across files. Each flash agent starts "
            "with an empty context, uses the calling agent's configured flash "
            "delegate LLM service (or its current llm_service when unset), "
            "runs asynchronously, and disappears when its "
            "delegated task completes. Include every fact, file path, "
            "constraint, and expected output format it needs in its prompt "
            "and message. Read and integrate returned results; do not ignore "
            "them."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Short temporary agent name chosen by the caller",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "System instructions for this temporary agent. Include the role, scope, constraints, and expected output format because the agent has no prior context.",
                            },
                            "message": {
                                "type": "string",
                                "description": "The concrete task to run. Include relevant paths, snippets, user requirements, and success criteria because the flash agent starts empty.",
                            },
                            "id": {
                                "type": "string",
                                "description": "Optional task ID for tracking",
                            },
                            "tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional tool whitelist for the flash agent. Keep it narrow for audit/search tasks; omit only when the subtask genuinely needs broad tool access.",
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional skills to inject into the flash prompt",
                            },
                        },
                        "required": ["name", "prompt", "message"],
                    },
                    "description": "Temporary agents to create and run in parallel",
                },
            },
            "required": ["tasks"],
        }

    @staticmethod
    def _runtime_name(parent_agent: str, flash_name: str) -> str:
        parent = re.sub(r"[^A-Za-z0-9_.-]+", "_", parent_agent or "agent").strip("_")
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", flash_name or "flash").strip("_")
        if not name:
            name = "flash"
        return f"{parent or 'agent'}::flash::{name}"

    def _resolve_flash_llm_service(self, parent_conv_id: str,
                                   src_agent: str, src_svc: str) -> str:
        """Resolve the optional per-agent service override for flash work.

        Published MCP calls use an external source id for attribution. Their
        configuration still belongs to the conversation agent wired into this
        handler, so consult that agent while preserving the external identity
        on the spawned task.
        """
        config_agent = src_agent
        if src_agent.startswith("published_mcp"):
            config_agent = self._agent_name or src_agent
        try:
            from core.conv_agent_config import get_agent_config
            config = get_agent_config(parent_conv_id, config_agent)
        except Exception:
            logger.debug(
                "[flash-delegate] could not load config for %s/%s",
                parent_conv_id[:8], config_agent, exc_info=True)
            return src_svc
        return str(config.get("flash_delegate_llm_service") or src_svc).strip()

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import AgentTask, SubAgentExecutor
        from core.handlers._arg_normalize import validate_object_list, normalize_string_list
        from core.agent_prompt_policy import inject_common_agent_system_prompt
        import uuid

        tasks_spec, err = validate_object_list(
            arguments.get("tasks"),
            param_name="tasks",
            required_keys=["name", "prompt", "message"],
            example=('tasks=[{"name": "critic", "prompt": "<role>", '
                     '"message": "<task>", "id"?: "<optional>"}, ...]'),
        )
        if err:
            return f"Error: {err}"

        user_id = self._user_id
        raw_conv_id = self._conversation_id
        from core.service_registry import _parent_conversation_id
        parent_conv_id = _parent_conversation_id(raw_conv_id) or raw_conv_id
        source_task_id = (raw_conv_id.split("::task::", 1)[1]
                          if "::task::" in raw_conv_id else "")

        src_agent, src_svc = self._resolve_source_context()
        flash_svc = self._resolve_flash_llm_service(
            parent_conv_id, src_agent, src_svc)
        delegate_tc_id = getattr(self._local, 'delegate_tc_id', '') or ''
        if not src_agent:
            return (
                "Error: flash_delegate could not determine the calling"
                " agent: no thread-local source context and no agent"
                " instance name is configured for this conversation."
            )
        if not flash_svc:
            return (
                "Error: flash_delegate could not resolve an llm_service for"
                f" source agent '{src_agent}'. Set the agent's llm_service or"
                " flash_delegate_llm_service in the conversation agent config"
                " (conv_agents)."
            )

        from core.agent_executor import get_live_delegate, queue_live_delegate_message
        agent_tasks = []
        injected_results = []
        seen_runtime_names = set()
        for spec in tasks_spec:
            flash_name = str(spec.get("name", "")).strip()
            prompt = str(spec.get("prompt", "")).strip()
            message = str(spec.get("message", ""))
            if not flash_name or not prompt or not message:
                return "Error: each flash task requires non-empty name, prompt, and message"
            runtime_name = self._runtime_name(src_agent, flash_name)
            if runtime_name in seen_runtime_names:
                return f"Error: duplicate flash agent name '{flash_name}' in one call"
            seen_runtime_names.add(runtime_name)
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            if parent_conv_id:
                live = get_live_delegate(parent_conv_id, src_agent, runtime_name)
                if live:
                    live_client = live.get("client")
                    live_tid = live.get("task_id", "")
                    delivered = False
                    if live_client and hasattr(live_client, "send_user_message"):
                        try:
                            delivered = bool(live_client.send_user_message(message))
                        except Exception as exc:
                            logger.warning(
                                "[flash-delegate] preempt to %s failed: %s",
                                live_tid, exc)
                    if not delivered:
                        queue_live_delegate_message(
                            parent_conv_id, src_agent, runtime_name, message)
                    injected_results.append({
                        "task_id": live_tid,
                        "name": flash_name,
                        "agent": runtime_name,
                        "status": "injected" if delivered else "injected_queued",
                    })
                    continue

            identity = (
                f"[IDENTITY] You are temporary flash agent \"{flash_name}\". "
                f"Runtime id: \"{runtime_name}\". You were created by "
                f"agent \"{src_agent}\" for one delegated task. You start "
                f"with empty context and disappear when this task completes.\n\n"
            )
            system_prompt = inject_common_agent_system_prompt(identity + prompt)
            skills = normalize_string_list(spec.get("skills"))
            if skills:
                from core.skill_resolver import inject_available_skills_into_prompt
                system_prompt = inject_available_skills_into_prompt(
                    system_prompt, skills, user_id,
                    conversation_id=self._conversation_id)
            tools = normalize_string_list(spec.get("tools")) or None
            task = AgentTask(
                id=task_id,
                agent_name=runtime_name,
                message=message,
                system_prompt=system_prompt,
                tools=tools,
                max_iterations=50,
                max_depth=1000,
                timeout=180,
                llm_service=flash_svc,
                user_id=user_id,
                source_agent=src_agent,
                source_llm_service=flash_svc,
                context_mode="isolated",
                parent_conversation_id=parent_conv_id,
                delegate_tc_id=delegate_tc_id,
                persist=False,
                source_task_id=source_task_id,
            )
            agent_tasks.append(task)

        if not agent_tasks:
            if injected_results:
                return json.dumps(injected_results, ensure_ascii=False, indent=2)
            return "Error: no valid flash tasks to spawn."

        if self._on_event and delegate_tc_id:
            self._on_event("delegate_group_start", {
                "delegate_tc_id": delegate_tc_id,
                "source_agent": src_agent,
                "mode": "flash",
                "agents": [
                    {"name": t.agent_name, "task_id": t.id,
                     "message": t.message, "llm_service": t.llm_service}
                    for t in agent_tasks
                ],
                "total": len(agent_tasks),
                "source_task_id": source_task_id,
            })

        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )
        result_conv_id = raw_conv_id
        def _bg_callback(result, task):
            from core.agent_executor import record_finished_delegate
            record_finished_delegate(
                parent_conv_id, src_agent, task.agent_name,
                getattr(result, "task_id", task.id),
                getattr(result, "status", ""),
                getattr(result, "error", ""),
                getattr(result, "duration_ms", 0.0))
            self._inject_bg_result(result, task, result_conv_id, user_id, src_agent)

        results = executor.spawn(agent_tasks, wait=False,
                                 on_bg_complete=_bg_callback)
        spawned = [
            {"task_id": r.task_id, "agent": t.agent_name,
             "name": t.agent_name.split("::flash::", 1)[-1]}
            for r, t in zip(results, agent_tasks)
        ]
        reply = {
            "status": "spawned",
            "flash_agents": spawned,
            "message": (
                f"Spawned {len(spawned)} flash agent(s) in background. "
                "You are not blocked. Read and integrate each delegate "
                "result when it returns."
            ),
        }
        if injected_results:
            reply["injected"] = injected_results
        return json.dumps(reply, ensure_ascii=False)


class FlashStatusHandler(FlashAgentHandler):
    """Report the caller's flash agents: live ones and recent finished ones."""

    _FLASH_MARKER = "::flash::"

    @property
    def name(self) -> str:
        return "flash_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of your flash agents. Returns the live flash "
            "agents you spawned with flash_delegate (name, task_id, age, "
            "queued follow-ups) and the recently finished ones (status, "
            "error, duration). Use it to verify delegated work is actually "
            "running instead of inferring liveness from silence. It reports "
            "status only — results are still delivered asynchronously."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time

        from core.agent_executor import (
            list_finished_delegates, list_live_delegates,
        )
        from core.service_registry import _parent_conversation_id

        raw_conv_id = self._conversation_id
        parent_conv_id = _parent_conversation_id(raw_conv_id) or raw_conv_id
        src_agent, _src_svc = self._resolve_source_context()
        if not src_agent:
            return (
                "Error: flash_status could not determine the calling agent:"
                " no thread-local source context and no agent instance name"
                " is configured for this conversation."
            )

        now = time.time()
        live = []
        for entry in list_live_delegates(parent_conv_id, src_agent):
            target = entry.pop("target")
            if self._FLASH_MARKER not in target:
                continue
            started = entry.pop("started_at", 0.0)
            entry.pop("caller", None)
            entry["name"] = target.split(self._FLASH_MARKER, 1)[-1]
            entry["agent"] = target
            entry["age_seconds"] = round(now - started, 1) if started else None
            live.append(entry)

        finished = []
        for entry in list_finished_delegates(parent_conv_id, src_agent):
            target = entry.pop("target")
            if self._FLASH_MARKER not in target:
                continue
            entry.pop("caller", None)
            entry["name"] = target.split(self._FLASH_MARKER, 1)[-1]
            entry["agent"] = target
            finished.append(entry)

        return json.dumps({
            "live": live,
            "finished": finished,
            "counts": {"live": len(live), "finished": len(finished)},
        }, ensure_ascii=False, indent=2)
