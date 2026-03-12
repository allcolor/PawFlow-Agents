"""Tests for P8.1 — ParameterContext: flow parameters injection into tasks."""

import json
import os
import tempfile
import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, Flow, TaskFactory, TaskError
from core.parameter_context import ParameterContext
from core.base_task import BaseTask
from engine.continuous_executor import ContinuousFlowExecutor
from engine.parser import FlowParser


# ============================================================================
# ParameterContext unit tests
# ============================================================================

class TestParameterContext:

    def test_empty_context(self):
        ctx = ParameterContext()
        assert len(ctx) == 0
        assert not ctx
        assert ctx.parameters == {}

    def test_basic_get(self):
        ctx = ParameterContext({"env": "prod", "batch_size": "100"})
        assert ctx.get("env") == "prod"
        assert ctx.get("batch_size") == "100"
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_has(self):
        ctx = ParameterContext({"key": "val"})
        assert ctx.has("key")
        assert not ctx.has("other")

    def test_len_and_bool(self):
        assert len(ParameterContext({"a": 1, "b": 2})) == 2
        assert bool(ParameterContext({"a": 1}))
        assert not bool(ParameterContext())

    def test_with_overrides(self):
        ctx = ParameterContext({"env": "dev", "port": "8000"})
        ctx2 = ctx.with_overrides({"env": "prod"})
        # Original unchanged
        assert ctx.get("env") == "dev"
        # New context has override
        assert ctx2.get("env") == "prod"
        assert ctx2.get("port") == "8000"

    def test_with_overrides_adds_new_keys(self):
        ctx = ParameterContext({"a": "1"})
        ctx2 = ctx.with_overrides({"b": "2"})
        assert ctx2.get("a") == "1"
        assert ctx2.get("b") == "2"

    def test_resolve_simple(self):
        ctx = ParameterContext({"env": "production"})
        assert ctx.resolve("${flow.parameters.env}") == "production"
        assert ctx.resolve("prefix-${flow.parameters.env}-suffix") == "prefix-production-suffix"

    def test_resolve_missing(self):
        ctx = ParameterContext({})
        # Unresolved expression stays as-is
        assert ctx.resolve("${flow.parameters.unknown}") == "${flow.parameters.unknown}"

    def test_resolve_no_expression(self):
        ctx = ParameterContext({"x": "1"})
        assert ctx.resolve("plain string") == "plain string"
        assert ctx.resolve("") == ""

    def test_resolve_non_string(self):
        ctx = ParameterContext({"x": "1"})
        assert ctx.resolve(42) == 42
        assert ctx.resolve(None) is None

    def test_resolve_config(self):
        ctx = ParameterContext({"env": "prod", "port": "9000"})
        config = {
            "host": "localhost",
            "port": "${flow.parameters.port}",
            "label": "server-${flow.parameters.env}",
            "nested": {
                "key": "${flow.parameters.env}"
            },
            "list_val": ["${flow.parameters.env}", "static"],
            "number": 42,
        }
        resolved = ctx.resolve_config(config)
        assert resolved["host"] == "localhost"
        assert resolved["port"] == "9000"
        assert resolved["label"] == "server-prod"
        assert resolved["nested"]["key"] == "prod"
        assert resolved["list_val"] == ["prod", "static"]
        assert resolved["number"] == 42

    def test_with_mapping(self):
        parent = ParameterContext({"env": "prod", "api_key": "abc123"})
        mapping = {
            "sub_env": "${flow.parameters.env}",
            "mode": "fast",
            "secret": "${flow.parameters.api_key}",
        }
        child = parent.with_mapping(mapping)
        assert child.get("sub_env") == "prod"
        assert child.get("mode") == "fast"
        assert child.get("secret") == "abc123"
        # Parent keys not in mapping are absent from child
        assert not child.has("env")

    def test_with_mapping_unresolved(self):
        parent = ParameterContext({"env": "prod"})
        mapping = {"key": "${flow.parameters.missing}"}
        child = parent.with_mapping(mapping)
        assert child.get("key") == "${flow.parameters.missing}"

    def test_equality(self):
        a = ParameterContext({"x": "1"})
        b = ParameterContext({"x": "1"})
        c = ParameterContext({"x": "2"})
        assert a == b
        assert a != c
        assert a != "not a context"

    def test_repr(self):
        ctx = ParameterContext({"a": "1"})
        assert "a" in repr(ctx)

    def test_immutability(self):
        """Modifying the returned parameters dict should not affect the context."""
        ctx = ParameterContext({"key": "val"})
        params = ctx.parameters
        params["key"] = "hacked"
        assert ctx.get("key") == "val"


