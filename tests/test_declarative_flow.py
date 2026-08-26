import copy

import pytest

from core import FlowFile
from core.declarative_flow.macros import lower_control_block
from core.declarative_flow.operations import apply_operation
from core.declarative_flow.projection import project_definition
from core.declarative_flow.registry import DeclarativeBlockRegistry
from core.declarative_flow.validation import find_cycle
from core.flow_authoring import DraftConflict, FlowAuthoringService
from core.flow_layout_contracts import migrate_legacy_presentation
from engine import FlowParser
from engine.checkpoint import CheckpointManager
from engine.continuous_executor import ContinuousFlowExecutor
from tasks import register_all_tasks


@pytest.fixture(autouse=True)
def registered_tasks():
    register_all_tasks()


def _definition():
    return migrate_legacy_presentation({
        "name": "declarative",
        "tasks": {
            "input": {"type": "generateFlowFile", "parameters": {
                "content": "hello", "api_key": "do-not-project",
            }},
            "output": {"type": "log", "parameters": {}},
        },
        "services": {}, "groups": {},
        "relations": [{"from": "input", "to": "output", "type": "success"}],
        "entries": ["input"], "exits": ["output"], "layout": {"nodes": {}},
    })


def _agent_ref():
    return {
        "schema_version": 1,
        "resource_type": "agent",
        "name": "Reviewer",
        "scope": "conversation",
        "owner_id": "conversation-1",
        "version": "1.0.0",
        "content_digest": "a" * 64,
        "source_id": "repository:conversation:Reviewer:1.0.0",
    }


def test_catalog_covers_every_registered_task():
    from core import TaskFactory
    catalog = DeclarativeBlockRegistry.catalog()
    assert {row["task_type"] for row in catalog} == set(TaskFactory.list_types())
    assert all(row["version"] == 1 for row in catalog)


def test_projection_is_canonical_and_redacts_sensitive_config():
    definition = _definition()
    projection = project_definition(definition)
    assert [block["block_id"] for block in projection["blocks"]] == [
        "input", "output"]
    assert projection["blocks"][0]["config"]["api_key"] == "[REDACTED]"
    assert projection["relations"][0]["relation_id"] == (
        definition["relations"][0]["relation_id"])
    assert definition["tasks"]["input"]["parameters"]["api_key"] == (
        "do-not-project")


def test_projection_resolves_flow_defaults_and_keeps_validation_criteria():
    definition = _definition()
    definition["executor_profiles"] = {
        "writer": {"id": "writer", "kind": "llm", "model": "m"},
        "reviewer": {"id": "reviewer", "kind": "workflow_agent"},
    }
    definition["executor_defaults"] = {
        "primary": "writer", "reviewer": "reviewer"}
    definition["tasks"]["input"]["execution"] = {
        "strategy": "primary_then_review",
        "roles": {
            "primary": {"executor_profile": "inherited:primary"},
            "reviewer": {"executor_profile": "inherited:reviewer"},
        },
        "validation_criteria": [{
            "id": "correct", "kind": "semantic",
            "description": "The result is correct", "required": True,
        }],
    }
    block = project_definition(definition)["blocks"][0]
    assert block["execution"]["effective_roles"]["primary"] == {
        "profile_id": "writer",
        "source": "executor_defaults.primary",
        "kind": "llm",
        "model": "m",
        "service_ref": "",
        "agent_ref": None,
    }
    assert block["execution"]["validation_criteria"][0]["id"] == "correct"


def test_add_connect_update_and_remove_processors():
    definition = _definition()
    added, change = apply_operation(definition, {
        "version": 1, "op": "add_processor", "block_id": "route",
        "block_type": "route", "config": {"route_a": "${x}"},
        "layout_id": "technical", "position": {"x": 20, "y": 30},
    })
    assert change["changed_entity_ids"] == ["route"]
    assert added["tasks"]["route"]["type"] == "routeOnAttribute"
    connected, change = apply_operation(added, {
        "version": 1, "op": "connect_blocks", "from": "input",
        "to": "route", "output": "success",
    })
    relation_id = change["changed_entity_ids"][0]
    assert relation_id.startswith("rel_")
    updated, _ = apply_operation(connected, {
        "version": 1, "op": "update_processor", "block_id": "route",
        "config": {"route_b": "${y}"},
    })
    assert updated["tasks"]["route"]["parameters"] == {"route_b": "${y}"}
    removed, _ = apply_operation(updated, {
        "version": 1, "op": "remove_block", "block_id": "route",
    })
    assert "route" not in removed["tasks"]
    assert all(
        relation.get("to") != "route" for relation in removed["relations"])


