"""Prompt cache break detection for Anthropic KV cache.

Tracks system prompt, tools, and model across LLM calls to diagnose
unexpected cache misses (cache breaks). Integrated into the Anthropic
provider mixin to log warnings when cache_read_tokens drop significantly.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CacheBreakDetector:
    """Detect and diagnose Anthropic prompt cache breaks.

    A "cache break" occurs when cache_read_tokens drop significantly
    between consecutive calls, indicating the KV cache prefix was
    invalidated. Common causes: system prompt change, tools change,
    model switch, or conversation restructuring.
    """

    # Thresholds for detecting a cache break
    DROP_FRACTION = 0.05   # >5% drop
    DROP_ABSOLUTE = 2000   # >2000 tokens drop

    def __init__(self):
        self._last_system_hash: str = ""
        self._last_tools_hash: str = ""
        self._last_model: str = ""
        self._last_cache_read: int = 0

    @staticmethod
    def _hash(data: Any) -> str:
        """Stable hash of a JSON-serializable object."""
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def record_pre_call(
        self,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        model: str,
    ) -> None:
        """Record state before an LLM call for later comparison."""
        self._pre_system_hash = self._hash(system_prompt or "")
        self._pre_tools_hash = self._hash(tools or [])
        self._pre_model = model or ""

    def check_post_call(
        self,
        cache_read_tokens: int,
        cache_creation_tokens: int,
    ) -> Optional[str]:
        """Check if a cache break occurred and diagnose the cause.

        Args:
            cache_read_tokens: Tokens read from cache in this call.
            cache_creation_tokens: Tokens written to cache in this call.

        Returns:
            Diagnosis string if a cache break was detected, None otherwise.
        """
        diagnosis = None

        # Only diagnose if we have a previous call to compare against
        if self._last_cache_read > 0:
            drop = self._last_cache_read - cache_read_tokens
            drop_frac = drop / self._last_cache_read if self._last_cache_read else 0

            if drop > self.DROP_ABSOLUTE or drop_frac > self.DROP_FRACTION:
                # Cache break detected — find the cause
                causes = []
                if self._pre_model != self._last_model:
                    causes.append(
                        f"model changed: '{self._last_model}' -> '{self._pre_model}'"
                    )
                if self._pre_system_hash != self._last_system_hash:
                    causes.append("system prompt changed")
                if self._pre_tools_hash != self._last_tools_hash:
                    causes.append("tool definitions changed")
                if not causes:
                    causes.append("conversation prefix likely restructured (compaction?)")

                diagnosis = (
                    f"Cache break detected: cache_read dropped {self._last_cache_read} -> "
                    f"{cache_read_tokens} (-{drop} tokens, -{drop_frac:.0%}). "
                    f"Cause: {'; '.join(causes)}"
                )

        # Update state for next comparison
        self._last_system_hash = getattr(self, "_pre_system_hash", "")
        self._last_tools_hash = getattr(self, "_pre_tools_hash", "")
        self._last_model = getattr(self, "_pre_model", "")
        self._last_cache_read = cache_read_tokens

        return diagnosis
