"""ConversationStore id/naming/path + stamping + canonical row helpers."""

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsPathsMixin:
    """id/naming/path + stamping + canonical row helpers."""

    def _stamp_line(self, cid: str, line: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp the five-field invariant on every persisted record:
        ``(msg_id, ts, seq, conversation_id, user_id)``.

        Seq is the on-disk line index: assigned at WRITE time as
        ``last_persisted + 1``. Any pre-stamped seq on the incoming
        line is overwritten — producers cannot reserve a seq in
        advance because disk order is the sole source of truth.
        Callers MUST hold the per-conv lock while invoking this method
        and performing the subsequent write; the lock is what
        serializes mint + write into an atomic step per conv.
        """
        if not cid:
            raise ValueError(
                "_stamp_line requires a non-empty conversation_id — "
                "every persisted record lives inside a conversation")
        from core.llm_client import _next_persisted_seq
        if line.get("role") != "system" and not line.get("msg_id"):
            line["msg_id"] = uuid.uuid4().hex[:12]
        if "ts" not in line and "timestamp" not in line:
            line["ts"] = time.time()
        line["seq"] = _next_persisted_seq(cid)
        if not line.get("conversation_id"):
            line["conversation_id"] = cid
        if not line.get("user_id"):
            line["user_id"] = self._cid_user.get(cid, "")
        return line

    @staticmethod
    def _row_ts(row: Dict[str, Any]) -> Any:
        return row.get("ts") or row.get("timestamp") or time.time()

    @staticmethod
    def _new_msg_id() -> str:
        return uuid.uuid4().hex[:12]

    def _find_tool_call_parent_id(self, cid: str, tool_call_id: str) -> str:
        if not tool_call_id:
            return ""
        cached = self._tool_parent_cache.get(cid, {}).get(tool_call_id)
        if cached:
            return cached
        try:
            for row in self._transcript_log(cid).iter_rows_reverse():
                if (row.get("role") == "tool_call"
                        and (row.get("tool_call_id") or row.get("tc_id")) == tool_call_id):
                    parent_id = row.get("msg_id", "") or ""
                    if parent_id:
                        self._tool_parent_cache.setdefault(cid, {})[tool_call_id] = parent_id
                    return parent_id
        except Exception:
            logger.debug("tool_call parent lookup failed for %s/%s",
                         cid[:8], tool_call_id, exc_info=True)
        return ""

    def _remember_tool_call_parents(self, cid: str, rows: List[Dict[str, Any]]) -> None:
        cache = self._tool_parent_cache.setdefault(cid, {})
        for row in rows:
            if row.get("role") != "tool_call":
                continue
            tcid = str(row.get("tool_call_id") or row.get("tc_id") or "")
            msg_id = row.get("msg_id", "") or ""
            if tcid and msg_id:
                cache[tcid] = msg_id

    def _canonical_message_rows(
        self, cid: str, msg: Dict[str, Any],
        tool_call_parents: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Expand one incoming logical message into canonical stored rows."""
        tool_call_parents = tool_call_parents if tool_call_parents is not None else {}
        role = msg.get("role", "")
        if role != "assistant":
            row = dict(msg)
            if role != "system" and not row.get("msg_id"):
                row["msg_id"] = self._new_msg_id()
            if role == "tool_call":
                tcid = str(row.get("tool_call_id") or row.get("tc_id") or "")
                if tcid:
                    row["tool_call_id"] = tcid
                    tool_call_parents[tcid] = row.get("msg_id", "") or ""
            elif role == "tool":
                tcid = str(row.get("tool_call_id") or row.get("tc_id") or "")
                if tcid:
                    row["tool_call_id"] = tcid
                    parent_id = row.get("parent_message_id") or tool_call_parents.get(tcid)
                    if not parent_id:
                        parent_id = self._find_tool_call_parent_id(cid, tcid)
                    if parent_id:
                        row["parent_message_id"] = parent_id
            return [row]

        anchor = dict(msg)
        tool_calls = anchor.pop("tool_calls", None) or []
        thinking = anchor.pop("thinking", "") or ""
        thinking_signature = anchor.pop("thinking_signature", "") or ""
        anchor.pop("tool_call_id", None)
        if not anchor.get("msg_id"):
            anchor["msg_id"] = self._new_msg_id()
        anchor_id = anchor.get("msg_id", "")
        ts = self._row_ts(anchor)
        rows = [anchor]

        if thinking or thinking_signature:
            trow = {
                "role": "thinking",
                "content": thinking,
                "msg_id": self._new_msg_id(),
                "parent_message_id": anchor_id,
                "ts": ts,
            }
            if thinking_signature:
                trow["thinking_signature"] = thinking_signature
            for key in ("source", "channel", "conversation_id", "user_id"):
                if anchor.get(key) is not None:
                    trow[key] = anchor[key]
            rows.append(trow)

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            tcid = str(call.get("id") or call.get("tool_call_id") or call.get("tc_id") or "")
            crow = {
                "role": "tool_call",
                "content": call.get("content", ""),
                "msg_id": call.get("msg_id") or self._new_msg_id(),
                "parent_message_id": anchor_id,
                "tool_call_id": tcid,
                "ts": call.get("ts") or call.get("timestamp") or ts,
            }
            name = call.get("name") or call.get("tool_name") or call.get("tool") or ""
            if name:
                crow["tool_name"] = name
                crow["name"] = name
            if "arguments" in call:
                crow["arguments"] = call.get("arguments")
            elif "input" in call:
                crow["arguments"] = call.get("input")
            for key in ("source", "channel", "conversation_id", "user_id"):
                if anchor.get(key) is not None:
                    crow[key] = anchor[key]
            rows.append(crow)
            if tcid:
                tool_call_parents[tcid] = crow["msg_id"]
        return rows

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_.:@")
        return safe.replace(":", "__")

    @staticmethod
    def _canon_agent(name: str) -> str:
        """Canonical form of an agent name — lowercase + stripped.

        Agent identity is case-insensitive: 'Claude', 'claude', 'ClAuDe'
        all refer to the same agent. Apply this at every storage/lookup
        boundary so file paths, extras keys, and context caches never
        end up with two entries for the same agent.
        """
        return (name or "").strip().lower()

    @classmethod
    def _canon_extra_key(cls, key: str) -> str:
        """Lowercase the agent-name suffix on per-agent extras keys.

        Keys like 'claude_session:<agent>' encode an agent name in the
        suffix. Normalize the suffix only — leave other keys untouched.
        """
        for _prefix in (
                "claude_session:", "cc_session:", "codex_session:",
                "gemini_acp_session:", "gemini_acp_pool_idx:",
                "gemini_acp_session_version:"):
            if key.startswith(_prefix):
                return _prefix + cls._canon_agent(key[len(_prefix):])
        return key

    def _conv_dir(self, cid: str, user_id: str = "") -> Path:
        """Directory for a conversation: {store_dir}/{user}/{conv_id}/"""
        if user_id:
            self._cid_user[cid] = user_id  # cache for future lookups
            return self._store_dir / self._safe_name(user_id) / self._safe_name(cid)
        # Fast lookup from cid→user mapping (populated by _ensure_loaded + save)
        uid = self._cid_user.get(cid)
        if uid:
            return self._store_dir / self._safe_name(uid) / self._safe_name(cid)
        # Fallback: scan user dirs on disk
        if self._store_dir.is_dir():
            for user_dir in self._store_dir.iterdir():
                if user_dir.is_dir():
                    conv_dir = user_dir / self._safe_name(cid)
                    if conv_dir.is_dir():
                        self._cid_user[cid] = user_dir.name  # remember for next time
                        return conv_dir
        raise ValueError(f"Conversation {cid[:16]} not found and no user_id provided")

    def _transcript_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "transcript.jsonl"

    def _transcript_log(self, cid: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._transcript_path(cid), codec=self._codec_for(cid))

    def _shared_ctx_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "shared.jsonl"

    def _shared_ctx_log(self, cid: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._shared_ctx_path(cid), codec=self._codec_for(cid))

    def _agent_ctx_path(self, cid: str, agent: str) -> Path:
        safe_agent = self._safe_name(self._canon_agent(agent)) if agent else "_shared"
        return self._conv_dir(cid) / safe_agent / "context.jsonl"

    def _agent_ctx_log(self, cid: str, agent: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._agent_ctx_path(cid, agent), codec=self._codec_for(cid))

    def _content_seg(self, cid: str, path: Path) -> SegmentedJsonl:
        """A SegmentedJsonl over a content-bearing log under conversation ``cid``,
        wired to the conversation's encryption codec when it is enabled and
        unlocked (else plaintext passthrough). Use this — not a bare
        ``SegmentedJsonl(path)`` — anywhere row *content* is read or written, so
        encrypted conversations never round-trip plaintext to disk. Metadata-only
        ops (exists / delete_by_msg_ids / truncate_after_msg_id) work on a bare
        handle since they only touch the clear fields."""
        return SegmentedJsonl(path, codec=self._codec_for(cid))
