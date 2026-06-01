"""First-run installation bootstrap for PawFlow server."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess  # nosec B404
import threading
import time
from pathlib import Path
from typing import Any, Dict

import core.paths as _paths

logger = logging.getLogger(__name__)

INSTALL_STATE_FILE = _paths.RUNTIME_DIR / "install_state.json"
INSTALLER_INSTANCE_ID = "pawflow-installer"
INSTALLER_FLOW_FQN = "default.pawflow_installer:1.0.0"
INSTALLER_TEMPLATE = _paths.flow_version_file("default", "pawflow_installer", "1.0.0")
MAIN_INSTANCE_ID = "pawflow-agent"
MAIN_FLOW_FQN = "default.pawflow_agent:1.0.0"
MAIN_TEMPLATE = _paths.flow_version_file("default", "pawflow_agent", "1.0.0")
DEFAULT_BOOTSTRAP_GATEWAY_KEY = "RoyBetty"
BOOTSTRAP_GATEWAY_SECRET_REF = "privategateway.bootstrap"  # nosec B105
BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID = "_bootstrap_private_gateway"
FINAL_GATEWAY_SECRET_REF = "privategateway.main"  # nosec B105
FINAL_PRIVATE_GATEWAY_SERVICE_ID = "_private_gateway"
AUTH_GATEWAY_SERVICE_ID = "_auth_gateway"
SUMMARIZER_SERVICE_ID = "summarizer_service"
FIRST_RUN_AGENT = "assistant"
BOOTSTRAP_CERT_FILE = _paths.SSL_DIR / "bootstrap.crt"
BOOTSTRAP_KEY_FILE = _paths.SSL_DIR / "bootstrap.key"
FINAL_CERT_FILE = _paths.SSL_DIR / "server.crt"
FINAL_KEY_FILE = _paths.SSL_DIR / "server.key"
DEFAULT_INSTALLER_FLOW_DIR = Path(
    os.environ.get(
        "PAWFLOW_DEFAULT_INSTALLER_FLOW_DIR",
        "/app/default-data/repository/flows/global/default/pawflow_installer",
    )
)
INSTALL_STEPS = [
    "server",
    "certificates",
    "gateway",
    "auth",
    "admin",
    "llm_services",
    "summarizer_service",
    "relay_server",
    "variables",
    "secrets",
    "cli_credentials",
    "relay_image_profiles",
    "smoke_tests",
    "finalize",
]
CLIENT_RELAY_IMAGES = {
    "catalog": "config/relay_image_catalog.json",
    "generator": "scripts/generate-relay-image.py",
    "server_profile": "server-full",
    "server_minimal_profile": "server-minimal",
    "default_client_profile": "client-minimal",
    "advanced_features": True,
}


def _refresh_installer_template_from_default_data() -> bool:
    """Refresh the system installer flow from the image defaults if present."""
    if not DEFAULT_INSTALLER_FLOW_DIR.is_dir():
        return False
    installer_dir = INSTALLER_TEMPLATE.parent.parent
    if DEFAULT_INSTALLER_FLOW_DIR.resolve() == installer_dir.resolve():
        return False
    if not (DEFAULT_INSTALLER_FLOW_DIR / "versions" / "1.0.0.json").is_file():
        logger.warning(
            "Default installer flow missing version file: %s",
            DEFAULT_INSTALLER_FLOW_DIR,
        )
        return False
    tmp_dir = installer_dir.with_name(f"{installer_dir.name}.refreshing")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(DEFAULT_INSTALLER_FLOW_DIR, tmp_dir)
    if installer_dir.exists():
        shutil.rmtree(installer_dir)
    tmp_dir.replace(installer_dir)
    logger.info("Refreshed bootstrap installer template from %s", DEFAULT_INSTALLER_FLOW_DIR)
    return True


def _generate_self_signed_cert(cert_file: Path, key_file: Path, *,
                               hosts_env: str, default_hosts: str,
                               days: int) -> None:
    """Generate a self-signed TLS certificate with SubjectAltName entries."""
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    _paths.SSL_DIR.mkdir(parents=True, exist_ok=True)
    hosts = [
        h.strip()
        for h in os.environ.get(hosts_env, default_hosts).split(",")
        if h.strip()
    ]
    san_parts = []
    for host in hosts or ["localhost"]:
        if all(part.isdigit() for part in host.split(".") if part):
            san_parts.append(f"IP:{host}")
        else:
            san_parts.append(f"DNS:{host}")

    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-sha256", "-days", str(days), "-nodes",
        "-keyout", str(key_file),
        "-out", str(cert_file),
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=" + ",".join(san_parts),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)  # nosec B603
    key_file.chmod(0o600)
    cert_file.chmod(0o644)


def ensure_bootstrap_self_signed_cert() -> Dict[str, str]:
    """Create the first-run self-signed TLS certificate if missing."""
    if BOOTSTRAP_CERT_FILE.exists() and BOOTSTRAP_KEY_FILE.exists():
        return {
            "ssl_certfile": str(BOOTSTRAP_CERT_FILE),
            "ssl_keyfile": str(BOOTSTRAP_KEY_FILE),
            "ssl_mode": "self_signed",
        }

    try:
        _generate_self_signed_cert(
            BOOTSTRAP_CERT_FILE,
            BOOTSTRAP_KEY_FILE,
            hosts_env="PAWFLOW_BOOTSTRAP_CERT_HOSTS",
            default_hosts="localhost,127.0.0.1",
            days=30,
        )
    except Exception as exc:
        raise RuntimeError(
            "Failed to generate bootstrap self-signed certificate. "
            "Install openssl or provide certificates in the installer."
        ) from exc

    logger.info("Generated bootstrap self-signed TLS certificate: %s", BOOTSTRAP_CERT_FILE)
    return {
        "ssl_certfile": str(BOOTSTRAP_CERT_FILE),
        "ssl_keyfile": str(BOOTSTRAP_KEY_FILE),
        "ssl_mode": "self_signed",
    }


def _final_tls_config(payload: Dict[str, Any]) -> Dict[str, str]:
    """Resolve the TLS certificate used by the installed runtime listener."""
    certfile = str(payload.get("final_ssl_certfile") or payload.get("ssl_certfile") or "").strip()
    keyfile = str(payload.get("final_ssl_keyfile") or payload.get("ssl_keyfile") or "").strip()
    if certfile or keyfile:
        if not certfile or not keyfile:
            raise ValueError("ssl_certfile and ssl_keyfile must be provided together")
        missing = [path for path in (certfile, keyfile) if not Path(path).is_file()]
        if missing:
            raise ValueError(
                "provided TLS certificate files must exist in the PawFlow server container: "
                + ", ".join(missing))
        return {"ssl_mode": "provided", "ssl_certfile": certfile, "ssl_keyfile": keyfile}
    if not FINAL_CERT_FILE.exists() or not FINAL_KEY_FILE.exists():
        try:
            _generate_self_signed_cert(
                FINAL_CERT_FILE,
                FINAL_KEY_FILE,
                hosts_env="PAWFLOW_FINAL_CERT_HOSTS",
                default_hosts="localhost,127.0.0.1",
                days=3650,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to generate final self-signed certificate. "
                "Install openssl or provide final TLS certificates."
            ) from exc
    return {"ssl_mode": "self_signed", "ssl_certfile": str(FINAL_CERT_FILE), "ssl_keyfile": str(FINAL_KEY_FILE)}


def _final_listener_port(payload: Dict[str, Any]) -> int:
    """Resolve the listener port that the installed runtime must keep using."""
    raw_port = (
        payload.get("listener_port")
        or payload.get("http_port")
        or payload.get("port")
    )
    if raw_port in {None, ""}:
        try:
            from core.deployment_registry import DeploymentRegistry
            inst = DeploymentRegistry.get_instance().get(INSTALLER_INSTANCE_ID)
            if inst is not None:
                raw_port = inst.parameters.get("port")
        except Exception:
            logger.warning("Failed to read installer listener port", exc_info=True)
    if raw_port in {None, ""}:
        raise ValueError("listener port is required")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("listener port must be an integer") from exc
    if port < 1 or port > 65535:
        raise ValueError("listener port must be between 1 and 65535")
    return port


def _sync_main_flow_listener_port(port: int) -> None:
    """Keep an already-installed main flow aligned with the launched server port."""
    try:
        from core.deployment_registry import DeploymentRegistry
        registry = DeploymentRegistry.get_instance()
        inst = registry.get(MAIN_INSTANCE_ID)
        if inst is None:
            return
        listener_config = inst.service_configs.setdefault("http_listener", {})
        current = listener_config.get("port")
        if current == port:
            return
        try:
            if current is not None and int(current) == port:
                return
        except (TypeError, ValueError):
            pass
        listener_config["port"] = port
        registry._save_instance(inst)
        logger.info("Updated main flow listener port to %s", port)
    except Exception:
        logger.warning("Failed to sync main flow listener port", exc_info=True)


def _load_state() -> Dict[str, Any]:
    if not INSTALL_STATE_FILE.exists():
        return {}
    try:
        return json.loads(INSTALL_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Install bootstrap state is unreadable; keeping bootstrap enabled", exc_info=True)
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    INSTALL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_install_complete() -> bool:
    return bool(_load_state().get("install_complete"))


def _public_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    public: Dict[str, Any] = {}
    for section in (
        "server",
        "gateway",
        "auth",
        "llm_services",
        "summarizer_service",
        "flows",
        "conversation",
    ):
        value = draft.get(section)
        if isinstance(value, dict):
            public[section] = dict(value)
    return public


def get_install_status() -> Dict[str, Any]:
    """Return installer state without exposing bootstrap or gateway secrets."""
    from core.private_gateway_skins import list_skins

    state = _load_state()
    checks = dict(state.get("checks") or {})
    draft = _public_draft(dict(state.get("draft") or {}))
    return {
        "install_complete": bool(state.get("install_complete")),
        "current_step": state.get("current_step") or "server",
        "installer_instance_id": state.get("installer_instance_id", INSTALLER_INSTANCE_ID),
        "completed_steps": list(state.get("completed_steps") or []),
        "steps": list(INSTALL_STEPS),
        "client_relay_images": dict(CLIENT_RELAY_IMAGES),
        "private_gateway_skins": [
            {
                "name": str(skin.get("name") or ""),
                "title": str(skin.get("title") or skin.get("name") or ""),
                "description": str(skin.get("description") or ""),
            }
            for skin in list_skins()
        ],
        "checks": checks,
        "draft": draft,
    }


def _store_global_secret(secret_ref: str, value: str) -> str:
    """Persist a global secret value without rewriting unrelated raw entries."""
    from core.config_store import ConfigStore
    from core.secrets import get_secrets_manager

    _paths.GLOBAL_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    raw = ConfigStore.load_secrets_raw(_paths.GLOBAL_SECRETS_FILE)
    sm = get_secrets_manager()
    current = raw.get(secret_ref)
    if isinstance(current, str) and current:
        try:
            if sm.decrypt(current) == value:
                return secret_ref
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    raw[secret_ref] = sm.encrypt(value)
    _paths.GLOBAL_SECRETS_FILE.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return secret_ref


def _store_bootstrap_gateway_secret(bootstrap_key: str) -> str:
    """Persist the temporary bootstrap gateway key as an encrypted secret."""
    _store_global_secret(BOOTSTRAP_GATEWAY_SECRET_REF, bootstrap_key)
    return BOOTSTRAP_GATEWAY_SECRET_REF


def _delete_global_secret(secret_ref: str) -> None:
    """Best-effort removal for secrets written by a failed finalization."""
    from core.config_store import ConfigStore

    if not _paths.GLOBAL_SECRETS_FILE.exists():
        return
    raw = ConfigStore.load_secrets_raw(_paths.GLOBAL_SECRETS_FILE)
    if secret_ref not in raw:
        return
    raw.pop(secret_ref, None)
    _paths.GLOBAL_SECRETS_FILE.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _snapshot_file_state(paths: list[Path]) -> Dict[Path, bytes | None]:
    """Capture exact file contents before finalization mutates system state."""
    snapshot: Dict[Path, bytes | None] = {}
    for path in paths:
        try:
            snapshot[path] = path.read_bytes() if path.exists() else None
        except Exception:
            logger.warning("Install finalization could not snapshot %s", path, exc_info=True)
            snapshot[path] = None
    return snapshot


def _restore_file_state(snapshot: Dict[Path, bytes | None]) -> None:
    """Restore files captured before a failed finalization attempt."""
    for path, content in snapshot.items():
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        except Exception:
            logger.warning("Install finalization rollback could not restore %s", path, exc_info=True)


def _install_bootstrap_private_gateway(secret_ref: str) -> str:
    """Install the global privateGateway used only by the first-run installer."""
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    _register_all_services()
    ServiceRegistry.get_instance().install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID,
        service_type="privateGateway",
        config={
            "enabled": True,
            "secret_refs": secret_ref,
            "skin": "matrix",
        },
        description="Temporary private gateway for first-run installation",
        enabled=True,
    )
    return BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID


def _validate_gateway_skin(skin: str) -> str:
    from core.private_gateway_skins import DEFAULT_SKIN, resolve_skin

    selected = (skin or DEFAULT_SKIN).strip()
    if not selected:
        selected = DEFAULT_SKIN
    if resolve_skin(selected) is None:
        raise ValueError(f"unknown private gateway skin: {selected}")
    return selected


def _expected_bootstrap_key() -> str:
    return os.environ.get(
        "PAWFLOW_BOOTSTRAP_GATEWAY_KEY",
        DEFAULT_BOOTSTRAP_GATEWAY_KEY,
    )


def _require_bootstrap_key(payload: Dict[str, Any]) -> None:
    provided_key = str(
        payload.get("bootstrap_gateway_key")
        or payload.get("current_gateway_key")
        or ""
    )
    if provided_key != _expected_bootstrap_key():
        raise PermissionError("invalid bootstrap gateway key")


def require_bootstrap_key(payload: Dict[str, Any]) -> None:
    """Public bootstrap authorization helper for install HTTP endpoints."""
    _require_bootstrap_key(payload)


def _ensure_bootstrap_open() -> None:
    """Reject late writes after the first-run installer has finalized."""
    if is_install_complete():
        raise PermissionError("installer is already finalized")


def _install_final_private_gateway(secret_ref: str, skin: str) -> str:
    """Install the persistent Private Gateway used by the normal PawFlow flow."""
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    _register_all_services()
    ServiceRegistry.get_instance().install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=FINAL_PRIVATE_GATEWAY_SERVICE_ID,
        service_type="privateGateway",
        config={
            "enabled": True,
            "secret_refs": secret_ref,
            "skin": skin,
        },
        description="Persistent private gateway for PawFlow",
        enabled=True,
    )
    return FINAL_PRIVATE_GATEWAY_SERVICE_ID


def _validate_admin_password(payload: Dict[str, Any]) -> str:
    password = str(payload.get("admin_password") or "")
    confirm = str(payload.get("admin_password_confirm") or "")
    if not password:
        raise ValueError("admin_password is required")
    if password != confirm:
        raise ValueError("admin_password_confirm must match admin_password")
    if len(password) < 12:
        raise ValueError("admin_password must be at least 12 characters")
    if not any(ch.islower() for ch in password):
        raise ValueError("admin_password must include a lowercase letter")
    if not any(ch.isupper() for ch in password):
        raise ValueError("admin_password must include an uppercase letter")
    if not any(ch.isdigit() for ch in password):
        raise ValueError("admin_password must include a digit")
    if not any(not ch.isalnum() for ch in password):
        raise ValueError("admin_password must include a symbol")
    return password


def _configure_admin_user(payload: Dict[str, Any]) -> str:
    """Create or update the first admin user from installer input."""
    from core.security import SecurityManager, Role

    username = str(payload.get("admin_username") or "admin").strip()
    password = _validate_admin_password(payload)
    if not username:
        raise ValueError("admin_username is required")

    sm = SecurityManager.get_instance()
    if sm.get_user(username):
        sm.update_user(username, role=Role.ADMIN, password=password, enabled=True)
    else:
        sm.create_user(username, password, Role.ADMIN, display_name=username)

    if username != "admin" and sm.get_user("admin"):
        try:
            sm.update_user("admin", enabled=False)
        except ValueError:
            pass
    return username


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
        service_id = spec["service_id"]
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


def _install_llm_and_summarizer(payload: Dict[str, Any]) -> tuple[str, str, str]:
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry
    from services.llm_credential_oauth import PROVIDERS as CREDENTIAL_PROVIDERS, normalize_provider

    _register_all_services()
    admin_username = str(payload.get("admin_username") or "admin").strip() or "admin"
    specs = _llm_service_specs(payload)
    if not specs:
        raise ValueError("at least one LLM service is required")

    reg = ServiceRegistry.get_instance()
    installed_llm_ids = []
    installed_credential_ids = []
    seen_ids = set()
    for spec in specs:
        llm_service_id = spec["service_id"]
        if not llm_service_id:
            raise ValueError("llm_service_id is required")
        if llm_service_id in seen_ids:
            raise ValueError(f"duplicate LLM service id: {llm_service_id}")
        seen_ids.add(llm_service_id)
        config = dict(spec["config"])
        provider = normalize_provider(str(config.get("provider") or ""))
        model = str(config.get("default_model") or "").strip()
        if not provider:
            raise ValueError(f"provider is required for LLM service '{llm_service_id}'")
        if not model:
            raise ValueError(f"default_model is required for LLM service '{llm_service_id}'")
        config["provider"] = provider
        config["default_model"] = model
        config.setdefault("timeout", 600)

        api_key = str(config.get("api_key") or "").strip()
        if api_key:
            secret_ref = f"llm.{llm_service_id}.api_key"
            _store_global_secret(secret_ref, api_key)
            config["api_key"] = "${" + secret_ref + "}"
        else:
            credential_service_id = str(config.get("credential_service_id") or "").strip()
            if provider in CREDENTIAL_PROVIDERS:
                credential_service_id = _install_llm_credential_pool_for_scope(
                    provider, spec["credential_scope"], admin_username, credential_service_id)
                if credential_service_id:
                    config["credential_service_id"] = credential_service_id
                    installed_credential_ids.append(credential_service_id)
            else:
                raise ValueError(f"llm_api_key is required for provider '{provider}'")

        config = {k: v for k, v in config.items() if v not in ("", None)}
        llm_scope = spec["scope"]
        reg.install(
            scope=llm_scope,
            scope_id=_install_scope_id(llm_scope, admin_username),
            service_id=llm_service_id,
            service_type="llmConnection",
            config=config,
            description="Installed LLM service from first-run bootstrap",
            enabled=True,
        )
        installed_llm_ids.append(llm_service_id)

    summarizer = _summarizer_spec(payload, installed_llm_ids[0])
    if summarizer["config"].get("llm_service") not in installed_llm_ids:
        raise ValueError("summarizer llm_service must reference one of the configured LLM services")
    reg.install(
        scope=summarizer["scope"],
        scope_id=_install_scope_id(summarizer["scope"], admin_username),
        service_id=summarizer["service_id"],
        service_type="summarizer",
        config=summarizer["config"],
        description="Summarizer service for conversation compaction",
        enabled=True,
    )
    return installed_llm_ids[0], summarizer["service_id"], (installed_credential_ids[0] if installed_credential_ids else "")


def _install_relay_server(payload: Dict[str, Any], admin_username: str) -> str:
    spec = _relay_server_spec(payload)
    if not spec:
        return ""
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry
    from core.server_relay_manager import ServerRelayManager
    from tasks.ai.actions.service_flow import _wait_for_service_connected

    _register_all_services()
    reg = ServiceRegistry.get_instance()
    manager = ServerRelayManager.get_instance()
    scope = spec["scope"]
    scope_id = _install_scope_id(scope, admin_username)
    service_id = spec["service_id"]
    config = dict(spec["config"])
    token = secrets.token_urlsafe(32)
    config["token"] = token
    config["server_managed"] = True
    config.update(manager.service_relay_config(
        service_id,
        scope=scope,
        scope_id=scope_id,
        user_id=admin_username,
        kind=str(config.get("server_kind") or "workspace"),
    ))
    reg.install(
        scope=spec["scope"],
        scope_id=scope_id,
        service_id=service_id,
        service_type="relay",
        config=config,
        description="Tool relay server installed from first-run bootstrap",
        enabled=True,
    )
    if not _wait_for_service_connected(reg, scope, scope_id, service_id):
        reg.uninstall(scope, scope_id, service_id)
        raise RuntimeError(
            f"Managed server relay '{service_id}' container started but did not connect. "
            f"Check Docker logs for {config.get('server_container_name', service_id)}.")
    return service_id


def _deploy_main_flow(private_gateway_service_id: str,
                      tls_config: Dict[str, str],
                      auth_config: Dict[str, Any],
                      listener_port: int) -> str:
    from core.deployment_registry import DeploymentRegistry

    if not MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {MAIN_TEMPLATE}")
    reg = DeploymentRegistry.get_instance()
    params = {"private_gateway_service_id": private_gateway_service_id, "port": listener_port}
    service_configs = {
        "http_listener": {
            "port": listener_port,
            "ssl_certfile": tls_config["ssl_certfile"],
            "ssl_keyfile": tls_config["ssl_keyfile"],
            "private_gateway_service_id": private_gateway_service_id,
        },
        "auth": auth_config,
    }
    inst = reg.get(MAIN_INSTANCE_ID)
    if inst is None:
        reg.deploy(
            template_path=str(MAIN_TEMPLATE),
            owner=None,
            parameters=params,
            service_configs=service_configs,
            source="bootstrap",
            instance_id=MAIN_INSTANCE_ID,
        )
    else:
        inst.parameters.update(params)
        inst.service_configs.update(service_configs)
        inst.source = "bootstrap"
        reg._save_instance(inst)
    reg.update_status(MAIN_INSTANCE_ID, "running")
    return MAIN_INSTANCE_ID


def _start_main_flow_executor(instance_id: str) -> None:
    """Start the main PawFlow executor immediately after bootstrap finalization."""
    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry

    executors = ExecutorRegistry.get_instance()
    if executors.get(instance_id) is not None:
        return

    inst = DeploymentRegistry.get_instance().get(instance_id)
    if inst is None:
        raise RuntimeError(f"main PawFlow deployment is missing: {instance_id}")
    ok = executors._restore_instance(
        instance_id,
        inst.flow_path,
        inst.max_workers,
        inst.max_retries,
        flow_fqn=getattr(inst, "flow_fqn", "") or "",
        flow_scope=getattr(inst, "flow_scope", "") or "",
        parameters=inst.parameters,
        service_overrides=inst.service_overrides,
        service_configs=inst.service_configs,
        owner=inst.owner or "",
        conversation_id=inst.conversation_id or "",
        agent_name=getattr(inst, "agent_name", "") or "",
    )
    if not ok:
        raise RuntimeError(f"failed to start main PawFlow executor: {instance_id}")


def _stop_installer_executor_soon(delay: float = 1.0) -> None:
    """Stop the installer executor after its final HTTP response can drain."""
    def _stop() -> None:
        try:
            from core.executor_registry import ExecutorRegistry
            executors = ExecutorRegistry.get_instance()
            executor = executors.get(INSTALLER_INSTANCE_ID)
            if executor is not None:
                executor.stop()
            executors.unregister(INSTALLER_INSTANCE_ID)
        except Exception:
            logger.warning("Install bootstrap finalized but installer executor stop failed", exc_info=True)

    timer = threading.Timer(delay, _stop)
    timer.daemon = True
    timer.start()


def _rollback_failed_finalization(
    *,
    llm_service_id: str = "",
    llm_scope: str = "global",
    summarizer_scope: str = "global",
    service_refs: list[Dict[str, str]] | None = None,
    admin_user: str = "admin",
    first_conversation_id: str = "",
) -> None:
    """Remove runtime artifacts created by a finalization that did not pass checks."""
    try:
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.get_instance().undeploy(MAIN_INSTANCE_ID)
    except Exception:
        logger.warning("Install finalization rollback failed to undeploy main flow", exc_info=True)

    try:
        if first_conversation_id:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().delete(first_conversation_id, user_id=admin_user)
    except Exception:
        logger.warning("Install finalization rollback failed to delete first conversation", exc_info=True)

    try:
        from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
        reg = ServiceRegistry.get_instance()
        for service_id in (FINAL_PRIVATE_GATEWAY_SERVICE_ID, AUTH_GATEWAY_SERVICE_ID):
            reg.uninstall(SCOPE_GLOBAL, "", service_id)
        if service_refs:
            for ref in service_refs:
                scope = ref.get("scope") or "global"
                service_id = ref.get("service_id") or ""
                if service_id:
                    reg.uninstall(scope, _install_scope_id(scope, admin_user), service_id)
        else:
            if llm_service_id:
                reg.uninstall(llm_scope, _install_scope_id(llm_scope, admin_user), llm_service_id)
            reg.uninstall(summarizer_scope, _install_scope_id(summarizer_scope, admin_user), SUMMARIZER_SERVICE_ID)
    except Exception:
        logger.warning("Install finalization rollback failed to uninstall services", exc_info=True)

    try:
        _delete_global_secret(FINAL_GATEWAY_SECRET_REF)
    except Exception:
        logger.warning("Install finalization rollback failed to delete final gateway secret", exc_info=True)


def _rollback_service_refs(payload: Dict[str, Any]) -> list[Dict[str, str]]:
    from services.llm_credential_oauth import PROVIDERS as CREDENTIAL_PROVIDERS, default_credential_service_id, normalize_provider

    refs: list[Dict[str, str]] = []
    specs = _llm_service_specs(payload)
    for spec in specs:
        if spec["service_id"]:
            refs.append({"scope": spec["scope"], "service_id": spec["service_id"]})
        config = spec["config"]
        provider = normalize_provider(str(config.get("provider") or ""))
        if provider in CREDENTIAL_PROVIDERS and not str(config.get("api_key") or "").strip():
            cred_id = str(config.get("credential_service_id") or "").strip() or default_credential_service_id(provider)
            if cred_id:
                refs.append({"scope": spec["credential_scope"], "service_id": cred_id})
    if specs:
        summarizer = _summarizer_spec(payload, specs[0]["service_id"])
        refs.append({"scope": summarizer["scope"], "service_id": summarizer["service_id"]})
    relay = _relay_server_spec(payload)
    if relay:
        refs.append({"scope": relay["scope"], "service_id": relay["service_id"]})
    return refs


def _create_first_conversation(
    admin_user: str,
    payload: Dict[str, Any],
    default_llm_service_id: str,
    installed_llm_ids: list[str],
) -> str:
    from core.conversation_store import ConversationStore
    from core.conv_agent_config import add_agent_to_conv
    from core.resource_store import ResourceStore, GLOBAL_USER_ID

    spec = _first_conversation_spec(payload, default_llm_service_id)
    rs = ResourceStore.instance()
    for agent in spec["agents"]:
        definition = agent["definition"]
        if agent["llm_service"] not in installed_llm_ids:
            raise ValueError(
                f"agent '{agent['instance_name']}' references unknown LLM service '{agent['llm_service']}'")
        if rs.get_any("agent", definition, admin_user) is None:
            rs.create(
                "agent",
                definition,
                GLOBAL_USER_ID,
                {
                    "prompt": "You are ${agent.name}, a helpful assistant.",
                    "description": "General-purpose assistant.",
                    "parameters": {
                        "name": {
                            "required": True,
                            "description": "Agent display name",
                        }
                    },
                },
            )

    store = ConversationStore.instance()
    conv_id = store.generate_id()
    store.save(conv_id, [], user_id=admin_user)
    store.set_extra(conv_id, "title", spec["title"])
    agent_names = [agent["instance_name"] for agent in spec["agents"]]
    store.set_extra(
        conv_id,
        "active_resources",
        {"agents": agent_names, "agent": agent_names[0]},
    )
    if spec.get("relay_id"):
        from core.relay_bindings import link_relay, set_default_relay
        link_relay(conv_id, spec["relay_id"], user_id=admin_user)
        set_default_relay(conv_id, spec["relay_id"])
    for agent in spec["agents"]:
        add_agent_to_conv(
            conv_id,
            agent["instance_name"],
            llm_service=agent["llm_service"],
            definition=agent["definition"],
            params=agent["params"],
            model=agent["model"],
            tools=agent["tools"],
            max_depth=agent["max_depth"],
            skills=agent["skills"],
        )
    return conv_id


def _run_install_smoke_checks(
    *,
    final_gateway_key: str,
    admin_user: str,
    llm_service_id: str,
    summarizer_service_id: str,
    credential_service_id: str,
    provider: str,
    main_instance_id: str,
    first_conversation_id: str,
    auth_config: Dict[str, Any],
    relay_service_id: str = "",
) -> Dict[str, Any]:
    """Run final internal smoke checks before marking first-run install complete."""
    from core.conversation_store import ConversationStore
    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.security import SecurityManager
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    reg = ServiceRegistry.get_instance()
    details: Dict[str, Any] = {}

    def record(name: str, ok: bool, **extra: Any) -> None:
        details[name] = {"ok": bool(ok), **extra}

    final_gateway = reg.get_definition(SCOPE_GLOBAL, "", FINAL_PRIVATE_GATEWAY_SERVICE_ID)
    record(
        "final_private_gateway",
        final_gateway is not None and final_gateway.enabled
        and (final_gateway.config or {}).get("secret_refs") == FINAL_GATEWAY_SECRET_REF,
    )
    try:
        from services.private_gateway import verify_secret
        final_gateway_key_ok = verify_secret(final_gateway_key, FINAL_GATEWAY_SECRET_REF)
    except Exception:
        logger.warning("Install smoke check failed to verify final gateway key", exc_info=True)
        final_gateway_key_ok = False
    record("final_private_gateway_key", final_gateway_key_ok)

    auth_gateway = reg.get_definition(SCOPE_GLOBAL, "", AUTH_GATEWAY_SERVICE_ID)
    auth_providers = (auth_config.get("providers") or {}) if isinstance(auth_config, dict) else {}
    record(
        "auth_gateway",
        auth_gateway is not None and auth_gateway.enabled and "builtin" in auth_providers,
        providers=sorted(auth_providers),
    )

    record("admin_user", SecurityManager.get_instance().get_user(admin_user) is not None)

    if relay_service_id:
        relay_def = reg.resolve_definition(relay_service_id, user_id=admin_user)
        record(
            "relay_server",
            relay_def is not None and relay_def.enabled and relay_def.service_type == "relay",
            service_id=relay_service_id,
        )
    else:
        record("relay_server", True, skipped=True)

    llm_def = reg.resolve_definition(llm_service_id, user_id=admin_user)
    record(
        "llm_service",
        llm_def is not None and llm_def.enabled and llm_def.service_type == "llmConnection",
        service_id=llm_service_id,
    )

    if credential_service_id:
        pool_status = _llm_credential_pool_status(provider, credential_service_id)
        record("llm_credential_pool", bool(pool_status.get("ready")), **pool_status)
    else:
        record("llm_credential_pool", True, skipped=True)

    summarizer_def = reg.resolve_definition(summarizer_service_id, user_id=admin_user)
    record(
        "summarizer_service",
        summarizer_def is not None and summarizer_def.enabled
        and summarizer_def.service_type == "summarizer"
        and (summarizer_def.config or {}).get("llm_service") == llm_service_id,
        service_id=summarizer_service_id,
    )
    summarizer = reg.resolve(summarizer_service_id, user_id=admin_user)
    resolved_llm, _ctx_max, resolved_llm_id = (
        summarizer.resolve_llm_service(user_id=admin_user)
        if summarizer and hasattr(summarizer, "resolve_llm_service")
        else (None, 0, "")
    )
    record(
        "summarizer_llm_resolution",
        resolved_llm is not None and resolved_llm_id == llm_service_id,
        llm_service=resolved_llm_id,
    )

    deployment = DeploymentRegistry.get_instance().get(main_instance_id)
    record(
        "main_flow_deployed",
        deployment is not None and deployment.status == "running",
        instance_id=main_instance_id,
    )
    record(
        "main_flow_executor",
        ExecutorRegistry.get_instance().get(main_instance_id) is not None,
        instance_id=main_instance_id,
    )

    conv_store = ConversationStore.instance()
    active_resources = conv_store.get_extra(first_conversation_id, "active_resources", {})
    record(
        "first_conversation",
        conv_store.exists(first_conversation_id)
        and isinstance(active_resources, dict)
        and bool(active_resources.get("agent"))
        and active_resources.get("agent") in (active_resources.get("agents") or []),
        conversation_id=first_conversation_id,
    )

    failed = [name for name, item in details.items() if not item.get("ok")]
    if failed:
        raise RuntimeError("install smoke checks failed: " + ", ".join(failed))
    return details


def finalize_install(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Finalize first-run bootstrap after replacing the gateway key.

    The current bootstrap key authorizes the public bootstrap API. The new
    gateway key is never persisted in clear text; only a SHA-256 digest is kept
    so the state file can prove replacement without becoming a secret store.
    """
    state = _load_state()
    if state.get("install_complete"):
        return get_install_status()

    _ensure_bootstrap_open()

    new_key = str(
        payload.get("new_gateway_key")
        or payload.get("gateway_key")
        or ""
    ).strip()
    if not new_key:
        raise ValueError("new_gateway_key is required")
    if new_key in {_expected_bootstrap_key(), DEFAULT_BOOTSTRAP_GATEWAY_KEY}:
        raise ValueError("new_gateway_key must replace the bootstrap key")
    if len(new_key) < 16:
        raise ValueError("new_gateway_key must be at least 16 characters")

    admin_username = str(payload.get("admin_username") or "admin").strip()
    admin_password = _validate_admin_password(payload)
    if not admin_username:
        raise ValueError("admin_username is required")
    llm_specs = _llm_service_specs(payload)
    if not llm_specs:
        raise ValueError("at least one LLM service is required")
    primary_provider = str((llm_specs[0].get("config") or {}).get("provider") or "")
    summarizer_plan = _summarizer_spec(payload, llm_specs[0]["service_id"])
    relay_plan = _relay_server_spec(payload)
    rollback_refs = _rollback_service_refs(payload)
    if not MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {MAIN_TEMPLATE}")
    system_snapshot = _snapshot_file_state([
        _paths.GLOBAL_SECRETS_FILE,
        _paths.USERS_FILE,
        _paths.SESSIONS_FILE,
        _paths.SECURITY_FILE,
        FINAL_CERT_FILE,
        FINAL_KEY_FILE,
    ])

    admin_user = str(admin_username)
    llm_service_id = str(payload.get("llm_service_id") or "").strip()
    relay_service_id = ""
    first_conversation_id = ""
    runtime_artifacts_created = False
    try:
        _validate_llm_services_auth_ready(payload)
        gateway_skin = _validate_gateway_skin(str(payload.get("gateway_skin") or ""))
        tls_config = _final_tls_config(payload)
        listener_port = _final_listener_port(payload)
        auth_config = _build_auth_gateway_config(payload, admin_username)
        final_secret_ref = _store_global_secret(FINAL_GATEWAY_SECRET_REF, new_key)
        final_gateway_service_id = _install_final_private_gateway(final_secret_ref, gateway_skin)
        runtime_artifacts_created = True
        admin_user = _configure_admin_user(payload)
        auth_gateway_service_id = _install_auth_gateway(auth_config)
        llm_service_id, summarizer_service_id, credential_service_id = _install_llm_and_summarizer(payload)
        main_instance_id = _deploy_main_flow(final_gateway_service_id, tls_config, auth_config, listener_port)
        _start_main_flow_executor(main_instance_id)
        relay_service_id = _install_relay_server(payload, admin_user)
        first_conversation_id = _create_first_conversation(
            admin_user,
            payload,
            llm_service_id,
            [spec["service_id"] for spec in llm_specs],
        )
        smoke_checks = _run_install_smoke_checks(
            final_gateway_key=new_key,
            admin_user=admin_user,
            llm_service_id=llm_service_id,
            summarizer_service_id=summarizer_service_id,
            credential_service_id=credential_service_id,
            provider=primary_provider,
            main_instance_id=main_instance_id,
            first_conversation_id=first_conversation_id,
            auth_config=auth_config,
            relay_service_id=relay_service_id,
        )
    except Exception:
        if runtime_artifacts_created or first_conversation_id:
            _rollback_failed_finalization(
                llm_service_id=llm_service_id,
                service_refs=rollback_refs,
                admin_user=admin_user,
                first_conversation_id=first_conversation_id,
            )
        _restore_file_state(system_snapshot)
        raise

    now = time.time()
    state.setdefault("version", 1)
    state["install_complete"] = True
    state["current_step"] = "complete"
    state["updated_at"] = now
    state["completed_at"] = now
    state["installer_instance_id"] = INSTALLER_INSTANCE_ID

    completed = list(state.get("completed_steps") or [])
    for step in INSTALL_STEPS:
        if step not in completed:
            completed.append(step)
    state["completed_steps"] = completed

    checks = state.setdefault("checks", {})
    checks["gateway_replaced"] = True
    checks["final_private_gateway"] = True
    checks["final_private_gateway_key"] = True
    checks["auth_gateway"] = True
    checks["admin_user"] = True
    checks["llm_service"] = True
    checks["llm_credential_pool"] = True
    checks["summarizer_service"] = True
    checks["relay_server"] = True
    checks["summarizer_llm_resolution"] = True
    checks["main_flow_deployed"] = True
    checks["main_flow_executor"] = True
    checks["first_conversation"] = True
    checks["smoke_tests"] = True
    checks["finalized"] = True

    draft = state.setdefault("draft", {})
    gateway = draft.setdefault("gateway", {})
    gateway["service_id"] = final_gateway_service_id
    gateway["secret_ref"] = final_secret_ref
    gateway["skin"] = gateway_skin
    gateway["key_sha256"] = hashlib.sha256(new_key.encode("utf-8")).hexdigest()
    gateway["replaced_at"] = now
    draft["server"] = {
        "port": listener_port,
        "ssl_mode": tls_config["ssl_mode"],
        "ssl_certfile": tls_config["ssl_certfile"],
        "ssl_keyfile": tls_config["ssl_keyfile"],
    }
    draft["auth"] = {
        "service_id": auth_gateway_service_id,
        "admin_user": admin_user,
        "providers": sorted(auth_config.get("providers", {})),
        "admin_links": sorted((auth_config.get("admin_links") or {}).keys()),
    }
    draft["llm_services"] = {
        "primary": llm_service_id,
        "services": [
            {
                "service_id": spec["service_id"],
                "scope": spec["scope"],
                "provider": (spec.get("config") or {}).get("provider", ""),
                "credential_service_id": (spec.get("config") or {}).get("credential_service_id", ""),
                "credential_pool_scope": spec.get("credential_scope", ""),
            }
            for spec in llm_specs
        ],
        "credential_service_id": credential_service_id,
    }
    draft["summarizer_service"] = {
        "service_id": summarizer_service_id,
        "scope": summarizer_plan["scope"],
        "llm_service": summarizer_plan["config"].get("llm_service", ""),
    }
    draft["relay_server"] = {
        "enabled": bool(relay_plan),
        "service_id": relay_plan["service_id"] if relay_plan else "",
        "scope": relay_plan["scope"] if relay_plan else "",
    }
    draft["flows"] = {"main_instance_id": main_instance_id}
    draft["conversation"] = {
        "conversation_id": first_conversation_id,
        **_first_conversation_spec(payload, llm_service_id),
    }
    draft["smoke_tests"] = smoke_checks

    _write_state(state)

    try:
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.get_instance().update_status(INSTALLER_INSTANCE_ID, "stopped")
    except Exception:
        logger.warning("Install bootstrap finalized but installer status update failed", exc_info=True)

    try:
        from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
        ServiceRegistry.get_instance().disable(
            SCOPE_GLOBAL, "", BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID)
    except Exception:
        logger.warning("Install bootstrap finalized but bootstrap gateway disable failed", exc_info=True)

    _stop_installer_executor_soon()
    logger.info("Install bootstrap finalized")
    return get_install_status()


