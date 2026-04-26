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
                {"id": "a", "label": "foo()", "language": "python",
                 "source_file": "a.py"},
                {"id": "b", "label": "bar()", "language": "python",
                 "source_file": "b.py"},
                {"id": "c", "label": "Baz", "language": "javascript",
                 "source_file": "c.js"},
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
        self.assertIn("Top files", out)
        self.assertIn("a.py", out)
        self.assertIn("God nodes", out)

    def test_god_nodes_use_label(self):
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": "node_x", "label": "my_function()",
                 "source_file": "x.py"},
                {"id": "node_y", "label": "helper()",
                 "source_file": "y.py"},
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

    def test_languages_inferred_from_extension(self):
        # No language metadata on nodes — the digest should derive it
        # from the source_file extension instead of saying 'unknown'.
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": "a", "label": "f", "source_file": "core/a.py"},
                {"id": "b", "label": "g", "source_file": "ui/b.ts"},
            ],
            "edges": [{"source": "a", "target": "b"}],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("python (1)", out)
        self.assertIn("typescript (1)", out)
        self.assertNotIn("unknown", out)

    def test_builtin_noise_filtered_from_god_nodes(self):
        # `.get` and `str` dominate the connection count but are noise.
        # The actual project node `MyClass` (lower count) should still
        # surface as the top displayed god node.
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": ".get", "label": ".get", "source_file": ""},
                {"id": "str", "label": "str", "source_file": ""},
                {"id": "MyClass", "label": "MyClass",
                 "source_file": "core/x.py"},
                {"id": "helper", "label": "helper",
                 "source_file": "core/y.py"},
            ],
            "edges": [
                # .get hit 100 times — should be filtered out
                *[{"source": "x" + str(i), "target": ".get"}
                  for i in range(100)],
                # str hit 50 times — also filtered
                *[{"source": "y" + str(i), "target": "str"}
                  for i in range(50)],
                # MyClass hit 5 times — the real signal
                *[{"source": "helper", "target": "MyClass"}
                  for i in range(5)],
            ],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("MyClass", out)
        # Builtin noise should NOT appear in the god-nodes section.
        # (We accept they may appear inside Top files if the file
        # itself is named that, but that's unrelated.)
        god_section = out.split("God nodes:", 1)[-1] if "God nodes:" in out else ""
        self.assertNotIn(".get", god_section)
        self.assertNotIn("str (", god_section)

    def test_top_files_section(self):
        # File a.py has 3 entities, b.py has 2, c.py has 1
        self._seed_graph("u", "c", {
            "nodes": [
                {"id": "a1", "label": "one", "source_file": "a.py"},
                {"id": "a2", "label": "two", "source_file": "a.py"},
                {"id": "a3", "label": "three", "source_file": "a.py"},
                {"id": "b1", "label": "four", "source_file": "b.py"},
                {"id": "b2", "label": "five", "source_file": "b.py"},
                {"id": "c1", "label": "six", "source_file": "c.py"},
            ],
            "edges": [{"source": "a1", "target": "b1"}],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("Top files: a.py (3)", out)
        self.assertIn("b.py (2)", out)

    def test_unknown_languages_default(self):
        self._seed_graph("u", "c", {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"source": "a", "target": "b"}],
        })
        out = build_project_graph_digest("u", "c")
        self.assertIn("Languages: unknown", out)


if __name__ == "__main__":
    unittest.main()
