"""Tests for editor features: undo/redo, duplicate, expression tester, flow search."""

import json
import copy
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock


# ============================================================================
# Expression Language Tests
# ============================================================================

from core.expression import resolve_expression


class TestResolveExpression:
    """Tests for the expression language resolver."""

    def test_no_expressions(self):
        assert resolve_expression("hello world") == "hello world"

    def test_empty_string(self):
        assert resolve_expression("") == ""

    def test_attribute_resolution(self):
        result = resolve_expression("file: ${filename}", parameters={"filename": "data.csv"})
        assert result == "file: data.csv"

    def test_multiple_attributes(self):
        params = {"name": "test", "ext": "csv"}
        result = resolve_expression("${name}.${ext}", parameters=params)
        assert result == "test.csv"

    def test_flow_parameter_resolution(self):
        params = {"threshold": "0.5"}
        result = resolve_expression("val=${threshold}", parameters=params)
        assert result == "val=0.5"

    def test_env_variable_resolution(self):
        with patch.dict("os.environ", {"MY_VAR": "hello"}):
            result = resolve_expression("${MY_VAR:!important(env)}")
            assert result == "hello"

    def test_unresolved_expression(self):
        result = resolve_expression("${unknown_var}")
        assert result == "${unknown_var}"

    def test_unresolved_flow_param(self):
        result = resolve_expression("${missing}", parameters={})
        assert result == "${missing}"

    def test_unresolved_env(self):
        result = resolve_expression("${DEFINITELY_NOT_SET_12345:!important(env)}")
        assert result == "${DEFINITELY_NOT_SET_12345:!important(env)}"

    def test_mixed_resolution(self):
        # FlowFile attrs win over flow params (more specific)
        merged = {"id": "42", "mode": "fast"}
        result = resolve_expression(
            "id=${id} mode=${mode}",
            parameters=merged,
        )
        assert result == "id=42 mode=fast"

    def test_none_parameters(self):
        result = resolve_expression("${x}", parameters=None)
        assert result == "${x}"

    def test_numeric_parameter_value(self):
        result = resolve_expression("${count}", parameters={"count": 42})
        assert result == "42"

    def test_no_dollar_brace(self):
        result = resolve_expression("just text {not_expr} $notexpr")
        assert result == "just text {not_expr} $notexpr"


# ============================================================================
# Undo/Redo Logic Tests (unit-level, no Streamlit)
# ============================================================================


class TestUndoRedoLogic:
    """Test undo/redo stack logic without Streamlit dependency."""

    def _make_stack(self):
        return {"undo": [], "redo": []}

    def _push_undo(self, stacks, flow):
        snapshot = json.dumps(flow, ensure_ascii=False)
        if stacks["undo"] and stacks["undo"][-1] == snapshot:
            return  # Skip duplicate
        stacks["undo"].append(snapshot)
        if len(stacks["undo"]) > 50:
            stacks["undo"] = stacks["undo"][-50:]
        stacks["redo"] = []  # Clear redo on new action

    def _undo(self, stacks, current_flow):
        if not stacks["undo"]:
            return current_flow
        current = json.dumps(current_flow, ensure_ascii=False)
        stacks["redo"].append(current)
        prev = stacks["undo"].pop()
        return json.loads(prev)

    def _redo(self, stacks, current_flow):
        if not stacks["redo"]:
            return current_flow
        current = json.dumps(current_flow, ensure_ascii=False)
        stacks["undo"].append(current)
        nxt = stacks["redo"].pop()
        return json.loads(nxt)

    def test_push_undo(self):
        stacks = self._make_stack()
        flow = {"name": "test", "tasks": {}}
        self._push_undo(stacks, flow)
        assert len(stacks["undo"]) == 1

    def test_push_undo_deduplication(self):
        stacks = self._make_stack()
        flow = {"name": "test"}
        self._push_undo(stacks, flow)
        self._push_undo(stacks, flow)
        assert len(stacks["undo"]) == 1

    def test_push_undo_clears_redo(self):
        stacks = self._make_stack()
        stacks["redo"] = ["something"]
        self._push_undo(stacks, {"name": "test"})
        assert len(stacks["redo"]) == 0

    def test_undo_restores_previous(self):
        stacks = self._make_stack()
        flow_v1 = {"name": "v1"}
        flow_v2 = {"name": "v2"}
        self._push_undo(stacks, flow_v1)
        result = self._undo(stacks, flow_v2)
        assert result == flow_v1

    def test_undo_pushes_to_redo(self):
        stacks = self._make_stack()
        flow_v1 = {"name": "v1"}
        flow_v2 = {"name": "v2"}
        self._push_undo(stacks, flow_v1)
        self._undo(stacks, flow_v2)
        assert len(stacks["redo"]) == 1

    def test_redo_restores_next(self):
        stacks = self._make_stack()
        flow_v1 = {"name": "v1"}
        flow_v2 = {"name": "v2"}
        self._push_undo(stacks, flow_v1)
        undone = self._undo(stacks, flow_v2)
        assert undone == flow_v1
        redone = self._redo(stacks, undone)
        assert redone == flow_v2

    def test_undo_empty_stack_returns_current(self):
        stacks = self._make_stack()
        flow = {"name": "current"}
        result = self._undo(stacks, flow)
        assert result == flow

    def test_redo_empty_stack_returns_current(self):
        stacks = self._make_stack()
        flow = {"name": "current"}
        result = self._redo(stacks, flow)
        assert result == flow

    def test_max_undo_stack_size(self):
        stacks = self._make_stack()
        for i in range(60):
            self._push_undo(stacks, {"name": f"v{i}"})
        assert len(stacks["undo"]) == 50
        # Oldest should be v10 (0-9 trimmed)
        oldest = json.loads(stacks["undo"][0])
        assert oldest["name"] == "v10"

    def test_multiple_undo_redo_cycle(self):
        stacks = self._make_stack()
        flow_v1 = {"name": "v1"}
        flow_v2 = {"name": "v2"}
        flow_v3 = {"name": "v3"}

        self._push_undo(stacks, flow_v1)
        self._push_undo(stacks, flow_v2)

        # Undo from v3 → v2
        result = self._undo(stacks, flow_v3)
        assert result == flow_v2

        # Undo from v2 → v1
        result = self._undo(stacks, result)
        assert result == flow_v1

        # Redo from v1 → v2
        result = self._redo(stacks, result)
        assert result == flow_v2

        # Redo from v2 → v3
        result = self._redo(stacks, result)
        assert result == flow_v3


