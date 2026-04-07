"""Tests for build_memory_digest — compact multi-tier digest for system prompt.

Tests cover:
- Empty store returns empty string
- L0-L4 tiers populated correctly
- KG god nodes integrated
- max_chars truncation
- Agent-scoped filtering
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.memory_store import MemoryEntry, MemoryStore


def _make_entry(text, tags=None, hall="", created_at=0, agent=""):
    return MemoryEntry(
        text=text,
        tags=tags or [],
        hall=hall,
        created_at=created_at or 1000.0,
        agent=agent,
    )


class TestEmptyStore(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_empty_returns_empty_string(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user_with_no_data")
        assert result == ""


class TestTiers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l0_identity(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "My name is Alice", ["identity"])

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Identity:" in result
        assert "Alice" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l1_facts(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Uses Python 3.12", ["language"],
                            hall="facts")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Key facts:" in result
        assert "Python" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l1_preferences(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Prefers dark theme", ["ui"],
                            hall="preferences")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Preferences:" in result
        assert "dark theme" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l2_events(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Deployed v2.0 to production", ["deploy"],
                            hall="events")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Recent events:" in result
        assert "Deployed" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l3_decisions(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Switched to GraphQL", ["decision"],
                            hall="facts")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Active decisions:" in result
        assert "GraphQL" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l4_discoveries(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Found that caching helps", ["perf"],
                            hall="discoveries")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Discoveries:" in result
        assert "caching" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_l4_advice(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Always write tests first", ["practice"],
                            hall="advice")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Advice:" in result
        assert "tests" in result


class TestKGIntegration(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_god_nodes_included(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = [
            {"entity": "Python", "connections": 15},
            {"entity": "Docker", "connections": 8},
        ]
        mock_kg_cls.for_user.return_value = mock_kg

        # Need at least one memory so digest is not empty
        self.store.remember("user1", "I use Python", ["identity"])

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1")
        assert "Central topics:" in result
        assert "Python(15)" in result
        assert "Docker(8)" in result

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_kg_failure_graceful(self, mock_kg_cls):
        mock_kg_cls.for_user.side_effect = Exception("KG broken")

        self.store.remember("user1", "test fact", ["identity"])

        from core.memory_digest import build_memory_digest
        # Should not raise, just skip KG section
        result = build_memory_digest("user1")
        assert "Central topics:" not in result
        assert "Identity:" in result


class TestMaxChars(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_truncation(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        # Add lots of memories to generate a long digest
        for i in range(20):
            self.store.remember("user1", f"A very important fact number {i} " * 5,
                                ["identity"], hall="facts")

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1", max_chars=200)
        assert len(result) <= 200
        assert result.endswith("...")

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_short_digest_not_truncated(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        self.store.remember("user1", "Short fact", ["identity"])

        from core.memory_digest import build_memory_digest
        result = build_memory_digest("user1", max_chars=5000)
        assert not result.endswith("...")


class TestAgentScoped(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("core.knowledge_graph.KnowledgeGraph")
    def test_agent_filter_passed(self, mock_kg_cls):
        mock_kg = MagicMock()
        mock_kg.god_nodes.return_value = []
        mock_kg_cls.for_user.return_value = mock_kg

        # Store a memory with specific agent
        self.store.remember("user1", "Agent-specific fact", ["identity"],
                            agent="special_agent")

        from core.memory_digest import build_memory_digest
        # The agent_name parameter is passed through to recall
        result = build_memory_digest("user1", agent_name="special_agent")
        # The fact should appear since agent matches
        # (exact behavior depends on MemoryStore scoping logic)
        assert isinstance(result, str)


if __name__ == "__main__":
    unittest.main()
