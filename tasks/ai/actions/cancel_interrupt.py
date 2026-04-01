"""AgentLoopTask actions — cancel interrupt"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_cancel_interrupt(self, action, body, store, user_id, flowfile):
    """Handle cancel interrupt actions. Returns [flowfile] or None."""


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
            self.cancel_agent(_task_cid, agent_name="", silent=True)
            if agent_name:
                # Also bump the agent-specific key just in case
                self.cancel_agent(_task_cid, agent_name=agent_name, silent=True)

            try:
                from services.tool_relay_service import ToolRelayService
                ToolRelayService.cancel_agent(_task_cid, agent_name)
            except Exception:
                pass

            # 3. Kill task's Claude Code subprocess
            with self._active_contexts_lock:
                _cc_keys = [k for k in self._active_claude_client
                            if f"::task::{task_id}" in k]
                _cc_clients = [(k, self._active_claude_client.get(k)) for k in _cc_keys]
            for _cc_key, client in _cc_clients:
                if client and hasattr(client, 'cancel_claude_code'):
                    client.cancel_claude_code(force=True)

            # 4. Clear task's active context + active_thoughts
            with self._active_contexts_lock:
                for k in list(self._active_contexts):
                    if f"::task::{task_id}" in k:
                        del self._active_contexts[k]
            with self._active_lock:
                self._active_thoughts.discard(_task_cid)
                self._active_thoughts.discard(f"{conv_id}::task_verify::{task_id}")

            # 5. Cancel the scheduled task in the poller
            try:
                from engine.continuous_executor import PollScheduler
                PollScheduler.instance().cancel(_task_cid)
            except Exception:
                pass

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

        self.cancel_agent(conv_id, agent_name=agent_name)
        # Cancel in-flight tool calls for this agent
        try:
            from services.tool_relay_service import ToolRelayService
            ToolRelayService.cancel_agent(conv_id, agent_name)
        except Exception:
            pass
        # Kill Claude Code subprocess (check keyed entries)
        with self._active_contexts_lock:
            _cc_keys = [f"{conv_id}:{agent_name}"] if agent_name else \
                [k for k in self._active_claude_client if (k == conv_id or k.startswith(conv_id + ":")) and "::task::" not in k and "::task_verify::" not in k]
            _cc_clients = [(k, self._active_claude_client.get(k)) for k in _cc_keys]
        for _cc_key, client in _cc_clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=True)
        # Kill the thread and force UI cleanup
        _killed = 0
        for t in threading.enumerate():
            if t.is_alive() and (t.name == f"agent-stream-{conv_id}" or
                    t.name.startswith(f"agent-stream-{conv_id}:")):
                _killed += 1
        store.set_status(conv_id, "idle")
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conv_id, "done", {
                "response": "[Force stopped by user]",
                "agent_name": agent_name or "",
                "force_stopped": True,
            })
        # Clear active tracking
        with self._active_lock:
            self._active_conversations.pop(conv_id, None)
            self._user_active_conversations.discard(conv_id)
        with self._active_contexts_lock:
            for k in list(self._active_contexts):
                if (k == conv_id or k.startswith(conv_id + ":")) and "::task::" not in k and "::task_verify::" not in k:
                    del self._active_contexts[k]
        logger.info(f"[agent:{conv_id[:8]}] FORCE STOPPED ({_killed} thread(s))")
        flowfile.set_content(json.dumps({
            "cancelled": True, "conversation_id": conv_id,
            "agent_name": agent_name or "all",
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
        self.interrupt_agent(_target_cid, agent_name)
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
            all_agents = rs.list_all("agent", user_id)
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
