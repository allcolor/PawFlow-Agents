import json
import runpy
import sys
import types
from pathlib import Path

from core import pfp_package

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "pawflow.comfyui-operator.pfpdir"
TASKS = PACKAGE / "content" / "flow-tasks"
FLOWS = PACKAGE / "content" / "flows"


class FakePfp:
    def __init__(self, payload, *, context=None, tool_result=None):
        self.payload = payload
        self.context = context or {}
        self.tool_result = tool_result
        self.result_value = None
        self.tool_calls = []

    @staticmethod
    def flowfile(content, attributes):
        return {"content": content, "attributes": attributes}

    def result(self, value=None, *, flowfiles=None):
        self.result_value = flowfiles if flowfiles is not None else value

    def error(self, message):
        raise AssertionError(message)

    def call_tool(self, name, **arguments):
        self.tool_calls.append((name, arguments))
        return self.tool_result


def run_task(monkeypatch, tmp_path, task_name, content, config=None, *,
             attributes=None, context=None, tool_result=None):
    source = tmp_path / f"{task_name}.json"
    source.write_text(
        content if isinstance(content, str) else json.dumps(content),
        encoding="utf-8",
    )
    fake = FakePfp(
        {
            "task_config": config or {},
            "flowfile": {
                "content_path": str(source),
                "attributes": attributes or {},
            },
        },
        context=context,
        tool_result=tool_result,
    )
    module = types.ModuleType("pawflow")
    module.pfp = fake
    monkeypatch.setitem(sys.modules, "pawflow", module)
    runpy.run_path(str(TASKS / task_name / "task.py"), run_name="__main__")
    assert fake.result_value
    return fake, fake.result_value[0]


def test_manifest_inspection_lists_the_complete_operator_bundle():
    plan = pfp_package.inspect_pfp(str(PACKAGE), user_id="alice", scope="user")
    ids = {item["id"] for item in plan["objects"]}
    assert ids == {
        "skill:operate-comfyui",
        "flow_task:normalize",
        "flow_task:probe",
        "flow_task:validate",
        "flow:ensure-ready",
        "flow:provision-assets",
        "flow:generate-video",
        "flow:validate-video",
        "mcp_server:comfy-mcp",
    }
    assert plan["version"] == "1.1.0"


def test_all_flow_graphs_are_closed_and_versioned():
    definitions = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FLOWS.glob("*.json"))
    ]
    assert {flow["fqn"] for flow in definitions} == {
        "pawflow.comfyui.ensure-ready:1.0.0",
        "pawflow.comfyui.provision-assets:1.0.0",
        "pawflow.comfyui.generate-video:1.0.0",
        "pawflow.comfyui.validate-video:1.0.0",
    }
    for flow in definitions:
        task_ids = set(flow["tasks"])
        assert flow["scope"] == "conversation"
        assert set(flow["entries"]) <= task_ids
        assert set(flow["exits"]) <= task_ids
        for relation in flow["relations"]:
            assert relation["from"] in task_ids
            assert relation["to"] in task_ids
            assert relation["type"]

    ensure = next(flow for flow in definitions if flow["id"].endswith("ensure-ready"))
    assert ensure["tasks"]["request_mode"]["type"] == "requestConfirmation"
    assert ensure["tasks"]["wait_mode"]["type"] == "durableWait"

    generate = next(flow for flow in definitions if flow["id"].endswith("generate-video"))
    assert generate["tasks"]["generate"]["type"] == "tool.generate_video"


def test_normalize_request_applies_defaults_and_decodes_confirmation(
        monkeypatch, tmp_path):
    fake, output = run_task(
        monkeypatch,
        tmp_path,
        "normalize",
        {"prompt": "A slow dolly shot"},
        {
            "operation": "generate_video",
            "video_service": "comfy-video",
            "max_partner_cost_usd": 3,
        },
        attributes={"durable.wait.value": json.dumps("approve")},
    )
    normalized = json.loads(output["content"])
    assert normalized["operation"] == "generate_video"
    assert normalized["video_service"] == "comfy-video"
    assert normalized["duration"] == 5
    assert output["attributes"]["comfyui.choice"] == "approve"
    assert fake.tool_calls == []


