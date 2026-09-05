"""Grok Build CLI defaults and private completion support for outbound ACP."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core._llm_types import LLMClientError
from core.acp.grok_session import GrokAcpProcessSession
from core.llm_providers._native_acp_runtime import (
    LLMNativeAcpRuntimeMixin,
    validate_native_acp_config,
)

PROVIDER = "grok-build-acp"


def validate_grok_build_acp_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Authenticate headlessly with a configured API key or the CLI's cached login."""
    validated = validate_native_acp_config(config, PROVIDER, ["agent", "stdio"], "Grok Build")
    if not validated["auth_method_id"]:
        api_key = validated["env"].get("XAI_API_KEY", "")
        validated["auth_method_id"] = "xai.api_key" if api_key.strip() else "cached_token"
    return validated


class LLMGrokBuildAcpMixin(LLMNativeAcpRuntimeMixin):
    """Place before LLMAcpMixin; dispatch grok-build-acp to _stream_acp."""

    def _acp_config(self) -> dict[str, Any]:
        if self.provider != PROVIDER:
            return super()._acp_config()
        return validate_grok_build_acp_config(self._config_ref)

    def _acp_process_class(self) -> type:
        if self.provider != PROVIDER:
            return super()._acp_process_class()
        return GrokAcpProcessSession

    def _acp_authenticate(self, process: Any, initialized: Any, config: Mapping[str, Any]) -> None:
        if self.provider != PROVIDER:
            return super()._acp_authenticate(process, initialized, config)
        method_id = config["auth_method_id"]
        advertised = {method.id for method in initialized.auth_methods or []}
        if method_id not in advertised:
            raise LLMClientError(
                f"Grok ACP authentication method {method_id!r} is unavailable; "
                "run grok login or configure XAI_API_KEY in acp_env"
            )
        # The Python SDK puts keyword extras directly in wire _meta.
        process.call("authenticate", method_id=method_id, headless=True)


__all__ = ["PROVIDER", "LLMGrokBuildAcpMixin", "validate_grok_build_acp_config"]
