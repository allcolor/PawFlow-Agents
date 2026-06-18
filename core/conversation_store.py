"""ConversationStore — directory-based conversation storage.

Each conversation is a directory:
  data/conversations/{user}/{conv_id}/
    transcript.jsonl              — all messages (faithful replay)
    shared.jsonl                  — shared context (public messages for all agents)
    {agent}/context.jsonl         — per-agent LLM context
    extras.json                   — atomic JSON metadata (no duplication)

Stored message invariants (EVERY record in transcript/context streams):
  - msg_id  : own UUID, unique per line (not shared across records)
  - ts      : wall-clock epoch seconds when the line was written
  - seq     : per-conversation strictly-increasing integer. If line B
              follows line A in the file, seq_B > seq_A — always.

Transcript, shared context, and per-agent contexts use the same row shape:
  {"role":"assistant", "msg_id":"A", "content":"" or "text", ...}
  {"role":"thinking", "msg_id":"T", "parent_message_id":"A", ...}
  {"role":"tool_call", "msg_id":"C", "parent_message_id":"A", "tool_call_id":"tc", ...}
  {"role":"tool", "msg_id":"R", "parent_message_id":"C", "tool_call_id":"tc", ...}

Metadata lives in extras.json. Transcript/context streams are message rows only.

Per-conversation locks ensure atomicity of logical operations.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl
import core.paths as _paths

logger = logging.getLogger(__name__)

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)
import core._conversation_store_base as _csb  # noqa: E402
from core._conversation_store_paths import _CsPathsMixin  # noqa: E402
from core._conversation_store_encryption import _CsEncryptionMixin  # noqa: E402
from core._conversation_store_git import _CsGitMixin  # noqa: E402
from core._conversation_store_ctxio import _CsCtxIoMixin  # noqa: E402
from core._conversation_store_cache import _CsCacheMixin  # noqa: E402
from core._conversation_store_append import _CsAppendMixin  # noqa: E402
from core._conversation_store_agentctx import _CsAgentCtxMixin  # noqa: E402
from core._conversation_store_transcript import _CsTranscriptMixin  # noqa: E402
from core._conversation_store_sessions import _CsSessionsMixin  # noqa: E402
from core._conversation_store_maint import _CsMaintMixin  # noqa: E402


class ConversationStore(
    _CsPathsMixin,
    _CsEncryptionMixin,
    _CsGitMixin,
    _CsCtxIoMixin,
    _CsCacheMixin,
    _CsAppendMixin,
    _CsAgentCtxMixin,
    _CsTranscriptMixin,
    _CsSessionsMixin,
    _CsMaintMixin,
):
    """Singleton JSONL conversation store. Thread-safe, append-only."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or str(_paths.CONVERSATIONS_DIR))
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._conv_locks: Dict[str, _ConversationTimedRLock] = {}
        self._conv_locks_lock = threading.Lock()
        self._extras_locks: Dict[str, threading.RLock] = {}
        self._extras_locks_lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._ctx_cache: Dict[str, Dict[str, List[Dict]]] = {}  # cid -> {agent -> messages}
        self._ctx_cache_lock = threading.Lock()
        self._agent_ctx_exists_cache = set()
        self._append_agents_cache: Dict[str, set] = {}
        self._tool_parent_cache: Dict[str, Dict[str, str]] = {}
        self._hot_metadata_flush: Dict[str, Dict[str, Any]] = {}
        self._context_usage_repair_mtime: Dict[str, float] = {}
        self._cid_user: Dict[str, str] = {}  # cid -> user_id (fast lookup, no scan)
        self._enc_enabled: Dict[str, bool] = {}  # cid -> encryption-enabled (cached)
        self._loaded = False
        try:
            _csb._HOT_METADATA_EXECUTOR.submit(lambda: None)
        except Exception:
            logger.debug("hot metadata executor prestart failed", exc_info=True)

    @classmethod
    def instance(cls) -> "ConversationStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            try:
                SegmentedJsonl.close_all_append_handles()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            cls._instance = None

    def _get_conv_lock(self, cid: str) -> _ConversationTimedRLock:
        with self._conv_locks_lock:
            if cid not in self._conv_locks:
                self._conv_locks[cid] = _ConversationTimedRLock(cid)
            return self._conv_locks[cid]

    def _get_extras_lock(self, cid: str) -> threading.RLock:
        with self._extras_locks_lock:
            if cid not in self._extras_locks:
                self._extras_locks[cid] = threading.RLock()
            return self._extras_locks[cid]


















    # ── Encryption at rest (phase 4 — conversation DEK lifecycle) ─────























    # ── Git per conversation ──────────────────────────────────────────






























    # ── Cross-file UUID invariant ────────────────────────────────────
    #
    # msg_id IS a UUID — universally unique by construction. A given
    # logical message is created ONCE (LLMMessage.__post_init__ mints
    # its uuid) and the same object flows through every write path via
    # dict(msg) transforms that never touch msg_id. So the invariant
    # is preserved by construction: no runtime heuristic needed.
    #
    # If the same logical content appears with two different msg_ids,
    # that's a caller bug (someone rebuilt the LLMMessage instead of
    # reusing it). Fix the caller. Do NOT try to "realign" here by
    # guessing which row is the canonical one from ts/content — the
    # msg_id IS the identity.

    # ── Context file helpers ──────────────────────────────────────────



















    # ══════════════════════════════════════════════════════════════════
    #  SINGLE READ POINT
    # ══════════════════════════════════════════════════════════════════






    # ══════════════════════════════════════════════════════════════════
    #  CACHE
    # ══════════════════════════════════════════════════════════════════






















    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════



    # ── Create / Save ─────────────────────────────────────────────────


    # ════════════════════════════════════════════════════════════════════
    # UNIFIED PERSISTENCE ROUTER — append_message()
    # ════════════════════════════════════════════════════════════════════
    #
    # Single write path. Every message (assistant block, tool call,
    # tool result, user input, delegate request/reply, context
    # injection, display_only) goes through here exactly ONCE.
    #
    # The router decides per-message which files to write based on
    # (role, source.type, display_only, tool_calls). Atomic under the
    # per-conv lock. SSE publication is the ConversationWriter's job
    # AFTER this returns successfully — visible ⇒ persisted.
    #
    # Does NOT git_snapshot. Git commits are per-turn and called
    # explicitly by the agent loop via core.conversation_git.commit_turn().
    #
    # NO DEDUP LOGIC — each call must carry a unique msg_id. A duplicate
    # indicates an upstream bug and is the caller's responsibility.
    # ════════════════════════════════════════════════════════════════════




    # ── Context ops ───────────────────────────────────────────────────
























    # ── Transcript read ───────────────────────────────────────────────
















    # ── Metadata ──────────────────────────────────────────────────────


    # ── Extras ────────────────────────────────────────────────────────











    # ── Bindings (repository associations) ──────────────────







    # ── Delete ────────────────────────────────────────────────────────








    # ── List ──────────────────────────────────────────────────────────



    # ── Display trace ─────────────────────────────────────────────────



    # ── Cleanup ───────────────────────────────────────────────────────








    # ── Compat ────────────────────────────────────────────────────────