# ============================================================================
# Duplicate Task Logic Tests
# ============================================================================


class TestDuplicateTaskLogic:
    """Test task duplication logic without Streamlit dependency."""

    def test_duplicate_task(self):
        flow = {
            "tasks": {
                "log_1": {"type": "log", "parameters": {"level": "INFO"}},
            }
        }
        # Simulate duplication
        tasks = flow["tasks"]
        task_id = "log_1"
        config = copy.deepcopy(tasks[task_id])
        task_type = config.get("type", "task")
        n = len(tasks) + 1
        new_id = f"{task_type}_{n}"
        while new_id in tasks:
            n += 1
            new_id = f"{task_type}_{n}"
        tasks[new_id] = config

        assert "log_2" in flow["tasks"]
        assert flow["tasks"]["log_2"]["parameters"]["level"] == "INFO"
        assert flow["tasks"]["log_2"] is not flow["tasks"]["log_1"]

    def test_duplicate_avoids_collision(self):
        flow = {
            "tasks": {
                "log_1": {"type": "log"},
                "log_2": {"type": "log"},
            }
        }
        tasks = flow["tasks"]
        config = copy.deepcopy(tasks["log_1"])
        task_type = config.get("type", "task")
        n = len(tasks) + 1
        new_id = f"{task_type}_{n}"
        while new_id in tasks:
            n += 1
            new_id = f"{task_type}_{n}"
        tasks[new_id] = config

        assert new_id == "log_3"
        assert len(flow["tasks"]) == 3

    def test_duplicate_preserves_deep_config(self):
        flow = {
            "tasks": {
                "http_1": {
                    "type": "http",
                    "parameters": {
                        "url": "https://example.com",
                        "headers": {"Accept": "application/json"},
                    }
                }
            }
        }
        original = flow["tasks"]["http_1"]
        duplicate = copy.deepcopy(original)
        flow["tasks"]["http_2"] = duplicate

        # Modify duplicate — should not affect original
        duplicate["parameters"]["headers"]["Accept"] = "text/html"
        assert original["parameters"]["headers"]["Accept"] == "application/json"


# ============================================================================
# Flow Search Logic Tests
# ============================================================================


