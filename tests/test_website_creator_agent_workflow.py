"""Shipped Website Creator Workflow Agent resource contracts."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import yaml

from core import pfp_package
from core.flow_layout_contracts import validate_flow_presentation
from core.workflow_agent_resources import validate_agent_workflow_definition
from tasks import register_all_tasks


FLOW_PATH = Path(
    "data/repository/flows/global/pawflow/agents/website-creator/"
    "versions/1.0.0.json"
)
LATEST_PATH = FLOW_PATH.parent.parent / "latest.json"
AGENT_PATH = Path("data/repository/agents/global/website-creator.md")
PACKAGE_PATH = Path("packages/pawflow.website-creator.pfpdir")
DOC_PATH = Path("docs/WEBSITE_CREATOR_WORKFLOW_AGENT.md")


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


def test_shipped_website_creator_flow_and_agent_binding_are_valid():
    register_all_tasks()
    flow = _flow()
    report = validate_agent_workflow_definition(flow)
    assert report["ok"] is True, report["problems"]
    assert flow["fqn"] == "pawflow.agents.website-creator:1.0.0"
    assert json.loads(LATEST_PATH.read_text(encoding="utf-8")) == {
        "version": "1.0.0"
    }
    raw = AGENT_PATH.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(raw.split("---", 2)[1])
    workflow = frontmatter["runtime_defaults"]["workflow"]
    assert workflow["flow_fqn"] == flow["fqn"]
    assert workflow["input_port"] == "agent_request"
    assert workflow["terminal_port"] == "agent_terminal"
    assert workflow["parameters"]["creator_llm"] == "summarizer_service"
    assert set(workflow["allowed_effects"]) == set(
        flow["agent_contract"]["allowed_effects"]
    )


def test_website_creator_flow_is_fully_presented_and_colored():
    flow = _flow()
    assert validate_flow_presentation(flow, require_relation_ids=True) == []
    assert flow["default_layout_id"] == "functional"
    layout = flow["layouts"]["functional"]
    assert layout["name"] == "Website Creator functional stages"
    members = [
        task_id
        for frame in layout["frames"].values()
        for task_id in frame["member_ids"]
    ]
    assert len(members) == len(set(members))
    assert set(members) == set(flow["tasks"])
    assert set(layout["nodes"]) == set(flow["tasks"])
    colors = {
        (frame["style"]["fill"], frame["style"]["border"])
        for frame in layout["frames"].values()
    }
    assert len(colors) == len(layout["frames"])
    for frame in layout["frames"].values():
        assert frame["label"]
        assert frame["description"]
    for task in flow["tasks"].values():
        assert task["label"]
        assert task["description"]


def test_website_creator_has_two_durable_gates_and_bounded_review_loop():
    flow = _flow()
    tasks = flow["tasks"]
    assert tasks["request_mapping_approval"]["type"] == "requestUserInput"
    assert tasks["request_mapping_approval"]["parameters"]["payload_attribute"] == (
        "website.mapping_decision"
    )
    assert tasks["wait_mapping_approval"]["type"] == "durableWait"
    assert tasks["request_final_review"]["type"] == "requestUserInput"
    assert tasks["request_final_review"]["parameters"]["payload_attribute"] == (
        "website.review_decision"
    )
    assert tasks["wait_final_review"]["type"] == "durableWait"

    assert "build_site" in _reachable(
        flow, "apply_mapping_decision", "approved"
    )
    assert "agent_terminal" in _reachable(
        flow, "apply_mapping_decision", "rejected"
    )
    assert "correct_site" in _reachable(
        flow, "apply_review_decision", "revise"
    )
    assert "correct_site" in _reachable(
        flow, "prepare_review_decision", "revise"
    )
    assert "agent_terminal" in _reachable(
        flow, "apply_review_decision", "accepted"
    )
    assert "request_final_review" in _reachable(flow, "correct_site", "success")
    correction_edge = next(
        row for row in flow["relations"]
        if row["from"] == "correct_site" and row["to"] == "review_site"
    )
    assert correction_edge["explicit_loop"] is True
    assert "max_visits" not in correction_edge


def test_website_creator_tools_and_workspace_are_explicit():
    flow = _flow()
    explore = flow["tasks"]["explore_sites"]
    build = flow["tasks"]["build_site"]
    correct = flow["tasks"]["correct_site"]
    assert explore["type"] == "websiteCreatorTool"
    assert explore["parameters"]["phase"] == "explore"
    assert explore["parameters"]["required_tools"] == ["screen", "see"]
    assert build["parameters"]["phase"] == "build"
    assert correct["parameters"]["phase"] == "correct"
    for task in flow["tasks"].values():
        if task["type"] == "websiteCreatorTool":
            assert "timeout" not in task["parameters"]
            assert task["parameters"]["max_iterations"] == 0
            assert task["parameters"]["max_tokens"] == 0
    assert flow["agent_contract"]["parameters"]["workspace_root"]["default"] == (
        "/workspace/pawflow-sites"
    )


def test_website_creator_package_manifest_contains_flow_before_agent():
    manifest = json.loads(
        (PACKAGE_PATH / "pfp.json").read_text(encoding="utf-8")
    )
    assert manifest["package"] == "pawflow.website-creator"
    assert [row["id"] for row in manifest["objects"]] == [
        "flow:website-creator", "agent:website-creator"
    ]
    assert manifest["objects"][1]["requires"] == ["flow:website-creator"]
    packaged_flow = json.loads(
        (PACKAGE_PATH / "content/flows/website-creator.json").read_text(
            encoding="utf-8"
        )
    )
    assert packaged_flow == _flow()


def test_website_creator_package_builds_with_bounded_review_cycle(tmp_path):
    package_path = tmp_path / "pawflow.website-creator.pfpdir"
    shutil.copytree(PACKAGE_PATH, package_path)
    keypair = pfp_package.create_signing_key()
    manifest_path = package_path / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["developer"]["public_key"] = keypair["public_key"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    built = pfp_package.build_pfp(
        str(package_path), private_key=keypair["private_key"])
    inspection = pfp_package.inspect_pfp(built["path"], user_id="alice")

    assert built["ok"] is True
    assert all(row["status"] != "blocked" for row in inspection["objects"])
    flow = json.loads(
        (package_path / "content/flows/website-creator.json").read_text(
            encoding="utf-8"))
    report = validate_agent_workflow_definition(flow)
    assert report["ok"] is True, report["problems"]


def test_website_creator_documentation_states_catalogs_and_safe_v1_scope():
    documentation = DOC_PATH.read_text(encoding="utf-8")
    assert "HTML5 UP" in documentation
    assert "Start Bootstrap" in documentation
    assert "ThemeWagon" in documentation
    assert "static HTML/CSS/JavaScript" in documentation
    assert "do not expose a shell" in documentation
    assert "No implicit pass count, timeout, or deadline" in documentation
    assert "No relay service name is hard-coded" in documentation
