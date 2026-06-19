"""First-run installation bootstrap for PawFlow server.

Public facade. The install logic is layered across:
  core._install_base         - constants, certs, state, secrets, gateway, admin
  core._install_credentials  - LLM credential pool + service/spec builders
  core._install_actions      - install action steps + rollback
This module keeps the top-level orchestration (finalize_install,
ensure_install_bootstrap, smoke checks, first conversation) and re-exports the
public API for import stability.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess  # noqa: F401  # nosec B404  # re-exported so tests can patch ib.subprocess
import threading  # noqa: F401  # re-exported so tests can patch ib.threading
import time
from typing import Any, Dict

import core.paths as _paths
from core import _install_base

# Path constants read across layers are accessed via the _install_base module
# (e.g. _install_base.INSTALL_STATE_FILE) so a single monkeypatch on the base
# module redirects every reader; value-imports would leave stale bindings.
from core._install_base import (
    AUTH_GATEWAY_SERVICE_ID,
    DEFAULT_BOOTSTRAP_GATEWAY_KEY,
    FINAL_GATEWAY_SECRET_REF,
    FINAL_PRIVATE_GATEWAY_SERVICE_ID,
    INSTALLER_INSTANCE_ID,
    INSTALL_STEPS,
    MAIN_INSTANCE_ID,
    _cleanup_bootstrap_artifacts,
    _configure_admin_user,
    _ensure_bootstrap_open,
    _expected_bootstrap_key,
    _final_listener_port,
    _final_tls_config,
    _install_bootstrap_private_gateway,
    _install_final_private_gateway,
    _load_state,
    _refresh_installer_template_from_default_data,
    _restore_file_state,
    _snapshot_file_state,
    _store_bootstrap_gateway_secret,
    _store_global_secret,
    _sync_main_flow_listener_port,
    _validate_admin_password,
    _validate_gateway_skin,
    _write_state,
    ensure_bootstrap_self_signed_cert,
    get_install_status,
    is_install_complete,
    require_bootstrap_key,
)
from core._install_credentials import (
    _build_auth_gateway_config,
    _first_conversation_spec,
    _install_auth_gateway,
    _install_llm_credential_pool_for_scope,  # noqa: F401  # re-exported for tests
    _install_scope_id,
    _llm_credential_pool_status,
    _llm_service_specs,
    _relay_server_spec,
    _summarizer_spec,
    _validate_llm_services_auth_ready,
    _voice_service_specs,
    prepare_llm_credential_pool,
    save_llm_credential,
)
from core._install_actions import (
    _deploy_main_flow,
    _install_llm_and_summarizer,
    _install_relay_server,
    _install_voice_services,
    _rollback_failed_finalization,
    _rollback_service_refs,
    _start_main_flow_executor,
    _stop_installer_executor_soon,
)

# Read-only base constants re-exported for import stability (callers and tests
# reference them via core.install_bootstrap). Patchable path constants are
# intentionally NOT re-exported here; patch them on core._install_base.
from core._install_base import (  # noqa: F401,E402
    BOOTSTRAP_GATEWAY_SECRET_REF,
    BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID,
    CLIENT_RELAY_IMAGES,
    FIRST_RUN_AGENT,
    INSTALLER_FLOW_FQN,
    MAIN_FLOW_FQN,
    SUMMARIZER_SERVICE_ID,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ensure_bootstrap_self_signed_cert",
    "ensure_install_bootstrap",
    "finalize_install",
    "get_install_status",
    "is_install_complete",
    "prepare_llm_credential_pool",
    "require_bootstrap_key",
    "save_llm_credential",
]


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
    voice_services: list[Dict[str, str]] | None = None,
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

    voice_services = list(voice_services or [])
    voice_ok = True
    for item in voice_services:
        service_id = item.get("service_id") or ""
        scope = item.get("scope") or "global"
        expected_type = item.get("service_type") or ""
        sdef = reg.get_definition(scope, _install_scope_id(scope, admin_user), service_id)
        ok = sdef is not None and sdef.enabled and sdef.service_type == expected_type
        voice_ok = voice_ok and ok
        record(f"voice_{item.get('kind') or service_id}_service", ok,
               service_id=service_id, service_type=expected_type)
    record("voice_services", voice_ok, skipped=not voice_services,
           services=[item.get("service_id", "") for item in voice_services])

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
    _validate_admin_password(payload)  # validate early; raises on invalid input
    if not admin_username:
        raise ValueError("admin_username is required")
    llm_specs = _llm_service_specs(payload)
    if not llm_specs:
        raise ValueError("at least one LLM service is required")
    primary_provider = str((llm_specs[0].get("config") or {}).get("provider") or "")
    summarizer_plan = _summarizer_spec(payload, llm_specs[0]["service_id"])
    relay_plan = _relay_server_spec(payload)
    voice_plan = _voice_service_specs(payload)
    rollback_refs = _rollback_service_refs(payload)
    if not _install_base.MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {_install_base.MAIN_TEMPLATE}")
    system_snapshot = _snapshot_file_state([
        _paths.GLOBAL_SECRETS_FILE,
        _paths.USERS_FILE,
        _paths.SESSIONS_FILE,
        _paths.SECURITY_FILE,
        _install_base.FINAL_CERT_FILE,
        _install_base.FINAL_KEY_FILE,
    ])

    admin_user = str(admin_username)
    llm_service_id = str(payload.get("llm_service_id") or "").strip()
    relay_service_id = ""
    voice_services: list[Dict[str, str]] = []
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
        voice_services = _install_voice_services(payload, admin_user)
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
            voice_services=voice_services,
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
    checks["voice_services"] = True
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
    draft["voice_services"] = {
        "enabled": bool(voice_plan),
        "services": [
            {
                "kind": spec["kind"],
                "service_id": spec["service_id"],
                "scope": spec["scope"],
                "service_type": spec["service_type"],
            }
            for spec in voice_plan
        ],
    }
    draft["flows"] = {"main_instance_id": main_instance_id}
    draft["conversation"] = {
        "conversation_id": first_conversation_id,
        **_first_conversation_spec(payload, llm_service_id),
    }
    draft["smoke_tests"] = smoke_checks

    _write_state(state)

    _cleanup_bootstrap_artifacts()
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
            _install_base.INSTALL_STATE_FILE.unlink(missing_ok=True)
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
        _cleanup_bootstrap_artifacts()
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

    if not _install_base.INSTALLER_TEMPLATE.exists():
        logger.error("Install bootstrap template missing: %s", _install_base.INSTALLER_TEMPLATE)
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
            template_path=str(_install_base.INSTALLER_TEMPLATE),
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