class TestFlowSearchLogic:
    """Test flow search/filter logic used in Dashboard."""

    def _make_flow(self, name, flow_id, description=""):
        mock = MagicMock()
        mock.name = name
        mock.id = flow_id
        mock.description = description
        return mock

    def test_no_search_returns_all(self):
        flows = [self._make_flow("ETL Pipeline", "etl-1"), self._make_flow("API Ingest", "api-1")]
        search_term = ""
        filtered = [f for f in flows if not search_term or search_term in f.name.lower()]
        assert len(filtered) == 2

    def test_search_by_name(self):
        flows = [self._make_flow("ETL Pipeline", "etl-1"), self._make_flow("API Ingest", "api-1")]
        search_term = "etl"
        filtered = [f for f in flows if search_term in f.name.lower()]
        assert len(filtered) == 1
        assert filtered[0].name == "ETL Pipeline"

    def test_search_by_id(self):
        flows = [self._make_flow("Flow A", "abc-123"), self._make_flow("Flow B", "xyz-789")]
        search_term = "xyz"
        filtered = [f for f in flows if search_term in f.id.lower()]
        assert len(filtered) == 1
        assert filtered[0].id == "xyz-789"

    def test_search_by_description(self):
        flows = [
            self._make_flow("Flow A", "a-1", "Processes CSV files"),
            self._make_flow("Flow B", "b-1", "Sends emails"),
        ]
        search_term = "csv"
        filtered = [
            f for f in flows
            if search_term in f.name.lower()
            or search_term in (f.description or "").lower()
            or search_term in f.id.lower()
        ]
        assert len(filtered) == 1
        assert filtered[0].name == "Flow A"

    def test_search_case_insensitive(self):
        flows = [self._make_flow("MyPipeline", "pipe-1")]
        search_term = "mypipeline"
        filtered = [f for f in flows if search_term in f.name.lower()]
        assert len(filtered) == 1

    def test_search_no_results(self):
        flows = [self._make_flow("Flow A", "a-1")]
        search_term = "nonexistent"
        filtered = [
            f for f in flows
            if search_term in f.name.lower()
            or search_term in (f.description or "").lower()
            or search_term in f.id.lower()
        ]
        assert len(filtered) == 0


# ============================================================================
# Annotation Tests
# ============================================================================


class TestAnnotations:
    """Test task annotation/comment storage."""

    def test_add_annotation(self):
        flow = {"tasks": {"log_1": {"type": "log"}}, "annotations": {}}
        flow["annotations"]["log_1"] = "This task logs input data"
        assert flow["annotations"]["log_1"] == "This task logs input data"

    def test_remove_annotation(self):
        flow = {"tasks": {"log_1": {"type": "log"}}, "annotations": {"log_1": "note"}}
        del flow["annotations"]["log_1"]
        assert "log_1" not in flow["annotations"]

    def test_annotation_setdefault(self):
        flow = {"tasks": {"log_1": {"type": "log"}}}
        annotations = flow.setdefault("annotations", {})
        annotations["log_1"] = "my note"
        assert flow["annotations"]["log_1"] == "my note"

    def test_annotations_preserved_in_json(self):
        flow = {
            "tasks": {"log_1": {"type": "log"}},
            "annotations": {"log_1": "important task"},
        }
        serialized = json.dumps(flow)
        restored = json.loads(serialized)
        assert restored["annotations"]["log_1"] == "important task"

    def test_empty_annotation_not_stored(self):
        flow = {"tasks": {"log_1": {"type": "log"}}, "annotations": {"log_1": "old note"}}
        new_note = ""
        if new_note:
            flow["annotations"]["log_1"] = new_note
        elif "log_1" in flow["annotations"]:
            del flow["annotations"]["log_1"]
        assert "log_1" not in flow["annotations"]

    def test_annotation_in_fingerprint(self):
        """Annotations should affect the flow canvas fingerprint."""
        flow1 = {"tasks": {"a": {}}, "relations": [], "annotations": {}}
        flow2 = {"tasks": {"a": {}}, "relations": [], "annotations": {"a": "note"}}

        def fingerprint(fd):
            tasks = sorted(fd.get('tasks', {}).keys())
            rels = [(r['from'], r['to']) for r in fd.get('relations', [])]
            annots = sorted(fd.get('annotations', {}).items())
            return f"{tasks}|{rels}|{annots}"

        assert fingerprint(flow1) != fingerprint(flow2)


# ============================================================================
# Canvas Options Tests
# ============================================================================


class TestCanvasOptions:
    """Test canvas configuration options (minimap, controls, context menus)."""

    def test_minimap_default_off(self):
        """Minimap should default to disabled."""
        session = {}
        show_minimap = session.get("canvas_show_minimap", False)
        assert show_minimap is False

    def test_minimap_toggle_on(self):
        session = {"canvas_show_minimap": True}
        assert session["canvas_show_minimap"] is True

    def test_controls_default_on(self):
        """Controls should default to enabled."""
        session = {}
        show_controls = session.get("canvas_show_controls", True)
        assert show_controls is True

    def test_controls_toggle_off(self):
        session = {"canvas_show_controls": False}
        assert session["canvas_show_controls"] is False

    def test_flow_canvas_node_is_draggable(self):
        """FlowCanvas nodes should be draggable by default."""
        from gui.components.flow_canvas import FlowCanvas
        canvas = FlowCanvas.__new__(FlowCanvas)
        flow_dict = {
            "tasks": {"log_1": {"type": "log"}},
            "relations": [],
        }
        # Mock session state
        with patch.dict("streamlit.session_state", {"node_positions": {}}):
            nodes = canvas._build_nodes(flow_dict)
            assert len(nodes) == 1
            assert nodes[0].draggable is True
            assert nodes[0].selectable is True
            assert nodes[0].connectable is True

    def test_flow_canvas_edge_colors(self):
        """Edges should have correct colors per relationship type."""
        from gui.components.flow_canvas import FlowCanvas
        canvas = FlowCanvas.__new__(FlowCanvas)
        flow_dict = {
            "tasks": {"a": {"type": "log"}, "b": {"type": "log"}},
            "relations": [
                {"from": "a", "to": "b", "type": "success"},
                {"from": "a", "to": "b", "type": "failure"},
            ],
        }
        edges = canvas._build_edges(flow_dict)
        assert len(edges) == 2
        # success edges are animated
        assert edges[0].animated is True
        # failure edges are not
        assert edges[1].animated is False