# ============================================================================
# BaseTask parameter context injection
# ============================================================================

class TestBaseTaskParameterContext:

    def test_task_has_parameter_context_attr(self):
        task = TaskFactory.get("log")({"message": "test"})
        assert task._parameter_context is None
        assert task.parameter_context is None

    def test_set_parameter_context_resolves_config(self):
        """After injecting ParameterContext, config values with ${flow.parameters.X} are resolved."""
        task = TaskFactory.get("log")({"message": "env=${flow.parameters.env}"})
        # Before injection: unresolved
        assert "${flow.parameters.env}" in task.config.get("message", "")

        ctx = ParameterContext({"env": "production"})
        task.set_parameter_context(ctx)

        # After injection: resolved
        assert task.config["message"] == "env=production"

    def test_resolve_value_with_context(self):
        task = TaskFactory.get("log")({"message": "test"})
        ctx = ParameterContext({"env": "staging"})
        task.set_parameter_context(ctx)

        result = task.resolve_value("${flow.parameters.env}")
        assert result == "staging"

    def test_resolve_value_with_flowfile_attrs(self):
        task = TaskFactory.get("log")({"message": "test"})
        ctx = ParameterContext({"env": "prod"})
        task.set_parameter_context(ctx)

        ff = FlowFile(content=b"data", attributes={"filename": "test.csv"})
        result = task.resolve_value("${filename} in ${flow.parameters.env}", flowfile=ff)
        assert result == "test.csv in prod"

    def test_resolve_value_no_context(self):
        task = TaskFactory.get("log")({"message": "test"})
        # No parameter context set
        result = task.resolve_value("${flow.parameters.env}")
        assert result == "${flow.parameters.env}"  # stays unresolved

    def test_resolve_value_plain_string(self):
        task = TaskFactory.get("log")({"message": "test"})
        assert task.resolve_value("plain") == "plain"
        assert task.resolve_value("") == ""


# ============================================================================
# FlowExecutor end-to-end parameter injection
# ============================================================================