def test_executor_profile_cannot_be_removed_while_referenced():
    definition, _ = apply_operation(_definition(), {
        "version": 1, "op": "set_executor_profile",
        "profile_id": "writer", "profile": {
            "kind": "llm", "service_ref": "llm", "model": "m",
            "context_policy": "block_input_only",
        },
    })
    definition, _ = apply_operation(definition, {
        "version": 1, "op": "set_block_execution", "block_id": "input",
        "execution": {
            "strategy": "single",
            "roles": {"primary": {"executor_profile": "writer"}},
        },
    })
    with pytest.raises(ValueError, match="still referenced"):
        apply_operation(definition, {
            "version": 1, "op": "remove_executor_profile",
            "profile_id": "writer",
        })


def test_authoring_operation_is_revision_locked(tmp_path):
    service = FlowAuthoringService(drafts_dir=tmp_path / "drafts")
    service._write_draft({
        "draft_id": "d_123456789abc", "user_id": "alice",
        "flow": "default.test", "scope": "user", "conv_id": "",
        "base_version": "", "revision": 4, "created_at": 1,
        "updated_at": 1, "definition": copy.deepcopy(_definition()),
    })
    operation = {
        "version": 1, "op": "update_processor", "block_id": "output",
        "config": {"message": "done"},
    }
    preview = service.apply_declarative_operation(
        "d_123456789abc", "alice", operation, 4, preview=True)
    assert preview["revision"] == 4
    assert service.load_draft(
        "d_123456789abc", "alice")["revision"] == 4
    saved = service.apply_declarative_operation(
        "d_123456789abc", "alice", operation, 4)
    assert saved["revision"] == 5
    with pytest.raises(DraftConflict):
        service.apply_declarative_operation(
            "d_123456789abc", "alice", operation, 4)


def test_if_macro_has_deterministic_ports_and_flattens_to_normal_tasks():
    group = lower_control_block("decision", "if", {
        "condition": {"attribute": "approved", "operator": "equals", "value": "yes"},
    })
    assert group["input_ports"] == ["decision.in.input"]
    assert group["output_ports"] == ["decision.out.true", "decision.out.false"]
    assert group["tasks"]["decision.route"]["type"] == "routeOnAttribute"
    assert all(relation.get("relation_id") for relation in group["relations"])

    definition = _definition()
    definition.update(tasks={}, relations=[], entries=[], exits=[], groups={"decision": group})
    flow = FlowParser.parse(definition)
    assert set(flow.tasks) == {
        "decision.in.input", "decision.route",
        "decision.out.true", "decision.out.false",
    }


def test_workflow_agent_macro_is_an_exact_drillable_process_group():
    group = lower_control_block("review", "workflow_agent", {
        "agent_ref": _agent_ref(), "message": "${content}",
    })
    assert group["tasks"]["review.invoke"]["type"] == "invokeWorkflowAgent"
    assert group["input_ports"] == ["review.in.input"]
    assert group["output_ports"] == [
        f"review.out.{name}" for name in (
            "submitted", "completed", "no_change", "failed", "cancelled",
            "timed_out", "superseded", "budget_exceeded", "force_stopped",
            "failure",
        )
    ]
    definition = _definition()
    definition.update(tasks={}, relations=[], entries=[], exits=[], groups={
        "review": group})
    block = project_definition(definition)["blocks"][0]
    assert block["descriptor"]["shape"] == "composite"
    assert block["descriptor"]["category"] == "Agents and LLM"
    assert "review.invoke" in block["canonical_task_ids"]
    assert block["execution"]["effective_roles"]["primary"]["kind"] == (
        "workflow_agent")


