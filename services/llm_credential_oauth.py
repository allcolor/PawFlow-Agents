"""OAuth credential provider service for CLI-backed LLM providers.

This service owns the encrypted credential pool used by Claude Code, Codex,
and Gemini CLI providers. LLM services reference it through
`credential_service_id` instead of storing login actions directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core import ServiceFactory, ServiceError
from core.base_service import BaseService

logger = logging.getLogger(__name__)

SERVICE_TYPE = "llmCredentialOAuthProvider"

# Canonical provider values used by the matching LLM services.
PROVIDERS = ("claude-code", "codex-app-server", "gemini")
_SHORT_PROVIDER = {
    "claude-code": "cc",
    "claude-code-interactive": "cc",
    "claude": "cc",
    "cc": "cc",
    "codex-app-server": "codex",
    "codex": "codex",
    "gemini": "gemini",
    "antigravity-interactive": "gemini",
    "antigravity": "gemini",
    "agy": "gemini",
}
_PROVIDER_BY_SHORT = {
    "cc": "claude-code",
    "codex": "codex-app-server",
    "gemini": "gemini",
}
_DEFAULT_CREDENTIAL_SERVICE_IDS = {
    "claude-code": "claude_code_oauth_credentials",
    "codex-app-server": "codex_oauth_credentials",
    "gemini": "gemini_oauth_credentials",
}


def normalize_provider(provider: str) -> str:
    """Return the canonical LLM provider name for a credential provider."""
    key = (provider or "").strip().lower()
    return _PROVIDER_BY_SHORT.get(_SHORT_PROVIDER.get(key, key), key)


def provider_short(provider: str) -> str:
    return _SHORT_PROVIDER.get((provider or "").strip().lower(), "")


def default_credential_service_id(provider: str) -> str:
    return _DEFAULT_CREDENTIAL_SERVICE_IDS.get(normalize_provider(provider), "")


def _all_service_defs(user_id: str = "", conv_id: str = ""):
    try:
        from core.service_registry import ServiceRegistry
        return ServiceRegistry.get_instance().resolve_all(
            user_id=user_id, conv_id=conv_id, enabled_only=False).values()
    except Exception:
        return []


def get_service_def(service_id: str, user_id: str = "", conv_id: str = ""):
    if not service_id:
        return None
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        found = reg.resolve_definition(service_id, user_id=user_id, conv_id=conv_id)
        if found:
            return found
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    for sdef in _all_service_defs(user_id=user_id, conv_id=conv_id):
        if getattr(sdef, "service_id", "") == service_id:
            return sdef
    return None


def is_credential_service_def(sdef: Any, provider: str = "") -> bool:
    if not sdef or getattr(sdef, "service_type", "") != SERVICE_TYPE:
        return False
    if not provider:
        return True
    cfg = getattr(sdef, "config", {}) or {}
    return normalize_provider(cfg.get("provider", "")) == normalize_provider(provider)


def resolve_credential_service_id(provider: str, service_id: str = "",
                                  user_id: str = "", conv_id: str = "") -> str:
    """Resolve the service id whose encrypted pool should be used.

    `service_id` may be either the new credential service id or a legacy LLM
    service id. If a matching LLM service references `credential_service_id`,
    that referenced id wins. Otherwise we return the legacy LLM id so existing
    pools keep working until migration has run.
    """
    provider = normalize_provider(provider)
    if service_id:
        sdef = get_service_def(service_id, user_id=user_id, conv_id=conv_id)
        if is_credential_service_def(sdef, provider):
            return service_id
        if sdef and getattr(sdef, "service_type", "") == "llmConnection":
            cfg = getattr(sdef, "config", {}) or {}
            if normalize_provider(cfg.get("provider", "")) == provider:
                cred_id = (cfg.get("credential_service_id") or "").strip()
                if cred_id:
                    return cred_id
                return service_id
        # Unknown id: preserve legacy behavior and let the old key lookup fail
        # naturally if there is no pool.
        return service_id

    # No explicit id: prefer LLM services that already reference a credential
    # provider, then standalone credential services, then legacy LLM services.
    legacy = ""
    for sdef in _all_service_defs(user_id=user_id, conv_id=conv_id):
        cfg = getattr(sdef, "config", {}) or {}
        if getattr(sdef, "service_type", "") == "llmConnection" and normalize_provider(cfg.get("provider", "")) == provider:
            cred_id = (cfg.get("credential_service_id") or "").strip()
            if cred_id:
                return cred_id
            legacy = legacy or getattr(sdef, "service_id", "")
    for sdef in _all_service_defs(user_id=user_id, conv_id=conv_id):
        if is_credential_service_def(sdef, provider):
            return getattr(sdef, "service_id", "")
    return legacy


def credential_pool_secret_key(service_id: str) -> str:
    return f"{service_id.replace('-', '_')}_credentials_pool"


class LLMCredentialOAuthProviderService(BaseService):
    TYPE = SERVICE_TYPE
    VERSION = "1.0.0"
    NAME = "LLM OAuth Credential Provider"
    DESCRIPTION = "Encrypted OAuth credential pools for Claude Code, Codex, and Gemini CLI providers"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    @property
    def provider(self) -> str:
        return normalize_provider(self.config.get("provider", ""))

    def _create_connection(self):
        if self.provider not in PROVIDERS:
            raise ServiceError(
                f"Unknown credential provider '{self.config.get('provider', '')}'. "
                f"Supported: {', '.join(PROVIDERS)}")
        return {"provider": self.provider, "ready": True}

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "provider": {
                "type": "select",
                "required": True,
                "default": "claude-code",
                "options": list(PROVIDERS),
                "description": "CLI provider whose OAuth credentials are stored here",
            },
            "label": {
                "type": "string",
                "default": "",
                "description": "Optional display label for this credential pool",
            },
        }

    def get_parameter_rules(self) -> list:
        return []

    def get_service_actions(self) -> list:
        return [
            {
                "id": "credential_pool_manage",
                "label": "Manage credentials",
                "icon": "",
                "flow": "credential_table",
                "server_action": "llm_credential_pool_list",
            },
            {
                "id": "claude_code_relay_login",
                "label": "Login via relay",
                "icon": "",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_list_relays",
                "flow": "claude_login_relay",
            },
            {
                "id": "claude_code_server_login",
                "label": "Login via server",
                "icon": "",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_server_login",
                "flow": "claude_login_server",
            },
            {
                "id": "claude_code_login",
                "label": "Set credentials",
                "icon": "",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_login_url",
                "flow": "oauth_code",
            },
            {
                "id": "codex_relay_login",
                "label": "Login via relay",
                "icon": "",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "claude_code_list_relays",
                "flow": "codex_login_relay",
            },
            {
                "id": "codex_server_login",
                "label": "Login via server",
                "icon": "",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "codex_server_login",
                "flow": "codex_login_server",
            },
            {
                "id": "codex_login",
                "label": "Set credentials",
                "icon": "",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "codex_login_url",
                "flow": "oauth_code",
            },
            {
                "id": "gemini_relay_login",
                "label": "Login via relay",
                "icon": "",
                "when": {"provider": ["gemini"]},
                "server_action": "claude_code_list_relays",
                "flow": "gemini_login_relay",
            },
            {
                "id": "gemini_server_login",
                "label": "Login via server",
                "icon": "",
                "when": {"provider": ["gemini"]},
                "server_action": "gemini_server_login",
                "flow": "gemini_login_server",
            },
            {
                "id": "gemini_login",
                "label": "Set credentials",
                "icon": "",
                "when": {"provider": ["gemini"]},
                "server_action": "gemini_login_url",
                "flow": "oauth_code",
            },
        ]


ServiceFactory.register(LLMCredentialOAuthProviderService)