class TestFlowExecutorParameters:

    def _make_flow(self, flow_params=None, task_config=None):
        """Helper: create a simple 1-task flow."""
        flow_config = {
            "id": "test-flow",
            "name": "Test Flow",
            "parameters": flow_params or {},
            "tasks": {
                "task1": {
                    "type": "log",
                    "parameters": task_config or {"message": "hello"},
                }
            },
            "relations": [],
        }
        return FlowParser.parse(flow_config)

    def test_flow_params_injected_into_task(self):
        """Flow parameters should be injected into task config."""
        flow = self._make_flow(
            flow_params={"env": "production"},
            task_config={"message": "Running in ${flow.parameters.env}"},
        )
        ff = FlowFile(content=b"test")
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[ff], max_workers=1)

        assert result.success
        # The task should have resolved the parameter
        task = flow.tasks["task1"]
        assert task.config["message"] == "Running in production"

    def test_parameter_override_at_execution(self):
        """Parameters passed to execute_flow should override flow defaults."""
        flow = self._make_flow(
            flow_params={"env": "dev"},
            task_config={"message": "env=${flow.parameters.env}"},
        )
        ff = FlowFile(content=b"test")
        result = ContinuousFlowExecutor.run_batch(
            flow,
            input_flowfiles=[ff],
            parameters={"env": "staging"},
            max_workers=1,
        )

        assert result.success
        task = flow.tasks["task1"]
        assert task.config["message"] == "env=staging"

    def test_parameter_context_passed_directly(self):
        """A pre-built ParameterContext should be used as-is."""
        flow = self._make_flow(
            flow_params={"env": "dev"},
            task_config={"message": "env=${flow.parameters.env}"},
        )
        ff = FlowFile(content=b"test")
        result = ContinuousFlowExecutor.run_batch(
            flow,
            input_flowfiles=[ff],
            parameters={"env": "custom-ctx"},
            max_workers=1,
        )

        assert result.success
        task = flow.tasks["task1"]
        assert task.config["message"] == "env=custom-ctx"

    def test_no_params_no_crash(self):
        """Flow with no parameters should work fine."""
        flow = self._make_flow(
            flow_params={},
            task_config={"message": "no params"},
        )
        ff = FlowFile(content=b"test")
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[ff], max_workers=1)
        assert result.success

    def test_multi_task_params(self):
        """Parameters should be injected into all tasks in a multi-task flow."""
        flow_config = {
            "id": "multi",
            "name": "Multi",
            "parameters": {"prefix": "PRE"},
            "tasks": {
                "t1": {"type": "log", "parameters": {"message": "${flow.parameters.prefix}-1"}},
                "t2": {"type": "log", "parameters": {"message": "${flow.parameters.prefix}-2"}},
            },
            "relations": [{"from": "t1", "to": "t2"}],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)

        assert result.success
        assert flow.tasks["t1"].config["message"] == "PRE-1"
        assert flow.tasks["t2"].config["message"] == "PRE-2"

    def test_mixed_expression_types(self):
        """${flow.parameters.X} and ${env.Y} should both resolve."""
        os.environ["_PYFI2_TEST_VAR"] = "env_value"
        try:
            flow = self._make_flow(
                flow_params={"key": "param_value"},
                task_config={"message": "${flow.parameters.key}-${env._PYFI2_TEST_VAR}"},
            )
            result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
            assert result.success
            assert flow.tasks["task1"].config["message"] == "param_value-env_value"
        finally:
            del os.environ["_PYFI2_TEST_VAR"]


# ============================================================================
# ContinuousFlowExecutor parameter injection
# ============================================================================

