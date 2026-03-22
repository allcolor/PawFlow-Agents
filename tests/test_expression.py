# Tests pour Expression Language et FlowParser

"""Tests unitaires pour le moteur d'expression et le FlowParser."""

import os
import unittest

# Import and register all tasks to trigger auto-registration
import tasks  # noqa: F401
tasks.register_all_tasks()

from core.expression import resolve_expression
from engine.parser import FlowParser
from core import TaskError


# ============================================================================
# TestExpression
# ============================================================================

class TestExpression(unittest.TestCase):
    """Tests pour le moteur de resolution d'expressions ${...}."""

    def test_no_expression(self):
        """Une string sans ${} retourne la meme string."""
        self.assertEqual(resolve_expression("Hello World"), "Hello World")

    def test_resolve_attribute(self):
        """${filename} resolu depuis les attributs."""
        result = resolve_expression("${filename}", attributes={"filename": "test.txt"})
        self.assertEqual(result, "test.txt")

    def test_resolve_multiple(self):
        """Plusieurs ${} resolus dans la meme string."""
        result = resolve_expression(
            "File ${filename} is ${fileSize} bytes",
            attributes={"filename": "test.txt", "fileSize": "100"}
        )
        self.assertEqual(result, "File test.txt is 100 bytes")

    def test_unresolved_kept(self):
        """${unknown} sans attribut correspondant reste tel quel."""
        result = resolve_expression("${unknown}", attributes={"filename": "test.txt"})
        self.assertEqual(result, "${unknown}")

    def test_flow_parameters(self):
        """${flow.parameters.env} resolu depuis les parametres."""
        result = resolve_expression("${flow.parameters.env}", parameters={"env": "prod"})
        self.assertEqual(result, "prod")

    def test_env_variable(self):
        """${env.PATH} resolu depuis l'environnement."""
        result = resolve_expression("${env.PATH}")
        self.assertEqual(result, os.environ.get("PATH", ""))

    def test_mixed(self):
        """Attributs + parametres flow resolus ensemble."""
        result = resolve_expression(
            "${filename} in ${flow.parameters.dir}",
            attributes={"filename": "test.txt"},
            parameters={"dir": "/home/user"}
        )
        self.assertEqual(result, "test.txt in /home/user")

    def test_empty_string(self):
        """String vide retourne string vide."""
        self.assertEqual(resolve_expression(""), "")

    def test_no_args(self):
        """Sans attributs ni parametres, les expressions restent."""
        self.assertEqual(resolve_expression("${foo}"), "${foo}")