def test_workflow_agent_macro_resolves_only_workflow_agent_profiles():
    definition = _definition()
    definition["executor_profiles"] = {
        "reviewer": {
            "id": "reviewer", "kind": "workflow_agent",
            "agent_ref": _agent_ref(),
            "limits": {"max_duration_seconds": 321},
        },
        "generic": {
            "id": "generic", "kind": "agent", "agent_ref": _agent_ref(),
        },
    }
    added, _change = apply_operation(definition, {
        "version": 1, "op": "add_control_block", "block_id": "review",
        "control_type": "workflow_agent", "config": {
            "executor_profile": "reviewer", "message": "Review this",
        },
    })
    group = added["groups"]["review"]
    parameters = group["tasks"]["review.invoke"]["parameters"]
    assert parameters["agent_ref"] == _agent_ref()
    assert parameters["terminal_timeout"] == "321s"
    projected = project_definition(added)["blocks"]
    block = next(item for item in projected if item["block_id"] == "review")
    assert block["execution"]["effective_roles"]["primary"]["profile_id"] == (
        "reviewer")

    with pytest.raises(ValueError, match="unavailable before WP9"):
        apply_operation(definition, {
            "version": 1, "op": "add_control_block", "block_id": "generic",
            "control_type": "workflow_agent", "config": {
                "executor_profile": "generic", "message": "Review this",
            },
        })


def test_workflow_agent_block_cannot_be_lowered_as_execute_flow():
    workflow = DeclarativeBlockRegistry.by_block_type("workflow_agent")
    subflow = DeclarativeBlockRegistry.by_block_type("subflow")
    assert workflow["task_type"] == "invokeWorkflowAgent"
    assert subflow["task_type"] == "executeFlow"
    with pytest.raises(ValueError, match="exact agent_ref"):
        lower_control_block("review", "workflow_agent", {
            "message": "Review this"})


def test_add_and_connect_parallel_join_as_semantic_blocks():
    definition, _ = apply_operation(_definition(), {
        "version": 1, "op": "add_control_block", "block_id": "fanout",
        "control_type": "parallel", "config": {"branches": ["left", "right"]},
    })
    definition, _ = apply_operation(definition, {
        "version": 1, "op": "add_control_block", "block_id": "gather",
        "control_type": "join", "config": {"branches": ["left", "right"]},
    })
    definition, _ = apply_operation(definition, {
        "version": 1, "op": "connect_blocks", "from": "input",
        "to": "fanout", "output": "success",
    })
    definition, _ = apply_operation(definition, {
        "version": 1, "op": "connect_blocks", "from": "fanout",
        "to": "gather", "output": "left", "input": "left",
    })
    projection = project_definition(definition)
    composites = {
        block["block_id"]: block for block in projection["blocks"]
        if block["descriptor"]["shape"] == "composite"
    }
    assert set(composites) == {"fanout", "gather"}
    assert composites["fanout"]["descriptor"]["outputs"] == ["left", "right"]
    assert projection["relations"][-1] == {
        "relation_id": definition["relations"][-1]["relation_id"],
        "from": "fanout", "to": "gather", "output": "left", "input": "left",
    }


def test_control_macro_rejects_unbounded_or_ambiguous_shapes():
    with pytest.raises(ValueError, match="2..64"):
        lower_control_block("fanout", "parallel", {"branches": ["only"]})
    with pytest.raises(ValueError, match="default must not duplicate"):
        lower_control_block("choice", "switch", {
            "cases": {"fallback": {"attribute": "x", "operator": "equals"}},
            "default": "fallback",
        })
    with pytest.raises(ValueError, match="fail-closed route task extension"):
        lower_control_block("decision", "if", {
            "condition": {}, "evaluation_failure": True,
        })


def test_declarative_connection_refuses_raw_back_edge():
    definition = _definition()
    with pytest.raises(ValueError, match="must remain acyclic"):
        apply_operation(definition, {
            "version": 1, "op": "connect_blocks", "from": "output",
            "to": "input", "output": "success",
        })


