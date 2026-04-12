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
        result = resolve_expression("${filename}", parameters={"filename": "test.txt"})
        self.assertEqual(result, "test.txt")

    def test_resolve_multiple(self):
        """Plusieurs ${} resolus dans la meme string."""
        result = resolve_expression(
            "File ${filename} is ${fileSize} bytes",
            parameters={"filename": "test.txt", "fileSize": "100"}
        )
        self.assertEqual(result, "File test.txt is 100 bytes")

    def test_unresolved_kept(self):
        """${unknown} sans attribut correspondant reste tel quel."""
        result = resolve_expression("${unknown}", parameters={"filename": "test.txt"})
        self.assertEqual(result, "${unknown}")

    def test_flow_parameters(self):
        """${key} resolu depuis les parametres flow."""
        result = resolve_expression("${env}", parameters={"env": "prod"})
        self.assertEqual(result, "prod")

    def test_env_variable(self):
        """${PATH:!important(env)} resolu depuis l'environnement."""
        result = resolve_expression("${PATH:!important(env)}")
        self.assertEqual(result, os.environ.get("PATH", ""))

    def test_mixed(self):
        """Attributs + parametres flow resolus ensemble."""
        result = resolve_expression(
            "${filename} in ${dir}",
            parameters={"filename": "test.txt", "dir": "/home/user"}
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
        from core.paths import GLOBAL_PARAMS_FILE, USER_CONFIG_DIR
        self._global_file = GLOBAL_PARAMS_FILE
        self._user_config_dir = USER_CONFIG_DIR
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
            "indirect": "${cascade_target}",
        }), encoding="utf-8")

    def tearDown(self):
        import shutil
        # Restore global params
        if self._orig_global is not None:
            self._global_file.write_text(self._orig_global, encoding="utf-8")
        # Cleanup user dir
        if self._user_dir and self._user_dir.exists():
            shutil.rmtree(self._user_dir)

    def test_cascade_with_owner(self):
        """${key} with owner resolves from user params first."""
        result = resolve_expression("${shared_key}", owner="testuser")
        self.assertEqual(result, "from_user")

    def test_cascade_to_global(self):
        """${key} falls back to global when not in user params."""
        result = resolve_expression("${only_global}", owner="testuser")
        self.assertEqual(result, "global_value")

    def test_cascade_no_owner(self):
        """${key} without owner cascades to global."""
        result = resolve_expression("${shared_key}")
        self.assertEqual(result, "from_global")

    def test_important_global(self):
        """${key:!important(global)} resolves from global ONLY."""
        result = resolve_expression("${shared_key:!important(global)}", owner="testuser")
        self.assertEqual(result, "from_global")

    def test_important_user(self):
        """${key:!important(user)} resolves from user ONLY."""
        result = resolve_expression("${shared_key:!important(user)}", owner="testuser")
        self.assertEqual(result, "from_user")
        # Key only in global → unresolved with !important(user)
        result = resolve_expression("${only_global:!important(user)}", owner="testuser")
        self.assertEqual(result, "${only_global:!important(user)}")

    def test_flow_params_cascade_to_user(self):
        """${key} cascades to user when not in flow params."""
        result = resolve_expression(
            "${shared_key}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "from_user")

    def test_flow_params_cascade_to_global(self):
        """${key} cascades to global when not in flow or user."""
        result = resolve_expression(
            "${only_global}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "global_value")

    def test_flow_params_priority(self):
        """${key} prefers flow params over user/global."""
        result = resolve_expression(
            "${shared_key}",
            parameters={"shared_key": "from_flow"},
            owner="testuser",
        )
        self.assertEqual(result, "from_flow")

    def test_important_flow(self):
        """${key:!important(flow)} resolves from flow params ONLY."""
        result = resolve_expression(
            "${shared_key:!important(flow)}",
            parameters={"shared_key": "from_flow"},
            owner="testuser",
        )
        self.assertEqual(result, "from_flow")
        # Key only in user → unresolved with !important(flow)
        result = resolve_expression(
            "${user_only:!important(flow)}",
            parameters={},
            owner="testuser",
        )
        self.assertEqual(result, "${user_only:!important(flow)}")

    def test_recursive_resolution(self):
        """Resolved value containing ${...} gets resolved again."""
        # user indirect = "${cascade_target}" → resolves to "final_value"
        result = resolve_expression("${indirect}", owner="testuser")
        self.assertEqual(result, "final_value")

    def test_recursion_limit(self):
        """Recursion stops at depth 10 to prevent infinite loops."""
        # ${indirect} → ${cascade_target} → final_value (depth 2)
        result = resolve_expression("${indirect}", owner="testuser")
        self.assertEqual(result, "final_value")

    def test_user_overrides_global(self):
        """When both user and global have the key, user wins."""
        result = resolve_expression("${shared_key}", owner="testuser")
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
        flow = FlowParser.parse_from_file("data/repository/flows/global/default/exemple_flux/versions/1.0.0.json")
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
        flow = FlowParser.parse_from_file("data/repository/flows/global/default/example_pipeline/versions/1.0.0.json")
        self.assertEqual(len(flow.tasks), 4)
        self.assertEqual(len(flow.relations), 3)
        self.assertEqual(flow.tasks["read_files"].get_type(), "getFile")
        self.assertEqual(flow.tasks["write_output"].get_type(), "putFile")


if __name__ == "__main__":
    unittest.main()
