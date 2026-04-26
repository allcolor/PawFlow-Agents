"""AgentLoopTask actions — Gemini live-container registry ops.

Mirror of cc_live.py for the gemini CLI:
  - gemini_restart       kill all live gemini containers for a conv
                         (or a specific agent), forcing a fresh spawn next turn.
  - gemini_live_status   return a snapshot of live containers for the conv.

Independent file from cc_live.py / codex_live.py by design — see memory
"Separate pools per CLI".
"""

import json
import logging
from typing import Optional, List

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_gemini_live(self, action, body, store, user_id, flowfile):
    """Dispatch gemini_restart / gemini_live_status actions. Returns [flowfile] or None."""
    if action not in ("gemini_restart", "gemini_live_status"):
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

    from core.gemini_live_registry import GeminiLiveRegistry
    reg = GeminiLiveRegistry.instance()

    if action == "gemini_live_status":
        entries = [
            e for e in reg.status()
            if e.get("conv_id") == conv_id
            and (not agent_name or e.get("agent_name") == agent_name)
        ]
        flowfile.set_content(json.dumps({
            "action": "gemini_live_status",
            "conversation_id": conv_id,
            "agent_name": agent_name or None,
            "sessions": entries,
            "count": len(entries),
        }).encode())
        flowfile.set_attribute("http.response.content-type",
                                "application/json")
        return [flowfile]

    # action == "gemini_restart": kill + evict
    if agent_name:
        n = reg.kill_and_evict_by_conv_agent(
            conv_id, agent_name, reason="gemini_restart_command")
    else:
        n = reg.kill_and_evict_by_conv(
            conv_id, reason="gemini_restart_command")

    logger.info(
        "[gemini-live] /gemini_restart killed %d container(s) for %s%s",
        n, conv_id[:8], f"/{agent_name}" if agent_name else "")

    flowfile.set_content(json.dumps({
        "action": "gemini_restart",
        "conversation_id": conv_id,
        "agent_name": agent_name or None,
        "killed": n,
    }).encode())
    flowfile.set_attribute("http.response.content-type",
                            "application/json")
    return [flowfile]
