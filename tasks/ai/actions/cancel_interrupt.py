"""AgentLoopTask actions — cancel interrupt"""

import json
import logging
import time
import threading

from core.task_lifecycle import cleanup_agent_task_context

logger = logging.getLogger(__name__)


def _kill_live_cli_sessions(conv_id: str, agent_name: str, reason: str) -> int:
    """Force-kill live CLI provider containers for a conversation/agent.

    The Claude Code *interactive* pool is deliberately excluded. Its
    container is a persistent tmux session that also holds the agent's
    OAuth credentials and reloads the full PawFlow context on cold
    start. A force stop must only soft-interrupt that session (key
    injection via LLMClient.abort -> cancel_claude_code_interactive),
    never destroy the container. Compaction and the idle sweeper still
    evict it through the pool's own kill_and_evict / sweep paths.
    """
    total = 0
    for module_name, class_name in (
        ("core.cc_live_registry", "LiveSessionRegistry"),
        ("core.codex_live_registry", "CodexLiveRegistry"),
        ("core.gemini_live_registry", "GeminiLiveRegistry"),
    ):
        try:
            mod = __import__(module_name, fromlist=[class_name])
            reg = getattr(mod, class_name).instance()
            if agent_name:
                total += reg.kill_and_evict_by_conv_agent(conv_id, agent_name, reason)
            else:
                total += reg.kill_and_evict_by_conv(conv_id, reason)
        except Exception:
            logger.debug("force-stop live CLI kill failed for %s", module_name,
                         exc_info=True)
    return total


def _clear_force_stop_relaunch_state(conv_id: str, agent_name: str,
                                     store=None) -> None:
    """Remove queued replay state so force stop cannot relaunch itself."""
    if not conv_id:
        return
    try:
        if store is None:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
    except Exception:
        store = None
    agents = set()
    stopped_at = time.time()
    if agent_name:
        agents.add(agent_name.lower())
    elif store is not None:
        try:
            active = store.get_extra(conv_id, "active_resources") or {}
            active_agent = (active.get("agent", "") or "").lower()
            if active_agent:
                agents.add(active_agent)
        except Exception:
            logger.debug("force-stop active agent lookup failed", exc_info=True)
        if not agents:
            try:
                conv_dir = store._conv_dir(conv_id)
                for sub in conv_dir.iterdir():
                    if sub.is_dir() and (sub / "pending.jsonl").exists():
                        if sub.name != "_shared":
                            agents.add(sub.name.lower())
            except Exception:
                logger.debug("force-stop pending queue scan failed", exc_info=True)
    try:
        from core.pending_queue import PendingQueue
        for agent in sorted(agents):
            PendingQueue.for_agent(conv_id, agent).clear("force_stop")
    except Exception:
        logger.debug("force-stop pending queue cleanup failed", exc_info=True)
    try:
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().cancel_for_conversation(
            conv_id,
            key_prefixes=[f"{conv_id}::pending::"],
            reason_prefixes=["[pending]"],
        )
    except Exception:
        logger.debug("force-stop schedule cleanup failed", exc_info=True)
    try:
        if store is not None:
            store.set_extra(conv_id, "last_force_stop_at", stopped_at)
            for agent in sorted(agents):
                store.set_extra(conv_id, f"last_force_stop_at:{agent}", stopped_at)
                store.set_extra(conv_id, f"cancel_checkpoint:{agent}", None)
    except Exception:
        logger.debug("force-stop cancel checkpoint cleanup failed", exc_info=True)


def _clear_force_stop_runtime_state(executor, conv_id: str,
                                    agent_name: str = "") -> None:
    """Remove stale in-memory active markers after a force stop.

    The Python worker thread may remain alive briefly while its provider is
    being killed and its finally block runs. Once the user has force-stopped the
    turn, those stale markers must not make the next user message look like a
    live preempt/queued message.
    """
    if not executor or not conv_id:
        return
    agent_l = (agent_name or "").lower()

    def _matches(key: str) -> bool:
        if "::task::" in key or "::task_verify::" in key:
            return False
        if key != conv_id and not key.startswith(conv_id + ":"):
            return False
        if not agent_l:
            return True
        key_agent = key.split(":", 1)[1].lower() if ":" in key else ""
        return key_agent == agent_l

    try:
        with executor._active_lock:
            executor._active_conversations.pop(conv_id, None)
            executor._user_active_conversations.discard(conv_id)
    except Exception:
        logger.debug("force-stop active counter cleanup failed", exc_info=True)

    try:
        with executor._active_contexts_lock:
            for mapping_name in (
                "_active_contexts", "_active_turns", "_active_claude_client"):
                mapping = getattr(executor, mapping_name, None)
                if not isinstance(mapping, dict):
                    continue
                for key in list(mapping):
                    if _matches(str(key)):
                        mapping.pop(key, None)
    except Exception:
        logger.debug("force-stop runtime marker cleanup failed", exc_info=True)


