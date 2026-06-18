"""Conversational/proactive action handlers for AgentActionsMixin: telegram
conversation commands, random-thought scheduling, and session clearing.

Split out of agent_actions.py as a leaf mixin so the file stays <= 800 lines.
Methods rely on host state/methods of AgentActionsMixin (AgentLoopTask) via the
MRO.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


class _AgentActionsConvMixin:
    """Telegram conv-command + random-thought handlers for AgentActionsMixin."""

    def _handle_telegram_conv_command(
        self, text: str, tg_user_id: str, flowfile: FlowFile,
    ) -> Optional[List[FlowFile]]:
        """Handle /conv commands from Telegram for cross-channel conversation management.

        Commands:
          /conv list       - list the user's conversations
          /conv select ID  - switch active conversation
          /conv info       - show current active conversation
        """
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        resolved_user = ids.resolve_user("telegram", tg_user_id)
        if not resolved_user:
            flowfile.set_content(
                "Your Telegram account is not linked to a PawFlow user.\n"
                "Use /link telegram YOUR_TG_ID from the web chat to link it."
                .encode("utf-8")
            )
            return [flowfile]

        parts = text.split(maxsplit=2)
        subcmd = parts[1] if len(parts) > 1 else "info"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if subcmd == "list":
            convs = store.list_conversations(user_id=resolved_user)
            active = ids.get_active_conv(resolved_user, "telegram") or ""
            if not convs:
                flowfile.set_content("No conversations found.".encode("utf-8"))
                return [flowfile]
            lines = []
            for c in convs[:20]:  # limit to 20
                cid = c.get("conversation_id", "")
                short_id = cid[:12]
                marker = " *" if cid == active else ""
                msg_count = c.get("message_count", 0)
                lines.append(f"{'>' if cid == active else ' '} {short_id} ({msg_count} msgs){marker}")
            header = f"Your conversations ({len(convs)}):\n"
            footer = "\n\nUse /conv select ID to switch."
            flowfile.set_content((header + "\n".join(lines) + footer).encode("utf-8"))
            return [flowfile]

        if subcmd == "select":
            conv_id_prefix = parts[2].strip() if len(parts) > 2 else ""
            if not conv_id_prefix:
                flowfile.set_content(
                    "Usage: /conv select <conversation_id>".encode("utf-8")
                )
                return [flowfile]
            # Find conversation matching prefix
            convs = store.list_conversations(user_id=resolved_user)
            match = None
            for c in convs:
                cid = c.get("conversation_id", "")
                if cid == conv_id_prefix or cid.startswith(conv_id_prefix):
                    match = cid
                    break
            if not match:
                flowfile.set_content(
                    f"Conversation '{conv_id_prefix}' not found.".encode("utf-8")
                )
                return [flowfile]
            ids.set_active_conv(resolved_user, "telegram", match)
            flowfile.set_content(
                f"Switched to conversation {match[:12]}".encode("utf-8")
            )
            return [flowfile]

        if subcmd == "new":
            try:
                from tasks.io.telegram_agent_client import TelegramAgentClientTask
                new_id, agent_name = (
                    TelegramAgentClientTask._create_conversation_from_command(
                        parts[2] if len(parts) > 2 else "", resolved_user)
                )
            except ValueError as exc:
                flowfile.set_content(str(exc).encode("utf-8"))
                return [flowfile]
            ids.set_active_conv(resolved_user, "telegram", new_id)
            flowfile.set_content(
                f"Created and selected conversation: {new_id}\n"
                f"Agent: {agent_name}".encode("utf-8")
            )
            return [flowfile]

        # /conv info (default)
        active = ids.get_active_conv(resolved_user, "telegram")
        if active:
            count = int(store.get_extra_snapshot(
                active, "_meta_msg_count", 0) or 0)
            flowfile.set_content(
                f"Active conversation: {active[:12]} ({count} msgs)\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        else:
            flowfile.set_content(
                f"No active conversation. Use /conv list after creating "
                f"a conversation from webchat or PawCode.\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        return [flowfile]

    # Random thought


    def _handle_random_thought(self, body: Dict, conv_id: str,
                               user_id: str, flowfile: FlowFile) -> List[FlowFile]:
        """Handle the ``random_thought`` action (on/off/status/now)."""
        import random as _rng
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        sub = body.get("sub", "status")
        agent_name = body.get("agent", "")
        store = ConversationStore.instance()
        # If no agent specified, use the currently selected agent for this conversation
        if not agent_name and conv_id:
            active_res = store.get_extra(conv_id, "active_resources") or {}
            agent_name = active_res.get("agent", "")
        if not agent_name:
            raise RuntimeError("No agent resolved for this conversation. Add an agent first.")
        # Resolve nickname -> real name (case-insensitive)
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        # Normalize agent name for key consistency (case-insensitive)
        _agent_key = agent_name.lower()
        scheduler = PollScheduler.instance()

        if not conv_id:
            flowfile.set_content(json.dumps({"error": "No conversation"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # Resolve target agents (ALL = assistant + all ResourceStore agents)
        if agent_name.upper() == "ALL":
            from core.resource_store import ResourceStore
            all_agents = ResourceStore.instance().list_all(
                "agent", user_id, conversation_id=conv_id)
            target_agents = [a["name"] for a in all_agents]
        else:
            target_agents = [agent_name]

        if sub == "on":
            freq = body.get("frequency", "6/1m")
            try:
                min_iv, max_iv = self._parse_thought_frequency(freq)
            except ValueError as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]

            results = []
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                _tgt_extra_key = f"random_thought::{_tgt_key}"

                scheduler.cancel(_tgt_thought_key)
                if not store.set_extra(conv_id, _tgt_extra_key, {"_probe": True}):
                    store.save(conv_id, [], user_id=user_id)
                store.set_extra(conv_id, _tgt_extra_key, {
                    "enabled": True,
                    "min_interval": min_iv,
                    "max_interval": max_iv,
                    "agent": _tgt,
                    "frequency": freq,
                })
                delay = _rng.randint(min_iv, max_iv)  # nosec B311
                scheduler.schedule_delay(
                    conv_id, delay, key=_tgt_thought_key,
                    reason=f"[random_thought] spontaneous thought ({_tgt})",
                    user_id=user_id,
                )
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(conv_id, "thought_scheduled", {
                        "agent": _tgt, "delay": delay, "frequency": freq,
                    })
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                results.append({"agent": _tgt, "delay": delay})

            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "frequency": freq,
                "next_in_seconds": results[0]["delay"] if results else 0,
                "agents": [r["agent"] for r in results],
            }).encode())
            return [flowfile]

        if sub == "off":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_extra_key = f"random_thought::{_tgt_key}"
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                store.set_extra(conv_id, _tgt_extra_key, {"enabled": False})
                scheduler.cancel(_tgt_thought_key)
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "disabled": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        if sub == "now":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                scheduler.schedule_delay(
                    conv_id, 1, key=_tgt_thought_key,
                    reason=f"[random_thought] manual trigger ({_tgt})",
                    user_id=user_id,
                )
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "triggered": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        # sub == "status" (default)
        import time as _t
        statuses = []
        for _tgt in target_agents:
            _tgt_key = _tgt.lower()
            _tgt_extra_key = f"random_thought::{_tgt_key}"
            _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
            cfg = store.get_extra(conv_id, _tgt_extra_key)
            enabled = bool(cfg and cfg.get("enabled"))
            sched = scheduler.get(_tgt_thought_key)
            next_at = sched["recheck_at"] if sched else None
            next_in = int(next_at - _t.time()) if next_at else None
            statuses.append({
                "agent": _tgt, "enabled": enabled,
                "frequency": cfg.get("frequency", "") if cfg else "",
                "next_in_seconds": max(0, next_in) if next_in is not None else None,
            })

        any_enabled = any(s["enabled"] for s in statuses)
        flowfile.set_content(json.dumps({
            "enabled": any_enabled, "agent": agent_name,
            "agents": statuses,
        }).encode())
        return [flowfile]


    @staticmethod
    def _parse_thought_frequency(spec: str):
        """Parse frequency spec like '2-3/h' -> (min_interval, max_interval) in seconds.

        Format: ``<count_min>[-<count_max>]/<number?><unit>``
        Units: s=1, m=60, h=3600, d=86400.

        Returns ``(min_interval_sec, max_interval_sec)`` or raises ValueError.
        """
        import re
        m = re.match(r'^(\d+)(?:-(\d+))?/(\d*)([smhd])$', spec)
        if not m:
            raise ValueError(f"Invalid frequency: {spec}")
        count_min = int(m.group(1))
        count_max = int(m.group(2) or count_min)
        if count_min <= 0 or count_max < count_min:
            raise ValueError(f"Invalid frequency counts: {spec}")
        duration_num = int(m.group(3) or 1)
        unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(4)]
        period = duration_num * unit
        # More counts -> shorter intervals
        max_interval = period // count_min
        min_interval = period // count_max
        return (min_interval, max_interval)


    @staticmethod
    def _clear_claude_session(conv_id: str, agent_name: str):
        """Clear Claude Code session_id + purge stale jsonl on disk.

        Called after rebuild/compact/summary/restart — the context changed,
        so Claude Code must start a new session with the updated context
        and the old session jsonl must not survive (orphan workers may
        still be writing to it).

        Delegates to `ConversationStore.invalidate_claude_session_for_agent`
        which knows the real on-disk layout:
            CLAUDE_SESSIONS_DIR/<user>/<conv>/claude/projects/-workspace/<sid>.jsonl
        plus the companion `<sid>/` directory.
        """
        if not conv_id:
            return
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            if agent_name:
                store.invalidate_claude_session_for_agent(conv_id, agent_name)
            else:
                # Shared context changed — invalidate every agent's CC session
                # in this conversation (clears extras + purges jsonls on disk).
                store.invalidate_claude_sessions(conv_id)
        except Exception as _e:
            logger.warning("_clear_claude_session failed for %s/%s: %s",
                           conv_id[:8], agent_name, _e)
