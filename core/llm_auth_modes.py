"""Credential modes for an LLM service: none, api_key, or oauth.

Authentication used to be decided by provider family: CLI providers could use
an OAuth credential pool, everything else had to carry an ``api_key``. That is
not a property of the provider -- an API endpoint can sit behind an identity
provider, and a CLI can be pointed at an unauthenticated local gateway -- so
the mode is now explicit and the same three values apply to every provider.

``none`` exists on purpose rather than being spelled "api_key with an empty
key". Collapsing the two makes a forgotten key indistinguishable from a
deliberate choice: the service would install cleanly and fail later at the
provider with a 401 nobody can place. Explicit modes let validation refuse an
empty ``api_key`` service at install time, which is the rule the rest of the
project already follows -- no silent fallbacks.
"""

from __future__ import annotations

NONE = "none"
API_KEY = "api_key"
OAUTH = "oauth"

MODES = (NONE, API_KEY, OAUTH)

#: Providers that reach their credentials through a CLI binary's own login.
#: They default to ``oauth`` because that is how they are normally set up.
CLI_PROVIDERS = frozenset({
    "claude-code", "claude-code-interactive", "antigravity-interactive",
    "codex-app-server", "codex-interactive", "gemini", "acp",
    "cc_mcp", "codex_mcp", "agy_mcp",
})


def default_mode(provider: str, config: dict) -> str:
    """The mode a service without an explicit one is treated as having.

    Chosen so that every service that worked before this field existed keeps
    working untouched: a CLI service with a credential pool is ``oauth``, a
    service with a key is ``api_key``, and a CLI service with neither is
    ``none`` (its binary may already hold a session).
    """
    config = config or {}
    if str(config.get("credential_service_id") or "").strip():
        return OAUTH
    if (str(config.get("api_key") or "").strip()
            or str(config.get("api_keys_pool") or "").strip()):
        return API_KEY
    return NONE if str(provider or "") in CLI_PROVIDERS else API_KEY


def resolve_mode(provider: str, config: dict) -> str:
    """Return the configured mode, or the inferred default when unset."""
    config = config or {}
    mode = str(config.get("auth_mode") or "").strip().lower()
    if mode in MODES:
        return mode
    return default_mode(provider, config)


def validation_error(provider: str, config: dict) -> str:
    """Return why this service cannot authenticate, or "" when it can.

    The same rule for every provider. Returning a message rather than raising
    keeps the caller free to wrap it in its own service error type.
    """
    config = config or {}
    mode = resolve_mode(provider, config)
    has_key = bool(str(config.get("api_key") or "").strip()
                   or str(config.get("api_keys_pool") or "").strip())
    has_pool = bool(str(config.get("credential_service_id") or "").strip())
    if mode == API_KEY and not has_key:
        return ("auth_mode=api_key requires api_key or api_keys_pool. "
                "Use auth_mode=none for an endpoint that takes no "
                "credential, so a forgotten key cannot look deliberate.")
    if mode == OAUTH and not has_pool:
        return ("auth_mode=oauth requires credential_service_id pointing at "
                "an llmCredentialOAuthProvider service")
    if mode == NONE and (has_key or has_pool):
        return ("auth_mode=none must not carry an api_key or a "
                "credential_service_id; clear them or pick another mode")
    return ""
