"""Summarizer service.

Wraps the LLM service used for compaction summaries and carries the
background bucket-compaction tuning values.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from core import ServiceError, ServiceFactory
from core.base_service import BaseService
from core.bucket_store import (
    BUCKET_OUTPUT_TARGET, HEADER_BUDGET, L1_TRIGGER_MSGS,
    ROLLUP_TRIGGER_COUNT, TAIL_RESERVE, TAIL_TOKEN_BUDGET,
)

logger = logging.getLogger(__name__)


_BG_DEFAULTS: Dict[str, Any] = {
    "l1_trigger_msgs": L1_TRIGGER_MSGS,
    "bucket_target_tokens": BUCKET_OUTPUT_TARGET,
    "header_budget_tokens": HEADER_BUDGET,
    "rollup_trigger_count": ROLLUP_TRIGGER_COUNT,
    "tail_reserve_msgs": TAIL_RESERVE,
    "tail_token_budget": TAIL_TOKEN_BUDGET,
    "token_trigger_fraction": 0.7,
    "bulk_catchup_multiplier": 5,
    "partial_min_msgs": 5,
    "min_input_multiplier": 4,
    "chars_per_token": 3.5,
    "overshoot_warn_multiplier": 1.5,
    "header_char_multiplier": 3.0,
}


class SummarizerService(BaseService):
    """Service selecting the LLM and tuning values used for summarization."""

    TYPE = "summarizer"
    VERSION = "1.0.0"
    NAME = "Summarizer Service"
    DESCRIPTION = "Selects the LLM service and background compaction settings for summaries"

    def _create_connection(self):
        llm_service = str(self.config.get("llm_service", "") or "")
        if not llm_service:
            raise ServiceError("llm_service is required")
        return {"ready": True, "llm_service": llm_service}

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "llm_service": {
                "type": "service_ref",
                "service_type": "llmConnection",
                "required": True,
                "default": "",
                "description": "LLM service used to execute summarization calls",
            },
            "l1_trigger_msgs": {
                "type": "integer", "default": L1_TRIGGER_MSGS,
                "description": "Shared-message count used for a level-1 background bucket",
            },
            "bucket_target_tokens": {
                "type": "integer", "default": BUCKET_OUTPUT_TARGET,
                "description": "Target token size for level-1 and rollup summaries",
            },
            "header_budget_tokens": {
                "type": "integer", "default": HEADER_BUDGET,
                "description": "Pyramid header token budget before rollup pressure",
            },
            "rollup_trigger_count": {
                "type": "integer", "default": ROLLUP_TRIGGER_COUNT,
                "description": "Object-count ceiling before consolidating old buckets",
            },
            "tail_reserve_msgs": {
                "type": "integer", "default": TAIL_RESERVE,
                "description": "Recent shared messages never absorbed into buckets",
            },
            "tail_token_budget": {
                "type": "integer", "default": TAIL_TOKEN_BUDGET,
                "description": "Estimated transcript-token budget since last pyramid coverage",
            },
            "token_trigger_fraction": {
                "type": "float", "default": 0.7,
                "description": "Fraction of tail_token_budget that triggers async bucketing",
            },
            "bulk_catchup_multiplier": {
                "type": "float", "default": 5,
                "description": "Empty-pyramid shortcut threshold multiplier",
            },
            "partial_min_msgs": {
                "type": "integer", "default": 5,
                "description": "Minimum shared messages for a partial bucket",
            },
            "min_input_multiplier": {
                "type": "float", "default": 4,
                "description": "Minimum useful input as bucket_target_tokens multiplier",
            },
            "chars_per_token": {
                "type": "float", "default": 3.5,
                "description": "Token estimation ratio for background triggers",
            },
            "overshoot_warn_multiplier": {
                "type": "float", "default": 1.5,
                "description": "Warn when summaries exceed target by this multiplier",
            },
            "header_char_multiplier": {
                "type": "float", "default": 3.0,
                "description": "Converts header_budget_tokens into rollup pressure chars",
            },
        }

    @staticmethod
    def _coerce_positive_int(name: str, raw: Any, default: int,
                             minimum: int = 1) -> int:
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            logger.warning("[summarizer] invalid %s=%r; using default %r",
                           name, raw, default)
            return default
        if value < minimum:
            logger.warning("[summarizer] invalid %s=%r; minimum is %d; using default %r",
                           name, raw, minimum, default)
            return default
        return value

    @staticmethod
    def _coerce_positive_float(name: str, raw: Any, default: float,
                               minimum: float = 0.0) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning("[summarizer] invalid %s=%r; using default %r",
                           name, raw, default)
            return default
        if value <= minimum:
            logger.warning("[summarizer] invalid %s=%r; must be > %s; using default %r",
                           name, raw, minimum, default)
            return default
        return value

    def bg_compact_config(self) -> Dict[str, Any]:
        """Return validated background-compaction tuning values."""
        raw = {name: self.config.get(name, default)
               for name, default in _BG_DEFAULTS.items()}
        return {
            "l1_trigger_msgs": self._coerce_positive_int(
                "l1_trigger_msgs", raw["l1_trigger_msgs"], L1_TRIGGER_MSGS),
            "bucket_target_tokens": self._coerce_positive_int(
                "bucket_target_tokens", raw["bucket_target_tokens"], BUCKET_OUTPUT_TARGET),
            "header_budget_tokens": self._coerce_positive_int(
                "header_budget_tokens", raw["header_budget_tokens"], HEADER_BUDGET),
            "rollup_trigger_count": self._coerce_positive_int(
                "rollup_trigger_count", raw["rollup_trigger_count"], ROLLUP_TRIGGER_COUNT),
            "tail_reserve_msgs": self._coerce_positive_int(
                "tail_reserve_msgs", raw["tail_reserve_msgs"], TAIL_RESERVE,
                minimum=0),
            "tail_token_budget": self._coerce_positive_int(
                "tail_token_budget", raw["tail_token_budget"], TAIL_TOKEN_BUDGET),
            "token_trigger_fraction": self._coerce_positive_float(
                "token_trigger_fraction", raw["token_trigger_fraction"], 0.7),
            "bulk_catchup_multiplier": self._coerce_positive_float(
                "bulk_catchup_multiplier", raw["bulk_catchup_multiplier"], 5),
            "partial_min_msgs": self._coerce_positive_int(
                "partial_min_msgs", raw["partial_min_msgs"], 5),
            "min_input_multiplier": self._coerce_positive_float(
                "min_input_multiplier", raw["min_input_multiplier"], 4),
            "chars_per_token": self._coerce_positive_float(
                "chars_per_token", raw["chars_per_token"], 3.5),
            "overshoot_warn_multiplier": self._coerce_positive_float(
                "overshoot_warn_multiplier", raw["overshoot_warn_multiplier"], 1.5),
            "header_char_multiplier": self._coerce_positive_float(
                "header_char_multiplier", raw["header_char_multiplier"], 3.0),
        }

    def resolve_llm_service(self, user_id: str = "", conversation_id: str = "") -> Tuple[Any, int, str]:
        """Resolve the configured LLM service live instance."""
        llm_service = str(self.config.get("llm_service", "") or "")
        if not llm_service:
            return None, 0, ""
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc = reg.resolve(llm_service, user_id=user_id, conv_id=conversation_id)
        if svc and hasattr(svc, "complete"):
            ctx_max = int((getattr(svc, "config", {}) or {}).get("max_context_size", 0) or 0)
            return svc, ctx_max, llm_service
        return None, 0, llm_service


ServiceFactory.register(SummarizerService)