def _handle_cancel_interrupt(self, action, body, store, user_id, flowfile):
    """Handle cancel interrupt actions. Returns [flowfile] or None."""
    if action in ("cancel_agent", "force_stop"):
        action = "cancel"
    elif action == "broadcast":
        action = "broadcast_agents"

    # Resolve to the execution instance — self may be the actions-only
    # dispatcher (AgentActionsTask) which has its own empty state dicts.
    from tasks.ai.agent_loop import AgentLoopTask
    _exec = AgentLoopTask._live_instance or self


    if action == "cancel":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        task_id = body.get("task_id", "")
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # Task-targeted cancel: stop a specific task agent
        if task_id:
            _task_cid = f"{conv_id}::task::{task_id}"

            # 1. Mark task as cancelled in store — prevents poller reschedule
            try:
                _all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
                if task_id in _all_tasks:
                    _all_tasks[task_id]["status"] = "cancelled"
                    store.set_extra(conv_id, "agent_tasks", _all_tasks)
                    logger.info(f"[agent:{conv_id[:8]}] task {task_id} marked cancelled in store")
            except Exception as _e:
                logger.warning(f"[agent:{conv_id[:8]}] failed to mark task cancelled: {_e}")

            # 2. Bump generation — tells running thread to stop
            #    cancel_agent without agent_name bumps the gen_key = _task_cid
            #    which matches the poller's _thought_gen_key = entry_key
            _exec.cancel_agent(_task_cid, agent_name="", silent=True)
            if agent_name:
                # Also bump the agent-specific key just in case
                _exec.cancel_agent(_task_cid, agent_name=agent_name, silent=True)

            try:
                from services.tool_relay_service import ToolRelayService
                ToolRelayService.cancel_agent(_task_cid, agent_name)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            _live_killed = _kill_live_cli_sessions(
                _task_cid, agent_name, "force_stop")
            if _live_killed:
                logger.info("[agent:%s] force-stopped %d live CLI container(s)",
                            conv_id[:8], _live_killed)

            # 3. Kill task's Claude Code subprocess
            with _exec._active_contexts_lock:
                _cc_keys = [k for k in _exec._active_claude_client
                            if f"::task::{task_id}" in k]
                _cc_clients = [(k, _exec._active_claude_client.get(k)) for k in _cc_keys]
            for _cc_key, client in _cc_clients:
                if client and hasattr(client, 'cancel_claude_code'):
                    client.cancel_claude_code(force=True)
                if client and hasattr(client, 'abort'):
                    client.abort()

            # 3b. Mark the SubAgentExecutor task as cancelled so its
            #     iteration loop breaks at the next check.
            try:
                from core.agent_executor import cancel_sub_agent_task
                cancel_sub_agent_task(task_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            # 4. Clear task's active context + active_thoughts
            with _exec._active_contexts_lock:
                for k in list(_exec._active_contexts):
                    if f"::task::{task_id}" in k:
                        del _exec._active_contexts[k]
            with _exec._active_lock:
                _exec._active_thoughts.discard(_task_cid)
                _exec._active_thoughts.discard(f"{conv_id}::task_verify::{task_id}")

            # 5. Cancel the scheduled task in the poller
            try:
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().cancel(_task_cid)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            cleanup_agent_task_context(
                conv_id, task_id, agent_name, store, clear_runtime=True,
                reason="task_force_cancel")

            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conv_id, "task_stopped", {
                    "task_id": task_id,
                    "agent_name": agent_name or "",
                    "force": True,
                })
            logger.info(f"[agent:{conv_id[:8]}] task {task_id} FORCE STOPPED")
            flowfile.set_content(json.dumps({
                "cancelled": True, "conversation_id": conv_id,
                "task_id": task_id, "agent_name": agent_name or "",
            }).encode())
            return [flowfile]

        _exec.cancel_agent(conv_id, agent_name=agent_name)
        _clear_force_stop_relaunch_state(conv_id, agent_name, store)
        # Cancel in-flight tool calls for this agent
        try:
            from services.tool_relay_service import ToolRelayService
            ToolRelayService.cancel_agent(conv_id, agent_name)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        _live_killed = _kill_live_cli_sessions(conv_id, agent_name, "force_stop")
        if _live_killed:
            logger.info("[agent:%s] force-stopped %d live CLI container(s)",
                        conv_id[:8], _live_killed)

        # Kill Claude Code subprocess (check keyed entries)
        with _exec._active_contexts_lock:
            _cc_keys = [f"{conv_id}:{agent_name}"] if agent_name else \
                [k for k in _exec._active_claude_client if (k == conv_id or k.startswith(conv_id + ":")) and "::task::" not in k and "::task_verify::" not in k]
            _cc_clients = [(k, _exec._active_claude_client.get(k)) for k in _cc_keys]
        for _cc_key, client in _cc_clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=True)
            if client and hasattr(client, 'abort'):
                client.abort()
        # Kill the thread and force UI cleanup
        _killed = 0
        for t in threading.enumerate():
            if t.is_alive() and (t.name == f"agent-stream-{conv_id}" or
                    t.name.startswith(f"agent-stream-{conv_id}:")):
                _killed += 1
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conv_id, "done", {
                "response": "[Force stopped by user]",
                "agent_name": agent_name or "",
                "force_stopped": True,
            })
        # Clear active tracking so the next user message starts a fresh turn,
        # even if the killed worker thread is still unwinding.
        _clear_force_stop_runtime_state(_exec, conv_id, agent_name)
        logger.info(f"[agent:{conv_id[:8]}] FORCE STOPPED ({_killed} thread(s))")
        flowfile.set_content(json.dumps({
            "cancelled": True, "conversation_id": conv_id,
            "agent_name": agent_name or "all",
        }).encode())
        return [flowfile]

    if action == "cancel_sub_agent":
        task_id = body.get("task_id", "")
        if not task_id:
            flowfile.set_content(json.dumps({"error": "Missing task_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.agent_executor import cancel_sub_agent_task
        cancel_sub_agent_task(task_id)
        logger.info("[cancel_sub_agent] task %s marked for cancellation", task_id)
        flowfile.set_content(json.dumps({
            "cancelled": True, "task_id": task_id,
        }).encode())
        return [flowfile]

    if action == "interrupt":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        task_id = body.get("task_id", "")
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Task-targeted interrupt: use task's conversation ID
        _target_cid = f"{conv_id}::task::{task_id}" if task_id else conv_id
        _exec.interrupt_agent(_target_cid, agent_name)
        flowfile.set_content(json.dumps({
            "interrupted": True, "conversation_id": conv_id,
            "agent_name": agent_name or "",
            **({"task_id": task_id} if task_id else {}),
        }).encode())
        return [flowfile]

    if action == "btw":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        question = body.get("question", "") or body.get("message", "")
        if agent_name and agent_name.upper() != "ALL":
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id or not question:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or message"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        user_id = flowfile.get_attribute("http.auth.principal") or ""
        # Handle ALL — spawn btw for each agent + default
        if agent_name.upper() == "ALL":
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            all_agents = rs.list_all("agent", user_id, conversation_id=conv_id)
            targets = [a["name"] for a in all_agents]
            for t in targets:
                thread = threading.Thread(
                    target=self._btw_query,
                    args=(conv_id, t, question, user_id),
                    daemon=True,
                    name=f"btw-{t}-{conv_id[:8]}",
                )
                thread.start()
        else:
            thread = threading.Thread(
                target=self._btw_query,
                args=(conv_id, agent_name, question, user_id),
                daemon=True,
                name=f"btw-{agent_name or 'agent'}-{conv_id[:8]}",
            )
            thread.start()
        flowfile.set_content(json.dumps({
            "ok": True, "conversation_id": conv_id,
        }).encode())
        return [flowfile]

    if action == "broadcast_agents":
        # Send the same message to ALL defined agents in parallel
        conv_id = body.get("conversation_id", "")
        message = body.get("message", "")
        if not conv_id or not message:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and message are required",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Launch broadcast in background thread
        thread = threading.Thread(
            target=self._broadcast_agents,
            args=(conv_id, message, user_id),
            daemon=True,
            name=f"broadcast-{conv_id[:8]}",
        )
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "broadcasting",
            "conversation_id": conv_id,
        }).encode())
        return [flowfile]

    return None
