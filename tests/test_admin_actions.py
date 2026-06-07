import json


def test_admin_get_flow_loads_repository_definition_for_deployed_instance(tmp_path, monkeypatch):
    import core.paths as paths
    from core.deployment_registry import DeployedInstance
    from core.repository import ScopedRepository
    from tasks.io.admin_actions import _admin_get_flow

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    ScopedRepository.instance().create_flow(
        "default.telegram_agent:1.0.0", "global", {
            "id": "telegram_agent",
            "name": "telegram_agent",
            "parameters": {"agent_runtime_port": "pawflow_agent.agent_runtime_in"},
            "tasks": {"agent_client": {"type": "telegramAgentClient", "parameters": {}}},
            "relations": [],
            "runtime_links": [{
                "from": "agent_client",
                "to": "${agent_runtime_port}",
                "type": "agentRuntime",
            }],
        })
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps({
        "id": "telegram_agent",
        "name": "telegram_agent",
        "tasks": {"agent_client": {"type": "telegramAgentClient", "parameters": {}}},
        "relations": [],
    }), encoding="utf-8")
    inst = DeployedInstance(
        instance_id="telegram_agent__123456",
        flow_id="telegram_agent",
        flow_name="telegram_agent",
        flow_fqn="default.telegram_agent:1.0.0",
        flow_scope="global",
        flow_path=str(stale_path),
        owner="alice",
    )

    class DeployRegistry:
        def get(self, instance_id):
            return inst if instance_id == inst.instance_id else None

    class ExecutorRegistry:
        def get(self, instance_id):
            return None

    payload = _admin_get_flow(
        {"instance_id": inst.instance_id}, ExecutorRegistry(), DeployRegistry(), None, None)

    assert payload["runtime_links"] == [{
        "from": "agent_client",
        "to": "${agent_runtime_port}",
        "type": "agentRuntime",
    }]
    assert payload["resolved_parameters"] == {
        "agent_runtime_port": "pawflow_agent.agent_runtime_in",
    }