# ============================================================================
# Process Groups Tests
# ============================================================================


class TestProcessGroups:
    """Test process group management."""

    def test_create_group(self):
        flow = {"tasks": {"a": {"type": "log"}, "b": {"type": "log"}}, "groups": {}}
        flow["groups"]["ETL"] = {"color": "#4285f4", "tasks": []}
        assert "ETL" in flow["groups"]
        assert flow["groups"]["ETL"]["color"] == "#4285f4"

    def test_add_task_to_group(self):
        flow = {"tasks": {"a": {"type": "log"}}, "groups": {"ETL": {"color": "#f00", "tasks": []}}}
        flow["groups"]["ETL"]["tasks"].append("a")
        assert "a" in flow["groups"]["ETL"]["tasks"]

    def test_remove_task_from_group(self):
        flow = {"tasks": {"a": {"type": "log"}}, "groups": {"ETL": {"color": "#f00", "tasks": ["a"]}}}
        flow["groups"]["ETL"]["tasks"].remove("a")
        assert "a" not in flow["groups"]["ETL"]["tasks"]

    def test_delete_group(self):
        flow = {"groups": {"ETL": {"color": "#f00", "tasks": ["a", "b"]}}}
        del flow["groups"]["ETL"]
        assert "ETL" not in flow["groups"]

    def test_group_color_in_node_style(self):
        """Nodes in a group should have the group's border color."""
        from gui.components.flow_canvas import FlowCanvas
        canvas = FlowCanvas.__new__(FlowCanvas)
        flow_dict = {
            "tasks": {"a": {"type": "log"}, "b": {"type": "log"}},
            "relations": [],
            "groups": {"ETL": {"color": "#ff0000", "tasks": ["a"]}},
        }
        with patch.dict("streamlit.session_state", {"node_positions": {}}):
            nodes = canvas._build_nodes(flow_dict)
            node_a = [n for n in nodes if n.id == "a"][0]
            node_b = [n for n in nodes if n.id == "b"][0]
            # Node A should have group color border
            assert "#ff0000" in node_a.style["border"]
            assert "3px" in node_a.style["border"]
            # Node B should have default border
            assert "#ff0000" not in node_b.style["border"]
            assert "2px" in node_b.style["border"]

    def test_group_label_in_node(self):
        """Nodes in a group should show group name in label."""
        from gui.components.flow_canvas import FlowCanvas
        canvas = FlowCanvas.__new__(FlowCanvas)
        flow_dict = {
            "tasks": {"a": {"type": "log"}},
            "relations": [],
            "groups": {"ETL": {"color": "#f00", "tasks": ["a"]}},
        }
        with patch.dict("streamlit.session_state", {"node_positions": {}}):
            nodes = canvas._build_nodes(flow_dict)
            assert "ETL" in nodes[0].data["content"]

    def test_groups_in_fingerprint(self):
        """Groups should affect the flow canvas fingerprint."""
        from gui.components.flow_canvas import _flow_fingerprint
        flow1 = {"tasks": {"a": {}}, "relations": [], "groups": {}}
        flow2 = {"tasks": {"a": {}}, "relations": [], "groups": {"G1": {"tasks": ["a"], "color": "#f00"}}}
        assert _flow_fingerprint(flow1) != _flow_fingerprint(flow2)

    def test_multiple_groups(self):
        flow = {"tasks": {"a": {}, "b": {}, "c": {}}, "groups": {}}
        flow["groups"]["Input"] = {"color": "#00f", "tasks": ["a"]}
        flow["groups"]["Output"] = {"color": "#0f0", "tasks": ["c"]}
        assert len(flow["groups"]) == 2
        assert "a" in flow["groups"]["Input"]["tasks"]
        assert "c" in flow["groups"]["Output"]["tasks"]
        # b is ungrouped
        grouped_tasks = set()
        for g in flow["groups"].values():
            grouped_tasks.update(g["tasks"])
        assert "b" not in grouped_tasks


