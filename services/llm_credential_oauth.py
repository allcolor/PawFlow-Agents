"""OAuth credential provider service for LLM providers.

This service owns the encrypted credential pool an LLM service reaches through
`credential_service_id` instead of storing login actions directly.

Four provider values. Three name a CLI vendor and carry that vendor's login
flows (Claude Code, Codex, Gemini). The fourth, `generic`, carries an identity
provider plus client id/secret instead of a vendor preset, which is what lets
an API provider use OAuth at all -- and lets one of the three CLIs be pointed
at a different OAuth-authenticated backend, since a CLI is not bound to its
vendor's identity provider either.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from core.base_service import BaseService

logger = logging.getLogger(__name__)

SERVICE_TYPE = "llmCredentialOAuthProvider"

#: Provider value for a pool that is not tied to a CLI vendor. Accepted by
#: every LLM provider, CLI included.
GENERIC = "generic"

# Canonical provider values used by the matching LLM services.
PROVIDERS = ("claude-code", "codex-app-server", "gemini", GENERIC)
_SHORT_PROVIDER = {
    "claude-code": "cc",
    "claude-code-interactive": "cc",
    "claude": "cc",
    "cc": "cc",
    "codex-app-server": "codex",
    "codex-interactive": "codex",
    "codex": "codex",
    "gemini": "gemini",
    "antigravity-interactive": "gemini",
    "antigravity": "gemini",
    "agy": "gemini",
    GENERIC: GENERIC,
}
_PROVIDER_BY_SHORT = {
    "cc": "claude-code",
    "codex": "codex-app-server",
    "gemini": "gemini",
    GENERIC: GENERIC,
}
_DEFAULT_CREDENTIAL_SERVICE_IDS = {
    "claude-code": "claude_code_oauth_credentials",
    "codex-app-server": "codex_oauth_credentials",
    "gemini": "gemini_oauth_credentials",
    GENERIC: "generic_oauth_credentials",
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
    pool_provider = normalize_provider(cfg.get("provider", ""))
    # A generic pool carries its own identity provider, so it is not bound to
    # any one LLM provider: a CLI pointed at another OAuth backend uses it the
    # same way an API provider does.
    if pool_provider == GENERIC:
        return True
    return pool_provider == normalize_provider(provider)


def resolve_credential_service_id(provider: str, service_id: str = "",
                                  user_id: str = "", conv_id: str = "") -> str:
    """Resolve the credential service id whose encrypted pool should be used."""
    provider = normalize_provider(provider)
    if service_id:
        sdef = get_service_def(service_id, user_id=user_id, conv_id=conv_id)
        if is_credential_service_def(sdef, provider):
            return service_id
        return ""

    # No explicit id: prefer LLM services that already reference a credential
    # provider, then standalone credential services.
    for sdef in _all_service_defs(user_id=user_id, conv_id=conv_id):
        cfg = getattr(sdef, "config", {}) or {}
        if getattr(sdef, "service_type", "") == "llmConnection" and normalize_provider(cfg.get("provider", "")) == provider:
            cred_id = (cfg.get("credential_service_id") or "").strip()
            if cred_id:
                return cred_id
    for sdef in _all_service_defs(user_id=user_id, conv_id=conv_id):
        if is_credential_service_def(sdef, provider):
            return getattr(sdef, "service_id", "")
    return ""


def credential_service_id_from_llm_service(provider: str, llm_service_id: str,
                                           user_id: str = "", conv_id: str = "") -> str:
    """Return the credential service referenced by an LLM service config."""
    provider = normalize_provider(provider)
    sdef = get_service_def(llm_service_id, user_id=user_id, conv_id=conv_id)
    if not sdef or getattr(sdef, "service_type", "") != "llmConnection":
        return ""
    cfg = getattr(sdef, "config", {}) or {}
    if normalize_provider(cfg.get("provider", "")) != provider:
        return ""
    cred_id = (cfg.get("credential_service_id") or "").strip()
    return resolve_credential_service_id(provider, cred_id, user_id=user_id, conv_id=conv_id)


def credential_pool_secret_key(service_id: str) -> str:
    return f"{service_id.replace('-', '_')}_credentials_pool"


class LLMCredentialOAuthProviderService(BaseService):
    TYPE = SERVICE_TYPE
    VERSION = "1.0.0"
    NAME = "LLM OAuth Credential Provider"
    DESCRIPTION = (
        "Encrypted OAuth credential pools for LLM providers: the Claude Code, "
        "Codex and Gemini CLIs, or any identity provider via 'generic'")

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
        if self.provider == GENERIC:
            # A vendor pool inherits its endpoints from the CLI it belongs to.
            # A generic pool has none to inherit, so refuse an unusable one at
            # install time rather than at the first token exchange.
            missing = [
                key for key in ("client_id", "client_secret")
                if not str(self.config.get(key) or "").strip()
            ]
            if missing:
                raise ServiceError(
                    "generic credential provider requires "
                    + " and ".join(missing))
            if not self._generic_endpoints().get("token_url"):
                raise ServiceError(
                    "generic credential provider requires a token_url, "
                    "either from identity_provider or set explicitly")
        return {"provider": self.provider, "ready": True}

    def _generic_endpoints(self) -> Dict[str, str]:
        """Resolve authorize/token URLs from the preset, honouring overrides.

        Presets come from services/auth_providers/generic_oauth.py so the two
        OAuth surfaces cannot describe the same identity provider differently.
        Placeholders such as {domain} are filled from ``preset_vars``.
        """
        from services.auth_providers.generic_oauth import PRESETS
        preset = PRESETS.get(
            str(self.config.get("identity_provider") or "").strip().lower(), {})
        variables = self.config.get("preset_vars") or {}
        resolved: Dict[str, str] = {}
        for key in ("authorize_url", "token_url", "scope"):
            value = str(self.config.get(key) or "").strip() or preset.get(key, "")
            if value and isinstance(variables, dict):
                for name, replacement in variables.items():
                    value = value.replace(
                        "{" + str(name) + "}", str(replacement))
            resolved[key] = value
        return resolved

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        from services.auth_providers.generic_oauth import PRESETS
        return {
            "provider": {
                "type": "select",
                "required": True,
                "default": "claude-code",
                "options": list(PROVIDERS),
                "description": (
                    "Which credentials this pool holds. The three CLI values "
                    "carry that vendor's login flows; 'generic' carries its "
                    "own identity provider and is accepted by every LLM "
                    "provider, CLI included."
                ),
            },
            "identity_provider": {
                "type": "select",
                "default": "",
                "options": [""] + sorted(PRESETS) + ["custom"],
                "description": (
                    "generic only: identity provider preset. Fills the "
                    "authorize and token URLs; 'custom' means set them by "
                    "hand."
                ),
            },
            "client_id": {
                "type": "string", "default": "",
                "description": "generic only: OAuth2 client ID",
            },
            "client_secret": {
                "type": "string", "default": "", "sensitive": True,
                "description": "generic only: OAuth2 client secret",
            },
            "authorize_url": {
                "type": "string", "default": "",
                "description": (
                    "generic only: authorization endpoint. Empty uses the "
                    "identity_provider preset."
                ),
            },
            "token_url": {
                "type": "string", "default": "",
                "description": (
                    "generic only: token endpoint. Empty uses the "
                    "identity_provider preset."
                ),
            },
            "scope": {
                "type": "string", "default": "",
                "description": "generic only: OAuth2 scopes",
            },
            "audience": {
                "type": "string", "default": "",
                "description": (
                    "generic only: audience claim. Several identity "
                    "providers need it to mint an API-usable token."
                ),
            },
            "preset_vars": {
                "type": "object", "default": {},
                "description": (
                    "generic only: values for preset placeholders, e.g. "
                    "{\"domain\": \"acme.okta.com\"} or {\"host\": ..., "
                    "\"realm\": ...} for Keycloak."
                ),
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
                "id": "generic_oauth_login",
                "label": "Set credentials",
                "icon": "",
                "when": {"provider": [GENERIC]},
                # The UI's oauth_code flow shows the instructions this action
                # returns, then posts the pasted document to the same name
                # with _url swapped for _code.
                "server_action": "generic_oauth_login_url",
                "flow": "oauth_code",
            },
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
                "label": "Login via server (Gemini CLI)",
                "icon": "",
                "when": {"provider": ["gemini"]},
                "server_action": "gemini_server_login",
                "flow": "gemini_login_server",
            },
            {
                "id": "agy_server_login",
                "label": "Login via server (Agy)",
                "icon": "",
                "when": {"provider": ["gemini"]},
                "server_action": "agy_server_login",
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
