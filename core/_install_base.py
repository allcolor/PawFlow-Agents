"""First-run installation bootstrap for PawFlow server."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess  # nosec B404
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
DEFAULT_BOOTSTRAP_GATEWAY_KEY = "RoyBatty"
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
    "voice_services",
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
        "voice_services",
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
    # First secret written on a fresh install: seed a per-install scrypt salt
    # before any key is derived, so password-based master keys are salted
    # uniquely. No-op on existing installs (keeps the legacy salt).
    from core.secrets import ensure_install_salt
    ensure_install_salt()
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


def _cleanup_bootstrap_artifacts() -> None:
    """Remove first-run-only deployment and service artifacts after install."""
    try:
        from core.deployment_registry import DeploymentRegistry
        registry = DeploymentRegistry.get_instance()
        if registry.get(INSTALLER_INSTANCE_ID) is not None:
            registry.update_status(INSTALLER_INSTANCE_ID, "stopped")
            registry.undeploy(INSTALLER_INSTANCE_ID)
    except Exception:
        logger.warning("Install bootstrap cleanup could not undeploy installer", exc_info=True)

    try:
        from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
        ServiceRegistry.get_instance().uninstall(
            SCOPE_GLOBAL, "", BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID)
    except Exception:
        logger.warning("Install bootstrap cleanup could not uninstall bootstrap gateway", exc_info=True)


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