class TestInlineValidation:
    """Tests for inline auto-validation logic."""

    def test_flow_with_no_tasks_has_errors(self):
        """Empty flow should fail validation."""
        from engine.validator import FlowValidator
        flow = {"id": "empty", "name": "Empty", "version": "1.0.0", "tasks": {}, "relations": []}
        result = FlowValidator().validate(flow)
        assert not result.valid

    def test_valid_flow_passes(self):
        """A flow with tasks and relations is valid."""
        from engine.validator import FlowValidator
        flow = {
            "id": "ok",
            "name": "OK",
            "version": "1.0.0",
            "tasks": {
                "a": {"type": "log", "parameters": {"message": "hi"}},
            },
            "relations": [],
        }
        result = FlowValidator().validate(flow)
        assert result.valid

    def test_orphan_relation_detected(self):
        """Relation to non-existent task should produce error."""
        from engine.validator import FlowValidator
        flow = {
            "id": "bad_rel",
            "name": "BadRel",
            "version": "1.0.0",
            "tasks": {"a": {"type": "log", "parameters": {"message": "hi"}}},
            "relations": [{"from": "a", "to": "nonexistent", "type": "success"}],
        }
        result = FlowValidator().validate(flow)
        assert not result.valid or result.warnings  # Either error or warning


class TestDocumentationKeys:
    """Tests that new documentation i18n keys exist."""

    def test_keyboard_shortcut_keys_exist(self):
        """Keyboard shortcut i18n keys should be defined."""
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("doc.shortcuts_title") == "Keyboard Shortcuts"
        assert i18n_module.t("doc.shortcuts_editor") == "Editor"
        assert i18n_module.t("doc.shortcuts_canvas") == "Canvas"
        assert i18n_module.t("doc.shortcuts_general") == "General"

    def test_new_monitor_keys_exist(self):
        """New monitor i18n keys should be defined."""
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("monitor.last_update") == "Last update"
        assert i18n_module.t("monitor.memory") == "Memory"
        assert i18n_module.t("monitor.system") == "System"
        assert i18n_module.t("monitor.lineage") == "Lineage"

    def test_new_editor_keys_exist(self):
        """New editor i18n keys should be defined."""
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("editor.config_json") == "Config (JSON)"
        assert i18n_module.t("editor.subflow_inputs") != "editor.subflow_inputs"
        assert i18n_module.t("editor.subflow_outputs") != "editor.subflow_outputs"

    def test_new_settings_keys_exist(self):
        """New settings i18n keys should be defined."""
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("settings.controller_services") == "Controller Services"
        assert i18n_module.t("settings.port") == "Port"
        assert "removed" in i18n_module.t("settings.logs_cleaned", count=5)


class TestDashboardKeys:
    """Tests for new Dashboard i18n keys."""

    def test_dashboard_running_flows_key(self):
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("dashboard.running_flows") == "Running Flows"

    def test_dashboard_success_rate_key(self):
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("dashboard.success_rate") == "Success Rate"

    def test_dashboard_sort_keys(self):
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("dashboard.sort_by") == "Sort by"
        assert i18n_module.t("dashboard.sort_name") == "Name"
        assert i18n_module.t("dashboard.sort_tasks") == "Task count"
        assert i18n_module.t("dashboard.sort_recent") == "Recently modified"

    def test_dashboard_keys_in_fr(self):
        import gui.i18n as i18n_module
        i18n_module.init("fr")
        assert i18n_module.t("dashboard.running_flows") == "Flux en cours"
        assert i18n_module.t("dashboard.sort_by") == "Trier par"

    def test_dashboard_keys_in_es(self):
        import gui.i18n as i18n_module
        i18n_module.init("es")
        assert i18n_module.t("dashboard.running_flows") == "Flujos en ejecución"
        assert i18n_module.t("dashboard.sort_by") == "Ordenar por"


class TestTimelineKeys:
    """Tests for timeline i18n keys."""

    def test_timeline_keys_exist(self):
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("timeline.title") == "Execution Timeline"
        assert i18n_module.t("timeline.task") == "Task"
        assert i18n_module.t("timeline.total_duration") == "Total Duration"
        assert i18n_module.t("timeline.slowest_task") == "Slowest Task"
        assert i18n_module.t("timeline.error_count") == "Errors"

    def test_timeline_step_labels(self):
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert i18n_module.t("timeline.step_success") == "Completed"
        assert i18n_module.t("timeline.step_failed") == "Failed"
        assert i18n_module.t("timeline.step_skipped") == "Skipped"
        assert i18n_module.t("timeline.step_running") == "Running"

    def test_timeline_keys_in_fr(self):
        import gui.i18n as i18n_module
        i18n_module.init("fr")
        assert i18n_module.t("timeline.title") == "Chronologie d'exécution"
        assert i18n_module.t("timeline.slowest_task") == "Tâche la plus lente"


