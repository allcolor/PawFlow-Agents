"""AgentLoopTask actions — Claude Code live-session registry ops.

Handles:
  - cc_restart       kill all live CC subprocesses for a conv (or a
                     specific agent), forcing a fresh spawn on next turn.
  - cc_live_status   return a snapshot of live sessions for the conv.
"""

import json
import logging
from typing import Optional, List

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_cc_live(self, action, body, store, user_id, flowfile):
    """Dispatch cc_restart / cc_live_status actions. Returns [flowfile] or None."""
    if action not in ("cc_restart", "cc_live_status"):
        return None

    conv_id = body.get("conversation_id", "")
    agent_name = body.get("agent_name", "") or ""
    if not conv_id:
        flowfile.set_content(
            json.dumps({"error": "Missing conversation_id"}).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if agent_name:
        agent_name = self._resolve_agent_name(agent_name, conv_id)

    from core.cc_live_registry import LiveSessionRegistry
    reg = LiveSessionRegistry.instance()

    if action == "cc_live_status":
        entries = [
            e for e in reg.status()
            if e.get("conv_id") == conv_id
            and (not agent_name or e.get("agent_name") == agent_name)
        ]
        flowfile.set_content(json.dumps({
            "action": "cc_live_status",
            "conversation_id": conv_id,
            "agent_name": agent_name or None,
            "sessions": entries,
            "count": len(entries),
        }).encode())
        flowfile.set_attribute("http.response.content-type",
                                "application/json")
        return [flowfile]

    # action == "cc_restart": kill + evict
    # Use the CC provider's _kill_cc_hard when available so pgid/
    # container semantics stay consistent with the rest of the cleanup
    # machinery. Falls back to default teardown (terminate/wait/kill)
    # if no suitable killer is reachable.
    killer = _find_cc_killer(self)
    if agent_name:
        n = reg.kill_and_evict_by_conv_agent(
            conv_id, agent_name,
            reason="cc_restart_command", killer=killer)
    else:
        n = reg.kill_and_evict_by_conv(
            conv_id, reason="cc_restart_command", killer=killer)

    logger.info(
        "[cc-live] /cc_restart killed %d session(s) for %s%s",
        n, conv_id[:8], f"/{agent_name}" if agent_name else "")

    flowfile.set_content(json.dumps({
        "action": "cc_restart",
        "conversation_id": conv_id,
        "agent_name": agent_name or None,
        "killed": n,
    }).encode())
    flowfile.set_attribute("http.response.content-type",
                            "application/json")
    return [flowfile]


def _find_cc_killer(self):
    """Resolve a `_kill_cc_hard(proc)` callable from a live CC client.

    Looks at the agent's active Claude Code clients in the current
    execution instance. Returns None if none found — the registry
    falls back to a generic terminate/wait/kill sequence in that case.
    """
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        _exec = AgentLoopTask._live_instance or self
        if not hasattr(_exec, "_active_claude_client"):
            return None
        with _exec._active_contexts_lock:
            clients = list(_exec._active_claude_client.values())
        for client in clients:
            if client and hasattr(client, "_kill_cc_hard"):
                return client._kill_cc_hard
    except Exception:
        logger.debug("failed to find cc killer", exc_info=True)
    return None
