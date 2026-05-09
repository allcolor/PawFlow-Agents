"""First-run installation bootstrap for PawFlow server."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
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
BOOTSTRAP_GATEWAY_SECRET_REF = "privategateway.bootstrap"
BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID = "_bootstrap_private_gateway"
FINAL_GATEWAY_SECRET_REF = "privategateway.main"
FINAL_PRIVATE_GATEWAY_SERVICE_ID = "_private_gateway"
AUTH_GATEWAY_SERVICE_ID = "_auth_gateway"
DEFAULT_LLM_SERVICE_ID = "codex_appserver_llm_service"
SUMMARIZER_SERVICE_ID = "summarizer_service"
SKILL_REVIEW_SERVICE_ID = "skill_review_service"
FIRST_RUN_AGENT = "assistant"
BOOTSTRAP_CERT_FILE = _paths.SSL_DIR / "bootstrap.crt"
BOOTSTRAP_KEY_FILE = _paths.SSL_DIR / "bootstrap.key"
INSTALL_STEPS = [
    "server",
    "certificates",
    "gateway",
    "auth",
    "admin",
    "llm_services",
    "summarizer_service",
    "skill_review_service",
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
    "default_client_profile": "client-minimal",
    "advanced_features": True,
}


def ensure_bootstrap_self_signed_cert() -> Dict[str, str]:
    """Create the first-run self-signed TLS certificate if missing."""
    if BOOTSTRAP_CERT_FILE.exists() and BOOTSTRAP_KEY_FILE.exists():
        return {
            "ssl_certfile": str(BOOTSTRAP_CERT_FILE),
            "ssl_keyfile": str(BOOTSTRAP_KEY_FILE),
            "ssl_mode": "self_signed",
        }

    _paths.SSL_DIR.mkdir(parents=True, exist_ok=True)
    hosts = [
        h.strip()
        for h in os.environ.get(
            "PAWFLOW_BOOTSTRAP_CERT_HOSTS", "localhost,127.0.0.1"
        ).split(",")
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
        "-sha256", "-days", "30", "-nodes",
        "-keyout", str(BOOTSTRAP_KEY_FILE),
        "-out", str(BOOTSTRAP_CERT_FILE),
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=" + ",".join(san_parts),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        BOOTSTRAP_KEY_FILE.chmod(0o600)
        BOOTSTRAP_CERT_FILE.chmod(0o644)
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
        "skill_review_service",
        "flows",
        "conversation",
    ):
        value = draft.get(section)
        if isinstance(value, dict):
            public[section] = dict(value)
    return public


def get_install_status() -> Dict[str, Any]:
    """Return installer state without exposing bootstrap or gateway secrets."""
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
            pass
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


def _install_final_private_gateway(secret_ref: str) -> str:
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
            "skin": "matrix",
        },
        description="Persistent private gateway for PawFlow",
        enabled=True,
    )
    return FINAL_PRIVATE_GATEWAY_SERVICE_ID


def _configure_admin_user(payload: Dict[str, Any]) -> str:
    """Create or update the first admin user from installer input."""
    from core.security import SecurityManager, Role

    username = str(payload.get("admin_username") or "admin").strip()
    password = str(payload.get("admin_password") or "")
    if not username:
        raise ValueError("admin_username is required")
    if not password:
        raise ValueError("admin_password is required")
    if len(password) < 12:
        raise ValueError("admin_password must be at least 12 characters")

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


def _install_auth_gateway() -> str:
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    _register_all_services()
    ServiceRegistry.get_instance().install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=AUTH_GATEWAY_SERVICE_ID,
        service_type="authGateway",
        config={
            "providers": {"builtin": {"enabled": True}},
            "session_ttl": 86400,
        },
        description="Builtin authentication for the installed PawFlow server",
        enabled=True,
    )
    return AUTH_GATEWAY_SERVICE_ID


def _install_llm_and_summarizer(payload: Dict[str, Any]) -> tuple[str, str]:
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL

    _register_all_services()
    provider = str(payload.get("llm_provider") or "codex-app-server").strip()
    model = str(payload.get("llm_model") or "gpt-5.5").strip()
    llm_service_id = str(payload.get("llm_service_id") or DEFAULT_LLM_SERVICE_ID).strip()
    if not provider:
        raise ValueError("llm_provider is required")
    if not llm_service_id:
        raise ValueError("llm_service_id is required")

    llm_config: Dict[str, Any] = {
        "provider": provider,
        "default_model": model,
        "timeout": 600,
    }
    base_url = str(payload.get("llm_base_url") or "").strip()
    if base_url:
        llm_config["base_url"] = base_url
    api_key = str(payload.get("llm_api_key") or "").strip()
    if api_key:
        secret_ref = f"llm.{llm_service_id}.api_key"
        _store_global_secret(secret_ref, api_key)
        llm_config["api_key"] = "${" + secret_ref + "}"

    reg = ServiceRegistry.get_instance()
    reg.install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=llm_service_id,
        service_type="llmConnection",
        config=llm_config,
        description="Default installed LLM service for the first PawFlow agent",
        enabled=True,
    )
    reg.install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=SUMMARIZER_SERVICE_ID,
        service_type="summarizer",
        config={"llm_service": llm_service_id},
        description="Summarizer service for conversation compaction",
        enabled=True,
    )
    reg.install(
        scope=SCOPE_GLOBAL,
        scope_id="",
        service_id=SKILL_REVIEW_SERVICE_ID,
        service_type="skillReview",
        config={"llm_service": llm_service_id},
        description="Skill review service for prompt-injection checks",
        enabled=True,
    )
    return llm_service_id, SUMMARIZER_SERVICE_ID, SKILL_REVIEW_SERVICE_ID


def _deploy_main_flow(private_gateway_service_id: str) -> str:
    from core.deployment_registry import DeploymentRegistry

    if not MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {MAIN_TEMPLATE}")
    reg = DeploymentRegistry.get_instance()
    params = {"private_gateway_service_id": private_gateway_service_id}
    inst = reg.get(MAIN_INSTANCE_ID)
    if inst is None:
        reg.deploy(
            template_path=str(MAIN_TEMPLATE),
            owner=None,
            parameters=params,
            source="bootstrap",
            instance_id=MAIN_INSTANCE_ID,
        )
    else:
        inst.parameters.update(params)
        inst.source = "bootstrap"
        reg._save_instance(inst)
    reg.update_status(MAIN_INSTANCE_ID, "running")
    return MAIN_INSTANCE_ID


def _create_first_conversation(admin_user: str, llm_service_id: str) -> str:
    from core.conversation_store import ConversationStore
    from core.conv_agent_config import add_agent_to_conv
    from core.resource_store import ResourceStore, GLOBAL_USER_ID

    rs = ResourceStore.instance()
    if rs.get_any("agent", FIRST_RUN_AGENT, admin_user) is None:
        rs.create(
            "agent",
            FIRST_RUN_AGENT,
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
    store.set_extra(conv_id, "title", "Welcome to PawFlow")
    store.set_extra(
        conv_id,
        "active_resources",
        {"agents": [FIRST_RUN_AGENT], "agent": FIRST_RUN_AGENT},
    )
    add_agent_to_conv(
        conv_id,
        FIRST_RUN_AGENT,
        llm_service=llm_service_id,
        definition=FIRST_RUN_AGENT,
        params={"name": FIRST_RUN_AGENT},
        max_depth=1000,
    )
    return conv_id


def finalize_install(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Finalize first-run bootstrap after replacing the gateway key.

    The current bootstrap key authorizes the public bootstrap API. The new
    gateway key is never persisted in clear text; only a SHA-256 digest is kept
    so the state file can prove replacement without becoming a secret store.
    """
    state = _load_state()
    if state.get("install_complete"):
        return get_install_status()

    expected_key = os.environ.get(
        "PAWFLOW_BOOTSTRAP_GATEWAY_KEY",
        DEFAULT_BOOTSTRAP_GATEWAY_KEY,
    )
    provided_key = str(
        payload.get("bootstrap_gateway_key")
        or payload.get("current_gateway_key")
        or ""
    )
    if provided_key != expected_key:
        raise PermissionError("invalid bootstrap gateway key")

    new_key = str(
        payload.get("new_gateway_key")
        or payload.get("gateway_key")
        or ""
    ).strip()
    if not new_key:
        raise ValueError("new_gateway_key is required")
    if new_key in {expected_key, DEFAULT_BOOTSTRAP_GATEWAY_KEY}:
        raise ValueError("new_gateway_key must replace the bootstrap key")
    if len(new_key) < 16:
        raise ValueError("new_gateway_key must be at least 16 characters")

    admin_username = str(payload.get("admin_username") or "admin").strip()
    admin_password = str(payload.get("admin_password") or "")
    if not admin_username:
        raise ValueError("admin_username is required")
    if not admin_password:
        raise ValueError("admin_password is required")
    if len(admin_password) < 12:
        raise ValueError("admin_password must be at least 12 characters")
    if not str(payload.get("llm_provider") or "codex-app-server").strip():
        raise ValueError("llm_provider is required")
    if not str(payload.get("llm_service_id") or DEFAULT_LLM_SERVICE_ID).strip():
        raise ValueError("llm_service_id is required")
    if not MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {MAIN_TEMPLATE}")

    final_secret_ref = _store_global_secret(FINAL_GATEWAY_SECRET_REF, new_key)
    final_gateway_service_id = _install_final_private_gateway(final_secret_ref)
    admin_user = _configure_admin_user(payload)
    auth_gateway_service_id = _install_auth_gateway()
    llm_service_id, summarizer_service_id, skill_review_service_id = _install_llm_and_summarizer(payload)
    main_instance_id = _deploy_main_flow(final_gateway_service_id)
    first_conversation_id = _create_first_conversation(admin_user, llm_service_id)

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
    checks["auth_gateway"] = True
    checks["admin_user"] = True
    checks["llm_service"] = True
    checks["summarizer_service"] = True
    checks["skill_review_service"] = True
    checks["main_flow_deployed"] = True
    checks["first_conversation"] = True
    checks["finalized"] = True

    draft = state.setdefault("draft", {})
    gateway = draft.setdefault("gateway", {})
    gateway["service_id"] = final_gateway_service_id
    gateway["secret_ref"] = final_secret_ref
    gateway["key_sha256"] = hashlib.sha256(new_key.encode("utf-8")).hexdigest()
    gateway["replaced_at"] = now
    draft["auth"] = {"service_id": auth_gateway_service_id, "admin_user": admin_user}
    draft["llm_services"] = {"primary": llm_service_id}
    draft["summarizer_service"] = {"service_id": summarizer_service_id}
    draft["skill_review_service"] = {"service_id": skill_review_service_id}
    draft["flows"] = {"main_instance_id": main_instance_id}
    draft["conversation"] = {
        "conversation_id": first_conversation_id,
        "agent": FIRST_RUN_AGENT,
    }

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

    logger.info("Install bootstrap finalized")
    return get_install_status()


def ensure_install_bootstrap(port: int = 9090) -> bool:
    """Deploy the installer flow for a fresh server data volume.

    Returns True when the installer deployment was created or refreshed.
    Existing non-installer deployments are treated as an already-configured
    server and are left untouched.
    """
    if os.environ.get("PAWFLOW_BOOTSTRAP_DISABLED", "").lower() in {"1", "true", "yes"}:
        logger.info("Install bootstrap disabled by PAWFLOW_BOOTSTRAP_DISABLED")
        return False

    state = _load_state()
    if state.get("install_complete"):
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
