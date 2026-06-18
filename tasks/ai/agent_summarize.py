"""AgentLoopTask mixin — summarization methods.

Unified approach: ALL providers use the file-based method.
1. Write text to FileStore
2. LLM reads pages via tool loop, then calls compact_result
3. Works for any size (LLM paginates), no chunking needed

Extracted from tasks/ai/agent_compaction.py.
All methods access self (AgentLoopTask instance).
"""
import logging
import uuid
from typing import List, Optional

from core.llm_client import (
    LLMClient, LLMMessage,
)
from tasks.ai._summarize_text import (
    _PTL_DROP_SCHEDULE,
    _PTL_MAX_RETRIES,
    _compact_scope_id,
    _is_ptl_error,
    _truncate_head,
)
from tasks.ai._summarize_backends import _AgentSummarizeBackendMixin

logger = logging.getLogger(__name__)



class AgentSummarizeMixin(_AgentSummarizeBackendMixin):
    """Summarization methods extracted from AgentCompactionMixin."""

    def _summarize_messages(
        self,
        old_messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        target_tokens: int = 0,
        conversation_id: str = "",
        agent_name: str = "",
        compact_instructions: str = "",
        user_id: str = "",
    ) -> str:
        """Summarize messages using the file-based approach.

        Unified strategy (all providers):
        1. Convert messages to text
        2. Write to FileStore
        3. LLM reads pages via tool loop, calls compact_result
        No chunking — the LLM paginates through the file itself.
        """
        if not target_tokens:
            target_tokens = max(500, int(max_tokens / 4))

        total_text = "\n".join(
            self._sanitize_for_llm(self._messages_to_text([m]))
            for m in old_messages)

        return self._call_summarize(
            client, total_text, target_tokens,
            user_id=user_id,
            agent_name=agent_name,
            conversation_id=conversation_id,
            compact_instructions=compact_instructions,
        )

    def _summarize_chunked(self, client: LLMClient, text: str,
                            chunk_char_limit: int,
                            target_tokens: int = 0,
                            user_id: str = "", agent_name: str = "",
                            llm_service: str = "",
                            conversation_id: str = "",
                            compact_instructions: str = "",
                            final: bool = True) -> str:
        """Divide-and-conquer summarization for inputs that don't fit one pass.

        Splits `text` into chunks ≤ `chunk_char_limit` on natural newline
        boundaries, summarizes each via `_call_summarize` (recursive call,
        each chunk fits so chunking branch never re-fires), then a final
        pass summarizes the concatenated chunk-summaries.

        Per-chunk target is sized so that the final pass input is itself
        bounded (cap chunk summaries to keep the joined input small enough
        for one CC session).
        """
        # Split on newlines, never mid-line. Greedy fill.
        lines = text.split("\n")
        chunks: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for line in lines:
            ln_len = len(line) + 1  # +1 for the newline
            if cur and cur_len + ln_len > chunk_char_limit:
                chunks.append("\n".join(cur))
                cur = [line]
                cur_len = ln_len
            else:
                cur.append(line)
                cur_len += ln_len
        if cur:
            chunks.append("\n".join(cur))

        n = len(chunks)
        # Per-chunk target so the joined output fits the final pass.
        # final_input ≈ n * per_chunk_chars; we want it ≤ chunk_char_limit
        # so the final pass does a single _call_summarize without re-chunking.
        per_chunk_target = max(500, (chunk_char_limit // n) // 4)  # chars→tokens ~/4
        logger.info(
            "[compact] chunked: %d chars → %d chunks of ≤%d chars, "
            "per-chunk target=%d tokens, final target=%d tokens",
            len(text), n, chunk_char_limit, per_chunk_target, target_tokens)

        # Intermediate chunk summaries are persisted under
        # data/runtime/compact_cache/<cid>/ keyed by sha256(chunk).
        # On crash / retry, unchanged chunks hit the cache and skip
        # re-summarization. Cleared at the end of the final pass.
        # Every compact runs in a conversation — the cache_dir helper
        # raises if conversation_id is empty (impossible-state bug).
        cache_dir = self._compact_chunk_cache_dir(conversation_id)
        resumed = 0

        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            cache_path = self._compact_chunk_cache_path(cache_dir, chunk)
            if cache_path.exists():
                try:
                    _cached = cache_path.read_text(encoding="utf-8")
                    if _cached and len(_cached.strip()) >= 20:
                        resumed += 1
                        logger.info(
                            "[compact] chunk %d/%d cached (%d chars) — "
                            "skipping LLM call",
                            i, n, len(_cached))
                        chunk_summaries.append(
                            f"=== Chunk {i}/{n} notes ===\n{_cached}")
                        continue
                except OSError:
                    pass  # fall through to recompute

            logger.info("[compact] chunk %d/%d: %d chars", i, n, len(chunk))
            _instr = (
                f"This is chunk {i}/{n} of a larger conversation. "
                f"Output bullet notes only — facts, file paths, decisions, "
                f"errors. No template, no headers."
            )
            if compact_instructions:
                _instr = f"{compact_instructions}\n\n{_instr}"
            # Per-chunk passes are intermediate: free-form notes, no
            # 7-section template (would impose a ~4000-char floor and
            # waste output tokens on small chunks).
            summary = self._call_summarize(
                client, chunk,
                target_tokens=per_chunk_target,
                user_id=user_id, agent_name=agent_name,
                llm_service=llm_service,
                conversation_id=conversation_id,
                compact_instructions=_instr,
                final=False,
            )
            # Persist immediately so a crash on chunk N+1 doesn't cost
            # us chunks 1..N. Best-effort on the WRITE only — a disk
            # failure costs the resume benefit, not correctness.
            if summary and len(summary.strip()) >= 20:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(summary, encoding="utf-8")
                except OSError as _cache_err:
                    logger.debug(
                        "[compact] chunk cache write failed (%s): %s",
                        cache_path, _cache_err)
            chunk_summaries.append(
                f"=== Chunk {i}/{n} notes ===\n{summary}")

        if resumed:
            logger.info(
                "[compact] resumed %d/%d chunk(s) from cache",
                resumed, n)

        joined = "\n\n".join(chunk_summaries)
        logger.info("[compact] chunked: joined summaries = %d chars, "
                     "running %s pass",
                     len(joined), "final" if final else "intermediate")
        _final_instr = (
            "Below are bullet notes from consecutive chunks of one large "
            "conversation. Build the overall summary from them. Drop "
            "redundancy across chunks. Apply recency weighting — emphasize "
            "the LATEST chunks."
        )
        if compact_instructions:
            _final_instr = f"{compact_instructions}\n\n{_final_instr}"
        # Recursive call: same `final` semantic as the caller. If the
        # joined chunk notes still exceed the chunk limit (rare, only
        # when n was very large), this re-chunks one more level — same
        # rule applies: intermediate stays free-form, final builds the
        # 7-section structure.
        result = self._call_summarize(
            client, joined,
            target_tokens=target_tokens,
            user_id=user_id, agent_name=agent_name,
            llm_service=llm_service,
            conversation_id=conversation_id,
            compact_instructions=_final_instr,
            final=final,
        )
        # Wipe the chunk cache only after the FINAL pass succeeded.
        # Intermediate chunked calls (final=False) are nested — leave
        # the cache to the outer caller to wipe.
        if final and result and cache_dir.is_dir():
            try:
                import shutil as _sh
                _sh.rmtree(cache_dir, ignore_errors=True)
            except Exception:
                logger.debug(
                    "[compact] chunk cache cleanup failed", exc_info=True)
        return result

    @staticmethod
    def _compact_chunk_cache_dir(conversation_id: str):
        """Directory for persisting intermediate chunk summaries.

        Every compact runs inside a conversation — there is no such
        thing as a summary without a cid. Raises if the caller passed
        an empty value so the bug surfaces where it belongs instead
        of silently disabling resume.
        """
        if not conversation_id:
            raise ValueError(
                "_compact_chunk_cache_dir requires a non-empty "
                "conversation_id — every compact runs inside a conv, "
                "and a missing cid is a caller bug")
        import core.paths as _paths
        from pathlib import Path as _Path
        safe = "".join(c for c in conversation_id if c.isalnum()
                        or c in "-_@")
        if not safe:
            raise ValueError(
                f"conversation_id {conversation_id!r} has no path-safe "
                "characters — caller should pass a real cid")
        return _Path(_paths.RUNTIME_DIR) / "compact_cache" / safe

    @staticmethod
    def _compact_chunk_cache_path(cache_dir, chunk: str):
        """On-disk path for this chunk's summary cache entry.
        Keyed by sha256(chunk) so identical chunks hit the same slot
        across crash / retry cycles."""
        if not chunk:
            raise ValueError("chunk must be non-empty")
        import hashlib as _hl
        h = _hl.sha256(chunk.encode("utf-8", errors="replace")).hexdigest()[:16]
        return cache_dir / f"chunk_{h}.txt"

    def _call_summarize(self, client: LLMClient, text: str,
                        target_tokens: int = 0,
                        user_id: str = "", agent_name: str = "",
                        llm_service: str = "",
                        conversation_id: str = "",
                        compact_instructions: str = "",
                        final: bool = True) -> str:
        """Summarize text via file-based tool loop (unified for all providers).

        1. Write text to FileStore
        2. For CC: use complete_stream (CC handles tool loop)
        3. For API: run mini tool loop with read + compact_result

        `final=True`  → produce the structured 7-section summary that the
                        agent will see (USER_INTENT/DECISIONS/…).
        `final=False` → intermediate chunk pass: free-form, just preserve
                        facts. Avoids the 7-section minimum bloat (~4000
                        chars floor) when summarizing small chunks.
        """
        _svc_id = llm_service
        _svc_ctx_max = 0
        try:
            try:
                _resolved_client, _svc_ctx_max, _resolved_svc = self._get_summarizer_client(user_id, conversation_id=conversation_id)
            except TypeError as exc:
                if "conversation_id" not in str(exc):
                    raise
                _resolved_client, _svc_ctx_max, _resolved_svc = self._get_summarizer_client(user_id)
            if _resolved_client is not None:
                _old_provider = getattr(client, "provider", "") or getattr(getattr(client, "_client", None), "provider", "")
                _new_provider = getattr(_resolved_client, "provider", "") or getattr(getattr(_resolved_client, "_client", None), "provider", "")
                if _old_provider and _new_provider and _old_provider != _new_provider:
                    logger.warning(
                        "[compact] replacing stale summarizer client provider=%s with service '%s' provider=%s",
                        _old_provider, _resolved_svc, _new_provider)
                client = _resolved_client
            if not _svc_id:
                _svc_id = _resolved_svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not _svc_id:
            raise RuntimeError(
                "No summarizer_service configured. Set `summarizer_service` "
                "in the flow/agent config — compaction has no default.")
        if not _svc_ctx_max:
            raise RuntimeError(
                f"summarizer_service '{_svc_id}' has no max_context_size "
                f"configured. Set it explicitly — chunk sizing has no default.")
        logger.info(f"[compact] summarize via summarizer_service='{_svc_id}', "
                     f"summary_target={target_tokens} tokens, "
                     f"input={len(text)} chars, "
                     f"svc max_context={_svc_ctx_max} tokens")
        if not target_tokens:
            target_tokens = 2000

        # Divide-and-conquer for inputs that don't fit one summarizer pass.
        # Chunk size = 60% of the service's max_context_size. 2/3 was
        # too aggressive (CC's internal paginated `read` loop + tool
        # scaffolding + output tipped us over the window, triggering
        # CC's own compact which loses the summary state); 1/3 was too
        # conservative (chunked 7.5M-char inputs into 33 passes, bloating
        # compact wall time). 0.6 leaves 40% headroom for tool-loop
        # overhead + output + system prompt while cutting chunk count
        # nearly in half. Tokens→chars uses ~3.5 (mixed text+code).
        _CHUNK_CHAR_LIMIT = int(_svc_ctx_max * 0.6 * 3.5)
        if len(text) > _CHUNK_CHAR_LIMIT:
            return self._summarize_chunked(
                client, text,
                chunk_char_limit=_CHUNK_CHAR_LIMIT,
                target_tokens=target_tokens,
                user_id=user_id, agent_name=agent_name,
                llm_service=llm_service,
                conversation_id=conversation_id,
                compact_instructions=compact_instructions,
                final=final,
            )

        from core.file_store import FileStore
        from core.handlers.compact_result import set_compact_key

        compact_key = "CK_" + uuid.uuid4().hex[:8]
        compact_scope = _compact_scope_id(conversation_id, compact_key)
        file_id = FileStore.instance().store(
            "compact_input.txt", text.encode("utf-8"), "text/plain",
            user_id=user_id, conversation_id=compact_scope,
            category="compact")
        logger.info("[compact] wrote %d chars as %s, key=%s", len(text), file_id, compact_key)

        set_compact_key(compact_key)

        def _pub(detail):
            # No-op SSE: the UI only displays "Compacting..." which is
            # already published by _run_bg_context_op (start/done). Per-
            # chunk / per-attempt detail is server-log territory only —
            # publishing it would flood SSE with N×retries events that
            # the UI ignores anyway.
            return

        _focus = f"\n- FOCUS: {compact_instructions}" if compact_instructions else ""
        if final:
            # Final pass: 9-section structured summary, analysis-first.
            # Inspired by Claude Code's compact prompt: the <analysis>
            # block is a scratchpad the model uses to enumerate work
            # chronologically BEFORE it compresses. Forcing the draft
            # step catches work that a straight-to-summary pass skips,
            # and it's cheap — caller strips the <analysis> tags and
            # keeps only the <summary> body. Nine sections (vs the old
            # seven) add:
            #   * Problem Solving (split from Errors)
            #   * All User Messages (verbatim — critical for intent)
            # and demand DIRECT QUOTES for Current Work + Next Step so
            # the post-compact agent doesn't drift on what was being
            # done right before the cut.
            _format_rules = (
                f"- Total output ≤ {target_tokens} tokens (hard cap).\n"
                f"- Structure your reply as TWO blocks, in this order:\n"
                f"  <analysis>…scratchpad…</analysis>\n"
                f"  <summary>…final 9-section summary…</summary>\n"
                f"\n"
                f"<analysis> — drafting scratchpad (caller strips it):\n"
                f"  1. Walk the conversation chronologically. For each segment\n"
                f"     list: user's explicit request, your approach, key\n"
                f"     decisions, file names, full code snippets, function\n"
                f"     signatures, file edits, errors + how you fixed them,\n"
                f"     specific user feedback.\n"
                f"  2. Double-check technical accuracy + completeness.\n"
                f"\n"
                f"<summary> — the authoritative output. Nine numbered\n"
                f"  sections, every one MUST be present:\n"
                f"  1. Primary Request and Intent — every user request, in detail.\n"
                f"  2. Key Technical Concepts — technologies, frameworks,\n"
                f"     patterns discussed.\n"
                f"  3. Files and Code Sections — enumerate every file read,\n"
                f"     modified, or created. Quote code snippets for the\n"
                f"     most recent / most important edits. Explain WHY\n"
                f"     each touch mattered.\n"
                f"  4. Errors and Fixes — every error hit + the fix.\n"
                f"     Call out user feedback that redirected the fix.\n"
                f"  5. Problem Solving — problems resolved + ongoing\n"
                f"     troubleshooting (separate from raw errors).\n"
                f"  6. All User Messages — list EVERY non-tool-result user\n"
                f"     message, verbatim. Critical for intent continuity.\n"
                f"  7. Pending Tasks — explicit asks still open.\n"
                f"  8. Current Work — precisely what was being worked on\n"
                f"     immediately before this summary. Include file names\n"
                f"     and code snippets. Use DIRECT QUOTES from the most\n"
                f"     recent messages showing exactly where you left off\n"
                f"     (verbatim, no paraphrase).\n"
                f"  9. Optional Next Step — the next action, IF it lines up\n"
                f"     with the user's most recent explicit request and the\n"
                f"     work you were doing at step 8. If the last task was\n"
                f"     concluded, list next steps ONLY when explicitly asked\n"
                f"     — do NOT start on tangential or old completed work\n"
                f"     without confirming. Include verbatim quotes showing\n"
                f"     what was in flight.\n"
                f"\n"
                f"- Skip raw tool output, JSON blobs, and technical plumbing.\n"
                f"- RECENCY WEIGHTING: emphasize the LATEST work. Older\n"
                f"  threads (especially content carried over from a prior\n"
                f"  compacted summary or tagged as 'earlier planning') are\n"
                f"  compressed into one short line under section 1 — just\n"
                f"  enough to know it happened. If an older topic was\n"
                f"  clearly finished or superseded, drop it. The summary's\n"
                f"  job is to set up CURRENT state, not to preserve history\n"
                f"  indefinitely."
            )
        else:
            # Intermediate chunk pass: free-form, no 9-section template.
            # Section structure has a floor that bloats per-chunk summaries
            # 5× over their target. The final pass builds structure from
            # the chunk notes.
            _format_rules = (
                f"- Output AT MOST {target_tokens} tokens. Stay terse.\n"
                f"- No headers, no template — free-form bullet notes.\n"
                f"- Preserve concrete facts ONLY: file paths, decisions "
                f"made, errors hit, commands run, file contents discussed. "
                f"No fluff, no narration, no meta-commentary.\n"
                f"- Skip raw tool output and JSON plumbing."
            )
        _analysis_note = (
            "\n- When calling compact_result(summary=...), pass ONLY the\n"
            "  9-section body. Do NOT include the <analysis>...</analysis>\n"
            "  scratchpad and do NOT wrap the body in <summary> tags. The\n"
            "  analysis is for your own drafting; the downstream reader\n"
            "  only sees what you put in `summary`."
        ) if final else ""
        prompt = (
            f"You are a summarizer. Read the file and produce a summary.\n\n"
            f"STEP 1: Read the file:\n"
            f"  read(path=\"{file_id}\", source=\"filestore\")\n"
            f"  The file may be large — paginate with offset/limit until you've read ALL of it.\n\n"
            f"STEP 2: After reading ALL pages, deliver your summary:\n"
            f"  compact_result(summary=\"<your summary>\", compact_key=\"{compact_key}\")\n\n"
            f"RULES:\n"
            f"- You may ONLY use these 2 tools: read and compact_result.\n"
            f"- Do NOT respond with text. Your ONLY output is tool calls.\n"
            f"{_format_rules}"
            f"{_analysis_note}"
            f"{_focus}\n"
            f"\ncompact_key (use EXACTLY this): {compact_key}"
        )

        _pub(f"Compacting {len(text)} chars...")

        # Detect provider
        _provider = getattr(client, 'provider', '') or (
            getattr(client, '_client', None) and getattr(client._client, 'provider', ''))

        max_retries = 3

        def _build_prompt_for(_fid: str, _ckey: str) -> str:
            """Rebuild the summarizer prompt for a given file_id + key.

            Extracted so PTL retries (which allocate a fresh file_id and
            compact_key per attempt) can regenerate the prompt verbatim
            with the new values. Structure matches the block below —
            kept identical so behaviour is unchanged on attempt 0.
            """
            return (
                f"You are a summarizer. Read the file and produce a summary.\n\n"
                f"STEP 1: Read the file:\n"
                f"  read(path=\"{_fid}\", source=\"filestore\")\n"
                f"  The file may be large — paginate with offset/limit until you've read ALL of it.\n\n"
                f"STEP 2: After reading ALL pages, deliver your summary:\n"
                f"  compact_result(summary=\"<your summary>\", compact_key=\"{_ckey}\")\n\n"
                f"RULES:\n"
                f"- You may ONLY use these 2 tools: read and compact_result.\n"
                f"- Do NOT respond with text. Your ONLY output is tool calls.\n"
                f"{_format_rules}"
                f"{_focus}\n"
                f"\ncompact_key (use EXACTLY this): {_ckey}"
            )

        def _run_once(_text: str, _fid: str, _ckey: str, _prompt: str) -> str:
            if _provider == "claude-code":
                return self._summarize_via_cc(
                    client, _prompt, _fid, _ckey, target_tokens,
                    max_retries, _pub, conversation_id, user_id,
                    compact_scope=compact_scope)
            return self._summarize_via_api(
                client, _prompt, _fid, _ckey, target_tokens,
                max_retries, _pub, conversation_id, user_id,
                compact_scope=compact_scope)

        # PTL retry loop: if the summarizer LLM itself raises a
        # prompt-too-long-family error (rare — we already chunk above
        # _CHUNK_CHAR_LIMIT, but tool-loop overhead or provider-side
        # count mismatch can still tip over), truncate the head of the
        # input text, store it as a fresh file, rebuild the prompt with
        # the new file_id + compact_key, and retry. Up to 3 shots with
        # increasing cut depth (25% / 50% / 75%). Better to ship a
        # lossy summary than leave the user blocked.
        cur_text = text
        cur_fid = file_id
        cur_key = compact_key
        cur_prompt = prompt
        issued_fids = [file_id]  # for cleanup in finally
        last_err: Optional[BaseException] = None
        try:
            for attempt in range(_PTL_MAX_RETRIES + 1):
                try:
                    return _run_once(cur_text, cur_fid, cur_key, cur_prompt)
                except Exception as e:
                    last_err = e
                    if not _is_ptl_error(e) or attempt >= _PTL_MAX_RETRIES:
                        raise
                    drop = _PTL_DROP_SCHEDULE[attempt]
                    truncated = _truncate_head(cur_text, drop)
                    if not truncated or len(truncated) >= len(cur_text):
                        logger.error(
                            "[compact] PTL retry exhausted — nothing left "
                            "to drop (attempt %d, drop=%.0f%%)",
                            attempt + 1, drop * 100)
                        raise
                    cur_text = truncated
                    cur_key = "CK_" + uuid.uuid4().hex[:8]
                    cur_fid = FileStore.instance().store(
                        "compact_input.txt", cur_text.encode("utf-8"),
                        "text/plain",
                        user_id=user_id, conversation_id=compact_scope,
                        category="compact")
                    issued_fids.append(cur_fid)
                    set_compact_key(cur_key)
                    cur_prompt = _build_prompt_for(cur_fid, cur_key)
                    logger.warning(
                        "[compact] PTL retry %d/%d: %s → drop %.0f%% "
                        "(%d → %d chars, new file=%s)",
                        attempt + 1, _PTL_MAX_RETRIES,
                        str(e)[:120], drop * 100,
                        len(text), len(cur_text), cur_fid)
            # Unreachable — loop exits via return/raise.
            raise last_err if last_err else RuntimeError(
                "compact summarizer exhausted retries without exception")
        finally:
            for _fid in issued_fids:
                try:
                    FileStore.instance().delete(_fid)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

