# Tests pour Expression Language et FlowParser

"""Tests unitaires pour le moteur d'expression et le FlowParser."""

import os
import unittest

# Import des tasks pour declencher l'auto-registration
import tasks  # noqa: F401

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
