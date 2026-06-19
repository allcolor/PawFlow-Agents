"""AgentLoopTask actions  - service flow"""

import logging

from tasks.ai.actions._sf_base import (  # noqa: F401
    _UNHANDLED,
    _oauth_pending,
    _is_admin,
    _service_scope_id,
    _normalize_service_scope,
    _SERVICE_CATEGORY_ORDER,
    _SERVICE_CATEGORY_BY_TYPE,
    _DISABLED_DIRECT_SERVICE_INSTALL_TYPES,
    _DISABLED_DIRECT_SERVICE_INSTALL_MESSAGES,
    _service_category,
    _service_type_sort_key,
    _service_requires_connected_state,
    _wait_for_service_connected,
    _validate_required_service_config,
    _service_started_for_listing,
    _service_install_state_for_listing,
    _credential_provider_for_service,
    _credential_module,
    _store_claude_tokens,
    _store_codex_tokens,
    _store_gemini_tokens,
    _resolve_service_definition_for_action,
    _PARAM_SCHEMA_KEYS,
    _schema_entry_from_value,
    _normalize_flow_parameters,
    _template_roots,
    _resolve_flow_template_path,
    _flow_template_storage_info,
    _validate_flow_package_name,
    _ensure_template_scope_edit_allowed,
    _rewrite_flow_template_package,
    _service_parameter_schema,
    _flow_services_schema,
    _flow_deploy_schema_payload,
    _flow_one_shot_trigger_payload,
    _load_flow_instance_template_raw,
    _set_instance_config,
    _restart_running_flow_instance,
    _service_override_matches,
    _refresh_running_flow_service_bindings,
    _find_http_listener,
)
from tasks.ai.actions._sf_routes import (  # noqa: F401
    _ws_upgrade_required,
    _docker_published_host,
    _ensure_vnc_routes,
    _novnc_backend_http_ready,
    _vnc_login_host_candidates,
    _wait_for_vnc_login_backend,
    _docker_container_ip,
    _ensure_terminal_routes,
    _ensure_code_server_routes,
    _publish_command_result,
    _notify_remote_mounts_after_service_change,
)
from tasks.ai.actions._sf_k1 import _handle_sf_k1
from tasks.ai.actions._sf_k2 import _handle_sf_k2
from tasks.ai.actions._sf_k3 import _handle_sf_k3
from tasks.ai.actions._sf_k4 import _handle_sf_k4
from tasks.ai.actions._sf_k5 import _handle_sf_k5
from tasks.ai.actions._sf_k6 import _handle_sf_k6
from tasks.ai.actions._sf_k7 import _handle_sf_k7
from tasks.ai.actions._sf_k8 import _handle_sf_k8
from tasks.ai.actions._sf_k9 import _handle_sf_k9

logger = logging.getLogger(__name__)


def _handle_service_flow(self, action, body, store, user_id, flowfile):
    """Handle service flow actions. Returns [flowfile] or None."""
    def _find_relay_svc(relay_id):
        """Find relay service across conv > user > global scopes."""
        from core.service_registry import ServiceRegistry
        _cid = (body.get("conversation_id", "")
                or flowfile.get_attribute("http.conversation_id") or "")
        return ServiceRegistry.get_instance().resolve(
            relay_id, user_id=user_id, conv_id=_cid)


    def _audio_lookup_token(sid: str) -> str:
        """Return the capability token minted for an audio session, or
        empty string if there is none. Used by the URL builders that
        emit `audio_token` alongside `audio_session` so the frontend
        can build /audio/<sid>/<token>/stream."""
        try:
            from services.audio_proxy import get_audio_token
            return get_audio_token(sid)
        except Exception:
            return ""

    def _get_server_relay_container_ip(relay_id):
        """Return the Docker-network IP for a managed server relay container."""
        import subprocess  # nosec B404
        from core.docker_utils import docker_cmd as _dkr_cmd
        candidates = []
        try:
            from core.server_relay_manager import ServerRelayManager
            for entry in ServerRelayManager.get_instance().list_all():
                if entry.get("relay_id") != relay_id:
                    continue
                cname = entry.get("container_id") or entry.get("container_name") or ""
                if cname:
                    candidates.append(cname)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            svc = _find_relay_svc(relay_id)
            cfg = getattr(svc, "config", {}) if svc else {}
            for cname in (
                str(cfg.get("server_container_id") or ""),
                str(cfg.get("server_container_name") or ""),
                f"pawflow-relay-srv-{relay_id}",
            ):
                if cname and cname not in candidates:
                    candidates.append(cname)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        for cname in candidates:
            try:
                r = subprocess.run(  # nosec B603
                    _dkr_cmd() + [
                        "inspect", "-f",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                        cname,
                    ],
                    capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return ""

    def _get_relay_published_port(relay_id, container_port):
        """Return the Docker-published host port for a relay container port."""
        import subprocess  # nosec B404
        from core.docker_utils import docker_cmd as _dkr_cmd
        candidates = []
        try:
            from core.server_relay_manager import ServerRelayManager
            for entry in ServerRelayManager.get_instance().list_all():
                if entry.get("relay_id") != relay_id:
                    continue
                cname = entry.get("container_id") or entry.get("container_name") or ""
                if cname:
                    candidates.append(cname)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            svc = _find_relay_svc(relay_id)
            info = getattr(svc, '_relay_info', {}) if svc else {}
            cfg = getattr(svc, "config", {}) if svc else {}
            for cname in (
                str(info.get("container_id") or ""),
                str(info.get("container_name") or ""),
                str(cfg.get("server_container_id") or ""),
                str(cfg.get("server_container_name") or ""),
                f"pawflow-relay-srv-{relay_id}",
            ):
                if cname and cname not in candidates:
                    candidates.append(cname)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        for cname in candidates:
            try:
                r = subprocess.run(  # nosec B603
                    _dkr_cmd() + ["port", cname, str(container_port)],
                    capture_output=True, text=True, timeout=5)
                if r.returncode != 0 or not r.stdout.strip():
                    continue
                endpoint = r.stdout.strip().splitlines()[0]
                return int(endpoint.rsplit(":", 1)[-1])
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return 0

    def _server_relay_proxy_target(relay_id, container_port):
        """Return host/port the server container should use for relay desktop ports."""
        container_ip = _get_server_relay_container_ip(relay_id)
        if container_ip:
            return container_ip, container_port
        published_port = _get_relay_published_port(relay_id, container_port)
        if published_port:
            return _docker_published_host(), published_port
        return "", 0
    def _private_gateway_for_body():
        service_id = body.get("service_id", "") or body.get("private_gateway_service_id", "")
        if not service_id:
            return None
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            service_id, user_id=user_id,
            conv_id=body.get("conversation_id", "")
            or flowfile.get_attribute("http.conversation_id") or "")
        if not svc or getattr(svc, "TYPE", "") != "privateGateway":
            raise ValueError(f"Private gateway service '{service_id}' not found")
        return svc
    _helpers = (
        _find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
        _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body)
    for _handler in (_handle_sf_k1, _handle_sf_k2, _handle_sf_k3, _handle_sf_k4,
                     _handle_sf_k5, _handle_sf_k6, _handle_sf_k7, _handle_sf_k8,
                     _handle_sf_k9):
        _res = _handler(self, action, body, store, user_id, flowfile, _helpers)
        if _res is not _UNHANDLED:
            return _res
    return None
