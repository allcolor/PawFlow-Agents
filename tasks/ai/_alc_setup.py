"""agent_core split (<=800 lines): _ALCSetupMixin."""
import logging
import time
from typing import Dict, List

from core.llm_client import LLMClient, LLMMessage

from tasks.ai._alc_base import (  # noqa: F401
    _ALCState, _ALC_BREAK, _ALC_CONTINUE, _strip_context_ack,
    _preempt_rescue_requires_retrigger, _apply_bg_results, _svc_rates,
    _usage_cost_usd, _check_budget, _CONTEXT_ACK_PATTERNS)

logger = logging.getLogger(__name__)


def _repair_tool_result_order(messages, conversation_id):
    """Make every assistant tool-call block valid for strict API providers.

    Delegates to the shared ``core.llm_tool_sequence`` repair, which also
    fixes results persisted BEFORE their assistant message, duplicates,
    orphans, and interleaved results — the cases a cancel/compact/rewind can
    leave behind and that strict providers reject with a 400.
    """
    from core.llm_tool_sequence import repair_tool_sequence
    return repair_tool_sequence(messages, conversation_id)


class _ALCSetupMixin:
    def _alc_setup(self, st):
        st.conversation_id = st.ctx.get("conversation_id", "")
        st.start_time = time.time()
        st.ctx["_started_at"] = st.start_time
        st.total_tokens_in = 0
        st.total_tokens_out = 0
        st.total_cache_read = 0
        st.total_cache_write = 0
        st.tools_called: List[str] = []
        st.iteration = 0
        st.final_model = ""
        st.finish_reason = ""
        st.response_content = ""
        st.final_msg_id = ""
        # Per-turn reset: without it, a turn that persists no visible text
        # inherits the PREVIOUS turn's last message id and names it final.
        if hasattr(st, "client") and st.client is not None:
            st.client._last_turn_msg_id = ""
            st.client._last_turn_visible_msg_id = ""
        st._need_more_retried = False
        st._fatal_error = False
        st._fatal_error_msg = ""
        # Cumulative spend-budget precheck (core/budget_store.py) runs once
        # per external turn, on the first _alc_llm_turn call — see there.
        st._budget_precheck_done = False

        st.client: LLMClient = st.ctx["client"]
        st.registry = st.ctx["registry"]
        st.tool_defs = st.ctx["tool_defs"]
        st.messages: List[LLMMessage] = st.ctx["messages"]
        st.model = st.ctx["model"]
        st.conversation_id = st.ctx.get("conversation_id", "")
        st.use_conv_store = st.ctx.get("use_conv_store", False)
        st.user_id = st.ctx.get("user_id", "")

        # Each main agent loop runs on its OWN cloned LLMClient — fully
        # isolated from the resolver's singleton. Concurrent main agents
        # (different conversations / users on the same server) would
        # otherwise clobber each other's per-call state on `self.*`:
        #   * client._claude_proc — set per spawn; A's preempt would
        #     route to B's proc after B's spawn overwrote it.
        #   * client._conversation_id / _agent_name — read by
        #     send_user_message to identify the active stream; with
        #     concurrent setattrs the wrong (conv, agent) wins.
        #   * client._cc_container_pid / _current_session_id /
        #     _result_emitted / _compacting / _stderr_buffer / etc.
        # Compact / memory-extract / btw / sub-agent delegate already
        # clone for their calls; main was the last remaining path on
        # the shared singleton. Closing the gap.
        if hasattr(st.client, 'clone_for_call'):
            st.client = st.client.clone_for_call()
        # Set context on client for providers that need it (claude-code)
        st.client._conversation_id = st.conversation_id
        st.client._user_id = st.user_id
        st.client._agent_name = st.ctx.get("active_agent_name", "")
        st.client._agent_service = st.ctx.get("active_llm_service", "")
        st.client._event_cid = st.ctx.get("_event_cid", st.conversation_id)
        st.client._agent_ctx = st.ctx  # for SSE event enrichment (task_iteration etc)
        # Whether this turn's context is a resume delta belongs to the
        # context, and is stamped on the turn's OWN clone. The resolver
        # singleton is shared by every conversation on the service: a marker
        # left there is read, or cleared, by whichever turn reaches it next.
        st.client._pawflow_context_is_delta = bool(
            st.ctx.get("_context_is_delta", False))
        # PawFlow budget. Provider-reported windows are hard caps, not a
        # reason to exceed a smaller configured context budget.
        st.client._max_context_size = int(st.ctx.get("max_context_size", 0) or 0)

        # Register active LLM client for cancellation/preempt. CLI providers
        # expose send_user_message for soft interrupts; API providers expose
        # abort() so force-stop can break a blocking HTTP stream instead of
        # merely hiding the UI row.
        st._agent_name_key = f"{st.conversation_id}:{st.ctx.get('active_agent_name', '')}" if st.ctx.get('active_agent_name') else st.conversation_id
        if st.conversation_id and (hasattr(st.client, 'send_user_message') or hasattr(st.client, 'abort')):
            with self._active_contexts_lock:
                self._active_claude_client[st._agent_name_key] = st.client
        # Clear cancelled relay/tool state from previous run for every
        # provider, not only CLI providers.
        if st.conversation_id:
            try:
                from services.tool_relay_service import ToolRelayService
                ToolRelayService.uncancel_agent(
                    st.conversation_id, st.ctx.get("active_agent_name", ""))
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        st.max_rounds = max(0, int(st.ctx.get("max_rounds", 0) or 0))
        st._consecutive_tool: Dict[str, int] = {}
        st._max_consec = max(
            0, int(st.ctx.get("max_consecutive_tool_calls", 0) or 0))
        self._alc_apply_model_overrides(st)
        # Client metadata
        st._client_provider = getattr(st.client, "provider", "") or ""
        if not isinstance(st._client_provider, str):
            st._client_provider = ""
        st._client_model = getattr(st.client, "default_model", "") or ""
        st._client_base_url = getattr(st.client, "base_url", "") or ""
        if not isinstance(st._client_base_url, str):
            st._client_base_url = ""

        st._agent_source = lambda tok_in=0, tok_out=0, model_override='', tok_cache_creation=0, tok_cache_read=0, include_context=True: self._alc_agent_source(st, tok_in, tok_out, model_override, tok_cache_creation, tok_cache_read, include_context)

        st._agent_source_cached = lambda tok_in=0, tok_out=0, model_override='', tok_cache_creation=0, tok_cache_read=0: self._alc_agent_source_cached(st, tok_in, tok_out, model_override, tok_cache_creation, tok_cache_read)

        st._patch_cc_turn_gauge = lambda response, msg_id: self._alc_patch_cc_turn_gauge(st, response, msg_id)

        st._schedule_cc_turn_gauge_patch = lambda response, msg_id, reason: self._alc_schedule_cc_turn_gauge_patch(st, response, msg_id, reason)

        # New messages tracking
        st.new_messages: List[LLMMessage] = []
        st.all_assistant_msg_ids: List[str] = []  # survives flush, for done event
        st.base_count = st.ctx.get("_base_message_count", 0)
        if len(st.messages) > st.base_count:
            st.new_messages.extend(st.messages[st.base_count:])

        self._alc_materialize_initial_context(st)

        st._auto_compact_state = {"running": False, "handoff": False}

        st._set_provider_compact_barrier = lambda reason: self._alc_set_provider_compact_barrier(st, reason)

        st._clear_provider_compact_barrier = lambda : self._alc_clear_provider_compact_barrier(st)

        st._compact_threshold_fraction = lambda raw_value: self._alc_compact_threshold_fraction(st, raw_value)

        st._agent_compact_threshold_fraction = lambda : self._alc_agent_compact_threshold_fraction(st)

        st._cold_start_trigger_fraction = lambda : self._alc_cold_start_trigger_fraction(st)

        st._auto_compact_usage = lambda max_ctx, source: self._alc_auto_compact_usage(st, max_ctx, source)

        st._maybe_auto_compact_after_append = lambda msg, reason: self._alc_maybe_auto_compact_after_append(st, msg, reason)

        st._append = lambda msg: self._alc_append(st, msg)

        st.messages, st._repaired = _repair_tool_result_order(
            st.messages, st.conversation_id)
        if st._repaired:
            logger.warning(
                "[agent:%s] repaired interrupted tool-call ordering in context",
                st.conversation_id[:8])

        # Start file checkpoint for /rewind support
        st._cp_id = ""
        if st.use_conv_store and st.conversation_id and not st.ctx.get("is_poll"):
            try:
                from core.checkpoint import CheckpointManager
                st._cp_id = CheckpointManager.start_checkpoint(st.conversation_id)
            except Exception as _cp_err:
                logger.debug(f"[checkpoint] init failed: {_cp_err}")
        self._alc_wire_registry(st)

        st.emitter.on_loop_start(st.ctx)
        st._summ = st.ctx.get("summarizer", (None, 0, ""))
        st.compact_client = st._summ[0]  # NO FALLBACK — if None, compact will error (by design)
        st._compact_svc_id = st._summ[2] if len(st._summ) > 2 else ""

        # Note: post-response compact is done by auto-compact on load
        # in _prepare_agent_context. No lazy flag needed.

    def _alc_apply_model_overrides(self, st):
        """Apply fast-mode and per-agent model overrides to st.model."""
        if not (st.use_conv_store and st.conversation_id):
            return
        try:
            from core.conversation_store import ConversationStore
            from tasks.ai.agent_utils import _resolve_extra
            st._cs = ConversationStore.instance()
            st._agent_n = st.ctx.get("active_agent_name") or ""
            # Fast mode: override model with fast variant
            st._fast = _resolve_extra(st._cs, st.conversation_id, "fast_mode", st.user_id)
            if st._fast:
                st.model = st._fast
            # Per-agent model override (takes priority over fast)
            st._mo = _resolve_extra(
                st._cs, st.conversation_id,
                f"model_override:{st._agent_n}", st.user_id)
            if st._mo:
                st.model = st._mo
        except Exception:
            logger.debug("exception suppressed", exc_info=True)

    def _alc_wire_registry(self, st):
        """Wire this turn's identity into the tool registry's handlers.

        Handlers are per-registry, and a rebuilt context brings a new one:
        without this, spawned sub-agents lose their source agent and file
        tools lose the checkpoint /rewind restores from.
        """
        from core.tool_registry import SpawnAgentsHandler as _SAH
        from core.handlers._fs_base import BaseFsHandler as _BFH
        try:
            _tools = st.registry.list_tools()
        except Exception:
            logger.debug("registry wiring skipped", exc_info=True)
            return
        for _h in _tools:
            if isinstance(_h, _SAH):
                _h.set_source_agent(
                    st.ctx.get("active_agent_name", ""),
                    st.ctx.get("active_llm_service", ""))
                break
        if getattr(st, "_cp_id", ""):
            for _h in _tools:
                if isinstance(_h, _BFH):
                    _h.set_checkpoint_id(st._cp_id)

    def _alc_materialize_initial_context(self, st):
        """Persist the start context a cold build just produced.

        When an agent context does not exist, preparation builds it from
        PawFlow shared context. Materialize that exact start context
        immediately so the context editor and later turns see the same state
        as the provider.
        """
        if not (st.ctx.get("_materialize_pawflow_initial_context") and st.use_conv_store
                and st.conversation_id and st.ctx.get("active_agent_name")):
            return
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().save_agent_context(
                st.conversation_id, st.ctx.get("active_agent_name", ""),
                self._serialize_messages(st.messages))
            logger.info(
                "[context:%s] materialized PawFlow initial %s context for %s: %d messages",
                st.conversation_id[:8],
                st.ctx.get("_pawflow_initial_context_source") or "shared",
                st.ctx.get("active_agent_name", ""), len(st.messages))
            st.ctx["_materialize_pawflow_initial_context"] = False
        except Exception:
            logger.warning(
                "[context:%s] failed to materialize PawFlow initial context for %s",
                st.conversation_id[:8], st.ctx.get("active_agent_name", ""),
                exc_info=True)

    def _alc_rebind_context(self, st, new_ctx):
        """Point a running loop at a context that was just rebuilt.

        The turn keeps its own identity -- iteration count, token totals,
        tools already called, its /rewind checkpoint -- but everything
        DERIVED from the context is recomputed, because a rebuild can hand
        back a different client, a different tool registry, a different tool
        list and a different message list. Patching a handful of fields left
        the loop straddling two contexts: the provider read the new tool defs
        from ctx while tools still executed through the old registry, and
        cancel/preempt still pointed at the clone of the abandoned one.
        """
        st.ctx.update(new_ctx)

        # Same clone boundary as the initial setup: the loop never runs on
        # the resolver's singleton.
        st.client = st.ctx["client"]
        if hasattr(st.client, 'clone_for_call'):
            st.client = st.client.clone_for_call()
        st.client._conversation_id = st.conversation_id
        st.client._user_id = st.user_id
        st.client._agent_name = st.ctx.get("active_agent_name", "")
        st.client._agent_service = st.ctx.get("active_llm_service", "")
        st.client._event_cid = st.ctx.get("_event_cid", st.conversation_id)
        st.client._agent_ctx = st.ctx
        st.client._pawflow_context_is_delta = bool(
            st.ctx.get("_context_is_delta", False))
        st.client._max_context_size = int(st.ctx.get("max_context_size", 0) or 0)
        # Cancel/preempt must reach the client that is actually running.
        if st.conversation_id and (hasattr(st.client, 'send_user_message')
                                   or hasattr(st.client, 'abort')):
            with self._active_contexts_lock:
                self._active_claude_client[st._agent_name_key] = st.client

        st.registry = st.ctx["registry"]
        st.tool_defs = st.ctx["tool_defs"]
        st.model = st.ctx["model"]
        self._alc_apply_model_overrides(st)
        st._client_provider = getattr(st.client, "provider", "") or ""
        if not isinstance(st._client_provider, str):
            st._client_provider = ""
        st._client_model = getattr(st.client, "default_model", "") or ""
        st._client_base_url = getattr(st.client, "base_url", "") or ""
        if not isinstance(st._client_base_url, str):
            st._client_base_url = ""
        self._alc_wire_registry(st)

        # Replace the message list IN PLACE: ctx, the emitter and every
        # closure built at setup hold this exact object.
        st.messages[:] = list(st.ctx["messages"] or [])
        st.ctx["messages"] = st.messages
        st.base_count = len(st.messages)
        st.ctx["_base_message_count"] = st.base_count
        # Everything this turn produced so far is persisted, and the rebuild
        # loaded it back: nothing is pending anymore.
        st.new_messages.clear()
        st.ctx.pop("_context_usage_cache", None)
        st.ctx.pop("_auto_compact_usage_cache", None)
        st.llm_context = list(st.messages)
        self._alc_materialize_initial_context(st)