def test_parallel_join_lowering_executes_as_one_correlated_wave():
    definition = migrate_legacy_presentation({
        "id": "parallel_join", "name": "Parallel Join", "version": "1.0.0",
        "tasks": {
            "start": {"type": "inputPort", "parameters": {}},
            "left": {"type": "log", "parameters": {"message": "left"}},
            "right": {"type": "log", "parameters": {"message": "right"}},
            "done": {"type": "outputPort", "parameters": {}},
        },
        "services": {}, "groups": {}, "relations": [],
        "entries": ["start"], "exits": ["done"], "layout": {"nodes": {}},
    })
    for block_id, control_type in (("fanout", "parallel"), ("gather", "join")):
        definition, _ = apply_operation(definition, {
            "version": 1, "op": "add_control_block", "block_id": block_id,
            "control_type": control_type,
            "config": {"branches": ["left", "right"]},
        })
    for source, target, output, input_name in (
        ("start", "fanout", "success", "input"),
        ("fanout", "left", "left", "input"),
        ("fanout", "right", "right", "input"),
        ("left", "gather", "success", "left"),
        ("right", "gather", "success", "right"),
        ("gather", "done", "success", "input"),
    ):
        definition, _ = apply_operation(definition, {
            "version": 1, "op": "connect_blocks", "from": source,
            "to": target, "output": output, "input": input_name,
        })

    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse(definition),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=4, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert len(execution.output_flowfiles) == 1
    assert execution.output_flowfiles[0].get_content() == b"payload\npayload"
    assert execution.output_flowfiles[0].get_attribute("merge.count") == "2"


def test_merge_content_restores_a_partial_bin_from_checkpoint(tmp_path):
    flow_definition = {
        "id": "durable_join", "name": "Durable Join", "version": "1.0.0",
        "tasks": {"merge": {"type": "mergeContent", "parameters": {
            "min_entries": 99, "correlation_attribute": "wave",
            "expected_count_attribute": "expected",
        }}},
        "relations": [], "entries": ["merge"], "exits": ["merge"],
    }
    first = ContinuousFlowExecutor(
        FlowParser.parse(flow_definition), enable_checkpoints=False)
    first._checkpoint_mgr = CheckpointManager(
        "durable_join", checkpoint_dir=str(tmp_path))
    buffered = FlowFile(content=b"first", attributes={
        "wave": "w1", "expected": "2"})
    assert first.get_task("merge").execute(buffered) == []
    assert first.save_checkpoint_now()

    recovered = ContinuousFlowExecutor(
        FlowParser.parse(flow_definition), enable_checkpoints=False)
    recovered._checkpoint_mgr = CheckpointManager(
        "durable_join", checkpoint_dir=str(tmp_path))
    recovered._recover_from_checkpoint()
    outputs = recovered.get_task("merge").execute(
        FlowFile(content=b"second", attributes={
            "wave": "w1", "expected": "2"}))
    assert len(outputs) == 1
    assert outputs[0].get_content() == b"first\nsecond"
    assert outputs[0].get_attribute("merge.count") == "2"


