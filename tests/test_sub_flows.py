"""Sub-flow execution via executeFlow + ProcessGroup.flow_ref."""

import json
import tempfile
from pathlib import Path

import pytest
from tasks import register_all_tasks

register_all_tasks()

from core import TaskFactory, TaskError  # noqa: E402
from engine import FlowParser
from engine.continuous_executor import ContinuousFlowExecutor
from core import FlowFile


def _write_flow(tmp: Path, name: str, config: dict) -> str:
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    return str(p)


def test_execute_flow_registered():
    """executeFlow must be back in the task factory."""
    cls = TaskFactory.get("executeFlow")
    assert cls.__name__ == "ExecuteFlowTask"


def test_process_group_with_flow_ref_synthesizes_executeFlow(tmp_path):
    """Parser: a ProcessGroup with flow_ref gets a runtime executeFlow task."""
    child_flow = _write_flow(tmp_path, "child", {
        "id": "child",
        "name": "Child",
        "version": "1.0.0",
        "tasks": {
            "in":  {"type": "inputPort",  "parameters": {"port_name": "main"}},
            "out": {"type": "outputPort", "parameters": {"port_name": "done"}},
        },
        "relations": [{"from": "in", "to": "out", "type": "success"}],
    })

    parent_cfg = {
        "id": "parent",
        "name": "Parent",
        "version": "1.0.0",
        "tasks": {},
        "groups": {
            "pg1": {
                "name": "Wrapped child",
                "flow_ref": {"path": child_flow, "version": "1.0.0"},
                "parameter_mapping": {},
                "port_mapping": {
                    "input":  {"port_task_id": "in"},
                    "output": {"out": "success"},
                },
            },
        },
        "relations": [],
    }
    flow = FlowParser.parse(parent_cfg)
    assert "pg1" in flow.tasks, (
        "ProcessGroup with flow_ref should be exposed as an executeFlow "
        "task named after the group id")
    assert flow.tasks["pg1"].__class__.__name__ == "ExecuteFlowTask"


def test_execute_flow_recursion_guard(tmp_path):
    """A sub-flow that references itself must abort with 'recursion detected'.

    Verified directly on the task: letting the executor handle it
    would still terminate (the recursion guard fires), but the executor
    discards failed flowfiles after max retries and reports 'success'
    because the sub-flow ran zero times. The guard itself is what we
    care about here.
    """
    from tasks.control.execute_flow import ExecuteFlowTask

    self_ref = str(tmp_path / "self.json")
    self_flow = {
        "id": "self",
        "name": "Self",
        "version": "1.0.0",
        "tasks": {
            "recurse": {
                "type": "executeFlow",
                "parameters": {"flow_path": self_ref},
            },
        },
        "relations": [],
    }
    Path(self_ref).write_text(json.dumps(self_flow), encoding="utf-8")

    # Seed the stack attribute to simulate we're already inside this
    # sub-flow, then invoke again — the guard must fire immediately.
    task = ExecuteFlowTask({"flow_path": self_ref})
    ff = FlowFile(content=b"x")
    import os
    ff.set_attribute("_subflow_stack", os.path.abspath(self_ref))
    with pytest.raises(TaskError, match="recursion"):
        task.execute(ff)


def test_execute_flow_missing_path_errors(tmp_path):
    from tasks.control.execute_flow import ExecuteFlowTask
    task = ExecuteFlowTask({"flow_path": ""})
    from core import TaskError
    with pytest.raises(TaskError):
        task.execute(FlowFile(content=b""))


def test_flow_ref_version_mismatch_raises(tmp_path):
    """flow_ref.version must match the loaded child's version field."""
    child_flow = _write_flow(tmp_path, "child", {
        "id": "child", "name": "Child", "version": "2.0.0",
        "tasks": {}, "relations": [],
    })
    parent_cfg = {
        "id": "parent", "name": "Parent", "version": "1.0.0",
        "tasks": {},
        "groups": {"pg1": {
            "name": "Wrapped",
            "flow_ref": {"path": child_flow, "version": "1.0.0"},
        }},
        "relations": [],
    }
    with pytest.raises(ValueError, match="version mismatch"):
        FlowParser.parse(parent_cfg)


def test_flow_ref_version_match_ok(tmp_path):
    """Matching versions load without error."""
    child_flow = _write_flow(tmp_path, "child", {
        "id": "child", "name": "Child", "version": "1.2.3",
        "tasks": {}, "relations": [],
    })
    parent_cfg = {
        "id": "parent", "name": "Parent", "version": "1.0.0",
        "tasks": {},
        "groups": {"pg1": {
            "name": "Wrapped",
            "flow_ref": {"path": child_flow, "version": "1.2.3"},
        }},
        "relations": [],
    }
    flow = FlowParser.parse(parent_cfg)
    assert "pg1" in flow.tasks


def test_port_mapping_input_unknown_raises(tmp_path):
    """port_mapping.input.port_task_id must point at an actual inputPort."""
    child_flow = _write_flow(tmp_path, "child", {
        "id": "child", "name": "Child", "version": "1.0.0",
        "tasks": {
            "in":  {"type": "inputPort",  "parameters": {"port_name": "main"}},
            "out": {"type": "outputPort", "parameters": {"port_name": "done"}},
        },
        "relations": [{"from": "in", "to": "out", "type": "success"}],
    })
    parent_cfg = {
        "id": "parent", "name": "Parent", "version": "1.0.0",
        "tasks": {},
        "groups": {"pg1": {
            "name": "Wrapped",
            "flow_ref": {"path": child_flow},
            "port_mapping": {"input": {"port_task_id": "doesnotexist"}},
        }},
        "relations": [],
    }
    with pytest.raises(Exception, match="port_mapping.input"):
        FlowParser.parse(parent_cfg)


def test_port_mapping_output_unknown_raises(tmp_path):
    """port_mapping.output keys must point at actual outputPort tasks."""
    child_flow = _write_flow(tmp_path, "child", {
        "id": "child", "name": "Child", "version": "1.0.0",
        "tasks": {
            "in":  {"type": "inputPort",  "parameters": {"port_name": "main"}},
            "out": {"type": "outputPort", "parameters": {"port_name": "done"}},
        },
        "relations": [{"from": "in", "to": "out", "type": "success"}],
    })
    parent_cfg = {
        "id": "parent", "name": "Parent", "version": "1.0.0",
        "tasks": {},
        "groups": {"pg1": {
            "name": "Wrapped",
            "flow_ref": {"path": child_flow},
            "port_mapping": {"output": {"in": "success"}},  # 'in' is inputPort
        }},
        "relations": [],
    }
    with pytest.raises(Exception, match="port_mapping.output"):
        FlowParser.parse(parent_cfg)


def test_flow_ref_missing_path_raises(tmp_path):
    """A flow_ref pointing at a non-existent file must fail at parse."""
    parent_cfg = {
        "id": "parent", "name": "Parent", "version": "1.0.0",
        "tasks": {},
        "groups": {"pg1": {
            "name": "Wrapped",
            "flow_ref": {"path": str(tmp_path / "missing.json")},
        }},
        "relations": [],
    }
    with pytest.raises(FileNotFoundError):
        FlowParser.parse(parent_cfg)
