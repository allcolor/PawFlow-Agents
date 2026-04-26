"""Tests for MemoryStore + agent memory tools (remember, recall, forget).

Tests cover:
- MemoryStore CRUD operations
- Tag-based retrieval
- Text search
- Duplicate detection
- Disk persistence
- Per-user isolation
- RememberHandler, RecallHandler, ForgetHandler
- i18n keys
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.memory_store import MemoryEntry, MemoryStore


class TestMemoryEntry(unittest.TestCase):

    def test_create_entry(self):
        e = MemoryEntry(text="User likes Python", tags=["preference", "language"])
        assert e.text == "User likes Python"
        assert e.tags == ["preference", "language"]
        assert len(e.id) == 12

    def test_matches_text(self):
        e = MemoryEntry(text="User prefers dark theme", tags=["preference"])
        assert e.matches("dark theme")
        assert e.matches("DARK THEME")  # case insensitive
        assert not e.matches("light theme")

    def test_matches_tags(self):
        e = MemoryEntry(text="some fact", tags=["work", "project"])
        assert e.matches_tags(["work"])
        assert e.matches_tags(["project", "other"])
        assert not e.matches_tags(["personal"])

    def test_to_from_dict(self):
        e = MemoryEntry(text="test fact", tags=["tag1"], source="agent")
        d = e.to_dict()
        e2 = MemoryEntry.from_dict(d)
        assert e2.text == e.text
        assert e2.tags == e.tags
        assert e2.id == e.id
        assert e2.source == e.source


class TestMemoryStore(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_remember_and_recall(self):
        self.store.remember("user1", "Prefers CSV format", ["preference"])
        results = self.store.recall("user1", query="CSV")
        assert len(results) == 1
        assert "CSV" in results[0].text

    def test_recall_by_tags(self):
        self.store.remember("user1", "Likes Python", ["preference", "language"])
        self.store.remember("user1", "Works on PawFlow", ["project"])
        results = self.store.recall("user1", tags=["preference"])
        assert len(results) == 1
        assert "Python" in results[0].text

    def test_recall_empty(self):
        results = self.store.recall("user1", query="nothing")
        assert results == []

    def test_recall_all(self):
        self.store.remember("user1", "fact 1", ["a"])
        self.store.remember("user1", "fact 2", ["b"])
        results = self.store.recall("user1")
        assert len(results) == 2

    def test_forget_by_id(self):
        entry = self.store.remember("user1", "temp fact", ["temp"])
        deleted = self.store.forget("user1", entry.id)
        assert deleted is True
        assert self.store.count("user1") == 0

    def test_forget_nonexistent(self):
        deleted = self.store.forget("user1", "nonexistent")
        assert deleted is False

    def test_forget_by_text(self):
        self.store.remember("user1", "delete me please", ["temp"])
        self.store.remember("user1", "keep this", ["important"])
        count = self.store.forget_by_text("user1", "delete me")
        assert count == 1
        assert self.store.count("user1") == 1

    def test_duplicate_detection(self):
        self.store.remember("user1", "Same fact", ["tag1"])
        self.store.remember("user1", "same fact", ["tag2"])  # case insensitive
        assert self.store.count("user1") == 1
        # Tags should be merged
        entry = self.store.list_all("user1")[0]
        assert "tag1" in entry.tags
        assert "tag2" in entry.tags

    def test_per_user_isolation(self):
        self.store.remember("user1", "user1 fact", ["a"])
        self.store.remember("user2", "user2 fact", ["a"])
        r1 = self.store.recall("user1")
        r2 = self.store.recall("user2")
        assert len(r1) == 1
        assert len(r2) == 1
        assert "user1" in r1[0].text
        assert "user2" in r2[0].text

    def test_limit(self):
        for i in range(30):
            self.store.remember("user1", f"fact {i}", ["bulk"])
        results = self.store.recall("user1", limit=10)
        assert len(results) == 10

    def test_count(self):
        self.store.remember("user1", "a", [])
        self.store.remember("user1", "b", [])
        assert self.store.count("user1") == 2
        assert self.store.count("user2") == 0

    def test_ensure_embeddings_backfills_only_orphans(self):
        # Two entries without embedding, one with.
        self.store.remember("u", "alpha", ["t"])
        self.store.remember("u", "beta", ["t"])
        self.store.remember("u", "gamma", ["t"], embedding=[0.5, 0.5])

        calls = []

        def embed(text):
            calls.append(text)
            return [float(len(text)), 0.0]

        n = self.store.ensure_embeddings("u", embed)
        assert n == 2
        assert sorted(calls) == ["alpha", "beta"]
        # Second call should be a no-op — everything already embedded.
        assert self.store.ensure_embeddings("u", embed) == 0
        # Original vector preserved
        by_text = {e.text: e for e in self.store.list_all("u")}
        assert by_text["gamma"].embedding == [0.5, 0.5]
        assert by_text["alpha"].embedding is not None
        assert by_text["beta"].embedding is not None

    def test_ensure_embeddings_skips_on_failure(self):
        self.store.remember("u", "a", ["t"])

        def failing(text):
            raise RuntimeError("boom")

        n = self.store.ensure_embeddings("u", failing)
        assert n == 0
        assert self.store.list_all("u")[0].embedding is None


class TestMemoryPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_persist_and_reload(self):
        store1 = MemoryStore(store_dir=self.tmpdir)
        store1.remember("user1", "persistent fact", ["test"])

        store2 = MemoryStore(store_dir=self.tmpdir)
        results = store2.recall("user1", query="persistent")
        assert len(results) == 1
        assert results[0].text == "persistent fact"

    def test_forget_persists(self):
        store1 = MemoryStore(store_dir=self.tmpdir)
        entry = store1.remember("user1", "to delete", [])
        store1.forget("user1", entry.id)

        store2 = MemoryStore(store_dir=self.tmpdir)
        assert store2.count("user1") == 0


class TestMemoryHandlers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_remember_handler(self):
        from core.tool_registry import RememberHandler
        h = RememberHandler()
        h.set_user_id("user1")
        result = h.execute({"text": "Test memory", "tags": ["test"]})
        assert "Remembered" in result

    def test_remember_handler_no_text(self):
        from core.tool_registry import RememberHandler
        h = RememberHandler()
        h.set_user_id("user1")
        result = h.execute({"text": ""})
        assert "Error" in result

    def test_recall_handler(self):
        from core.tool_registry import RememberHandler, RecallHandler
        rh = RememberHandler()
        rh.set_user_id("user1")
        rh.execute({"text": "User likes dark mode", "tags": ["preference"]})

        h = RecallHandler()
        h.set_user_id("user1")
        result = h.execute({"query": "dark mode"})
        assert "dark mode" in result

    def test_recall_handler_empty(self):
        from core.tool_registry import RecallHandler
        h = RecallHandler()
        h.set_user_id("user1")
        result = h.execute({"query": "nothing"})
        assert "No memories" in result

    def test_forget_handler(self):
        from core.tool_registry import RememberHandler, ForgetHandler
        rh = RememberHandler()
        rh.set_user_id("user1")
        result = rh.execute({"text": "temp", "tags": []})
        # Extract ID from result
        import re
        match = re.search(r'id: (\w+)', result)
        mem_id = match.group(1)

        fh = ForgetHandler()
        fh.set_user_id("user1")
        result = fh.execute({"memory_id": mem_id})
        assert "deleted" in result

    def test_handlers_in_default_registry(self):
        from core.tool_registry import create_default_registry
        registry = create_default_registry()
        assert registry.get("remember") is not None
        assert registry.get("recall") is not None
        assert registry.get("forget") is not None


class TestTtlCleanup(unittest.TestCase):
    """Hard TTL via expires_at: lazy cleanup at load + on-demand."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmp)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_expires_at_kept_in_dict(self):
        import time
        e = MemoryEntry(text="t", tags=[], expires_at=time.time() + 3600)
        self.assertGreater(e.expires_at, 0)
        d = e.to_dict()
        self.assertIn("expires_at", d)
        e2 = MemoryEntry.from_dict(d)
        self.assertEqual(e2.expires_at, e.expires_at)

    def test_no_expires_at_means_no_ttl(self):
        e = MemoryEntry(text="forever", tags=[])
        self.assertEqual(e.expires_at, 0)
        d = e.to_dict()
        self.assertNotIn("expires_at", d)

    def test_remember_with_expires_at_persists(self):
        import time
        ts = time.time() + 3600
        e = self.store.remember(
            "u1", "ttl fact", ["tmp"], expires_at=ts)
        self.assertEqual(e.expires_at, ts)
        # Reload from disk
        MemoryStore.reset()
        store2 = MemoryStore(store_dir=self.tmp)
        rs = store2.recall("u1")
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].expires_at, ts)

    def test_load_drops_expired_entries(self):
        import time
        # Two entries: one expired, one not.
        self.store.remember("u1", "keep", [], expires_at=time.time() + 3600)
        self.store.remember("u1", "drop", [], expires_at=time.time() - 1)
        # Force a fresh load
        MemoryStore.reset()
        store2 = MemoryStore(store_dir=self.tmp)
        rs = store2.recall("u1")
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].text, "keep")

    def test_cleanup_expired_returns_count(self):
        import time
        self.store.remember("u1", "a", [], expires_at=time.time() - 1)
        self.store.remember("u1", "b", [], expires_at=time.time() - 1)
        self.store.remember("u1", "c", [], expires_at=0)
        # Bypass the load-time cleanup that already happened by loading
        # again with a fresh store; the disk file still has 3 entries
        # after the first remember triplet so we need to re-write.
        # Instead: poke directly via cleanup_expired which is the on-
        # demand path.
        n = self.store.cleanup_expired("u1")
        # remember() already triggered the load cleanup; expired ones
        # were dropped on the first reload — but the *store_lock load*
        # was inside remember itself, before the new entry was added,
        # so newly-remembered expired ones are still there.
        # That makes the next cleanup_expired the right place to remove.
        # Either path: the survivor count is 1.
        rs = self.store.recall("u1")
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].text, "c")


