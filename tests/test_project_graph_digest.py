"""Tests for project_graph_digest.build_project_graph_digest."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.project_graph import ProjectGraph
from core.project_graph_digest import build_project_graph_digest


class TestProjectGraphDigest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        ProjectGraph._instances = {}

    def tearDown(self):
        ProjectGraph._instances = {}
        self._tmp.cleanup()

    def _seed_graph(self, user, conv, payload):
        path = Path(self._tmp.name) / user / conv / "graph.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        # Force ProjectGraph.for_conversation to land here
        ProjectGraph._instances[f"{user}::{conv}"] = ProjectGraph(str(path))

    def test_no_graph_returns_empty(self):
        # Conv has no graph file at all
        self.assertEqual(build_project_graph_digest("u", "c"), "")

    def test_empty_graph_returns_empty(self):
        self._seed_graph("u", "c", {"nodes": [], "edges": []})
        self.assertEqual(build_project_graph_digest("u", "c"), "")

    def test_no_user_or_conv(self):
        self.assertEqual(build_project_graph_digest("", "c"), "")
        self.assertEqual(build_project_graph_digest("u", ""), "")

    def test_basic_summary(self):
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": "a", "label": "foo()", "language": "python"},
                {"id": "b", "label": "bar()", "language": "python"},
                {"id": "c", "label": "Baz", "language": "javascript"},
            ],
            "edges": [
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c"},
                {"source": "a", "target": "c"},
            ],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("Codebase indexed: 3 entities, 3 edges", out)
        self.assertIn("python (2)", out)
        self.assertIn("javascript (1)", out)
        self.assertIn("God nodes", out)

    def test_god_nodes_use_label(self):
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": "node_x", "label": "my_function()"},
                {"id": "node_y", "label": "helper()"},
            ],
            "edges": [
                {"source": "node_x", "target": "node_y"},
                {"source": "node_x", "target": "node_y"},
            ],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("my_function()", out)
        # The id should NOT leak into the visible god-nodes section
        # when a label is available.
        self.assertNotIn("node_x (", out)

    def test_unknown_languages_default(self):
        self._seed_graph("u", "c", {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"source": "a", "target": "b"}],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("Languages: unknown", out)


if __name__ == "__main__":
    unittest.main()
