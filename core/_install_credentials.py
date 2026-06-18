"""LLM credential pool and service/spec builders for first-run install.

Extracted from install_bootstrap.py to keep files <=800 lines. Depends only on
core._install_base (downward-only). The install_bootstrap facade re-exports the
public names (prepare_llm_credential_pool, save_llm_credential).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from core._install_base import (
    AUTH_GATEWAY_SERVICE_ID,
    FIRST_RUN_AGENT,
    SUMMARIZER_SERVICE_ID,
    _ensure_bootstrap_open,
    _load_state,
    _store_global_secret,
    _write_state,
)


def _parse_csv_or_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value or "").replace(";", ",").split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _payload_value(payload: Dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _provider_secret(payload: Dict[str, Any], provider: str, field: str) -> str:
    value = _payload_value(
        payload,
        f"auth_{provider}_{field}",
        f"oauth_{provider}_{field}",
        f"{provider}_{field}",
    )
    if not value:
        return ""
    secret_ref = f"auth.{provider}.{field}"
    _store_global_secret(secret_ref, value)
    return "${" + secret_ref + "}"


def _build_auth_gateway_config(payload: Dict[str, Any], admin_username: str) -> Dict[str, Any]:
    """Build the final AuthGateway provider config from installer input."""
    providers: Dict[str, Dict[str, Any]] = {"builtin": {"enabled": True}}
    selected = set(_parse_csv_or_list(payload.get("auth_providers")))
    generic_provider_name = ""
    auth_provider = str(payload.get("auth_provider") or "builtin").strip()
    if auth_provider and auth_provider != "builtin":
        selected.add(auth_provider)

    known_oauth = ["google", "github", "microsoft", "x", "facebook", "amazon"]
    for provider in known_oauth:
        if str(payload.get(f"auth_{provider}_enabled") or "").lower() in {"1", "true", "yes", "on"}:
            selected.add(provider)
        client_id = _payload_value(
            payload,
            f"auth_{provider}_client_id",
            f"oauth_{provider}_client_id",
            f"{provider}_client_id",
        )
        client_secret = _provider_secret(payload, provider, "client_secret")
        if client_id or client_secret:
            selected.add(provider)
        if provider not in selected:
            continue
        if not client_id or not client_secret:
            raise ValueError(f"{provider} OAuth client_id and client_secret are required")
        providers[provider] = {
            "enabled": True,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    telegram_token = _provider_secret(payload, "telegram", "bot_token")
    telegram_username = _payload_value(
        payload,
        "auth_telegram_bot_username",
        "oauth_telegram_bot_username",
        "telegram_bot_username",
    )
    if str(payload.get("auth_telegram_enabled") or "").lower() in {"1", "true", "yes", "on"}:
        selected.add("telegram")
    if telegram_token or telegram_username:
        selected.add("telegram")
    if "telegram" in selected:
        if not telegram_token or not telegram_username:
            raise ValueError("telegram bot_token and bot_username are required")
        providers["telegram"] = {
            "enabled": True,
            "bot_token": telegram_token,
            "bot_username": telegram_username,
        }

    generic_name = _payload_value(payload, "auth_generic_name", "oauth_generic_name")
    generic_authorize = _payload_value(payload, "auth_generic_authorize_url", "oauth_generic_authorize_url")
    generic_token = _payload_value(payload, "auth_generic_token_url", "oauth_generic_token_url")
    generic_userinfo = _payload_value(payload, "auth_generic_userinfo_url", "oauth_generic_userinfo_url")
    if str(payload.get("auth_generic_enabled") or "").lower() in {"1", "true", "yes", "on"}:
        selected.add(generic_name or "generic")
    if "generic" in selected or generic_name or generic_authorize or generic_token or generic_userinfo:
        provider_name = generic_name or "generic"
        generic_provider_name = provider_name
        selected.add(provider_name)
        client_id = _payload_value(payload, "auth_generic_client_id", "oauth_generic_client_id")
        client_secret = _provider_secret(payload, "generic", "client_secret")
        if not all([client_id, client_secret, generic_authorize, generic_token, generic_userinfo]):
            raise ValueError("generic OAuth requires name, client_id, client_secret, authorize_url, token_url, and userinfo_url")
        providers[provider_name] = {
            "enabled": True,
            "client_id": client_id,
            "client_secret": client_secret,
            "authorize_url": generic_authorize,
            "token_url": generic_token,
            "userinfo_url": generic_userinfo,
            "display_name": _payload_value(payload, "auth_generic_display_name", "oauth_generic_display_name") or provider_name,
            "scope": _payload_value(payload, "auth_generic_scope", "oauth_generic_scope") or "openid email profile",
        }

    admin_links: Dict[str, Dict[str, str]] = {}
    for provider in sorted(selected):
        if provider == "builtin":
            continue
        aliases = [provider]
        if provider == generic_provider_name and provider != "generic":
            aliases.append("generic")
        enabled = str(payload.get(f"link_admin_{provider}") or "").lower() in {"1", "true", "yes", "on"}
        enabled = enabled or any(
            str(payload.get(f"link_admin_{alias}") or "").lower() in {"1", "true", "yes", "on"}
            for alias in aliases[1:]
        )
        identifier_names = []
        for alias in aliases:
            identifier_names.extend([
                f"admin_{alias}_id",
                f"admin_{alias}_email",
                f"link_admin_{alias}_id",
                f"link_admin_{alias}_email",
            ])
        identifier = _payload_value(payload, *identifier_names)
        if enabled and not identifier:
            raise ValueError(f"admin_{provider}_email or admin_{provider}_id is required to link admin")
        if identifier:
            admin_links[provider] = {
                "username": admin_username,
                "claim": "email" if "@" in identifier else "user_id",
                "value": identifier,
            }
    if admin_links:
        providers_config: Dict[str, Any] = {
            "providers": providers,
            "session_ttl": int(payload.get("auth_session_ttl") or 86400),
            "admin_links": admin_links,
        }
    else:
        providers_config = {
            "providers": providers,
            "session_ttl": int(payload.get("auth_session_ttl") or 86400),
        }
    return providers_config


def _install_auth_gateway(auth_config: Dict[str, Any]) -> str:
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    _register_all_services()
    ServiceRegistry.get_instance().install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=AUTH_GATEWAY_SERVICE_ID,
        service_type="authGateway",
        config=auth_config,
        description="Builtin authentication for the installed PawFlow server",
        enabled=True,
    )
    return AUTH_GATEWAY_SERVICE_ID


def _install_llm_credential_pool(provider: str) -> str:
    return _install_llm_credential_pool_for_scope(provider, "global", "")


def _normalize_install_scope(scope: str) -> str:
    selected = (scope or "global").strip().lower()
    if selected not in {"global", "user"}:
        raise ValueError("install service scope must be 'global' or 'user'")
    return selected


def _install_scope_id(scope: str, admin_username: str = "") -> str:
    return "" if scope == "global" else admin_username


def _service_scopes(payload: Dict[str, Any]) -> tuple[str, str, str]:
    default_scope = _normalize_install_scope(str(payload.get("service_scope") or "global"))
    llm_scope = _normalize_install_scope(str(payload.get("llm_service_scope") or default_scope))
    credential_scope = _normalize_install_scope(str(
        payload.get("credential_pool_scope") or payload.get("llm_credential_scope") or llm_scope))
    summarizer_scope = _normalize_install_scope(str(payload.get("summarizer_service_scope") or llm_scope))
    return llm_scope, credential_scope, summarizer_scope


def _install_llm_credential_pool_for_scope(provider: str, scope: str,
                                           admin_username: str,
                                           service_id: str = "") -> str:
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry
    from services.llm_credential_oauth import (
        PROVIDERS as CREDENTIAL_PROVIDERS,
        default_credential_service_id,
        normalize_provider,
    )

    credential_provider = normalize_provider(provider)
    if credential_provider not in CREDENTIAL_PROVIDERS:
        return ""
    service_id = service_id or default_credential_service_id(credential_provider)
    if not service_id:
        return ""

    _register_all_services()
    ServiceRegistry.get_instance().install(
        scope=scope,
        scope_id=_install_scope_id(scope, admin_username),
        service_id=service_id,
        service_type="llmCredentialOAuthProvider",
        config={
            "provider": credential_provider,
            "label": f"{credential_provider} OAuth credentials",
        },
        description="OAuth credential pool for CLI-backed LLM providers",
        enabled=True,
    )
    return service_id


def _credential_module_for_provider(provider: str):
    from services.llm_credential_oauth import normalize_provider

    provider = normalize_provider(provider)
    if provider == "claude-code":
        from core.llm_providers import claude_code_session as mod
        return mod
    if provider == "codex-app-server":
        from core.llm_providers import codex_session as mod
        return mod
    if provider == "gemini":
        from core.llm_providers import gemini_session as mod
        return mod
    raise ValueError(f"selected LLM provider does not support OAuth credentials: {provider}")


def _credential_entry_valid(credential: Dict[str, Any]) -> bool:
    if not credential.get("access_token") or not credential.get("refresh_token"):
        return False
    try:
        expires_at = int(credential.get("expires_at") or 0)
    except (TypeError, ValueError):
        return False
    expires_at_s = expires_at / 1000 if expires_at > 1e12 else expires_at
    return expires_at_s > time.time()


def _llm_credential_pool_status(provider: str, service_id: str) -> Dict[str, Any]:
    if not service_id:
        return {"service_id": "", "count": 0, "valid_count": 0, "ready": False}
    mod = _credential_module_for_provider(provider)
    pool = mod._load_credentials_pool(service_id)
    valid_count = sum(1 for credential in pool if _credential_entry_valid(credential))
    return {
        "service_id": service_id,
        "count": len(pool),
        "valid_count": valid_count,
        "ready": valid_count > 0,
    }


def _validate_llm_auth_ready(payload: Dict[str, Any]) -> str:
    """Validate that the selected LLM has an API key or usable OAuth pool."""
    from services.llm_credential_oauth import (
        PROVIDERS as CREDENTIAL_PROVIDERS,
        default_credential_service_id,
        normalize_provider,
    )

    provider = normalize_provider(str(payload.get("llm_provider") or ""))
    if str(payload.get("llm_api_key") or "").strip():
        return ""
    if provider not in CREDENTIAL_PROVIDERS:
        raise ValueError(f"llm_api_key is required for provider '{provider}'")
    service_id = str(payload.get("credential_service_id") or "").strip()
    service_id = service_id or default_credential_service_id(provider)
    status = _llm_credential_pool_status(provider, service_id)
    if not status["ready"]:
        raise ValueError(
            f"credential pool '{service_id}' has no valid OAuth credential; "
            "complete the CLI login before finalizing"
        )
    return service_id


def _json_field(payload: Dict[str, Any], key: str, default: Any) -> Any:
    value = payload.get(key, default)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key} must be valid JSON") from exc
    return value


def _llm_service_specs(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw = _json_field(payload, "llm_services", [])
    if raw:
        if not isinstance(raw, list):
            raise ValueError("llm_services must be a list")
        specs = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("each llm_services item must be an object")
            config = dict(item.get("config") or {})
            if "llm_model" in config and "default_model" not in config:
                config["default_model"] = config.pop("llm_model")
            specs.append({
                "service_id": str(item.get("service_id") or item.get("id") or "").strip(),
                "scope": _normalize_install_scope(str(item.get("scope") or item.get("llm_service_scope") or "global")),
                "credential_scope": _normalize_install_scope(str(
                    item.get("credential_scope")
                    or (item.get("credential_service") or {}).get("scope")
                    or item.get("credential_pool_scope")
                    or item.get("scope")
                    or "global")),
                "config": config,
            })
        return specs

    llm_scope, credential_scope, _summarizer_scope = _service_scopes(payload)
    return [{
        "service_id": str(payload.get("llm_service_id") or "").strip(),
        "scope": llm_scope,
        "credential_scope": credential_scope,
        "config": {
            "provider": str(payload.get("llm_provider") or "").strip(),
            "default_model": str(payload.get("llm_model") or "").strip(),
            "api_key": str(payload.get("llm_api_key") or "").strip(),
            "credential_service_id": str(payload.get("credential_service_id") or "").strip(),
            "base_url": str(payload.get("llm_base_url") or "").strip(),
            "timeout": 600,
        },
    }]


def _summarizer_spec(payload: Dict[str, Any], default_llm_service_id: str) -> Dict[str, Any]:
    raw = _json_field(payload, "summarizer_service", {})
    if raw:
        if not isinstance(raw, dict):
            raise ValueError("summarizer_service must be an object")
        config = dict(raw.get("config") or {})
        llm_service = str(config.get("llm_service") or raw.get("llm_service") or default_llm_service_id).strip()
        config["llm_service"] = llm_service
        return {
            "service_id": str(raw.get("service_id") or SUMMARIZER_SERVICE_ID).strip() or SUMMARIZER_SERVICE_ID,
            "scope": _normalize_install_scope(str(raw.get("scope") or "global")),
            "config": config,
        }

    _llm_scope, _credential_scope, summarizer_scope = _service_scopes(payload)
    return {
        "service_id": SUMMARIZER_SERVICE_ID,
        "scope": summarizer_scope,
        "config": {"llm_service": default_llm_service_id},
    }


def _relay_server_spec(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    raw = _json_field(payload, "relay_server", {})
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("relay_server must be an object")
    enabled = raw.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    service_id = str(raw.get("service_id") or "_tool_relay").strip()
    if not service_id:
        raise ValueError("relay_server service_id is required")
    scope = _normalize_install_scope(str(raw.get("scope") or "global"))
    config = dict(raw.get("config") or {})
    try:
        auto_background = float(
            config.get("auto_background_after_seconds")
            if config.get("auto_background_after_seconds") is not None
            else raw.get("auto_background_after_seconds") or 0
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("relay_server auto_background_after_seconds must be a number") from exc
    if auto_background < 0:
        raise ValueError("relay_server auto_background_after_seconds must be >= 0")
    return {
        "service_id": service_id,
        "scope": scope,
        "config": {
            "_service_id": service_id,
            "auto_background_after_seconds": auto_background,
            "mode": "readwrite",
            "server_kind": "workspace",
        },
    }


def _bool_payload(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _voice_service_specs(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw = _json_field(payload, "voice_services", {})
    if raw and not isinstance(raw, dict):
        raise ValueError("voice_services must be an object")
    raw = raw or {}

    specs: list[Dict[str, Any]] = []
    tts = raw.get("tts") or {}
    if tts and not isinstance(tts, dict):
        raise ValueError("voice_services.tts must be an object")
    if _bool_payload(tts.get("enabled") if tts else payload.get("install_tts_service")):
        service_id = str(tts.get("service_id") or payload.get("tts_service_id") or "supertonic_tts_service").strip()
        if not service_id:
            raise ValueError("TTS service id is required")
        config = {
            "base_url": "http://127.0.0.1:7788",
            "auto_start": True,
            "auto_install": True,
            "install_dir": "data/runtime/supertonic3",
            "voice": "M1",
            "lang": "na",
            "response_format": "wav",
        }
        config.update(dict(tts.get("config") or {}))
        specs.append({
            "kind": "tts",
            "service_id": service_id,
            "scope": _normalize_install_scope(str(tts.get("scope") or payload.get("voice_service_scope") or "global")),
            "service_type": "supertonicTTS",
            "config": {k: v for k, v in config.items() if v not in ("", None)},
        })

    stt = raw.get("stt") or {}
    if stt and not isinstance(stt, dict):
        raise ValueError("voice_services.stt must be an object")
    if _bool_payload(stt.get("enabled") if stt else payload.get("install_stt_service")):
        service_id = str(stt.get("service_id") or payload.get("stt_service_id") or "voicebox_service").strip()
        if not service_id:
            raise ValueError("STT service id is required")
        config = {
            "base_url": "http://127.0.0.1:17493",
            "client_id": "pawflow",
            "stt_model": "turbo",
            "auto_start": True,
            "auto_install": True,
            "install_dir": "data/runtime/voicebox",
            "preload_stt_model": True,
        }
        config.update(dict(stt.get("config") or {}))
        specs.append({
            "kind": "stt",
            "service_id": service_id,
            "scope": _normalize_install_scope(str(stt.get("scope") or payload.get("voice_service_scope") or "global")),
            "service_type": "voicebox",
            "config": {k: v for k, v in config.items() if v not in ("", None)},
        })
    return specs


def _list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            parsed = _json_field({"value": stripped}, "value", [])
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return []


def _first_conversation_spec(payload: Dict[str, Any], default_llm_service_id: str) -> Dict[str, Any]:
    raw = _json_field(payload, "first_conversation", {})
    if raw and not isinstance(raw, dict):
        raise ValueError("first_conversation must be an object")
    raw = raw or {}
    title = str(raw.get("title") or "Welcome to PawFlow").strip() or "Welcome to PawFlow"
    relay_id = str(raw.get("relay_id") or raw.get("default_relay") or "").strip()
    agent_items = raw.get("agents") or []
    if not agent_items:
        agent_items = [{
            "instance_name": FIRST_RUN_AGENT,
            "definition": FIRST_RUN_AGENT,
            "llm_service": default_llm_service_id,
            "params": {"name": FIRST_RUN_AGENT},
            "max_depth": 1000,
        }]
    if not isinstance(agent_items, list):
        raise ValueError("first_conversation.agents must be a list")

    agents = []
    seen = set()
    for item in agent_items:
        if not isinstance(item, dict):
            raise ValueError("each first_conversation agent must be an object")
        instance_name = str(item.get("instance_name") or item.get("name") or "").strip()
        if not instance_name:
            raise ValueError("first conversation agent instance_name is required")
        if instance_name in seen:
            raise ValueError(f"duplicate first conversation agent: {instance_name}")
        seen.add(instance_name)
        definition = str(item.get("definition") or instance_name).strip()
        llm_service = str(item.get("llm_service") or default_llm_service_id).strip()
        params = item.get("params") or {}
        if isinstance(params, str):
            params = _json_field({"params": params}, "params", {}) if params.strip() else {}
        if not isinstance(params, dict):
            raise ValueError(f"params for first conversation agent '{instance_name}' must be an object")
        params = dict(params)
        params.setdefault("name", instance_name)
        try:
            max_depth = int(item.get("max_depth") or 1000)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_depth for first conversation agent '{instance_name}' must be an integer") from exc
        agents.append({
            "instance_name": instance_name,
            "definition": definition,
            "llm_service": llm_service,
            "model": str(item.get("model") or "").strip(),
            "tools": _list_field(item.get("tools") or []),
            "skills": _list_field(item.get("skills") or []),
            "max_depth": max_depth,
            "params": params,
        })
    return {"title": title, "relay_id": relay_id, "agents": agents}


def _validate_llm_services_auth_ready(payload: Dict[str, Any]) -> None:
    from services.llm_credential_oauth import PROVIDERS as CREDENTIAL_PROVIDERS, default_credential_service_id, normalize_provider

    for spec in _llm_service_specs(payload):
        config = spec["config"]
        provider = normalize_provider(str(config.get("provider") or ""))
        if str(config.get("api_key") or "").strip():
            continue
        if provider not in CREDENTIAL_PROVIDERS:
            raise ValueError(f"llm_api_key is required for provider '{provider}'")
        credential_service_id = str(config.get("credential_service_id") or "").strip() or default_credential_service_id(provider)
        status = _llm_credential_pool_status(provider, credential_service_id)
        if not status["ready"]:
            raise ValueError(
                f"credential pool '{credential_service_id}' has no valid OAuth credential; "
                "complete the CLI login before finalizing")


def prepare_llm_credential_pool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create the CLI OAuth credential pool before final LLM installation."""
    from services.llm_credential_oauth import normalize_provider

    _ensure_bootstrap_open()
    admin_username = str(payload.get("admin_username") or "admin").strip() or "admin"
    provider = normalize_provider(str(payload.get("llm_provider") or ""))
    _llm_scope, credential_scope, _summarizer_scope = _service_scopes(payload)
    requested_service_id = str(payload.get("credential_service_id") or "").strip()
    service_id = _install_llm_credential_pool_for_scope(
        provider, credential_scope, admin_username, requested_service_id)
    if not service_id:
        raise ValueError("selected LLM provider does not use an OAuth credential pool")

    state = _load_state()
    state.setdefault("version", 1)
    state["install_complete"] = False
    state["updated_at"] = time.time()
    state.setdefault("draft", {})
    state["draft"].setdefault("llm_services", {})
    state["draft"]["llm_services"].update({
        "credential_provider": provider,
        "credential_service_id": service_id,
        "credential_pool_scope": credential_scope,
    })
    state.setdefault("checks", {})["llm_credential_pool"] = False
    _write_state(state)
    return {
        "ok": True,
        "provider": provider,
        "service_id": service_id,
        "scope": credential_scope,
        "flow": "paste_credentials",
        "message": _credential_paste_instructions(provider),
    }


