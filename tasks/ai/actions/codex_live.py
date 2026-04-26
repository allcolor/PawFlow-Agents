"""AgentLoopTask actions — Codex live-container registry ops.

Mirror of cc_live.py for the codex CLI:
  - codex_restart       kill all live codex containers for a conv
                        (or a specific agent), forcing a fresh spawn next turn.
  - codex_live_status   return a snapshot of live containers for the conv.

Independent file from cc_live.py by design — see memory "Separate pools
per CLI".
"""

import json
import logging
from typing import Optional, List

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_codex_live(self, action, body, store, user_id, flowfile):
    """Dispatch codex_restart / codex_live_status actions. Returns [flowfile] or None."""
    if action not in ("codex_restart", "codex_live_status"):
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

    from core.codex_live_registry import CodexLiveRegistry
    reg = CodexLiveRegistry.instance()

    if action == "codex_live_status":
        entries = [
            e for e in reg.status()
            if e.get("conv_id") == conv_id
            and (not agent_name or e.get("agent_name") == agent_name)
        ]
        flowfile.set_content(json.dumps({
            "action": "codex_live_status",
            "conversation_id": conv_id,
            "agent_name": agent_name or None,
            "sessions": entries,
            "count": len(entries),
        }).encode())
        flowfile.set_attribute("http.response.content-type",
                                "application/json")
        return [flowfile]

    # action == "codex_restart": kill + evict
    if agent_name:
        n = reg.kill_and_evict_by_conv_agent(
            conv_id, agent_name, reason="codex_restart_command")
    else:
        n = reg.kill_and_evict_by_conv(
            conv_id, reason="codex_restart_command")

    logger.info(
        "[codex-live] /codex_restart killed %d container(s) for %s%s",
        n, conv_id[:8], f"/{agent_name}" if agent_name else "")

    flowfile.set_content(json.dumps({
        "action": "codex_restart",
        "conversation_id": conv_id,
        "agent_name": agent_name or None,
        "killed": n,
    }).encode())
    flowfile.set_attribute("http.response.content-type",
                            "application/json")
    return [flowfile]
