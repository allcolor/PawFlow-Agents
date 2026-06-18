"""Install action steps for first-run finalize (LLM/relay/voice, deploy, rollback).

Extracted from install_bootstrap.py. Depends downward on core._install_base and
core._install_credentials only.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Any, Dict

from core import _install_base
from core._install_base import (
    AUTH_GATEWAY_SERVICE_ID,
    FINAL_GATEWAY_SECRET_REF,
    FINAL_PRIVATE_GATEWAY_SERVICE_ID,
    INSTALLER_INSTANCE_ID,
    MAIN_INSTANCE_ID,
    SUMMARIZER_SERVICE_ID,
    _delete_global_secret,
    _store_global_secret,
)
from core._install_credentials import (
    _install_llm_credential_pool_for_scope,
    _install_scope_id,
    _llm_service_specs,
    _relay_server_spec,
    _summarizer_spec,
    _voice_service_specs,
)

logger = logging.getLogger(__name__)


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


def _install_voice_services(payload: Dict[str, Any], admin_username: str) -> list[Dict[str, str]]:
    specs = _voice_service_specs(payload)
    if not specs:
        return []
    from tasks import _register_all_services
    from core.service_registry import ServiceRegistry

    _register_all_services()
    reg = ServiceRegistry.get_instance()
    installed = []
    for spec in specs:
        reg.install(
            scope=spec["scope"],
            scope_id=_install_scope_id(spec["scope"], admin_username),
            service_id=spec["service_id"],
            service_type=spec["service_type"],
            config=spec["config"],
            description=(
                "Supertonic TTS service installed from first-run bootstrap"
                if spec["kind"] == "tts"
                else "Voicebox STT service installed from first-run bootstrap"
            ),
            enabled=True,
        )
        installed.append({
            "kind": spec["kind"],
            "scope": spec["scope"],
            "service_id": spec["service_id"],
            "service_type": spec["service_type"],
        })
    return installed


def _deploy_main_flow(private_gateway_service_id: str,
                      tls_config: Dict[str, str],
                      auth_config: Dict[str, Any],
                      listener_port: int) -> str:
    from core.deployment_registry import DeploymentRegistry

    if not _install_base.MAIN_TEMPLATE.exists():
        raise ValueError(f"main PawFlow flow template is missing: {_install_base.MAIN_TEMPLATE}")
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
            template_path=str(_install_base.MAIN_TEMPLATE),
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
    for spec in _voice_service_specs(payload):
        refs.append({"scope": spec["scope"], "service_id": spec["service_id"]})
    return refs