def test_probe_uses_the_selected_relay_host_and_marks_ready(
        monkeypatch, tmp_path):
    probe_result = json.dumps({
        "base_url": "http://127.0.0.1:8188",
        "ready": True,
        "endpoints": {
            "/system_stats": {"ok": True},
            "/queue": {"ok": True},
        },
    })
    fake, output = run_task(
        monkeypatch,
        tmp_path,
        "probe",
        {"base_url": "http://127.0.0.1:8188"},
        {"include_object_info": False},
        context={"relay_id": "gpu-relay"},
        tool_result=probe_result,
    )
    name, arguments = fake.tool_calls[0]
    assert name == "bash"
    assert arguments["local"] is True
    assert arguments["relay"] == "gpu-relay"
    assert arguments["shell"] == "python"
    assert output["attributes"]["comfyui.ready"] == "true"


def test_plan_validator_rejects_shell_and_unpinned_model(
        monkeypatch, tmp_path):
    _, output = run_task(
        monkeypatch,
        tmp_path,
        "validate",
        {
            "plan": [{
                "action": "install_model",
                "source": "http://example.invalid/model.safetensors",
                "command": "curl example.invalid",
            }],
        },
        {"mode": "plan"},
    )
    result = json.loads(output["content"])
    assert result["valid"] is False
    assert any("forbidden field" in error for error in result["errors"])
    assert any("HTTPS" in error for error in result["errors"])
    assert output["attributes"]["comfyui.valid"] == "false"


def test_video_validator_gates_cost_and_accepts_a_local_request(
        monkeypatch, tmp_path):
    _, valid_output = run_task(
        monkeypatch,
        tmp_path,
        "validate",
        {
            "prompt": "A fixed camera shot",
            "duration": 5,
            "width": 1024,
            "height": 576,
            "partner_cost_usd": 1.5,
            "allow_partner_api": True,
            "max_partner_cost_usd": 2,
        },
        {"mode": "video_request"},
    )
    valid = json.loads(valid_output["content"])
    assert valid["prompt"] == "A fixed camera shot"
    assert valid_output["attributes"]["comfyui.valid"] == "true"
    assert valid_output["attributes"]["comfyui.needs_confirmation"] == "true"

    _, blocked_output = run_task(
        monkeypatch,
        tmp_path,
        "validate",
        {
            "prompt": "A fixed camera shot",
            "duration": 5,
            "width": 1024,
            "height": 576,
            "partner_cost_usd": 3,
            "allow_partner_api": True,
            "max_partner_cost_usd": 2,
        },
        {"mode": "video_request"},
    )
    blocked = json.loads(blocked_output["content"])
    assert blocked["valid"] is False
    assert any("exceeds cap" in error for error in blocked["errors"])

    _, disabled_output = run_task(
        monkeypatch,
        tmp_path,
        "validate",
        {
            "prompt": "A fixed camera shot",
            "duration": 5,
            "width": 1024,
            "height": 576,
            "partner_cost_usd": 0.5,
            "allow_partner_api": False,
            "max_partner_cost_usd": 2,
        },
        {"mode": "video_request"},
    )
    disabled = json.loads(disabled_output["content"])
    assert disabled["valid"] is False
    assert "partner API use is disabled" in disabled["errors"]


def test_workflow_validator_accepts_api_graph_and_rejects_ui_export(
        monkeypatch, tmp_path):
    api_graph = {
        "workflow": {
            "1": {
                "class_type": "KSampler",
                "inputs": {"seed": 1},
            }
        },
        "bindings": {
            "seed": {"node": "1", "input": "seed"},
        },
    }
    _, output = run_task(
        monkeypatch, tmp_path, "validate", api_graph, {"mode": "workflow"}
    )
    assert json.loads(output["content"]) == api_graph
    assert output["attributes"]["comfyui.valid"] == "true"

    _, ui_output = run_task(
        monkeypatch,
        tmp_path,
        "validate",
        {"workflow": {"nodes": [], "links": []}},
        {"mode": "workflow"},
    )
    assert json.loads(ui_output["content"])["valid"] is False