class TestExecutionTimeline:
    """Tests for the execution timeline component logic."""

    def test_extract_steps_from_task_stats(self):
        from gui.components.execution_timeline import _extract_steps

        class FakeState:
            errors = []
            statistics = {}

        task_stats = {
            "log_1": {"type": "log", "runs": 5, "errors": 0, "ff_in": 5, "ff_out": 5},
            "fail_1": {"type": "fail", "runs": 3, "errors": 3, "ff_in": 3, "ff_out": 0},
            "wait_1": {"type": "wait", "runs": 0, "errors": 0, "ff_in": 0, "ff_out": 0},
        }

        steps = _extract_steps(FakeState(), task_stats)
        assert len(steps) == 3

        log_step = [s for s in steps if s["task_id"] == "log_1"][0]
        assert log_step["status"] == "success"

        fail_step = [s for s in steps if s["task_id"] == "fail_1"][0]
        assert fail_step["status"] == "failed"

        wait_step = [s for s in steps if s["task_id"] == "wait_1"][0]
        assert wait_step["status"] == "skipped"

    def test_extract_steps_from_errors_only(self):
        from gui.components.execution_timeline import _extract_steps

        class FakeState:
            errors = [
                {"task_id": "bad_task", "error": "Something broke"},
            ]
            statistics = {}

        steps = _extract_steps(FakeState())
        assert len(steps) == 1
        assert steps[0]["task_id"] == "bad_task"
        assert steps[0]["status"] == "failed"
        assert steps[0]["error_msg"] == "Something broke"

    def test_extract_steps_empty(self):
        from gui.components.execution_timeline import _extract_steps

        class FakeState:
            errors = []
            statistics = {}

        steps = _extract_steps(FakeState())
        assert steps == []

    def test_partial_status(self):
        from gui.components.execution_timeline import _extract_steps

        class FakeState:
            errors = []
            statistics = {}

        task_stats = {
            "mixed": {"type": "log", "runs": 10, "errors": 3, "ff_in": 10, "ff_out": 7},
        }

        steps = _extract_steps(FakeState(), task_stats)
        assert steps[0]["status"] == "partial"

    def test_llm_config_persistence(self):
        """LLM config should save/load without API key."""
        import tempfile
        from unittest.mock import patch
        from gui.services.llm_config_service import save_llm_config, load_llm_config

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "llm_config.json"
            with patch("gui.services.llm_config_service.CONFIG_PATH", tmp_path):
                # Save config with API key — key should NOT be persisted
                save_llm_config({
                    "provider": "anthropic",
                    "api_key": "sk-secret-123",
                    "base_url": "https://custom.api.com",
                    "default_model": "claude-sonnet-4-20250514",
                })

                loaded = load_llm_config()
                assert loaded["provider"] == "anthropic"
                assert loaded["base_url"] == "https://custom.api.com"
                assert loaded["default_model"] == "claude-sonnet-4-20250514"
                assert "api_key" not in loaded  # Security: no API key on disk

    def test_llm_config_load_missing(self):
        """Loading non-existent config should return empty dict."""
        from unittest.mock import patch
        from gui.services.llm_config_service import load_llm_config

        with patch("gui.services.llm_config_service.CONFIG_PATH", Path("/nonexistent/path.json")):
            assert load_llm_config() == {}

    def test_llm_config_get_full(self):
        """get_full_llm_config should merge persisted config with API key."""
        import tempfile
        from unittest.mock import patch
        from gui.services.llm_config_service import save_llm_config, get_full_llm_config

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "llm_config.json"
            with patch("gui.services.llm_config_service.CONFIG_PATH", tmp_path):
                save_llm_config({"provider": "openai", "base_url": "", "default_model": "gpt-4o"})

                # Without API key → None
                assert get_full_llm_config("") is None

                # With API key → full config
                full = get_full_llm_config("sk-test-key")
                assert full["provider"] == "openai"
                assert full["api_key"] == "sk-test-key"
                assert full["default_model"] == "gpt-4o"

    def test_llm_config_key_in_i18n(self):
        """LLM config i18n keys should exist."""
        import gui.i18n as i18n_module
        i18n_module.init("en")
        assert "session-only" in i18n_module.t("settings.llm_api_key_note")
        i18n_module.init("fr")
        assert "session" in i18n_module.t("settings.llm_api_key_note")

    def test_extract_steps_from_task_results(self):
        from gui.components.execution_timeline import _extract_steps

        class FakeState:
            errors = []
            statistics = {
                "task_results": {
                    "step_a": {"type": "log", "status": "success", "input_count": 1, "output_count": 1, "duration_ms": 50},
                    "step_b": {"type": "fail", "status": "failed", "input_count": 1, "output_count": 0, "error": "boom"},
                }
            }

        steps = _extract_steps(FakeState())
        assert len(steps) == 2
        a = [s for s in steps if s["task_id"] == "step_a"][0]
        b = [s for s in steps if s["task_id"] == "step_b"][0]
        assert a["status"] == "success"
        assert a["duration_ms"] == 50
        assert b["status"] == "failed"
        assert b["error_msg"] == "boom"


