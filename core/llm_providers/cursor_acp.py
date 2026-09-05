"""Cursor CLI defaults for the shared outbound ACP engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.llm_providers._native_acp_runtime import (
    LLMNativeAcpRuntimeMixin,
    validate_native_acp_config,
)

PROVIDER = "cursor-acp"


def validate_cursor_acp_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Use the official CLI command and exact advertised authentication id."""
    validated = validate_native_acp_config(config, PROVIDER, ["acp"], "Cursor")
    validated["auth_method_id"] = validated["auth_method_id"] or "cursor_login"
    return validated


class LLMCursorAcpMixin(LLMNativeAcpRuntimeMixin):
    """Place before LLMAcpMixin; dispatch cursor-acp to _stream_acp."""

    def _acp_config(self) -> dict[str, Any]:
        if self.provider != PROVIDER:
            return super()._acp_config()
        return validate_cursor_acp_config(self._config_ref)

__all__ = ["PROVIDER", "LLMCursorAcpMixin", "validate_cursor_acp_config"]