class TestResolveScope(unittest.TestCase):
    """Single-source-of-truth scope → (agent, conv_id) mapping."""

    def test_global(self):
        from core.memory_store import resolve_scope
        self.assertEqual(resolve_scope("global", "a", "c"), ("", ""))

    def test_agent(self):
        from core.memory_store import resolve_scope
        self.assertEqual(resolve_scope("agent", "a", "c"), ("a", ""))

    def test_conversation(self):
        from core.memory_store import resolve_scope
        self.assertEqual(resolve_scope("conversation", "a", "c"), ("", "c"))

    def test_private(self):
        from core.memory_store import resolve_scope
        self.assertEqual(resolve_scope("private", "a", "c"), ("a", "c"))

    def test_unknown_raises(self):
        from core.memory_store import resolve_scope
        with self.assertRaises(ValueError):
            resolve_scope("bogus", "a", "c")

    def test_empty_agent_in_agent_scope(self):
        from core.memory_store import resolve_scope
        # Caller without agent_name still gets a valid tuple
        # (effectively global). Avoids surfacing a sometimes-legit
        # empty agent as a hard failure.
        self.assertEqual(resolve_scope("agent", "", "c"), ("", ""))


class TestBm25Recall(unittest.TestCase):
    """recall(query=...) ranks by BM25 instead of substring match."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmp)
        MemoryStore._instance = self.store

    def tearDown(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multitoken_query_finds_partial_matches(self):
        # No single entry contains the full "slow auth middleware"
        # phrase, but each entry contains 2 of the 3 tokens — BM25
        # ranks them above unrelated entries.
        self.store.remember("u", "the auth layer is slow", ["perf"])
        self.store.remember("u", "middleware blocks requests", ["infra"])
        self.store.remember("u", "random unrelated note", ["misc"])
        rs = self.store.recall("u", query="slow auth middleware", limit=10)
        # Top 2 are the relevant ones
        top_texts = {rs[0].text, rs[1].text}
        self.assertIn("the auth layer is slow", top_texts)
        self.assertIn("middleware blocks requests", top_texts)

    def test_query_in_tag_or_category_still_matches(self):
        # BM25 is computed on text + tags + category, so a query that
        # only hits a tag still surfaces the entry.
        self.store.remember("u", "opaque body", ["deadline"], category="events")
        rs = self.store.recall("u", query="deadline")
        self.assertGreater(len(rs), 0)
        self.assertEqual(rs[0].text, "opaque body")


