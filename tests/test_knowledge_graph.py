"""Tests for KnowledgeGraph — temporal entity-relationship graph.

Tests cover:
- add_triple (basic, duplicate, contradiction, confidence handling)
- query_entity (outgoing, incoming, both, temporal as_of)
- invalidate (mark fact expired)
- timeline (chronological, entity filter)
- stats (entity count, triple count, relationship types)
- query_graph (BFS, DFS, seed matching)
- god_nodes (degree ranking)
- for_user factory (path generation)
- Persistence (save/load)
- Confidence handling (string labels and float conversion)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.knowledge_graph import KnowledgeGraph


class TestAddTriple(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_add(self):
        result = self.kg.add_triple("Alice", "works_at", "Acme")
        assert result["status"] == "added"
        assert result["triple_id"]
        assert result["contradictions"] == []

    def test_adds_entities(self):
        self.kg.add_triple("Alice", "knows", "Bob")
        s = self.kg.stats()
        assert s["entities"] == 2

    def test_duplicate_detection(self):
        self.kg.add_triple("Alice", "works_at", "Acme")
        result = self.kg.add_triple("Alice", "works_at", "Acme")
        assert result["status"] == "duplicate"

    def test_contradiction_detection(self):
        self.kg.add_triple("Alice", "works_at", "Acme")
        result = self.kg.add_triple("Alice", "works_at", "Globex")
        assert result["status"] == "added"
        assert "Acme" in result["contradictions"]

    def test_duplicate_returns_contradictions_too(self):
        # First add two contradicting facts
        self.kg.add_triple("Alice", "lives_in", "Paris")
        self.kg.add_triple("Alice", "lives_in", "London")
        # Now add duplicate of the first — should report London as contradiction
        result = self.kg.add_triple("Alice", "lives_in", "Paris")
        assert result["status"] == "duplicate"
        assert "London" in result["contradictions"]


class TestConfidence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_string_extracted(self):
        self.kg.add_triple("A", "r", "B", confidence="EXTRACTED")
        t = self.kg._triples[0]
        assert t["confidence"] == "EXTRACTED"
        assert t["confidence_score"] == 1.0

    def test_string_inferred(self):
        self.kg.add_triple("A", "r", "B", confidence="INFERRED")
        t = self.kg._triples[0]
        assert t["confidence"] == "INFERRED"
        assert t["confidence_score"] == 0.7

    def test_string_ambiguous(self):
        self.kg.add_triple("A", "r", "B", confidence="AMBIGUOUS")
        t = self.kg._triples[0]
        assert t["confidence"] == "AMBIGUOUS"
        assert t["confidence_score"] == 0.3

    def test_string_invalid_defaults_to_extracted(self):
        self.kg.add_triple("A", "r", "B", confidence="BOGUS")
        t = self.kg._triples[0]
        assert t["confidence"] == "EXTRACTED"

    def test_float_high_becomes_extracted(self):
        self.kg.add_triple("A", "r", "B", confidence=0.95)
        t = self.kg._triples[0]
        assert t["confidence"] == "EXTRACTED"
        assert t["confidence_score"] == 0.95

    def test_float_mid_becomes_inferred(self):
        self.kg.add_triple("A", "r", "B", confidence=0.6)
        t = self.kg._triples[0]
        assert t["confidence"] == "INFERRED"

    def test_float_low_becomes_ambiguous(self):
        self.kg.add_triple("A", "r", "B", confidence=0.2)
        t = self.kg._triples[0]
        assert t["confidence"] == "AMBIGUOUS"


class TestQueryEntity(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)
        self.kg.add_triple("Alice", "works_at", "Acme")
        self.kg.add_triple("Bob", "knows", "Alice")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_outgoing(self):
        results = self.kg.query_entity("Alice", direction="outgoing")
        assert len(results) == 1
        assert results[0]["predicate"] == "works_at"

    def test_incoming(self):
        results = self.kg.query_entity("Alice", direction="incoming")
        assert len(results) == 1
        assert results[0]["subject"] == "Bob"

    def test_both(self):
        results = self.kg.query_entity("Alice", direction="both")
        assert len(results) == 2

    def test_temporal_as_of(self):
        self.kg.add_triple("Alice", "lives_in", "Paris", valid_from="2020-01-01")
        self.kg.invalidate("Alice", "lives_in", "Paris", ended="2023-06-01")
        self.kg.add_triple("Alice", "lives_in", "London", valid_from="2023-06-01")

        # Query as of 2022 — should see Paris
        results = self.kg.query_entity("Alice", as_of="2022-01-01")
        places = [r["object"] for r in results if r["predicate"] == "lives_in"]
        assert "Paris" in places
        assert "London" not in places

        # Query as of 2024 — should see London
        results = self.kg.query_entity("Alice", as_of="2024-01-01")
        places = [r["object"] for r in results if r["predicate"] == "lives_in"]
        assert "London" in places

    def test_current_flag(self):
        self.kg.add_triple("X", "rel", "Y")
        results = self.kg.query_entity("X")
        assert results[0]["current"] is True

    def test_nonexistent_entity(self):
        results = self.kg.query_entity("Nobody")
        assert results == []


class TestInvalidate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_invalidate_marks_expired(self):
        self.kg.add_triple("A", "r", "B")
        count = self.kg.invalidate("A", "r", "B", ended="2024-01-01")
        assert count == 1
        results = self.kg.query_entity("A")
        assert results[0]["current"] is False

    def test_invalidate_nonexistent_returns_zero(self):
        count = self.kg.invalidate("X", "y", "Z")
        assert count == 0

    def test_invalidate_already_expired_returns_zero(self):
        self.kg.add_triple("A", "r", "B")
        self.kg.invalidate("A", "r", "B", ended="2024-01-01")
        count = self.kg.invalidate("A", "r", "B", ended="2025-01-01")
        assert count == 0


class TestTimeline(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chronological_order(self):
        self.kg.add_triple("A", "r1", "B")
        self.kg.add_triple("A", "r2", "C")
        tl = self.kg.timeline()
        # Newest first (by extracted_at)
        assert tl[0]["predicate"] == "r2"

    def test_entity_filter(self):
        self.kg.add_triple("A", "r", "B")
        self.kg.add_triple("X", "r", "Y")
        tl = self.kg.timeline(entity="A")
        assert len(tl) == 1
        assert tl[0]["subject"] == "A"

    def test_limit(self):
        for i in range(10):
            self.kg.add_triple(f"E{i}", "r", "target")
        tl = self.kg.timeline(limit=3)
        assert len(tl) == 3


class TestStats(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_stats(self):
        s = self.kg.stats()
        assert s["entities"] == 0
        assert s["triples"] == 0
        assert s["current_facts"] == 0
        assert s["expired_facts"] == 0

    def test_populated_stats(self):
        self.kg.add_triple("A", "works_at", "B")
        self.kg.add_triple("A", "knows", "C")
        self.kg.invalidate("A", "works_at", "B")
        s = self.kg.stats()
        assert s["entities"] == 3
        assert s["triples"] == 2
        assert s["current_facts"] == 1
        assert s["expired_facts"] == 1
        assert "works_at" in s["relationship_types"]
        assert "knows" in s["relationship_types"]


class TestQueryGraph(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)
        # Build a small graph: A -> B -> C -> D
        self.kg.add_triple("Alice", "knows", "Bob")
        self.kg.add_triple("Bob", "knows", "Charlie")
        self.kg.add_triple("Charlie", "knows", "Dave")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_bfs_finds_neighbors(self):
        results = self.kg.query_graph("Alice", mode="bfs", depth=2)
        subjects_and_objects = set()
        for r in results:
            subjects_and_objects.add(r["subject"])
            subjects_and_objects.add(r["object"])
        assert "Alice" in subjects_and_objects
        assert "Bob" in subjects_and_objects

    def test_dfs_traces_path(self):
        results = self.kg.query_graph("Alice", mode="dfs", depth=5)
        assert len(results) >= 1

    def test_no_match_returns_empty(self):
        results = self.kg.query_graph("zzzznotfound")
        assert results == []

    def test_seed_matching_partial(self):
        # "ali" should match "Alice"
        results = self.kg.query_graph("ali", mode="bfs")
        assert len(results) > 0

    def test_expired_triples_excluded(self):
        self.kg.invalidate("Alice", "knows", "Bob")
        results = self.kg.query_graph("Alice", mode="bfs")
        # Alice->Bob is expired, so traversal from Alice finds nothing
        assert len(results) == 0


class TestGodNodes(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")
        self.kg = KnowledgeGraph(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_degree_ranking(self):
        # Hub connects to many nodes
        self.kg.add_triple("Hub", "knows", "A")
        self.kg.add_triple("Hub", "knows", "B")
        self.kg.add_triple("Hub", "knows", "C")
        self.kg.add_triple("X", "knows", "Y")
        gods = self.kg.god_nodes(limit=2)
        assert gods[0]["entity"] == "Hub"
        assert gods[0]["connections"] == 3

    def test_empty_graph(self):
        gods = self.kg.god_nodes()
        assert gods == []

    def test_limit(self):
        for i in range(20):
            self.kg.add_triple("Hub", "r", f"N{i}")
        gods = self.kg.god_nodes(limit=3)
        assert len(gods) == 3

    def test_expired_excluded(self):
        self.kg.add_triple("A", "r", "B")
        self.kg.invalidate("A", "r", "B")
        gods = self.kg.god_nodes()
        assert gods == []


class TestForUser(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file(self):
        kg = KnowledgeGraph.for_user("testuser", store_dir=self.tmpdir)
        kg.add_triple("A", "r", "B")
        expected = Path(self.tmpdir) / "testuser.json"
        assert expected.exists()

    def test_safe_filename(self):
        kg = KnowledgeGraph.for_user("user/with:special", store_dir=self.tmpdir)
        kg.add_triple("A", "r", "B")
        expected = Path(self.tmpdir) / "user_with_special.json"
        assert expected.exists()


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = str(Path(self.tmpdir) / "kg.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_reload(self):
        kg1 = KnowledgeGraph(self.path)
        kg1.add_triple("Alice", "knows", "Bob")
        kg1.add_triple("Alice", "works_at", "Acme")

        kg2 = KnowledgeGraph(self.path)
        s = kg2.stats()
        assert s["triples"] == 2
        assert s["entities"] == 3

    def test_invalidate_persists(self):
        kg1 = KnowledgeGraph(self.path)
        kg1.add_triple("A", "r", "B")
        kg1.invalidate("A", "r", "B")

        kg2 = KnowledgeGraph(self.path)
        s = kg2.stats()
        assert s["expired_facts"] == 1

    def test_load_corrupt_file(self):
        Path(self.path).write_text("not json")
        kg = KnowledgeGraph(self.path)
        assert kg.stats()["triples"] == 0


if __name__ == "__main__":
    unittest.main()
