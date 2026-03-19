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


class TestMemoryI18n(unittest.TestCase):

    def test_keys_in_all_locales(self):
        keys = [
            "memory.title", "memory.remember", "memory.recall",
            "memory.forget", "memory.no_memories", "memory.list",
            "memory.tags",
        ]
        for locale in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{locale}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing key '{key}' in {locale}.json"


if __name__ == "__main__":
    unittest.main()