class TestCascadeResolution(unittest.TestCase):
    """Tests for cascading expression resolution."""

    def setUp(self):
        import json, tempfile, shutil
        self._orig_global = None
        self._user_dir = None
        # Save original global params
        from core.expression import _GLOBAL_PARAMS_FILE, _USER_CONFIG_DIR
        self._global_file = _GLOBAL_PARAMS_FILE
        self._user_config_dir = _USER_CONFIG_DIR
        if self._global_file.exists():
            self._orig_global = self._global_file.read_text(encoding="utf-8")
        # Write test global params
        self._global_file.parent.mkdir(parents=True, exist_ok=True)
        self._global_file.write_text(json.dumps({
            "shared_key": "from_global",
            "only_global": "global_value",
            "cascade_target": "final_value",
        }), encoding="utf-8")
        # Write test user params
        self._user_dir = self._user_config_dir / "testuser"
        self._user_dir.mkdir(parents=True, exist_ok=True)
        (self._user_dir / "parameters.json").write_text(json.dumps({
            "shared_key": "from_user",
            "user_only": "user_value",
            "indirect": "${global.cascade_target}",
        }), encoding="utf-8")

    def tearDown(self):
        import shutil
        # Restore global params
        if self._orig_global is not None:
            self._global_file.write_text(self._orig_global, encoding="utf-8")
        # Cleanup user dir
        if self._user_dir and self._user_dir.exists():
            shutil.rmtree(self._user_dir)

    def test_user_found(self):
        """${user.X} resolves from user params when present."""
        result = resolve_expression("${user.shared_key}", owner="testuser")
        self.assertEqual(result, "from_user")

    def test_user_cascades_to_global(self):
        """${user.X} falls back to global when not in user params."""
        result = resolve_expression("${user.only_global}", owner="testuser")
        self.assertEqual(result, "global_value")

    def test_user_no_owner_cascades_to_global(self):
        """${user.X} without owner still cascades to global."""
        result = resolve_expression("${user.shared_key}")
        self.assertEqual(result, "from_global")

    def test_global_cascades(self):
        """${global.X} cascades: flow → conv → user → global."""
        # No user/flow context → resolves from global
        result = resolve_expression("${global.only_global}")
        self.assertEqual(result, "global_value")
        # With user context → user wins over global
        result = resolve_expression("${global.shared_key}", owner="testuser")
        self.assertEqual(result, "from_user")

    def test_global_important(self):
        """${global.X:!important} resolves from global ONLY."""
        result = resolve_expression("${global.shared_key:!important}", owner="testuser")
        self.assertEqual(result, "from_global")

    def test_user_important(self):
        """${user.X:!important} resolves from user ONLY."""
        result = resolve_expression("${user.shared_key:!important}", owner="testuser")
        self.assertEqual(result, "from_user")
        # Key only in global → unresolved with !important on user
        result = resolve_expression("${user.only_global:!important}", owner="testuser")
        self.assertEqual(result, "${user.only_global:!important}")

    def test_flow_params_cascade_to_user(self):
        """${flow.parameters.X} cascades to user when not in flow params."""
        result = resolve_expression(
            "${flow.parameters.shared_key}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "from_user")

    def test_flow_params_cascade_to_global(self):
        """${flow.parameters.X} cascades to global when not in flow or user."""
        result = resolve_expression(
            "${flow.parameters.only_global}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "global_value")

    def test_flow_params_priority(self):
        """${flow.parameters.X} prefers flow params over user/global."""
        result = resolve_expression(
            "${flow.parameters.shared_key}",
            parameters={"shared_key": "from_flow"},
            owner="testuser",
        )
        self.assertEqual(result, "from_flow")

    def test_flow_shorthand(self):
        """${flow.X} works as alias for ${flow.parameters.X}."""
        result = resolve_expression(
            "${flow.shared_key}",
            parameters={"shared_key": "from_flow"},
            owner="testuser",
        )
        self.assertEqual(result, "from_flow")

    def test_flow_shorthand_cascades(self):
        """${flow.X} cascades to user/global when not in flow params."""
        result = resolve_expression(
            "${flow.only_global}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "global_value")

    def test_all_prefixes_cascade_same(self):
        """All prefixes cascade identically: flow → conv → user → global."""
        # With flow param set, all prefixes find it
        for prefix in ["flow.", "flow.parameters.", "conv.", "user.", "global."]:
            result = resolve_expression(
                f"${{{prefix}shared_key}}",
                parameters={"shared_key": "from_flow"},
                owner="testuser",
            )
            self.assertEqual(result, "from_flow",
                             f"${{{prefix}shared_key}} should find flow param")

    def test_flow_important(self):
        """${flow.X:!important} resolves from flow params ONLY."""
        result = resolve_expression(
            "${flow.shared_key:!important}",
            parameters={"shared_key": "from_flow"},
            owner="testuser",
        )
        self.assertEqual(result, "from_flow")
        # Key only in user → unresolved with !important on flow
        result = resolve_expression(
            "${flow.user_only:!important}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "${flow.user_only:!important}")

    def test_recursive_resolution(self):
        """Resolved value containing ${...} gets resolved again."""
        result = resolve_expression("${user.indirect}", owner="testuser")
        self.assertEqual(result, "final_value")

    def test_recursion_limit(self):
        """Recursion stops at depth 10 to prevent infinite loops."""
        # ${user.indirect} → ${global.cascade_target} → final_value (depth 2)
        # This should work fine, just testing the mechanism works
        result = resolve_expression("${user.indirect}", owner="testuser")
        self.assertEqual(result, "final_value")

    def test_user_overrides_global(self):
        """When both user and global have the key, user wins."""
        result = resolve_expression("${user.shared_key}", owner="testuser")
        self.assertEqual(result, "from_user")


# ============================================================================
# TestFlowParser
# ============================================================================

class TestFlowParser(unittest.TestCase):
    """Tests pour le parser de flux JSON."""

    def test_parse_simple_flow(self):
        """Parser un dict avec 1 task log."""
        config = {
            "name": "Test Flow",
            "tasks": {
                "log1": {"type": "log", "parameters": {"message": "Test", "level": "INFO"}}
            },
            "relations": []
        }
        flow = FlowParser.parse(config)
        self.assertIn("log1", flow.tasks)
        self.assertEqual(flow.tasks["log1"].get_type(), "log")

    def test_parse_with_relations(self):
        """Parser avec 2 tasks et 1 relation."""
        config = {
            "name": "Test Relations",
            "tasks": {
                "task1": {"type": "log", "parameters": {"message": "1"}},
                "task2": {"type": "log", "parameters": {"message": "2"}},
            },
            "relations": [{"from": "task1", "to": "task2"}]
        }
        flow = FlowParser.parse(config)
        self.assertEqual(len(flow.tasks), 2)
        self.assertEqual(len(flow.relations), 1)
        self.assertEqual(flow.relations[0]["from"], "task1")
        self.assertEqual(flow.relations[0]["to"], "task2")

    def test_parse_from_file(self):
        """Parser flows/exemple_flux.json."""
        flow = FlowParser.parse_from_file("flows/exemple_flux.json")
        self.assertEqual(len(flow.tasks), 3)
        self.assertIn("log1", flow.tasks)
        self.assertIn("replace1", flow.tasks)
        self.assertIn("log2", flow.tasks)
        self.assertEqual(len(flow.relations), 2)

    def test_parse_unknown_task_type(self):
        """Type de task inconnu leve TaskError."""
        config = {
            "name": "Bad Flow",
            "tasks": {
                "bad": {"type": "nonexistent_task", "parameters": {}}
            },
            "relations": []
        }
        with self.assertRaises(TaskError):
            FlowParser.parse(config)

    def test_parse_from_json_string(self):
        """Parser depuis une chaine JSON."""
        json_string = '{"name": "JSON Flow", "tasks": {"a": {"type": "log", "parameters": {"message": "hi"}}}, "relations": []}'
        flow = FlowParser.parse_from_json(json_string)
        self.assertEqual(flow.name, "JSON Flow")
        self.assertIn("a", flow.tasks)

    def test_parse_empty_tasks(self):
        """Parser avec aucune task."""
        config = {"name": "Empty", "tasks": {}, "relations": []}
        flow = FlowParser.parse(config)
        self.assertEqual(len(flow.tasks), 0)

    def test_parse_with_variables(self):
        """Parser avec des variables."""
        config = {
            "name": "Vars Flow",
            "tasks": {},
            "relations": [],
            "variables": {"env": "production"}
        }
        flow = FlowParser.parse(config)
        self.assertEqual(flow.variables["env"], "production")

    def test_parse_pipeline_flow(self):
        """Parser le pipeline d'exemple complet."""
        flow = FlowParser.parse_from_file("flows/example_pipeline.json")
        self.assertEqual(len(flow.tasks), 4)
        self.assertEqual(len(flow.relations), 3)
        self.assertEqual(flow.tasks["read_files"].get_type(), "getFile")
        self.assertEqual(flow.tasks["write_output"].get_type(), "putFile")


if __name__ == "__main__":
    unittest.main()
