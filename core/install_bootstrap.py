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
DEFAULT_BOOTSTRAP_GATEWAY_KEY = "RoyBetty"
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
    for section in ("server", "gateway"):
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
    checks["finalized"] = True

    draft = state.setdefault("draft", {})
    gateway = draft.setdefault("gateway", {})
    gateway["key_sha256"] = hashlib.sha256(new_key.encode("utf-8")).hexdigest()
    gateway["replaced_at"] = now

    _write_state(state)

    try:
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.get_instance().update_status(INSTALLER_INSTANCE_ID, "stopped")
    except Exception:
        logger.warning("Install bootstrap finalized but installer status update failed", exc_info=True)

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
    ssl_params = ensure_bootstrap_self_signed_cert()
    installer_params = {
        "port": port,
        "bootstrap_gateway_key": bootstrap_key,
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
    state.setdefault("checks", {})
    state["checks"]["bootstrap_self_signed_cert"] = True
    _write_state(state)
    logger.info("Install bootstrap active: %s", INSTALLER_INSTANCE_ID)
    return True
