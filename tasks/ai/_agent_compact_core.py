"""AgentCompaction: the main reduce-to-cap ``_compact`` path.

Extracted from the ``_compact`` mega-method in tasks/ai/agent_compaction.py for
the <=800-line rule (invariant 2: composed back via MRO into
AgentCompactionMixin). Pyramid-header + token-budget tail-selection path; the
isolated-context path lives in _agent_compact_independent.py.
"""
import logging
import threading
import time
from typing import List

from core.llm_client import LLMClient, LLMMessage
from tasks.ai._agent_compact_base import (
    _is_synthetic_compact_msg, _collect_recent_files, _format_files_note,
    _clone_with_content,
)

logger = logging.getLogger(__name__)


class _AgentCompactCoreMixin:
    """The main compaction path; mixed into AgentCompactionMixin."""

    def _compact(
        self,
        messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        trigger_fraction: float = 0.8,
        target_fraction: float = 0.25,
        conversation_id: str = "",
        agent_name: str = "",
        tool_defs: list = None,
        chars_per_token: float = 0,
        compact_instructions: str = "",
        force: bool = False,
        user_id: str = "",
        budget_config: dict | None = None,
        independent_context: bool = False,
        post_hooks_async: bool = False,
    ) -> List[LLMMessage]:
        """Iterative reduce-to-cap compaction. Output ≤ target_fraction × max.

        Every token count is the REAL tokenizer cost for the target model:
        tiktoken cl100k_base output × service config token_multiplier
        (Opus 4.7 = 1.6, Sonnet/Haiku 4.6 = 1.1, OpenAI = 1.0). Thresholds,
        logs, and SSE events all operate in real-token space so behaviour
        matches what the context gauge displays.

        Two fractions govern behaviour (both of max_tokens):
          * trigger_fraction (default 0.8) — when NOT forced, skip compact
            while estimated tokens are still below this. 0.8 matches the
            LLM API auto-trigger: compact kicks in once the context hits
            80% of the budget. Manual /compact and CC compact_boundary
            pass force=True and bypass this check.
          * target_fraction (default 0.25) — HARD cap on output size. The
            iterative algorithm below guarantees output ≤ cap; a terminal
            force_fit brute-truncates content if earlier steps fell short.

        Algorithm (always converges, 5 steps):
          0. Cleanup (orphans, images, base64).
          1. Summarise tail[:-RECENT] into a new level-1 bucket; output =
             header + saved_recent. If ≤ cap → done.
          2. rollup_all_except_last (needs ≥ 3 buckets) → retry output.
          3. collapse_all (needs ≥ 2 buckets) → retry output.
          4. Shrink saved_recent from (25 conv / 100 msgs) to (6 / 20),
             summarise ejected messages into a new bucket → retry.
          5. force_fit brute-truncate message contents to cap.
        """
        _cpt = chars_per_token if chars_per_token > 0 else 3.5
        # Circuit breaker: skip auto-compact after N consecutive failures.
        # Forced compacts (manual /compact, CC compact_boundary) bypass —
        # the user / CC explicitly asked so we still try.
        if not force:
            _tripped = self._compact_breaker_should_skip(
                conversation_id, agent_name)
            if _tripped:
                logger.warning(
                    "[compact] circuit breaker tripped for %s/%s "
                    "(%d consecutive failures) — skipping auto-compact; "
                    "manual /compact still works.",
                    conversation_id[:8] if conversation_id else "?",
                    agent_name or "-", _tripped)
                return messages
        # Resolve budget-sensitive settings from the active agent LLM
        # service, not necessarily from `client`: compaction often uses a
        # separate summarizer client to write summaries, while the cap/gauge
        # must follow the service whose context will receive the result.
        _budget_cfg = (budget_config
                       or getattr(client, "config", None)
                       or getattr(client, "_config_ref", None)
                       or getattr(getattr(client, "_client", None), "_config_ref", None)
                       or {})
        from core.token_counter import resolve_token_multiplier
        _tmul = resolve_token_multiplier(_budget_cfg)

        # ── Phase -1: Advance the shared pyramid ──
        # The shared pyramid is the authoritative history asset, built
        # and maintained by core.bg_bucket_builder (the only writer).
        # This hot path is READ-ONLY on the pyramid.
        #
        # Always block on build_now_sync (when user_id is known) so
        # the pyramid is caught up before we assemble — partial flush
        # included for any msgs in progress. Strict guarantee: the
        # tail handed to assemble never exceeds TAIL_TOKEN_BUDGET, so
        # downstream steps stay deterministic (2a truncate + 2d
        # force_fit) without ever needing an LLM-summarize fallback.
        # In steady state the bg-builder keeps the gap small and this
        # call is a no-op (`_pick_chunk` returns []); it only blocks
        # if bg fell behind, which is exactly when blocking is the
        # correct behaviour — it pays a few seconds once to keep
        # output fidelity intact, instead of silently truncating
        # content via force_fit. Manual /compact and CC compact_
        # boundary already passed force=True; auto-trigger at 80%
        # benefits from the same guarantee.
        # ── Compact contract (instant, deterministic, no LLM call) ──
        #
        #   Output = system + pyramid_header + bridge + recent_tail
        #     pyramid_header   ≤ HEADER_BUDGET (30k)  — what bg already
        #                                              consolidated
        #     recent_tail      ≤ TAIL_TARGET (20k)    — walked back
        #                                              from end of
        #                                              transcript by
        #                                              token budget
        #     total            ≤ cap (50k = 0.25 × max for 200k model)
        #
        # NO bg build_now_sync here — that would spawn a CC subprocess
        # to summarize a pending partial bucket, defeating "compact is
        # instant". Bg runs async via maybe_trigger and stays caught
        # up under TAIL_TOKEN_BUDGET (20k) most of the time. If bg is
        # behind, the pyramid_header reflects fewer covered msgs and
        # the recent_tail walk-back below picks up the slack — same
        # final user-visible content, just sliced differently.
        # NO pre-filter on `m.seq > pyramid.last_seq` either. The
        # contract takes the X MOST RECENT transcript rows by token
        # budget, regardless of whether they're also covered by the
        # summary. Overlap is intentional: the summary is dense
        # context, the raw tail is fidelity for what the agent will
        # immediately respond to.
        _bucket_store = None
        if conversation_id and not independent_context:
            try:
                from core.bucket_store import BucketStore
                from core.conversation_store import ConversationStore
                _conv_dir = ConversationStore.instance()._conv_dir(conversation_id)
                _bucket_store = BucketStore.get(_conv_dir)
            except Exception as _bs_err:
                logger.warning(
                    "[compact] bucket store init failed: %s — falling back "
                    "to tail-only compact (no pyramid header)",
                    _bs_err)
                _bucket_store = None

        # ── Phase 0: Cleanup ──
        # A post-compact context is always rebuilt from the shared pyramid
        # header plus a raw recent tail. Messages injected by a previous
        # compact are neither; drop them before the tail walk-back so a
        # repeated compact never compacts an old bridge as if it were user
        # transcript.

        messages = [
            m for m in messages
            if getattr(m, 'role', '') != 'sub_agent_trace'
            and not _is_synthetic_compact_msg(m)
        ]

        # Remove orphan tool results
        _valid_tc_ids = set()
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    _valid_tc_ids.add(tc.id)
        _pre_orphan = len(messages)
        messages = [
            m for m in messages
            if m.role != "tool"
            or getattr(m, 'tool_call_id', None) in _valid_tc_ids
        ]
        if len(messages) < _pre_orphan:
            logger.info(f"[compact] Removed {_pre_orphan - len(messages)} orphan tool result(s)")

        # Deflate old images
        self._deflate_image_messages(messages, keep_last=True,
                                      user_id=user_id, conversation_id=conversation_id)

        # Strip base64 blobs
        import re as _re_b64
        for m in messages:
            if not isinstance(m.content, str) or len(m.content) < 5000:
                continue
            if not self._detect_base64_blob(m.content):
                continue
            m.content = _re_b64.sub(
                r'data:[^;]*;base64,[A-Za-z0-9+/=]+',
                '[base64 image removed — use show_file to view]',
                m.content,
            )
            m.content = _re_b64.sub(
                r'[A-Za-z0-9+/=]{1000,}',
                '[binary data removed]',
                m.content,
            )

        # NOTE: do NOT call _clear_seen_tool_results here — it stores to FileStore
        # which is wrong during compaction (thousands of files). Compaction uses
        # _truncate_tool_results (in-place truncation) in the output window.

        # ════════════════════════════════════════════════════════════════
        #  Iterative reduce-to-cap algorithm (5 steps, always converges)
        # ════════════════════════════════════════════════════════════════
        _original_count = len(messages)
        # Compact target precedence:
        #   1. service config `compact_target_tokens` (absolute, in tokens) —
        #      enforced ≤ 40% of max at service install time; runtime falls
        #      back to the formula if somehow that bound is exceeded here.
        #   2. legacy fraction (`target_fraction`, default 0.25 × max).
        # `_budget_cfg` is the active service budget config. Do not read the
        # summarizer config here: otherwise a Codex appserver compact can use
        # the summarizer's legacy 25% cap instead of the agent service's
        # explicit `compact_target_tokens`.
        try:
            _abs_cap = int(_budget_cfg.get("compact_target_tokens", 0) or 0)
        except (TypeError, ValueError):
            _abs_cap = 0
        if _abs_cap > 0 and _abs_cap <= int(max_tokens * 0.4):
            cap = _abs_cap
        else:
            if _abs_cap > 0:
                logger.warning(
                    "[compact] compact_target_tokens=%d exceeds 40%% of "
                    "max_context_size=%d — falling back to %.0f%% formula",
                    _abs_cap, max_tokens, target_fraction * 100)
            cap = int(max_tokens * target_fraction)
        trigger = int(max_tokens * trigger_fraction)
        _bucket_target = max(1000, int(max_tokens * 0.05))

        system_msg = messages[0] if messages and messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        from core.llm_client import _peek_persisted_seq
        import time as _t_compact



        # Pyramid header read ONCE here (fresh from store). Step 2c may
        # replace _pyramid_header with a compressed version (private to
        # this compact, does NOT touch the shared pyramid). _build_output
        # uses the current value of _pyramid_header at call time via
        # closure — re-binding it is how 2c takes effect.
        _pyramid_header = (
            _bucket_store.assemble_summary_header() if _bucket_store else "")
        try:
            from core.conversation_store import ConversationStore
            _restart_ctx = ConversationStore.instance().get_extra(
                conversation_id, "_restart_from_context") or {}
        except Exception:
            logger.debug("restart_from context lookup failed", exc_info=True)
            _restart_ctx = {}
        if _pyramid_header and isinstance(_restart_ctx, dict):
            _restart_msg_id = str(_restart_ctx.get("msg_id") or "").strip()
            _restart_boundary = str(
                _restart_ctx.get("boundary_msg_id") or "").strip()
            if _restart_msg_id:
                _restart_note = (
                    "\n[Restart context]\n"
                    f"Conversation was restarted from msg_id {_restart_msg_id}. "
                    "Treat summary details after that point as pre-restart "
                    "history, not current conversation state."
                )
                if _restart_boundary:
                    _restart_note += (
                        f" The transcript was kept through msg_id "
                        f"{_restart_boundary}."
                    )
                _pyramid_header += _restart_note + "\n"

        def _build_output(saved: List[LLMMessage]) -> List[LLMMessage]:
            """Assemble system + pyramid_header (context bridge) + saved.

            Pure assembly — NO truncation. Earlier versions called
            `self._truncate_tool_results(saved)` here unconditionally,
            which clipped every tool result > 800 chars on every
            assemble call regardless of whether the output actually
            fit the cap. Symptom: a compact with header=10k and a
            tail rich in tool I/O (e.g. 88 transcript rows) would
            truncate to ~30k even when the full content fit easily
            in the 50k cap. Step 2a in the caller now owns the
            decision to truncate (only when new_estimate > cap).
            """
            out: List[LLMMessage] = []
            if system_msg:
                out.append(system_msg)
            if _pyramid_header:
                if saved:
                    _frt = min(m.timestamp for m in saved)
                    _frs = min(m.seq for m in saved)
                else:
                    _frt = _t_compact.time()
                    _frs = _peek_persisted_seq(conversation_id) + 2
                _postamble = (
                    "\nThe recent messages below are the current state. "
                    "Do NOT restart or re-propose completed work. If you need "
                    "more detail than the summary above (commits, file contents, "
                    "tool arguments), call read_history."
                )
                _files_note = _format_files_note(
                    _collect_recent_files(messages, limit=5))
                out.append(LLMMessage(
                    role="user",
                    content=_pyramid_header + _postamble + _files_note,
                    timestamp=_frt - 0.002,
                    seq=_frs - 2,
                    source={"type": "context"},
                    conversation_id=conversation_id,
                ))
                out.append(LLMMessage(
                    role="assistant",
                    content="Understood. I have the summary and will continue from the recent messages.",
                    source={"type": "context"},
                    timestamp=_frt - 0.001,
                    seq=_frs - 1,
                    conversation_id=conversation_id,
                ))
            out.extend(saved)
            return out

        def _estimate(msgs: List[LLMMessage]) -> int:
            return self._estimate_tokens(
                msgs, tool_defs=tool_defs,
                chars_per_token=chars_per_token,
                token_multiplier=_tmul)

        def _estimate_tail_selection_cost(m: LLMMessage) -> int:
            """Estimate the token cost used for tail walk-back selection.

            Oversized tool results are selected at their post-truncation cost,
            because step 2a will truncate them before persisting the final
            compacted context. Using the raw cost here can stop the walk-back
            early and leave thousands of target tokens unused.
            """
            if m.role != "tool":
                return _estimate([m])
            content = m.content
            if isinstance(content, str):
                if len(content) <= self._TOOL_TRUNC_LIMIT:
                    return _estimate([m])
                content = (
                    content[:self._TOOL_TRUNC_LIMIT]
                    + "\n...[compacted — re-call tool if needed]..."
                )
            elif isinstance(content, list):
                text_parts = [p for p in content if p.get("type") == "text"]
                text = " ".join(p.get("text", "") for p in text_parts)
                if len(text) > self._TOOL_TRUNC_LIMIT:
                    content = (
                        text[:self._TOOL_TRUNC_LIMIT]
                        + "\n...[compacted — re-call tool if needed]..."
                    )
                else:
                    content = text
            else:
                return _estimate([m])
            return _estimate([LLMMessage(
                role=m.role,
                content=content,
                tool_call_id=getattr(m, 'tool_call_id', None),
                timestamp=getattr(m, 'timestamp', 0.0),
                seq=getattr(m, 'seq', 0),
                source=getattr(m, 'source', None),
                conversation_id=getattr(m, 'conversation_id', None),
            )])


        def _truncate_message_to_budget(m: LLMMessage,
                                        token_budget: int) -> LLMMessage:
            """Shrink one oversized non-tool tail message to its budget.

            The tail selector keeps at least the newest message. When that
            message is itself larger than the tail budget, the old fallback
            built an over-cap compact and let global force-fit crush the whole
            output far below compact_target_tokens. Fit just that message
            instead so the final compact still uses the configured budget.
            """
            content = getattr(m, 'content', None)
            if isinstance(content, list):
                text = " ".join(
                    str(p.get("text", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "text")
            elif isinstance(content, str):
                text = content
            else:
                return m
            if _estimate([m]) <= token_budget:
                return m

            marker = "\n...[compacted to fit tail budget; use read_history for full message]...\n"

            def _candidate(keep_chars: int) -> LLMMessage:
                if keep_chars <= 0:
                    body = marker.strip()
                elif len(text) <= keep_chars:
                    body = text
                else:
                    head = max(0, keep_chars // 2)
                    tail = max(0, keep_chars - head)
                    body = text[:head] + marker + (text[-tail:] if tail else "")
                return _clone_with_content(m, body)

            low, high = 0, len(text)
            best = _candidate(0)
            while low <= high:
                mid = (low + high) // 2
                cand = _candidate(mid)
                if _estimate([cand]) <= token_budget:
                    best = cand
                    low = mid + 1
                else:
                    high = mid - 1
            return best



        if independent_context:
            return self._compact_independent_context(
                messages=messages, client=client, max_tokens=max_tokens,
                tool_defs=tool_defs, chars_per_token=chars_per_token,
                compact_instructions=compact_instructions, force=force,
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name, post_hooks_async=post_hooks_async,
                system_msg=system_msg, start_idx=start_idx, cap=cap,
                trigger=trigger, _tmul=_tmul, _bucket_target=_bucket_target,
                _original_count=_original_count,
            )

        # Hot path never calls add_bucket. Bucket creation is owned
        # exclusively by core.bg_bucket_builder — either sync-fired
        # above (force=True) or async via maybe_trigger. The squeeze
        # phase below may LLM-digest the tail, but that digest stays
        # private (source.type="private_compaction") and never enters
        # the shared pyramid.

        # ── Skip/trigger check ──
        _initial_output = _build_output(messages[start_idx:])
        _original_tokens = _estimate(_initial_output)

        if not force and _original_tokens < trigger:
            return messages

        logger.info("[compact] %s: %d tokens (trigger=%d, cap=%d, %d msgs)",
                    "FORCED" if force else "TRIGGERED",
                    _original_tokens, trigger, cap, _original_count)

        # ── Pre-compact hooks ──
        _trigger_label = "manual" if force else "auto"
        _pre_ctx = {
            "trigger": _trigger_label,
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "user_id": user_id,
            "compact_instructions": compact_instructions or "",
            "force": bool(force),
            "original_tokens": _original_tokens,
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
            logger.info("[compact] pre-hook aborted compaction — returning "
                        "messages unchanged")
            return messages
        _pre_payload = _pre_result.get("payload") or {}
        compact_instructions = _pre_payload.get("compact_instructions", "") or ""
        _user_display = _pre_payload.get("user_display_message", "") or ""
        if _user_display and conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conversation_id, "compact_progress",
                    {"stage": "hook_message", "message": _user_display,
                     "agent": agent_name})
            except Exception:
                logger.debug("compact hook_message publish failed", exc_info=True)

        # Circuit breaker tracking: everything past this point counts as
        # "compact attempted" — a clean return resets the counter, any
        # exception increments. Skipped-by-trigger runs above don't
        # touch the counter so they don't mask real failures.
        try:
            # ═════════════════════════════════════════════════════════
            #  Token-budget tail selection (instant, deterministic)
            # ═════════════════════════════════════════════════════════
            # Walk back from end of transcript, accumulating tokens
            # until we approach the cap when combined with the
            # already-assembled header. Goal: output as close to cap
            # (50k for 200k model) as possible — header_actual + tail
            # ≈ cap. The user's mental model: header is bounded by
            # HEADER_BUDGET (30k) when the pyramid is full, leaving
            # 20k for tail at maximum header; but when the header is
            # smaller (fewer buckets / no rollup yet), the tail can
            # grow proportionally larger to fill the cap. Don't waste
            # cap with a fixed 20k tail ceiling — that artificially
            # caps output at 32k when header is 12k, which the user
            # called out as "Quand je dis 50k, je ne pense pas l'avoir
            # dit en rigolant."
            # Then orphan-fix: if the first kept msg is a tool/tool_
            # result whose tool_call sits OUTSIDE the kept slice,
            # extend backward to include the owning assistant turn
            # (or drop the orphan).
            # ─────────────────────────────────────────────────────────

            # Compute header-side overhead (system + pyramid bridge).
            _header_only = _build_output([])
            _header_tokens = _estimate(_header_only)
            # Bridge / format overhead headroom: each msg adds a small
            # per-message constant in _estimate (role separator etc.);
            # leave ~500 tokens of slack so the final assembled output
            # rounds under cap rather than just-over.
            _SAFETY_MARGIN = 500
            _tail_budget = max(1000, cap - _header_tokens - _SAFETY_MARGIN)

            _tail_msgs = messages[start_idx:]
            # Walk from end accumulating per-msg estimates. Tool results use
            # post-truncation cost here, matching step 2a below, so a large
            # relay/bash output does not block useful older context that still
            # fits inside the target budget after deterministic truncation.
            _accum = 0
            _take_from = len(_tail_msgs)
            _boundary_msg = None
            _boundary_original_cost = 0
            for _i in range(len(_tail_msgs) - 1, -1, -1):
                _cost = _estimate_tail_selection_cost(_tail_msgs[_i])
                if _accum + _cost > _tail_budget and _i < len(_tail_msgs) - 1:
                    # Include at LEAST one msg even if oversized — a single
                    # oversized recent msg beats an empty tail. If we already
                    # have newer messages, use the remaining budget for a
                    # truncated boundary text message instead of stopping at a
                    # tiny tail and wasting most of compact_target_tokens.
                    _remaining = _tail_budget - _accum
                    if _remaining > 0 and _tail_msgs[_i].role != "tool":
                        _candidate = _truncate_message_to_budget(
                            _tail_msgs[_i], _remaining)
                        _candidate_cost = _estimate([_candidate])
                        if _candidate_cost <= _remaining:
                            _boundary_msg = _candidate
                            _boundary_original_cost = _cost
                            _accum += _candidate_cost
                    break
                _accum += _cost
                _take_from = _i
            saved_recent = _tail_msgs[_take_from:]
            if _boundary_msg is not None:
                saved_recent = [_boundary_msg] + saved_recent
                logger.info(
                    "[compact] tail boundary message truncated: %d > "
                    "remaining budget -> %d tokens",
                    _boundary_original_cost, _estimate([_boundary_msg]))

            # Orphan tool_result fix: if the first kept msg is a tool
            # role whose tool_call_id has no preceding assistant
            # tool_call in saved_recent, extend backward until the
            # owning assistant turn is included, or drop the orphan.
            while saved_recent and saved_recent[0].role == "tool":
                _orphan_id = getattr(saved_recent[0], 'tool_call_id', '')
                _has_owner = False
                for _m in saved_recent[1:]:
                    if _m.role == "assistant" and _m.tool_calls:
                        if any(tc.id == _orphan_id for tc in _m.tool_calls):
                            _has_owner = True
                            break
                if _has_owner:
                    break
                # Extend one step back if possible
                if _take_from > 0:
                    _take_from -= 1
                    saved_recent = _tail_msgs[_take_from:]
                else:
                    # Hit start of context — drop the orphan
                    saved_recent = saved_recent[1:]
                    break

            if (len(saved_recent) == 1
                    and saved_recent[0].role != "tool"
                    and _estimate([saved_recent[0]]) > _tail_budget):
                _oversized_before = _estimate([saved_recent[0]])
                saved_recent = [
                    _truncate_message_to_budget(saved_recent[0], _tail_budget)
                ]
                _accum = _estimate(saved_recent)
                logger.info(
                    "[compact] tail oversized message truncated: %d > "
                    "budget %d -> %d tokens",
                    _oversized_before, _tail_budget, _accum)

            logger.info(
                "[compact] tail walk-back: kept %d/%d msgs "
                "(~%d tokens, budget=%d, header=%d, cap=%d)",
                len(saved_recent), len(_tail_msgs),
                _accum, _tail_budget, _header_tokens, cap)

            compacted = _build_output(saved_recent)
            new_estimate = _estimate(compacted)

            # ── STEP 2a: truncate tool results in tail (deterministic) ──
            # Should rarely fire now that the walk-back respects the
            # budget — only triggers if a single huge msg pushed us
            # over (the "include at least one" guarantee above).
            if new_estimate > cap:
                logger.info(
                    "[compact] step 2a tool-result truncate (%d > cap %d)",
                    new_estimate, cap)
                self._truncate_tool_results(saved_recent)
                compacted = _build_output(saved_recent)
                new_estimate = _estimate(compacted)

            # ── STEP 2d: force-fit (brute truncate) — hard guarantee ──
            # If we're still over cap after build_now_sync + 2a, the bg
            # builder's TAIL_TOKEN_BUDGET invariant is broken: either it
            # didn't fire (config mismatch / starved executor) or one
            # tool result is so big that even truncated to _TOOL_TRUNC_LIMIT
            # the tail busts cap. Either way we don't run an LLM call in
            # the hot path — force_fit is deterministic and guarantees
            # convergence. The WARNING is the alarm bell so chronic
            # invariant breakage gets noticed.
            if new_estimate > cap:
                logger.warning(
                    "[compact] step 2d force-fit: %d > cap %d after "
                    "tool-truncate. bg_bucket_builder TAIL_TOKEN_BUDGET "
                    "invariant likely broken — investigate why tail wasn't "
                    "absorbed.",
                    new_estimate, cap)
                compacted = self._force_fit_context(
                    compacted, cap,
                    chars_per_token=chars_per_token,
                    tool_defs=tool_defs,
                    token_multiplier=_tmul,
                    conversation_id=conversation_id)
                new_estimate = _estimate(compacted)

            logger.info("[compact] Final: %d tokens (was %d, cap %d), "
                        "%d messages (was %d)",
                        new_estimate, _original_tokens, cap,
                        len(compacted), _original_count)

            # ── Phase final: persist + orphan cleanup + SSE ──
            self._persist_context(compacted, conversation_id, agent_name)
            if conversation_id:
                self._cleanup_orphan_files(compacted, conversation_id)
            if conversation_id:
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    from core.conversation_store import ConversationStore
                    # Total messages in the conversation — what the
                    # user actually thinks of as "the conversation
                    # size". `before` / `_original_count` is the
                    # PER-AGENT context size, which starts much
                    # smaller and is meaningless to display without
                    # the total as a reference.
                    _conv_total = 0
                    try:
                        _conv_total = int(ConversationStore.instance()
                            .message_count(conversation_id))
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress", {
                            "stage": "done",
                            "agent": agent_name,
                            "before": _original_count,
                            "after": len(compacted),
                            "tokens_before": _original_tokens,
                            "tokens_after": new_estimate,
                            "target_tokens": cap,
                            "conv_total_messages": _conv_total,
                        })
                except Exception:
                    logger.debug("compact SSE publish failed", exc_info=True)
            # ── Post-compact hooks ──
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
            }
            def _run_post_hooks() -> None:
                _hooks_t0 = time.monotonic()
                logger.info(
                    "[compact] post hooks start cid=%s agent=%s async=%s",
                    conversation_id[:8], agent_name, post_hooks_async)
                try:
                    _hook_runner.run("post_compact", _post_ctx)
                except Exception:
                    logger.debug("post_compact hooks raised", exc_info=True)
                finally:
                    logger.info(
                        "[compact] post hooks done cid=%s agent=%s async=%s elapsed_ms=%.1f",
                        conversation_id[:8], agent_name, post_hooks_async,
                        (time.monotonic() - _hooks_t0) * 1000.0)
            if post_hooks_async:
                logger.info(
                    "[compact] post hooks scheduled async cid=%s agent=%s",
                    conversation_id[:8], agent_name)
                threading.Thread(
                    target=_run_post_hooks,
                    daemon=True,
                    name=f"post-compact-hooks-{conversation_id[:8]}",
                ).start()
            else:
                _run_post_hooks()
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=True)
            return compacted
        except Exception:
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=False)
            raise
