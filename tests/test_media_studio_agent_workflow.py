"""Shipped Media Studio Workflow Agent resource contracts."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from core.flow_layout_contracts import validate_flow_presentation
from core.workflow_agent_resources import validate_agent_workflow_definition
from tasks import register_all_tasks


FLOW_PATH = Path(
    "data/repository/flows/global/pawflow/agents/media-studio/"
    "versions/1.0.0.json"
)
LATEST_PATH = FLOW_PATH.parent.parent / "latest.json"
AGENT_PATH = Path("data/repository/agents/global/media-studio.md")


def _flow() -> dict:
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


def _reachable(flow: dict, source: str, relationship: str) -> set[str]:
    pending = [
        row["to"] for row in flow["relations"]
        if row["from"] == source and row["type"] == relationship
    ]
    seen = set(pending)
    while pending:
        current = pending.pop()
        for row in flow["relations"]:
            if row["from"] == current and row["to"] not in seen:
                seen.add(row["to"])
                pending.append(row["to"])
    return seen


def test_shipped_media_studio_flow_and_agent_binding_are_valid():
    register_all_tasks()
    flow = _flow()
    report = validate_agent_workflow_definition(flow)
    assert report["ok"] is True, report["problems"]
    assert flow["fqn"] == "pawflow.agents.media-studio:1.0.0"
    assert json.loads(LATEST_PATH.read_text(encoding="utf-8")) == {
        "version": "1.0.0",
    }
    raw = AGENT_PATH.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(raw.split("---", 2)[1])
    workflow = frontmatter["runtime_defaults"]["workflow"]
    assert workflow["flow_fqn"] == flow["fqn"]
    assert workflow["input_port"] == "agent_request"
    assert workflow["terminal_port"] == "agent_terminal"
    assert workflow["parameters"]["creative_llm"] == "summarizer_service"
    assert set(workflow["allowed_effects"]) == set(
        flow["agent_contract"]["allowed_effects"]
    )


def test_media_studio_flow_has_complete_colored_functional_presentation():
    flow = _flow()
    assert validate_flow_presentation(flow, require_relation_ids=True) == []
    assert flow["default_layout_id"] == "functional"
    layout = flow["layouts"]["functional"]
    assert layout["name"] == "Media Studio functional stages"
    frames = layout["frames"]
    assert len(frames) == 9
    members = [
        task_id
        for frame in frames.values()
        for task_id in frame["member_ids"]
    ]
    assert len(members) == len(set(members))
    assert set(members) == set(flow["tasks"])
    assert set(layout["nodes"]) == set(flow["tasks"])
    colors = {
        (frame["style"]["fill"], frame["style"]["border"])
        for frame in frames.values()
    }
    assert len(colors) == len(frames)
    for frame in frames.values():
        assert frame["label"]
        assert frame["description"]
    for task in flow["tasks"].values():
        assert task["label"]
        assert task["description"]


def test_media_studio_durable_branches_and_fail_closed_routes_are_wired():
    flow = _flow()
    tasks = flow["tasks"]
    assert tasks["request_details"]["type"] == "requestUserInput"
    assert tasks["request_details"]["parameters"]["payload_attribute"] == (
        "media.question"
    )
    assert tasks["wait_details"]["parameters"]["signal_id_attribute"] == (
        "interaction.signal_id"
    )
    assert tasks["request_scenario_approval"]["type"] == "requestConfirmation"
    assert tasks["wait_scenario_approval"]["type"] == "durableWait"
    assert tasks["request_voice_consent"]["type"] == "requestConfirmation"
    assert tasks["wait_voice_consent"]["type"] == "durableWait"
    assert tasks["request_engine_choice"]["parameters"]["payload_attribute"] == (
        "media.capability_question"
    )
    assert "options" not in tasks["request_engine_choice"]["parameters"]

    unsupported = _reachable(flow, "route_intent", "unsupported")
    assert "load_project" not in unsupported
    assert "snapshot_capabilities" not in unsupported
    assert "agent_terminal" in unsupported

    for source, relationship in (
        ("prepare_questions", "ask"),
        ("prepare_scenario", "scenario"),
        ("select_capability", "choice"),
        ("prepare_voice_consent", "ask"),
    ):
        reachable = _reachable(flow, source, relationship)
        assert any(tasks[node]["type"] == "durableWait" for node in reachable)
        assert "agent_terminal" in reachable

    assert "submit_generation" in _reachable(
        flow, "prepare_execution", "generate"
    )
    assert "compose_media" in _reachable(
        flow, "prepare_execution", "compose"
    )


def test_media_studio_llm_outputs_are_strictly_schema_validated():
    flow = _flow()
    intent_schema = flow["tasks"]["infer_intent"]["parameters"]["json_schema"]
    assert "relay_references" in intent_schema["required"]
    assert intent_schema["properties"]["relay_references"]["items"][
        "additionalProperties"] is False
    expected = {
        "infer_intent": "media.intent",
        "infer_brief": "media.brief",
        "infer_scenario": "media.scenario",
        "infer_ffmpeg_recipe": "media.ffmpeg_recipe",
    }
    for task_id, output_attribute in expected.items():
        parameters = flow["tasks"][task_id]["parameters"]
        assert parameters["response_format"] == "json_schema"
        assert parameters["output_target"] == "attribute"
        assert parameters["output_attribute"] == output_attribute
        assert parameters["json_schema"]["type"] == "object"
        assert parameters["json_schema"]["additionalProperties"] is False
