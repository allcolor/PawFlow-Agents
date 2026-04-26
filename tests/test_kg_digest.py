"""Tests for kg_digest.build_kg_digest — system-prompt KG summary."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.knowledge_graph import KnowledgeGraph
from core.kg_digest import build_kg_digest


class TestKgDigest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Patch for_user to land KGs in the tmp dir.
        self._patcher = patch.object(
            KnowledgeGraph, "for_user",
            classmethod(lambda cls, user_id, store_dir="":
                        cls(str(Path(self._tmp.name) / f"{user_id}.json"))))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_empty_returns_empty(self):
        self.assertEqual(build_kg_digest("u"), "")

    def test_no_user_id(self):
        self.assertEqual(build_kg_digest(""), "")

    def test_filters_lone_nodes_from_god_section(self):
        # Single triple: each node has 1 connection. <2 threshold → no
        # 'Most connected' line. But the triple still surfaces under
        # 'Recent facts'.
        kg = KnowledgeGraph.for_user("u")
        kg.add_triple("alice", "is_a", "engineer", source="test")
        out = build_kg_digest("u")
        self.assertNotIn("Most connected", out)
        self.assertIn("Recent facts", out)
        self.assertIn("alice is_a engineer", out)

    def test_god_nodes_appear_with_enough_connections(self):
        kg = KnowledgeGraph.for_user("u")
        # alice gets 3 connections, others get 1 each
        kg.add_triple("alice", "works_at", "acme", source="t")
        kg.add_triple("alice", "is_a", "cto", source="t")
        kg.add_triple("alice", "located_in", "paris", source="t")
        out = build_kg_digest("u")
        self.assertIn("Most connected", out)
        self.assertIn("alice", out)

    def test_excludes_expired_facts(self):
        kg = KnowledgeGraph.for_user("u")
        kg.add_triple("alice", "works_at", "oldco", source="t")
        kg.invalidate("alice", "works_at", "oldco", ended="2020-01-01")
        kg.add_triple("alice", "works_at", "newco", source="t")
        out = build_kg_digest("u")
        self.assertIn("newco", out)
        self.assertNotIn("oldco", out)

    def test_cap_chars(self):
        kg = KnowledgeGraph.for_user("u")
        for i in range(20):
            kg.add_triple(f"e{i}", "is_a", f"thing{i}" * 5, source="t")
        out = build_kg_digest("u", max_chars=200)
        self.assertLessEqual(len(out), 200)


if __name__ == "__main__":
    unittest.main()