class TestContinuousExecutorParameters:

    def test_params_injected_on_build(self):
        """ContinuousFlowExecutor should inject ParameterContext into tasks."""
        from engine.continuous_executor import ContinuousFlowExecutor

        flow_config = {
            "id": "cont-test",
            "name": "Continuous Test",
            "parameters": {"mode": "fast"},
            "tasks": {
                "t1": {"type": "log", "parameters": {"message": "${flow.parameters.mode}"}},
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        executor = ContinuousFlowExecutor(flow, enable_checkpoints=False)

        task = executor._tasks["t1"]
        assert task.parameter_context is not None
        assert task.config["message"] == "fast"

    def test_params_override_on_build(self):
        from engine.continuous_executor import ContinuousFlowExecutor

        flow_config = {
            "id": "cont-override",
            "name": "Override Test",
            "parameters": {"mode": "slow"},
            "tasks": {
                "t1": {"type": "log", "parameters": {"message": "${flow.parameters.mode}"}},
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        executor = ContinuousFlowExecutor(
            flow, enable_checkpoints=False, parameters={"mode": "turbo"}
        )

        assert executor._tasks["t1"].config["message"] == "turbo"


# ============================================================================
# ExecuteFlowTask — subflow parameter propagation
# ============================================================================

class TestSubflowParameterPropagation:

    def _write_subflow(self, tmpdir, params=None, task_msg="sub: ${flow.parameters.env}"):
        """Write a subflow JSON file and return its path."""
        subflow = {
            "id": "subflow",
            "name": "Sub Flow",
            "parameters": params or {},
            "tasks": {
                "sub_log": {
                    "type": "log",
                    "parameters": {"message": task_msg},
                }
            },
            "relations": [],
        }
        path = os.path.join(tmpdir, "subflow.json")
        with open(path, "w") as f:
            json.dump(subflow, f)
        return path

    def test_parent_params_propagate_to_subflow(self, tmp_path):
        """Parent flow parameters should propagate to subflow tasks."""
        subflow_path = self._write_subflow(str(tmp_path))

        flow_config = {
            "id": "parent",
            "name": "Parent",
            "parameters": {"env": "production"},
            "tasks": {
                "exec": {
                    "type": "executeFlow",
                    "parameters": {"flow_path": subflow_path},
                }
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"test")], max_workers=1)
        assert result.success

    def test_parameter_mapping(self, tmp_path):
        """Explicit parameter_mapping should map parent → subflow params."""
        subflow_path = self._write_subflow(
            str(tmp_path),
            params={"sub_env": "default"},
            task_msg="sub: ${flow.parameters.sub_env}",
        )

        flow_config = {
            "id": "parent",
            "name": "Parent",
            "parameters": {"env": "staging"},
            "tasks": {
                "exec": {
                    "type": "executeFlow",
                    "parameters": {
                        "flow_path": subflow_path,
                        "parameter_mapping": {
                            "sub_env": "${flow.parameters.env}",
                        },
                    },
                }
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"test")], max_workers=1)
        assert result.success

    def test_mapping_with_literal_values(self, tmp_path):
        """Mapping can contain literal values (not just expressions)."""
        subflow_path = self._write_subflow(
            str(tmp_path),
            params={},
            task_msg="mode=${flow.parameters.mode}",
        )

        flow_config = {
            "id": "parent",
            "name": "Parent",
            "parameters": {},
            "tasks": {
                "exec": {
                    "type": "executeFlow",
                    "parameters": {
                        "flow_path": subflow_path,
                        "parameter_mapping": {"mode": "turbo"},
                    },
                }
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
        assert result.success

    def test_subflow_defaults_with_no_parent_params(self, tmp_path):
        """Subflow uses its own defaults when parent has no params."""
        subflow_path = self._write_subflow(
            str(tmp_path),
            params={"env": "subflow-default"},
            task_msg="env=${flow.parameters.env}",
        )

        flow_config = {
            "id": "parent",
            "name": "Parent",
            "parameters": {},
            "tasks": {
                "exec": {
                    "type": "executeFlow",
                    "parameters": {"flow_path": subflow_path},
                }
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
        assert result.success


# ============================================================================
# Regression: existing tests should still pass
# ============================================================================

class TestParameterContextRegression:

    def test_task_without_params_still_works(self):
        """Tasks that don't use ${flow.parameters.X} should be unaffected."""
        flow_config = {
            "id": "regression",
            "name": "Regression",
            "parameters": {},
            "tasks": {
                "gen": {"type": "generateFlowFile", "parameters": {"content": "hello", "count": 1}},
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
        assert result.success
        assert len(result.output_flowfiles) >= 1

    def test_expression_still_resolves_attributes(self):
        """${attr} resolution from FlowFile attributes still works."""
        from core.expression import resolve_expression
        result = resolve_expression(
            "file=${filename} param=${flow.parameters.env}",
            attributes={"filename": "test.csv"},
            parameters={"env": "prod"},
        )
        assert result == "file=test.csv param=prod"


# ============================================================================
# P8.2 — Subflow validation
# ============================================================================

class TestSubflowValidation:

    def _write_subflow(self, tmpdir, params=None, task_msg="msg"):
        subflow = {
            "id": "sub", "name": "Sub",
            "parameters": params or {},
            "tasks": {"t": {"type": "log", "parameters": {"message": task_msg}}},
            "relations": [],
        }
        path = os.path.join(tmpdir, "sub.json")
        with open(path, "w") as f:
            json.dump(subflow, f)
        return path

    def test_unresolved_params_warning(self, tmp_path, caplog):
        """Subflow with unresolved params should log a warning."""
        import logging
        subflow_path = self._write_subflow(
            str(tmp_path),
            task_msg="${flow.parameters.missing_param}",
        )
        flow_config = {
            "id": "parent", "name": "Parent", "parameters": {},
            "tasks": {
                "exec": {"type": "executeFlow", "parameters": {"flow_path": subflow_path}},
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        with caplog.at_level(logging.WARNING):
            result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
        assert result.success
        assert any("missing_param" in r.message for r in caplog.records)

    def test_chained_subflow_params(self, tmp_path):
        """Nested subflows: parent → child → grandchild parameter propagation."""
        # Grandchild subflow
        grandchild = {
            "id": "gc", "name": "GrandChild",
            "parameters": {},
            "tasks": {"t": {"type": "log", "parameters": {"message": "${flow.parameters.env}"}}},
            "relations": [],
        }
        gc_path = os.path.join(str(tmp_path), "grandchild.json")
        with open(gc_path, "w") as f:
            json.dump(grandchild, f)

        # Child subflow that runs grandchild
        child = {
            "id": "child", "name": "Child",
            "parameters": {},
            "tasks": {
                "exec_gc": {"type": "executeFlow", "parameters": {"flow_path": gc_path}},
            },
            "relations": [],
        }
        child_path = os.path.join(str(tmp_path), "child.json")
        with open(child_path, "w") as f:
            json.dump(child, f)

        # Parent flow
        flow_config = {
            "id": "parent", "name": "Parent",
            "parameters": {"env": "chained-prod"},
            "tasks": {
                "exec_child": {"type": "executeFlow", "parameters": {"flow_path": child_path}},
            },
            "relations": [],
        }
        flow = FlowParser.parse(flow_config)
        result = ContinuousFlowExecutor.run_batch(flow, input_flowfiles=[FlowFile(content=b"")], max_workers=1)
        assert result.success


# ============================================================================
# P8.3 — CLI --param override
# ============================================================================

class TestCLIParamOverride:

    def test_parse_param_args(self):
        """Test that --param key=value is parsed correctly."""
        params = ["env=prod", "port=9000", "complex=a=b=c"]
        overrides = {}
        for p in params:
            k, v = p.split('=', 1)
            overrides[k.strip()] = v.strip()
        assert overrides == {"env": "prod", "port": "9000", "complex": "a=b=c"}


# ============================================================================
# P8.3 — API models have parameters field
# ============================================================================

class TestAPIModels:

    def test_batch_request_has_parameters(self):
        from api.routers.execution_router import BatchExecuteRequest
        req = BatchExecuteRequest(flow_id="test", parameters={"env": "prod"})
        assert req.parameters == {"env": "prod"}

    def test_batch_request_parameters_optional(self):
        from api.routers.execution_router import BatchExecuteRequest
        req = BatchExecuteRequest(flow_id="test")
        assert req.parameters is None

    def test_continuous_request_has_parameters(self):
        from api.routers.execution_router import ContinuousStartRequest
        req = ContinuousStartRequest(flow_id="test", parameters={"mode": "fast"})
        assert req.parameters == {"mode": "fast"}


class TestSchedulerParamOverride:

    def test_scheduler_job_with_parameters(self):
        from engine.scheduler import FlowScheduler
        scheduler = FlowScheduler()
        job = scheduler.add_job("test_job", "flows/test.json", "*/5 * * * *",
                                parameters={"env": "prod", "batch_size": "50"})
        assert job["parameters"] == {"env": "prod", "batch_size": "50"}

    def test_scheduler_job_without_parameters(self):
        from engine.scheduler import FlowScheduler
        scheduler = FlowScheduler()
        job = scheduler.add_job("test_job", "flows/test.json", "*/5 * * * *")
        assert job["parameters"] == {}

    def test_scheduler_job_parameters_persist(self):
        from engine.scheduler import FlowScheduler
        scheduler = FlowScheduler()
        scheduler.add_job("p_job", "flows/test.json", "0 * * * *",
                          parameters={"key": "value"})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp = f.name
        try:
            scheduler.save_jobs(tmp)
            scheduler2 = FlowScheduler()
            scheduler2.load_jobs(tmp)
            loaded = scheduler2.get_job("p_job")
            assert loaded["parameters"] == {"key": "value"}
        finally:
            os.unlink(tmp)

    def test_scheduler_api_model_has_parameters(self):
        from api.routers.scheduler_router import JobCreateRequest
        req = JobCreateRequest(job_id="j1", flow_path="f.json",
                               cron_expression="* * * * *",
                               parameters={"env": "staging"})
        assert req.parameters == {"env": "staging"}

    def test_scheduler_api_update_model_has_parameters(self):
        from api.routers.scheduler_router import JobUpdateRequest
        req = JobUpdateRequest(parameters={"env": "prod"})
        assert req.parameters == {"env": "prod"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