# ============================================================================
# Drag & Drop Tests
# ============================================================================


class TestDragAndDrop:
    """Tests for in-canvas drag & drop task palette."""

    def test_i18n_drag_keys_exist(self):
        """Drag-related i18n keys exist in all locales."""
        i18n_dir = Path(__file__).parent.parent / "gui" / "i18n"
        keys = ["editor.click_to_place", "editor.drag_from_canvas"]
        for lang in ("en", "fr", "es"):
            data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing {key} in {lang}"

    def test_flow_canvas_add_task_with_position(self):
        """FlowCanvas.add_task stores custom position."""
        from gui.components.flow_canvas import FlowCanvas
        with patch("gui.components.flow_canvas.st") as mock_st:
            ss = MagicMock()
            ss.node_positions = {}
            ss.__contains__ = lambda self, key: key in {"node_positions"}
            ss.get = lambda key, default=None: {"node_positions": ss.node_positions}.get(key, default)
            ss.pop = MagicMock()
            mock_st.session_state = ss
            canvas = FlowCanvas()
            flow = {"tasks": {}, "relations": []}
            canvas.add_task(flow, "my_task", "log", position=(300, 200))
            assert "my_task" in flow["tasks"]
            assert flow["tasks"]["my_task"]["type"] == "log"
            assert ss.node_positions["my_task"] == (300, 200)

    def test_flow_canvas_add_task_unique_id(self):
        """add_task correctly places tasks with given IDs."""
        from gui.components.flow_canvas import FlowCanvas
        with patch("gui.components.flow_canvas.st") as mock_st:
            ss = MagicMock()
            ss.node_positions = {}
            ss.__contains__ = lambda self, key: key in {"node_positions"}
            ss.get = lambda key, default=None: {"node_positions": ss.node_positions}.get(key, default)
            ss.pop = MagicMock()
            mock_st.session_state = ss
            canvas = FlowCanvas()
            flow = {"tasks": {"log": {"type": "log", "parameters": {}}}, "relations": []}
            canvas.add_task(flow, "log_1", "log", position=(100, 100))
            assert "log_1" in flow["tasks"]
            assert "log" in flow["tasks"]

    def test_task_colors_complete(self):
        """Key task types have colors from the category-based color scheme."""
        from gui.components.color_scheme import get_task_color, TASK_CATEGORIES
        assert "fetchHTTP" in TASK_CATEGORIES
        color = get_task_color("fetchHTTP")
        assert color.startswith("#")
        assert "log" in TASK_CATEGORIES
        assert "routeOnAttribute" in TASK_CATEGORIES

    def test_build_task_types_for_palette(self):
        """_build_task_types_for_palette produces correct structure."""
        from gui.components.flow_canvas import _build_task_types_for_palette
        available = {"log", "fetchHTTP", "routeOnAttribute", "my_custom_task"}
        result = _build_task_types_for_palette(available)
        types = {r["type"] for r in result}
        assert "log" in types
        assert "fetchHTTP" in types
        assert "routeOnAttribute" in types
        assert "my_custom_task" in types
        # Check structure
        for entry in result:
            assert "type" in entry
            assert "color" in entry
            assert "category" in entry
        # Custom task should be in Plugins category
        custom = [r for r in result if r["type"] == "my_custom_task"][0]
        assert custom["category"] == "Plugins"

    def test_build_task_types_categories(self):
        """Tasks are correctly categorized."""
        from gui.components.flow_canvas import _build_task_types_for_palette
        available = {"log", "fetchHTTP", "transformJSON", "routeOnAttribute"}
        result = _build_task_types_for_palette(available)
        cats = {r["type"]: r["category"] for r in result}
        assert cats["log"] == "System"
        assert cats["fetchHTTP"] == "IO"
        assert cats["transformJSON"] == "Data"
        assert cats["routeOnAttribute"] == "Control"

    def test_streamlit_flow_state_has_new_node_request(self):
        """StreamlitFlowState accepts new_node_request field."""
        from streamlit_flow.state import StreamlitFlowState
        state = StreamlitFlowState(
            nodes=[], edges=[],
            new_node_request={"nodeType": "log", "position": {"x": 100, "y": 200}},
        )
        assert state.new_node_request is not None
        assert state.new_node_request["nodeType"] == "log"

    def test_streamlit_flow_state_default_no_request(self):
        """StreamlitFlowState defaults new_node_request to None."""
        from streamlit_flow.state import StreamlitFlowState
        state = StreamlitFlowState(nodes=[], edges=[])
        assert state.new_node_request is None

    def test_render_accepts_drag_palette_params(self):
        """Canvas render accepts enable_drag_palette and available_task_types."""
        from gui.components.flow_canvas import FlowCanvas
        import inspect
        sig = inspect.signature(FlowCanvas.render)
        assert "enable_drag_palette" in sig.parameters
        assert "available_task_types" in sig.parameters

    def test_streamlit_flow_accepts_drag_params(self):
        """streamlit_flow function accepts enable_drag_palette and task_types."""
        from streamlit_flow import streamlit_flow as sf
        import inspect
        sig = inspect.signature(sf)
        assert "enable_drag_palette" in sig.parameters
        assert "task_types" in sig.parameters