def test_repeat_n_unrolls_a_bounded_body_with_stable_forward_ids():
    group = lower_control_block("repeat", "repeat_n", {
        "times": 3,
        "body": {
            "tasks": {
                "work": {"type": "log", "parameters": {"message": "work"}},
            },
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    assert {
        task_id for task_id in group["tasks"] if ".iteration_" in task_id
    } == {
        "repeat.iteration_1.work",
        "repeat.iteration_2.work",
        "repeat.iteration_3.work",
    }
    assert all(
        relation["from"] != relation["to"] for relation in group["relations"])
    flow = FlowParser.parse({
        "id": "repeat", "name": "Repeat", "version": "1.0.0",
        "tasks": {}, "groups": {"repeat": group}, "relations": [],
        "entries": ["repeat.in.input"], "exits": ["repeat.out.success"],
    })
    execution = ContinuousFlowExecutor.run_batch(
        flow, input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert len(execution.output_flowfiles) == 1


def test_repeat_n_rejects_large_or_cyclic_unrolling():
    with pytest.raises(ValueError, match="1..8"):
        lower_control_block("repeat", "repeat_n", {
            "times": 9,
            "body": {"tasks": {"work": {"type": "log"}},
                     "relations": [], "entries": ["work"], "exits": ["work"]},
        })


def test_try_catch_routes_failure_through_an_explicit_catch_body():
    group = lower_control_block("guard", "try_catch", {
        "try": {
            "tasks": {"danger": {"type": "fail", "parameters": {
                "message": "boom"}}},
            "relations": [], "entries": ["danger"], "exits": ["danger"],
        },
        "catch": {
            "tasks": {"handle": {"type": "log", "parameters": {
                "message": "handled"}}},
            "relations": [], "entries": ["handle"], "exits": ["handle"],
        },
    })
    assert group["output_ports"] == [
        "guard.out.success", "guard.out.caught", "guard.out.failure"]
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "guard", "name": "Guard", "version": "1.0.0",
            "tasks": {}, "groups": {"guard": group}, "relations": [],
            "entries": ["guard.in.input"], "exits": ["guard.out.caught"],
        }),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert len(execution.output_flowfiles) == 1
    assert execution.output_flowfiles[0].get_attribute("error.message") == "boom"
    assert execution.output_flowfiles[0].get_attribute("error.task") == (
        "guard.try.danger")


def test_try_catch_refuses_hidden_failure_edges_inside_a_body():
    with pytest.raises(ValueError, match="boundary owns them"):
        lower_control_block("guard", "try_catch", {
            "try": {
                "tasks": {"a": {"type": "log"}, "b": {"type": "log"}},
                "relations": [{"from": "a", "to": "b", "type": "failure"}],
                "entries": ["a"], "exits": ["b"],
            },
            "catch": {
                "tasks": {"handle": {"type": "log"}},
                "relations": [], "entries": ["handle"], "exits": ["handle"],
            },
        })


@pytest.mark.parametrize(
    ("control_type", "config", "parameter"),
    [
        ("wait_duration", {"duration": "5m"}, "duration"),
        ("wait_until", {"until": "2030-01-01T00:00:00Z"}, "until"),
    ],
)
def test_durable_timer_macros_have_stable_typed_ports(
    control_type, config, parameter,
):
    group = lower_control_block("pause", control_type, config)
    assert group["input_ports"] == ["pause.in.input"]
    assert group["output_ports"] == [
        "pause.out.elapsed", "pause.out.cancelled", "pause.out.failure"]
    assert group["tasks"]["pause.timer"] == {
        "type": "durableTimer", "parameters": {parameter: config[parameter]}}
    relationships = {relation["type"] for relation in group["relations"]}
    assert {"elapsed", "cancelled", "failure"} <= relationships


@pytest.mark.parametrize(
    ("control_type", "config", "outputs"),
    [
        ("ask_user", {"message": "Name?", "kind": "text"},
         ["answered", "timeout", "cancelled", "failure"]),
        ("confirm", {"message": "Deploy?"},
         ["yes", "no", "timeout", "cancelled", "failure"]),
    ],
)
def test_interaction_macros_lower_to_request_wait_and_typed_route(
    control_type, config, outputs,
):
    group = lower_control_block("human", control_type, config)
    assert group["output_ports"] == [f"human.out.{name}" for name in outputs]
    assert group["tasks"]["human.request"]["type"] == "requestUserInput"
    assert group["tasks"]["human.wait"]["parameters"][
        "signal_id_attribute"] == "interaction.signal_id"
    assert group["tasks"]["human.route"]["type"] == "routeOnAttribute"
    assert not find_cycle(group["tasks"], group["relations"])


def test_wait_event_macro_has_durable_typed_outputs():
    group = lower_control_block("event", "wait_event", {
        "signal_id": "deploy:ready", "timeout": "2h"})
    assert group["output_ports"] == [
        "event.out.signaled", "event.out.timeout", "event.out.cancelled",
        "event.out.failure",
    ]
    assert group["tasks"]["event.wait"] == {
        "type": "durableWait",
        "parameters": {"signal_id": "deploy:ready", "timeout": "2h"},
    }


def test_retry_lowers_to_forward_attempts_with_durable_backoff():
    group = lower_control_block("retry", "retry", {
        "max_attempts": 3,
        "backoff": "5m",
        "body": {
            "tasks": {"work": {"type": "inputPort", "parameters": {}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    assert group["output_ports"] == [
        "retry.out.success", "retry.out.exhausted", "retry.out.cancelled",
        "retry.out.failure",
    ]
    assert group["tasks"]["retry.backoff_1"] == {
        "type": "durableTimer", "parameters": {"duration": "5m"}}
    assert group["tasks"]["retry.attempt_3.marker"]["parameters"]["set"] == {
        "retry.attempt": "3", "retry.max_attempts": "3"}
    assert not find_cycle(group["tasks"], group["relations"])


def test_retry_rejects_unsafe_effects_without_explicit_review():
    config = {
        "max_attempts": 2, "backoff": 0,
        "body": {
            "tasks": {"work": {"type": "fail", "parameters": {
                "message": "boom"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    }
    with pytest.raises(ValueError, match="not safely idempotent"):
        lower_control_block("retry", "retry", config)
    config["idempotency_policy"] = "reviewed"
    group = lower_control_block("retry", "retry", config)
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "retry", "name": "Retry", "version": "1.0.0",
            "tasks": {}, "groups": {"retry": group}, "relations": [],
            "entries": ["retry.in.input"], "exits": ["retry.out.exhausted"],
        }),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_attribute("retry.attempt") == "2"
    assert execution.output_flowfiles[0].get_attribute("error.task") == (
        "retry.attempt_2.work")


def test_for_each_executes_bounded_collection_and_merges_in_order():
    group = lower_control_block("each", "for_each", {
        "collection_path": "$", "max_iterations": 3, "max_flowfiles": 3,
        "max_duration_seconds": 30, "max_accumulated_bytes": 1024,
        "accumulation": "merge", "separator": ",",
        "body": {
            "tasks": {"work": {"type": "log", "parameters": {
                "message": "item"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    assert group["output_ports"] == [
        "each.out.success", "each.out.empty", "each.out.exhausted",
        "each.out.cancelled", "each.out.failure",
    ]
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "each", "name": "For Each", "version": "1.0.0",
            "tasks": {}, "groups": {"each": group}, "relations": [],
            "entries": ["each.in.input"], "exits": ["each.out.success"],
        }),
        input_flowfiles=[FlowFile(content=b"[1,2,3]")],
        max_workers=3, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_content() == b"1,2,3"
    assert execution.output_flowfiles[0].get_attribute("merge.count") == "3"


def test_for_each_routes_empty_and_rejects_unbounded_config():
    config = {
        "collection_path": "$", "max_iterations": 2, "max_flowfiles": 2,
        "max_duration_seconds": 30, "max_accumulated_bytes": 1024,
        "accumulation": "merge",
        "body": {
            "tasks": {"work": {"type": "log", "parameters": {
                "message": "item"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    }
    group = lower_control_block("each", "for_each", config)
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "empty", "name": "Empty", "version": "1.0.0",
            "tasks": {}, "groups": {"each": group}, "relations": [],
            "entries": ["each.in.input"], "exits": ["each.out.empty"],
        }),
        input_flowfiles=[FlowFile(content=b"[]")], max_retries=1, timeout=5,
    )
    assert execution.success and execution.output_flowfiles
    with pytest.raises(ValueError, match="must equal"):
        lower_control_block("bad", "for_each", {**config, "max_flowfiles": 3})


def test_for_each_omitted_limits_are_unlimited():
    group = lower_control_block("each", "for_each", {
        "collection_path": "$", "accumulation": "merge", "separator": ",",
        "body": {
            "tasks": {"work": {"type": "log", "parameters": {
                "message": "item"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    assert group["tasks"]["each.split"]["parameters"]["max_fragments"] == 0
    assert group["tasks"]["each.guard"]["parameters"]["max_flowfiles"] == 0
    assert group["tasks"]["each.merge"]["parameters"]["max_bin_bytes"] == 0

    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "each-unlimited", "name": "Each Unlimited",
            "version": "1.0.0", "tasks": {}, "groups": {"each": group},
            "relations": [], "entries": ["each.in.input"],
            "exits": ["each.out.success"],
        }),
        input_flowfiles=[FlowFile(content=b'[1,2,3]')],
        max_workers=3, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_content() == b'1,2,3'


def test_repeat_until_controller_has_no_graph_cycle_and_exhausts():
    group = lower_control_block("until", "repeat_until", {
        "max_iterations": 1, "max_duration_seconds": 30,
        "condition": {"attribute": "done", "operator": "equals", "value": "yes"},
        "idempotency_policy": "reviewed",
        "body": {
            "tasks": {"work": {"type": "log", "parameters": {
                "message": "iteration"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    assert not find_cycle(group["tasks"], group["relations"])
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "until", "name": "Until", "version": "1.0.0",
            "tasks": {}, "groups": {"until": group}, "relations": [],
            "entries": ["until.in.input"], "exits": ["until.out.exhausted"],
        }),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_attribute(
        "repeat.until.iteration") == "1"


def test_repeat_until_stops_when_child_satisfies_condition():
    group = lower_control_block("until", "repeat_until", {
        "max_iterations": 3, "max_duration_seconds": 30,
        "condition": {"attribute": "done", "operator": "equals", "value": "yes"},
        "idempotency_policy": "reviewed",
        "body": {
            "tasks": {"work": {"type": "updateAttribute", "parameters": {
                "set": {"done": "yes"}}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "until", "name": "Until", "version": "1.0.0",
            "tasks": {}, "groups": {"until": group}, "relations": [],
            "entries": ["until.in.input"], "exits": ["until.out.success"],
        }),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, max_retries=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_attribute("done") == "yes"


def test_repeat_until_omitted_bounds_are_unlimited():
    group = lower_control_block("until", "repeat_until", {
        "condition": {"attribute": "done", "operator": "equals", "value": "yes"},
        "idempotency_policy": "reviewed",
        "body": {
            "tasks": {"work": {"type": "updateAttribute", "parameters": {
                "set": {"done": "yes"}}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })
    controller = group["tasks"]["until.controller"]["parameters"]
    assert controller["max_iterations"] == 0
    assert controller["max_duration_seconds"] == 0
    assert controller["iteration_timeout_seconds"] == 0

    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse({
            "id": "until-unlimited", "name": "Until Unlimited",
            "version": "1.0.0", "tasks": {}, "groups": {"until": group},
            "relations": [], "entries": ["until.in.input"],
            "exits": ["until.out.success"],
        }),
        input_flowfiles=[FlowFile(content=b"payload")],
        max_workers=1, timeout=5,
    )
    assert execution.success, execution.errors
    assert execution.output_flowfiles[0].get_attribute("done") == "yes"


def test_repeat_until_queues_exactly_one_checkpointable_next_iteration(monkeypatch):
    from core.executor_registry import ExecutorRegistry
    from tasks.control.repeat_until import RepeatUntilTask

    task = RepeatUntilTask({
        "max_iterations": 2, "max_duration_seconds": 30,
        "condition": {"attribute": "done", "operator": "equals", "value": "yes"},
        "body": {
            "tasks": {"work": {"type": "log", "parameters": {
                "message": "iteration"}}},
            "relations": [], "entries": ["work"], "exits": ["work"],
        },
    })

    class _Executor:
        is_running = True
        _tasks = {"controller": task}

        def __init__(self):
            self.injected = []

        def inject(self, flowfile, entry_task_id=None):
            self.injected.append((flowfile, entry_task_id))
            return True

    executor = _Executor()

    class _Registry:
        _executors = {"instance": executor}

        def get(self, instance_id):
            return self._executors.get(instance_id)

    monkeypatch.setattr(
        ExecutorRegistry, "get_instance", classmethod(lambda cls: _Registry()))
    assert task.execute(FlowFile(content=b"payload")) == []
    assert len(executor.injected) == 1
    queued, entry = executor.injected[0]
    assert entry == "controller"
    assert queued.get_attribute("repeat.until.iteration") == "1"
    exhausted = task.execute(queued)
    assert exhausted == [queued]
    assert queued.get_attribute("repeat.until.iteration") == "2"
    assert queued.get_attribute("route.relationship") == "exhausted"
    with pytest.raises(ValueError, match="body must be acyclic"):
        lower_control_block("repeat", "repeat_n", {
            "times": 2,
            "body": {
                "tasks": {"a": {"type": "log"}, "b": {"type": "log"}},
                "relations": [
                    {"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
                "entries": ["a"], "exits": ["b"],
            },
        })
