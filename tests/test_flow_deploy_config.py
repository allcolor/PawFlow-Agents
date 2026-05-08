"""Flow deployment configuration schema and bindings."""

from types import SimpleNamespace


def test_flow_deploy_schema_exposes_parameters_and_services():
    from tasks import register_all_tasks
    from tasks.ai.actions.service_flow import _flow_deploy_schema_payload

    register_all_tasks()
    raw = {
        "id": "demo-flow",
        "name": "Demo Flow",
        "parameters": {
            "recipient": {
                "type": "string",
                "default": "ops@example.com",
                "description": "Email recipient",
            },
            "enabled": True,
        },
        "services": {
            "llm_local": {
                "type": "llmConnection",
                "parameters": {
                    "provider": "openai",
                    "model": "qwen",
                    "custom_option": "kept",
                },
            },
        },
    }

    payload = _flow_deploy_schema_payload(raw)

    assert payload["parameters_schema"]["recipient"]["default"] == "ops@example.com"
    assert payload["parameters_schema"]["enabled"]["type"] == "boolean"
    svc = payload["services"]["llm_local"]
    assert svc["service_type"] == "llmConnection"
    assert svc["parameter_values"]["provider"] == "openai"
    assert svc["parameter_values"]["default_model"] == "qwen"
    assert "provider" in svc["parameters_schema"]
    assert svc["parameters_schema"]["custom_option"]["default"] == "kept"


def test_flow_deploy_schema_exposes_instance_only_parameters():
    from tasks.ai.actions.service_flow import _flow_deploy_schema_payload

    raw = {
        "id": "pawflow-agent",
        "name": "pawflow_agent",
        "parameters": {},
        "services": {},
    }

    payload = _flow_deploy_schema_payload(
        raw,
        parameters={
            "conversation_ttl": 0,
            "oauth_provider": "google",
            "_user_id": "hidden",
        },
    )

    assert payload["parameters_schema"]["conversation_ttl"]["type"] == "integer"
    assert payload["parameters_schema"]["oauth_provider"]["default"] == "google"
    assert "_user_id" not in payload["parameters_schema"]
    assert payload["parameter_values"]["conversation_ttl"] == 0


def test_set_instance_config_replaces_public_params_and_service_bindings():
    from tasks.ai.actions.service_flow import _set_instance_config

    inst = SimpleNamespace(
        parameters={"old": "remove", "_user_id": "user1"},
        service_overrides={"svc": "global:old"},
        service_configs={"svc": {"model": "old"}},
    )

    _set_instance_config(
        inst,
        parameters={"new": "value"},
        service_overrides={"svc": "global:llm-main", "local_svc": "local"},
        service_configs={"local_svc": {"model": "qwen"}},
    )

    assert inst.parameters == {"_user_id": "user1", "new": "value"}
    assert inst.service_overrides == {"svc": "global:llm-main"}
    assert inst.service_configs == {"local_svc": {"model": "qwen"}}


def test_executor_service_bindings_apply_configs_and_overrides(monkeypatch):
    from core.executor_registry import _apply_service_bindings

    local = SimpleNamespace(config={"model": "old"})
    forwarded = SimpleNamespace(config={"model": "existing"})
    flow = SimpleNamespace(services={"llm_local": local})

    class _Registry:
        def get_live_instance(self, scope, owner, service_id):
            assert (scope, owner, service_id) == ("global", "", "llm-main")
            return forwarded

    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Registry()))

    _apply_service_bindings(
        flow,
        service_overrides={"llm_local": "global:llm-main"},
        service_configs={"llm_local": {"model": "qwen"}},
    )

    assert local.config["model"] == "qwen"
    assert flow.services["llm_local"] is forwarded