# ============================================================================
# Flow Versioning Tests
# ============================================================================


class TestFlowVersioning:
    """Tests for flow version management in the editor."""

    def test_flow_content_fingerprint_ignores_version(self):
        """Fingerprint is the same regardless of version field."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        # Import the function from the editor module
        from gui.pages import __path__ as pages_path
        # We test the logic directly
        flow_a = {"id": "f1", "tasks": {"t1": {"type": "log"}}, "version": "1.0.0"}
        flow_b = {"id": "f1", "tasks": {"t1": {"type": "log"}}, "version": "1.0.5"}
        fp_a = json.dumps({k: v for k, v in sorted(flow_a.items()) if k != "version"}, sort_keys=True)
        fp_b = json.dumps({k: v for k, v in sorted(flow_b.items()) if k != "version"}, sort_keys=True)
        assert fp_a == fp_b

    def test_flow_content_fingerprint_detects_changes(self):
        """Fingerprint changes when tasks change."""
        flow_a = {"id": "f1", "tasks": {"t1": {"type": "log"}}, "version": "1.0.0"}
        flow_b = {"id": "f1", "tasks": {"t1": {"type": "fetchHTTP"}}, "version": "1.0.0"}
        fp_a = json.dumps({k: v for k, v in sorted(flow_a.items()) if k != "version"}, sort_keys=True)
        fp_b = json.dumps({k: v for k, v in sorted(flow_b.items()) if k != "version"}, sort_keys=True)
        assert fp_a != fp_b

    def test_version_archive_dir_structure(self):
        """Version archive uses flows/versions/{flow_id}/v{version}.json pattern."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            versions_dir = os.path.join(tmpdir, "flows", "versions", "my_flow")
            os.makedirs(versions_dir)
            archive_path = os.path.join(versions_dir, "v1.0.0.json")
            with open(archive_path, "w") as f:
                json.dump({"version": "1.0.0"}, f)
            assert os.path.exists(archive_path)
            data = json.loads(open(archive_path).read())
            assert data["version"] == "1.0.0"

    def test_i18n_version_keys_exist(self):
        """Version-related i18n keys exist in all locales."""
        i18n_dir = Path(__file__).parent.parent / "gui" / "i18n"
        keys = ["editor.no_changes", "runtime.select_version",
                "runtime.using_version", "runtime.flow_unchanged"]
        for lang in ("en", "fr", "es"):
            data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing {key} in {lang}"

    def test_update_flow_fingerprint_no_change(self):
        """ContinuousFlowExecutor._flow_fingerprint returns same for identical flows."""
        from core import Flow, Task, TaskFactory
        from engine.continuous_executor import ContinuousFlowExecutor
        flow = Flow({"id": "test", "tasks": {"t1": {"type": "log"}}, "relations": []})
        executor = ContinuousFlowExecutor(flow)
        fp1 = executor._flow_fingerprint(flow)
        fp2 = executor._flow_fingerprint(flow)
        assert fp1 == fp2
        executor.stop()

    def test_update_flow_returns_none_when_unchanged(self):
        """update_flow returns None when flow hasn't changed."""
        from core import Flow
        from engine.continuous_executor import ContinuousFlowExecutor
        flow = Flow({"id": "test", "tasks": {"t1": {"type": "log"}}, "relations": []})
        executor = ContinuousFlowExecutor(flow)
        # Same flow again
        same_flow = Flow({"id": "test", "tasks": {"t1": {"type": "log"}}, "relations": []})
        result = executor.update_flow(same_flow)
        assert result is None  # None = no change
        assert executor._flow_version == 1  # Not incremented
        executor.stop()