def ensure_install_bootstrap(port: int) -> bool:
    """Deploy the installer flow for a fresh server data volume.

    Returns True when the installer deployment was created or refreshed.
    Existing non-installer deployments are treated as an already-configured
    server and are left untouched.
    """
    if os.environ.get("PAWFLOW_BOOTSTRAP_DISABLED", "").lower() in {"1", "true", "yes"}:
        logger.info("Install bootstrap disabled by PAWFLOW_BOOTSTRAP_DISABLED")
        return False

    if os.environ.get("PAWFLOW_BOOTSTRAP_RESET", "").lower() in {"1", "true", "yes"}:
        logger.warning("Resetting install bootstrap state by PAWFLOW_BOOTSTRAP_RESET")
        try:
            INSTALL_STATE_FILE.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to remove install bootstrap state during reset", exc_info=True)
        try:
            from core.deployment_registry import DeploymentRegistry
            registry = DeploymentRegistry.get_instance()
            registry.undeploy(INSTALLER_INSTANCE_ID)
            registry.undeploy(MAIN_INSTANCE_ID)
        except Exception:
            logger.warning("Failed to undeploy bootstrap flows during reset", exc_info=True)

    state = _load_state()
    if state.get("install_complete"):
        _sync_main_flow_listener_port(port)
        return False

    from core.deployment_registry import DeploymentRegistry

    registry = DeploymentRegistry.get_instance()
    deployments = registry.get_all()
    non_installer = [iid for iid in deployments if iid != INSTALLER_INSTANCE_ID]
    if non_installer and not state:
        logger.info(
            "Install bootstrap skipped: existing deployments found (%d)",
            len(non_installer),
        )
        return False

    template_refreshed = _refresh_installer_template_from_default_data()

    if not INSTALLER_TEMPLATE.exists():
        logger.error("Install bootstrap template missing: %s", INSTALLER_TEMPLATE)
        return False

    bootstrap_key = os.environ.get(
        "PAWFLOW_BOOTSTRAP_GATEWAY_KEY",
        DEFAULT_BOOTSTRAP_GATEWAY_KEY,
    )
    bootstrap_secret_ref = _store_bootstrap_gateway_secret(bootstrap_key)
    private_gateway_service_id = _install_bootstrap_private_gateway(bootstrap_secret_ref)
    ssl_params = ensure_bootstrap_self_signed_cert()
    installer_params = {
        "port": port,
        "bootstrap_gateway_secret_ref": bootstrap_secret_ref,
        "private_gateway_service_id": private_gateway_service_id,
        **ssl_params,
    }

    if template_refreshed and INSTALLER_INSTANCE_ID in deployments:
        logger.info("Redeploying bootstrap installer after template refresh")
        registry.undeploy(INSTALLER_INSTANCE_ID)
        deployments = registry.get_all()

    if INSTALLER_INSTANCE_ID not in deployments:
        registry.deploy(
            template_path=str(INSTALLER_TEMPLATE),
            owner=None,
            parameters=installer_params,
            source="bootstrap",
            instance_id=INSTALLER_INSTANCE_ID,
        )
    else:
        inst = registry.get(INSTALLER_INSTANCE_ID)
        if inst is not None:
            inst.parameters.update(installer_params)
            registry._save_instance(inst)

    registry.update_status(INSTALLER_INSTANCE_ID, "running")
    state.setdefault("version", 1)
    state["install_complete"] = False
    state["current_step"] = state.get("current_step") or "server"
    state["installer_instance_id"] = INSTALLER_INSTANCE_ID
    state["updated_at"] = time.time()
    state.setdefault("completed_steps", [])
    state.setdefault("draft", {})
    state["draft"].setdefault("server", {})
    state["draft"]["server"].update({
        "ssl_mode": ssl_params["ssl_mode"],
        "ssl_certfile": ssl_params["ssl_certfile"],
        "ssl_keyfile": ssl_params["ssl_keyfile"],
    })
    state["draft"].setdefault("gateway", {})
    state["draft"]["gateway"].update({
        "service_id": private_gateway_service_id,
        "secret_ref": bootstrap_secret_ref,
    })
    state.setdefault("checks", {})
    state["checks"]["bootstrap_self_signed_cert"] = True
    state["checks"]["bootstrap_private_gateway"] = True
    _write_state(state)
    logger.info("Install bootstrap active: %s", INSTALLER_INSTANCE_ID)
    return True
