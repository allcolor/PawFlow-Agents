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


def test_flow_instance_template_falls_back_from_legacy_path(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from tasks.ai.actions import service_flow

    template = tmp_path / "pawflow-agent.json"
    template.write_text(
        '{"id":"pawflow-agent","name":"pawflow_agent",'
        '"parameters":{"conversation_ttl":0,"oauth_provider":"google"}}',
        encoding="utf-8",
    )
    inst = SimpleNamespace(
        flow_path=str(tmp_path / "missing.json"),
        flow_id="pawflow-agent",
        flow_name="pawflow_agent",
    )

    monkeypatch.setattr(
        service_flow,
        "_resolve_flow_template_path",
        lambda template_id, user_id, conversation_id="": template if template_id == "pawflow-agent" else None,
    )

    raw = service_flow._load_flow_instance_template_raw(inst, "user1")

    assert raw["parameters"]["conversation_ttl"] == 0
    assert raw["parameters"]["oauth_provider"] == "google"


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


def test_update_flow_params_restarts_running_executor(monkeypatch):
    from tasks.ai.actions.service_flow import _restart_running_flow_instance

    calls = []
    inst = SimpleNamespace(
        flow_path="/tmp/flow.json",
        max_workers=2,
        max_retries=1,
        flow_fqn="pkg.flow:1.0.0",
        flow_scope="global",
        parameters={"p": "new"},
        service_overrides={"auth": "global:_auth_gateway"},
        service_configs={},
        owner="user1",
        conversation_id="conv1",
        agent_name="assistant",
    )

    class _Executor:
        is_running = True

        def stop(self):
            calls.append(("stop",))

    class _Registry:
        def get(self, instance_id):
            assert instance_id == "flow1"
            return _Executor()

        def unregister(self, instance_id):
            calls.append(("unregister", instance_id))

        def _restore_instance(self, *args, **kwargs):
            calls.append(("restore", args, kwargs))

    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        staticmethod(lambda: _Registry()),
    )

    assert _restart_running_flow_instance("flow1", inst) is True
    assert calls[0] == ("stop",)
    assert calls[1] == ("unregister", "flow1")
    restore = calls[2]
    assert restore[0] == "restore"
    assert restore[1][0] == "flow1"
    assert restore[2]["service_overrides"] == {"auth": "global:_auth_gateway"}
    assert restore[2]["parameters"] == {"p": "new"}


def test_updated_global_service_rebinds_running_flow_override(monkeypatch):
    from tasks.ai.actions.service_flow import _refresh_running_flow_service_bindings

    old_auth = SimpleNamespace(name="old")
    new_auth = SimpleNamespace(name="new")
    flow = SimpleNamespace(services={"auth": old_auth, "local": SimpleNamespace()})
    executor = SimpleNamespace(_flow=flow)
    deployment = SimpleNamespace(service_overrides={"auth": "global:_auth_gateway"})

    class _Deployments:
        def get_all(self):
            return {"pawflow-agent": deployment}

    class _Executors:
        def get(self, instance_id):
            assert instance_id == "pawflow-agent"
            return executor

    class _Services:
        def get_live_instance(self, scope, scope_id, service_id):
            assert (scope, scope_id, service_id) == ("global", "", "_auth_gateway")
            return new_auth

    monkeypatch.setattr(
        "core.deployment_registry.DeploymentRegistry.get_instance",
        staticmethod(lambda: _Deployments()),
    )
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        staticmethod(lambda: _Executors()),
    )
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Services()),
    )

    refreshed = _refresh_running_flow_service_bindings("global", "", "_auth_gateway")

    assert refreshed == ["pawflow-agent"]
    assert flow.services["auth"] is new_auth


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
