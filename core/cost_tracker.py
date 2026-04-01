"""Cost tracking per conversation/model."""

import logging
import threading
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD)
MODEL_COSTS = {
    # Anthropic
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache_read": 0.075},
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache_read": 0.5},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6, "cache_read": 0.1},
    "o3": {"input": 2.0, "output": 8.0},
    "o3-mini": {"input": 1.1, "output": 4.4},
    "o4-mini": {"input": 1.1, "output": 4.4},
    # Default fallback
    "_default": {"input": 5.0, "output": 15.0},
}

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
        self._costs = {}  # conv_id -> {"total": float, "by_model": {model: {"in": N, "out": N, "cost": float}}}

    def track(self, conv_id, model, tokens_in=0, tokens_out=0,
              cache_read=0, cache_write=0):
        """Track token usage and calculate cost."""
        costs = self._get_model_costs(model)
        cost = (
            tokens_in * costs["input"] / 1_000_000
            + tokens_out * costs["output"] / 1_000_000
            + cache_read * costs.get("cache_read", costs["input"] * 0.1) / 1_000_000
            + cache_write * costs.get("cache_write", costs["input"] * 1.25) / 1_000_000
        )

        with self._lock:
            if conv_id not in self._costs:
                self._costs[conv_id] = {"total": 0.0, "by_model": {}}
            entry = self._costs[conv_id]
            entry["total"] += cost
            if model not in entry["by_model"]:
                entry["by_model"][model] = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
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

    def _get_model_costs(self, model):
        # Try exact match, then prefix match, then default
        if model in MODEL_COSTS:
            return MODEL_COSTS[model]
        for key in MODEL_COSTS:
            if key != "_default" and model.startswith(key):
                return MODEL_COSTS[key]
        return MODEL_COSTS["_default"]
