"""Cost tracking per conversation/model.

Pricing comes from the LLM service config (`cost_per_1m_input` /
`cost_per_1m_output` on the llmConnection service). There is NO hardcoded
price table — if a caller does not pass pricing, the turn is recorded at
$0. This keeps the cost consistent with what the user configured on the
service (e.g. `claude_code_llm_service` is priced at 0).
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class CostTracker:
    """Track LLM costs per conversation."""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        # conv_id -> {"total": float, "by_model": {model: {...}}}
        self._costs = {}

    def track(self, conv_id, model, tokens_in=0, tokens_out=0,
              cache_read=0, cache_write=0,
              cost_per_1m_input: Optional[float] = None,
              cost_per_1m_output: Optional[float] = None,
              cost_per_1m_cache_read: Optional[float] = None,
              cost_per_1m_cache_write: Optional[float] = None):
        """Track token usage and calculate cost for this turn.

        Pricing MUST be supplied by the caller (from the LLM service
        config). When omitted, cost is 0 — tokens are still tallied so
        `by_model` stays useful for diagnostics.

        Returns the delta cost recorded for this call (not the cumulative).
        """
        ci = float(cost_per_1m_input or 0.0)
        co = float(cost_per_1m_output or 0.0)
        # Sensible defaults when cache pricing isn't specified: read = 10%
        # of input, write = 125% of input (matches Anthropic's published
        # ratios). These kick in only when input pricing is configured.
        ccr = (float(cost_per_1m_cache_read)
               if cost_per_1m_cache_read is not None else ci * 0.1)
        ccw = (float(cost_per_1m_cache_write)
               if cost_per_1m_cache_write is not None else ci * 1.25)

        cost = (
            tokens_in * ci / 1_000_000
            + tokens_out * co / 1_000_000
            + cache_read * ccr / 1_000_000
            + cache_write * ccw / 1_000_000
        )

        with self._lock:
            if conv_id not in self._costs:
                self._costs[conv_id] = {"total": 0.0, "by_model": {}}
            entry = self._costs[conv_id]
            entry["total"] += cost
            if model not in entry["by_model"]:
                entry["by_model"][model] = {
                    "in": 0, "out": 0,
                    "cache_read": 0, "cache_write": 0, "cost": 0.0}
            m = entry["by_model"][model]
            m["in"] += tokens_in
            m["out"] += tokens_out
            m["cache_read"] += cache_read
            m["cache_write"] += cache_write
            m["cost"] += cost

        return cost

    def get_conversation_cost(self, conv_id):
        with self._lock:
            return dict(self._costs.get(conv_id, {"total": 0.0, "by_model": {}}))

    def get_total_cost(self):
        with self._lock:
            return sum(e["total"] for e in self._costs.values())
