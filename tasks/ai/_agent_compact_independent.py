"""AgentCompaction: isolated/independent-context compaction path.

Extracted from the ``_compact`` mega-method in tasks/ai/agent_compaction.py for
the <=800-line rule (invariant 2: composed back via MRO into
AgentCompactionMixin).
"""
import logging
import threading
import time
from typing import List

from core.llm_client import LLMMessage
from tasks.ai._agent_compact_base import _is_independent_summary

logger = logging.getLogger(__name__)


class _AgentCompactIndependentMixin:
    """The isolated-context compaction path; mixed into AgentCompactionMixin."""

    def _compact_independent_context(
        self, *, messages, client, max_tokens, tool_defs, chars_per_token,
        compact_instructions, force, user_id, conversation_id, agent_name,
        post_hooks_async, system_msg, start_idx, cap, trigger,
        _tmul, _bucket_target, _original_count,
    ) -> List[LLMMessage]:
        """Compact an isolated context without the shared bg pyramid."""
        def _estimate(msgs: List[LLMMessage]) -> int:
            return self._estimate_tokens(
                msgs, tool_defs=tool_defs,
                chars_per_token=chars_per_token,
                token_multiplier=_tmul)

        _initial_output = [system_msg] if system_msg else []
        _initial_output.extend(messages[start_idx:])
        _original_tokens = _estimate(_initial_output)
        if not force and _original_tokens < trigger:
            return messages

        logger.info("[compact] %s independent context: %d tokens "
                    "(trigger=%d, cap=%d, %d msgs)",
                    "FORCED" if force else "TRIGGERED",
                    _original_tokens, trigger, cap, _original_count)

        _trigger_label = "manual" if force else "auto"
        _pre_ctx = {
            "trigger": _trigger_label,
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "user_id": user_id,
            "compact_instructions": compact_instructions or "",
            "force": bool(force),
            "original_tokens": _original_tokens,
            "independent_context": True,
        }
        from core.agent_hooks import AgentHookRunner
        _hook_runner = AgentHookRunner(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
        )
        _pre_result = _hook_runner.run("pre_compact", _pre_ctx,
                                      fail_policy="closed")
        if _pre_result.get("decision") == "block":
            logger.info("[compact] pre-hook aborted independent compaction")
            return messages
        _pre_payload = _pre_result.get("payload") or {}
        _instructions = _pre_payload.get("compact_instructions", "") or ""
        _instructions = (
            (_instructions + "\n\n") if _instructions else ""
        ) + (
            "This is an isolated task/delegate context, not the main "
            "conversation shared history. Summarise only the messages "
            "provided here. If an earlier independent-context summary is "
            "present, merge it into one updated summary; do not stack or "
            "repeat old summary wrappers. Preserve task goals, decisions, "
            "files touched, tool outcomes, blockers, and remaining work."
        )

        try:
            _summary_target = max(500, min(_bucket_target, max(500, cap // 3)))
            _tail_budget = max(1000, cap - _summary_target - 500)
            _tail_msgs = messages[start_idx:]
            _accum = 0
            _take_from = len(_tail_msgs)
            for _i in range(len(_tail_msgs) - 1, -1, -1):
                _cost = _estimate([_tail_msgs[_i]])
                if _accum + _cost > _tail_budget and _i < len(_tail_msgs) - 1:
                    break
                _accum += _cost
                _take_from = _i
            saved_recent = _tail_msgs[_take_from:]

            # Previous independent summaries must be folded into the new
            # head summary, never kept as an additional recent-tail message.
            saved_recent = [m for m in saved_recent if not _is_independent_summary(m)]
            _saved_ids = {id(m) for m in saved_recent}
            head = [m for m in _tail_msgs if id(m) not in _saved_ids]

            # Keep tool results paired with their owning tool call at the
            # boundary. If we cannot find the owner, drop the orphan.
            while saved_recent and saved_recent[0].role == "tool":
                _orphan_id = getattr(saved_recent[0], 'tool_call_id', '')
                _has_owner = any(
                    m.role == "assistant" and m.tool_calls
                    and any(tc.id == _orphan_id for tc in m.tool_calls)
                    for m in saved_recent[1:]
                )
                if _has_owner:
                    break
                if _take_from > 0:
                    _take_from -= 1
                    saved_recent = [
                        m for m in _tail_msgs[_take_from:]
                        if not _is_independent_summary(m)
                    ]
                    _saved_ids = {id(m) for m in saved_recent}
                    head = [m for m in _tail_msgs if id(m) not in _saved_ids]
                else:
                    saved_recent = saved_recent[1:]
                    break

            compacted: List[LLMMessage] = []
            if system_msg:
                compacted.append(system_msg)
            if head:
                summary = self._summarize_messages(
                    head, client, max_tokens,
                    target_tokens=_summary_target,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    compact_instructions=_instructions,
                    user_id=user_id,
                )
                _ref_ts = min(
                    [m.timestamp for m in saved_recent if getattr(m, 'timestamp', 0)]
                    or [time.time()])
                _ref_seq = min(
                    [m.seq for m in saved_recent if getattr(m, 'seq', 0)]
                    or [0])
                compacted.append(LLMMessage(
                    role="user",
                    content=(
                        "[Independent context summary - earlier messages compacted]\n\n"
                        + (summary or "").strip()
                        + "\n\nThe recent messages below are the current state. "
                        "Continue this isolated task/delegate context from here."
                    ),
                    source={"type": "independent_compaction"},
                    timestamp=_ref_ts - 0.002,
                    seq=_ref_seq - 2 if _ref_seq else 0,
                    conversation_id=conversation_id,
                ))
                compacted.append(LLMMessage(
                    role="assistant",
                    content="Understood. I have the task context summary and will continue from the recent messages.",
                    source={"type": "context"},
                    timestamp=_ref_ts - 0.001,
                    seq=_ref_seq - 1 if _ref_seq else 0,
                    conversation_id=conversation_id,
                ))
            compacted.extend(saved_recent)

            new_estimate = _estimate(compacted)
            if new_estimate > cap:
                self._truncate_tool_results(saved_recent)
                compacted = ([system_msg] if system_msg else [])
                if head:
                    compacted.extend([
                        LLMMessage(
                            role="user",
                            content=(
                                "[Independent context summary - earlier messages compacted]\n\n"
                                + (summary or "").strip()
                                + "\n\nThe recent messages below are the current state. "
                                "Continue this isolated task/delegate context from here."
                            ),
                            source={"type": "independent_compaction"},
                            conversation_id=conversation_id,
                        ),
                        LLMMessage(
                            role="assistant",
                            content="Understood. I have the task context summary and will continue from the recent messages.",
                            source={"type": "context"},
                            conversation_id=conversation_id,
                        ),
                    ])
                compacted.extend(saved_recent)
                new_estimate = _estimate(compacted)
            if new_estimate > cap:
                compacted = self._force_fit_context(
                    compacted, cap,
                    chars_per_token=chars_per_token,
                    tool_defs=tool_defs,
                    token_multiplier=_tmul,
                    conversation_id=conversation_id)
                new_estimate = _estimate(compacted)

            logger.info("[compact] Final independent: %d tokens (was %d, cap %d), "
                        "%d messages (was %d)",
                        new_estimate, _original_tokens, cap,
                        len(compacted), _original_count)
            self._persist_context(compacted, conversation_id, agent_name)
            if conversation_id:
                self._cleanup_orphan_files(compacted, conversation_id)
            _compacted_payload = self._serialize_messages(compacted)
            _post_ctx = {
                "trigger": _trigger_label,
                "conversation_id": conversation_id,
                "agent_name": agent_name,
                "user_id": user_id,
                "before_messages": _original_count,
                "after_messages": len(compacted),
                "tokens_before": _original_tokens,
                "tokens_after": new_estimate,
                "target_tokens": cap,
                "compacted_messages": _compacted_payload,
                "compacted": _compacted_payload,
                "independent_context": True,
            }
            def _run_independent_post_hooks() -> None:
                _hooks_t0 = time.monotonic()
                logger.info(
                    "[compact] post hooks start cid=%s agent=%s async=%s independent=True",
                    conversation_id[:8], agent_name, post_hooks_async)
                try:
                    _hook_runner.run("post_compact", _post_ctx)
                except Exception:
                    logger.debug("post_compact hooks raised", exc_info=True)
                finally:
                    logger.info(
                        "[compact] post hooks done cid=%s agent=%s async=%s independent=True elapsed_ms=%.1f",
                        conversation_id[:8], agent_name, post_hooks_async,
                        (time.monotonic() - _hooks_t0) * 1000.0)
            if post_hooks_async:
                logger.info(
                    "[compact] post hooks scheduled async cid=%s agent=%s independent=True",
                    conversation_id[:8], agent_name)
                threading.Thread(
                    target=_run_independent_post_hooks,
                    daemon=True,
                    name=f"post-compact-hooks-{conversation_id[:8]}",
                ).start()
            else:
                _run_independent_post_hooks()
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=True)
            return compacted
        except Exception:
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=False)
            raise
