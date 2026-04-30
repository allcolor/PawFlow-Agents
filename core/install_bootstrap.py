"""First-run installation bootstrap for PawFlow server."""

from __future__ import annotations

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
