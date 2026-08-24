"""WP8 reference Wiki Agent workflow and deterministic task contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import yaml

from core import FlowFile
from core.workflow_agent_contracts import AgentWorkflowResult
from core.workflow_agent_resources import validate_agent_workflow_definition
from tasks import register_all_tasks
from tasks.ai.workflow.wiki_tasks import (
    FormatWikiWorkReportTask,
    MergeWikiExtractionsTask,
    PrepareWikiIntentTask,
    PrepareWikiReviewTask,
    RouteWikiIntentTask,
    SelectWikiSourceBatchTask,
    SplitWikiSourceBatchesTask,
    ValidateWikiPatchTask,
    ValidateWikiReviewTask,
    WIKI_EXTRACTION_SCHEMA,
    WIKI_INTENT_SCHEMA,
    WIKI_PATCH_SCHEMA,
    WIKI_REVIEW_SCHEMA,
)


FLOW_PATH = Path(
    "data/repository/flows/global/pawflow/agents/wiki/versions/1.0.0.json")
AGENT_PATH = Path("data/repository/agents/global/wiki.md")


def _flow() -> dict:
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


def _reachable(flow: dict, source: str, relationship: str) -> set[str]:
    relations = flow["relations"]
    pending = [row["to"] for row in relations
               if row["from"] == source and row["type"] == relationship]
    seen = set(pending)
    while pending:
        current = pending.pop()
        for row in relations:
            if row["from"] == current and row["to"] not in seen:
                seen.add(row["to"])
                pending.append(row["to"])
    return seen


def test_shipped_wiki_flow_and_agent_defaults_are_valid():
    register_all_tasks()
    flow = _flow()
    assert validate_agent_workflow_definition(flow)["ok"] is True
    assert flow["fqn"] == "pawflow.agents.wiki:1.0.0"
    assert flow["agent_contract"]["parameters"]["reviewer_llm"]["required"] is False
    for name in ("extractor_llm", "writer_llm", "reviewer_llm"):
        parameter = flow["agent_contract"]["parameters"][name]
        assert parameter["capability"] == "llm_resolvable"
        assert parameter["label"]
        assert parameter["description"]
    for parameter in flow["agent_contract"]["parameters"].values():
        assert parameter["label"]
        assert parameter["description"]
    raw = AGENT_PATH.read_text(encoding="utf-8")
    defaults = yaml.safe_load(raw.split("---", 2)[1])["runtime_defaults"]["workflow"]
    assert defaults["flow_fqn"] == flow["fqn"]
    assert defaults["input_port"] == "agent_request"
    assert defaults["terminal_port"] == "agent_terminal"
    assert flow["agent_contract"]["parameters"]["write_mode"]["default"] == "live"
    assert flow["tasks"]["apply_patch"]["parameters"]["write_mode"] == "${write_mode}"
    assert defaults["parameters"]["write_mode"] == "live"
    assert defaults["parameters"]["extractor_llm"] == "summarizer_service"
    assert defaults["parameters"]["writer_llm"] == "summarizer_service"


def test_intent_gate_precedes_project_access_and_terminal_routes_stop_llm_work():
    flow = _flow()
    llm_nodes = {
        task_id for task_id, task in flow["tasks"].items()
        if task["type"] == "agentLLMCall"
    }
    assert "infer_intent" in llm_nodes
    downstream_llm_nodes = llm_nodes - {"infer_intent"}
    assert {row["to"] for row in flow["relations"]
            if row["from"] == "validate_request"} == {"prepare_intent"}
    assert "scan_sources" in _reachable(flow, "route_intent", "maintenance")
    unsupported = _reachable(flow, "route_intent", "unsupported")
    assert "scan_sources" not in unsupported
    assert not (unsupported & downstream_llm_nodes)
    assert not (_reachable(flow, "select_batch", "no_change") & downstream_llm_nodes)
    assert not (_reachable(flow, "fetch_sources", "superseded") & downstream_llm_nodes)
    assert not (_reachable(flow, "apply_patch", "superseded") & downstream_llm_nodes)


def test_strict_llm_schemas_reject_missing_required_fields_before_project_access():
    flowfile = FlowFile(content=b'{"selection":{"entries":[]}}', attributes={
        "wiki.extraction": "{}", "wiki.patch": "{}", "wiki.review": "{}",
    })
    for task in (
        MergeWikiExtractionsTask({}),
        ValidateWikiPatchTask({}),
        ValidateWikiReviewTask({}),
    ):
        try:
            task.execute(flowfile)
        except jsonschema.ValidationError:
            pass
        else:
            raise AssertionError(f"{type(task).__name__} accepted an invalid schema")
    for schema in (
        WIKI_INTENT_SCHEMA, WIKI_EXTRACTION_SCHEMA,
        WIKI_PATCH_SCHEMA, WIKI_REVIEW_SCHEMA,
    ):
        jsonschema.Draft202012Validator.check_schema(schema)


def test_intent_gate_preserves_request_and_can_only_reduce_batch_size():
    request = {
        "request": {"message": "Analyse un petit lot de docs Wiki."},
        "conversation": {}, "turn": {},
    }
    flowfile = FlowFile(content=json.dumps(request).encode("utf-8"))
    PrepareWikiIntentTask({}).execute(flowfile)
    prompt = flowfile.get_attribute("wiki.intent_prompt")
    assert "Analyse un petit lot de docs Wiki." in prompt

    flowfile.set_attribute("wiki.intent", json.dumps({
        "intent": "wiki_maintenance", "batch_files": 3, "response": "",
    }))
    RouteWikiIntentTask({}).execute(flowfile)
    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == "maintenance"
    assert state["wiki_intent"]["request"] == request["request"]["message"]
    assert SelectWikiSourceBatchTask._batch_limit(state, 8) == 3
    state["wiki_intent"]["batch_files"] = 20
    assert SelectWikiSourceBatchTask._batch_limit(state, 8) == 8

    state["prepared"] = {"files": [], "source_text": ""}
    flowfile.set_content(json.dumps(state).encode("utf-8"))
    SplitWikiSourceBatchesTask({}).execute(flowfile)
    assert request["request"]["message"] in flowfile.get_attribute(
        "wiki.extract_prompt")
    flowfile.set_attribute("wiki.extraction", json.dumps({
        "claims": [], "relationships": [], "decisions": [],
        "invariants": [], "workflows": [], "candidate_pages": [],
    }))
    MergeWikiExtractionsTask({}).execute(flowfile)
    assert request["request"]["message"] in flowfile.get_attribute(
        "wiki.writer_prompt")


def test_unsupported_intent_stops_with_classifier_response():
    request = {
        "request": {"message": "Corrige le CSS de la barre d'onglets."},
        "conversation": {}, "turn": {},
    }
    flowfile = FlowFile(content=json.dumps(request).encode("utf-8"), attributes={
        "wiki.intent": json.dumps({
            "intent": "unsupported", "batch_files": None,
            "response": "Cette demande n'est pas adaptée à l'agent Wiki.",
        }),
    })
    RouteWikiIntentTask({}).execute(flowfile)
    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == "unsupported"
    assert state["result"] == {
        "status": "unsupported",
        "response": "Cette demande n'est pas adaptée à l'agent Wiki.",
    }


def test_patch_processed_sources_are_derived_from_selection_not_the_llm():
    selection = {"entries": [
        {"path": "README.md"}, {"path": "docs/architecture.md"},
    ]}
    flowfile = FlowFile(
        content=json.dumps({"selection": selection}).encode("utf-8"),
        attributes={"wiki.patch": json.dumps({"pages": []})},
    )
    task = ValidateWikiPatchTask({})
    wiki = SimpleNamespace(validate_update_patch=lambda _selection, payload: payload)
    task._project = lambda: ("relay", None, wiki)

    task.execute(flowfile)

    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert state["patch"]["processed_sources"] == [
        "README.md", "docs/architecture.md",
    ]


def test_optional_review_skips_without_service_and_validates_selected_sources():
    state = {
        "selection": {"entries": [{"path": "core/a.py"}]},
        "patch": {"pages": []}, "extraction": {"claims": []},
    }
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    PrepareWikiReviewTask({"reviewer_llm": ""}).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "skip"

    flowfile.set_attribute("wiki.review", json.dumps({
        "issues": [{
            "code": "unclear", "severity": "warning", "message": "Clarify",
            "sources": ["outside.py"],
        }],
        "suggested_corrections": [],
    }))
    try:
        ValidateWikiReviewTask({}).execute(flowfile)
    except ValueError as exc:
        assert "unselected source" in str(exc)
    else:
        raise AssertionError("review accepted an invented source")


def test_work_report_is_derived_from_actual_result_and_absorbed_turns():
    state = {
        "result": {
            "status": "updated", "created": ["architecture"],
            "updated": ["runtime"], "unchanged": ["storage"],
            "cleared": ["core/a.py", "core/b.py"], "remaining": 3,
        },
        "lint_after": {"missing_links": ["x"], "stale_pages": {}},
    }
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"), attributes={
        "wiki.preempt": json.dumps({"messages": [{"msg_id": "m2"}]})})
    task = FormatWikiWorkReportTask({})
    task.set_workflow_run_context(SimpleNamespace(root_turn_id="m1"))

    task.execute(flowfile)

    result = AgentWorkflowResult.from_dict(json.loads(
        flowfile.get_content().decode("utf-8")))
    assert result.answered_turn_ids == ("m1", "m2")
    assert result.response == (
        "Project wiki updated: 1 page(s) created, 1 updated, 1 unchanged; "
        "2 source(s) processed and 3 remaining. "
        "Created: architecture. Updated: runtime. Unchanged: storage. "
        "Lint reports 1 warning item(s).")