def _credential_paste_instructions(provider: str) -> str:
    if provider == "claude-code":
        return (
            "Run this on your machine:\n\n"
            "  claude auth login\n\n"
            "Then paste the content of ~/.claude/.credentials.json."
        )
    if provider == "codex-app-server":
        return (
            "Run this on your machine:\n\n"
            "  codex login\n\n"
            "Then paste the content of ~/.codex/auth.json."
        )
    if provider == "gemini":
        return (
            "Run this on your machine:\n\n"
            "  gemini\n\n"
            "Then paste the content of ~/.gemini/oauth_creds.json."
        )
    raise ValueError(f"selected LLM provider does not support paste login: {provider}")


def save_llm_credential(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist pasted CLI OAuth credentials into the prepared credential pool."""
    from core.service_registry import ServiceRegistry
    from services.llm_credential_oauth import normalize_provider

    _ensure_bootstrap_open()
    admin_username = str(payload.get("admin_username") or "admin").strip() or "admin"
    provider = normalize_provider(str(payload.get("llm_provider") or ""))
    service_id = str(payload.get("credential_service_id") or "").strip()
    _llm_scope, credential_scope, _summarizer_scope = _service_scopes(payload)
    credentials = str(payload.get("credentials") or "").strip()
    if not service_id:
        raise ValueError("credential_service_id is required")
    if not credentials:
        raise ValueError("credentials are required")

    sdef = ServiceRegistry.get_instance().get_definition(
        credential_scope, _install_scope_id(credential_scope, admin_username), service_id)
    if sdef is None or sdef.service_type != "llmCredentialOAuthProvider":
        raise ValueError(f"credential service not found: {service_id}")
    configured_provider = normalize_provider((sdef.config or {}).get("provider", ""))
    if configured_provider != provider:
        raise ValueError(
            f"credential service provider mismatch: expected {configured_provider}, got {provider}")

    if provider == "claude-code":
        parsed = json.loads(credentials)
        oauth = parsed.get("claudeAiOauth", {})
        access_token = oauth.get("accessToken", "")
        refresh_token = oauth.get("refreshToken", "")
        expires_at = oauth.get("expiresAt", 0)
        if not access_token:
            raise ValueError("invalid Claude credentials: no accessToken found")
        from core.llm_providers.claude_code_session import add_credential_to_pool
        add_credential_to_pool(access_token, refresh_token, expires_at, service_id=service_id)
    elif provider == "codex-app-server":
        from core.llm_providers.codex_session import add_credential_to_pool, parse_auth_json
        parsed = parse_auth_json(credentials)
        access_token = parsed.get("access_token", "")
        if not access_token:
            raise ValueError("invalid Codex credentials: no access_token found")
        add_credential_to_pool(
            access_token,
            parsed.get("refresh_token", ""),
            parsed.get("expires_at", 0),
            account=parsed.get("account", ""),
            service_id=service_id,
            id_token=parsed.get("id_token", ""),
        )
    elif provider == "gemini":
        from core.llm_providers.gemini_session import add_credential_to_pool, parse_oauth_creds_json
        parsed = parse_oauth_creds_json(credentials)
        access_token = parsed.get("access_token", "")
        if not access_token:
            raise ValueError("invalid Gemini credentials: no access_token found")
        add_credential_to_pool(
            access_token,
            parsed.get("refresh_token", ""),
            parsed.get("expires_at", 0),
            account=parsed.get("account", ""),
            service_id=service_id,
        )
    else:
        raise ValueError(f"selected LLM provider does not support paste login: {provider}")

    state = _load_state()
    pool_status = _llm_credential_pool_status(provider, service_id)
    if not pool_status["ready"]:
        raise ValueError("saved credential is not valid; check access_token, refresh_token, and expires_at")
    state.setdefault("checks", {})["llm_credential_pool"] = True
    state.setdefault("checks", {})["llm_credential_login"] = True
    state["updated_at"] = time.time()
    _write_state(state)
    return {"ok": True, "service_id": service_id, "provider": provider, "pool": pool_status}


