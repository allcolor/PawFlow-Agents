"""Prompt cache break detection for Anthropic KV cache.

Tracks system prompt, tools, and model across LLM calls to diagnose
unexpected cache misses (cache breaks). Integrated into the Anthropic
provider mixin to log warnings when cache_read_tokens drop significantly.

State is kept **per conversation**, not per client. An ``LLMConnection``
service owns a single ``LLMClient`` that every conversation using that
service shares, so a single set of ``_last_*`` fields would compare a turn
of conversation A against the previous turn of conversation B and report a
cache break on every switch — noise that hides the real ones.
"""

import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CacheBreakDetector:
    """Detect and diagnose Anthropic prompt cache breaks.

    A "cache break" occurs when cache_read_tokens drop significantly
    between consecutive calls **of the same conversation**, indicating the
    KV cache prefix was invalidated. Common causes: system prompt change,
    tools change, model switch, or conversation restructuring.
    """

    # Thresholds for detecting a cache break
    DROP_FRACTION = 0.05   # >5% drop
    DROP_ABSOLUTE = 2000   # >2000 tokens drop

    #: Conversations tracked at once. Bounded so a long-lived server does
    #: not accumulate one entry per conversation ever seen.
    MAX_TRACKED = 64

    def __init__(self):
        # key -> (system_hash, tools_hash, model, cache_read)
        self._last: OrderedDict = OrderedDict()
        # key -> (system_hash, tools_hash, model) recorded before the call
        self._pending: Dict[Any, Tuple[str, str, str]] = {}

    @staticmethod
    def _hash(data: Any) -> str:
        """Stable hash of a JSON-serializable object."""
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _key(conversation_id: str) -> str:
        # Calls without a conversation (one-shot service calls, tests) share
        # a single slot; they are not part of any conversation's prefix chain.
        return conversation_id or ""

    def record_pre_call(
        self,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        model: str,
        conversation_id: str = "",
    ) -> None:
        """Record state before an LLM call for later comparison."""
        self._pending[self._key(conversation_id)] = (
            self._hash(system_prompt or ""),
            self._hash(tools or []),
            model or "",
        )

    def check_post_call(
        self,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        conversation_id: str = "",
    ) -> Optional[str]:
        """Check if a cache break occurred and diagnose the cause.

        Args:
            cache_read_tokens: Tokens read from cache in this call.
            cache_creation_tokens: Tokens written to cache in this call.
            conversation_id: Conversation whose prefix chain this call
                belongs to. Comparisons never cross conversations.

        Returns:
            Diagnosis string if a cache break was detected, None otherwise.
        """
        key = self._key(conversation_id)
        pre = self._pending.pop(key, None)
        if pre is None:
            # No matching record_pre_call — nothing to compare against.
            return None
        pre_system, pre_tools, pre_model = pre

        diagnosis = None
        last = self._last.get(key)

        # Only diagnose if this conversation has a previous call to compare
        if last is not None and last[3] > 0:
            last_system, last_tools, last_model, last_read = last
            drop = last_read - cache_read_tokens
            drop_frac = drop / last_read if last_read else 0

            if drop > self.DROP_ABSOLUTE or drop_frac > self.DROP_FRACTION:
                # Cache break detected — find the cause
                causes = []
                if pre_model != last_model:
                    causes.append(
                        f"model changed: '{last_model}' -> '{pre_model}'"
                    )
                if pre_system != last_system:
                    causes.append("system prompt changed")
                if pre_tools != last_tools:
                    causes.append("tool definitions changed")
                if not causes:
                    causes.append("conversation prefix likely restructured (compaction?)")

                diagnosis = (
                    f"Cache break detected: cache_read dropped {last_read} -> "
                    f"{cache_read_tokens} (-{drop} tokens, -{drop_frac:.0%}). "
                    f"Cause: {'; '.join(causes)}"
                )

        # Update state for the next comparison on this conversation
        self._last[key] = (pre_system, pre_tools, pre_model, cache_read_tokens)
        self._last.move_to_end(key)
        while len(self._last) > self.MAX_TRACKED:
            self._last.popitem(last=False)

        return diagnosis
