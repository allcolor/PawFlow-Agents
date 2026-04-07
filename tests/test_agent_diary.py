"""Tests for AgentDiary — per-agent persistent journal.

Tests cover:
- write (basic, with type, with tags)
- read (limit, type filter, newest first ordering)
- build_diary_digest (max_chars, text usage)
- JSONL persistence (write then read from disk)
- Per-agent isolation (different agents don't see each other's entries)
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from core.agent_diary import AgentDiary


class TestWrite(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.diary = AgentDiary(store_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_write(self):
        record = self.diary.write("user1", "assistant", "User prefers dark mode")
        assert record["text"] == "User prefers dark mode"
        assert record["type"] == "observation"
        assert len(record["id"]) == 12
        assert record["ts"] > 0

    def test_write_with_type(self):
        record = self.diary.write("user1", "assistant", "Switched to new approach",
                                  entry_type="decision")
        assert record["type"] == "decision"

    def test_write_with_tags(self):
        record = self.diary.write("user1", "assistant", "Learned about X",
                                  tags=["Python", "  ML  "])
        assert record["tags"] == ["python", "ml"]

    def test_write_empty_tags_default(self):
        record = self.diary.write("user1", "assistant", "Something")
        assert record["tags"] == []

    def test_write_requires_fields(self):
        with self.assertRaises(ValueError):
            self.diary.write("", "assistant", "text")
        with self.assertRaises(ValueError):
            self.diary.write("user1", "", "text")
        with self.assertRaises(ValueError):
            self.diary.write("user1", "assistant", "")

    def test_write_returns_text(self):
        """Write returns a record with the text field."""
        record = self.diary.write("user1", "assistant", "test entry")
        assert record["text"] == "test entry"


class TestRead(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.diary = AgentDiary(store_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_empty(self):
        results = self.diary.read("user1", "assistant")
        assert results == []

    def test_read_returns_written(self):
        self.diary.write("user1", "assistant", "entry one")
        results = self.diary.read("user1", "assistant")
        assert len(results) == 1
        assert results[0]["text"] == "entry one"

    def test_newest_first(self):
        self.diary.write("user1", "assistant", "first")
        time.sleep(0.01)
        self.diary.write("user1", "assistant", "second")
        results = self.diary.read("user1", "assistant")
        assert results[0]["text"] == "second"
        assert results[1]["text"] == "first"

    def test_limit(self):
        for i in range(10):
            self.diary.write("user1", "assistant", f"entry {i}")
        results = self.diary.read("user1", "assistant", limit=3)
        assert len(results) == 3

    def test_type_filter(self):
        self.diary.write("user1", "assistant", "obs", entry_type="observation")
        self.diary.write("user1", "assistant", "dec", entry_type="decision")
        self.diary.write("user1", "assistant", "learn", entry_type="learning")
        results = self.diary.read("user1", "assistant", entry_type="decision")
        assert len(results) == 1
        assert results[0]["text"] == "dec"

    def test_read_no_user(self):
        results = self.diary.read("", "assistant")
        assert results == []

    def test_read_no_agent(self):
        results = self.diary.read("user1", "")
        assert results == []


class TestDiaryDigest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.diary = AgentDiary(store_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_digest(self):
        digest = self.diary.build_diary_digest("user1", "assistant")
        assert digest == ""

    def test_digest_content(self):
        self.diary.write("user1", "assistant", "User likes Python",
                         entry_type="observation")
        digest = self.diary.build_diary_digest("user1", "assistant")
        assert "[observation]" in digest.lower()

    def test_max_chars_truncation(self):
        for i in range(20):
            self.diary.write("user1", "assistant",
                             f"A very long observation number {i} with lots of details")
        digest = self.diary.build_diary_digest("user1", "assistant", max_chars=100)
        assert len(digest) <= 100
        assert digest.endswith("...")

    def test_digest_uses_text(self):
        """Digest should use the text field from diary entries."""
        self.diary.write("user1", "assistant", "User decided to use GraphQL")
        digest = self.diary.build_diary_digest("user1", "assistant")
        assert len(digest) > 0
        assert "GraphQL" in digest


class TestJSONLPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_persist_and_reload(self):
        diary1 = AgentDiary(store_dir=self.tmpdir)
        diary1.write("user1", "assistant", "persistent entry", tags=["keep"])

        diary2 = AgentDiary(store_dir=self.tmpdir)
        results = diary2.read("user1", "assistant")
        assert len(results) == 1
        assert results[0]["text"] == "persistent entry"
        assert results[0]["tags"] == ["keep"]

    def test_file_is_jsonl(self):
        diary = AgentDiary(store_dir=self.tmpdir)
        diary.write("user1", "agent1", "line one")
        diary.write("user1", "agent1", "line two")

        path = Path(self.tmpdir) / "user1" / "diary_agent1.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert "text" in record
            assert "ts" in record


class TestPerAgentIsolation(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.diary = AgentDiary(store_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_different_agents_isolated(self):
        self.diary.write("user1", "agent_a", "entry for A")
        self.diary.write("user1", "agent_b", "entry for B")

        results_a = self.diary.read("user1", "agent_a")
        results_b = self.diary.read("user1", "agent_b")

        assert len(results_a) == 1
        assert len(results_b) == 1
        assert results_a[0]["text"] == "entry for A"
        assert results_b[0]["text"] == "entry for B"

    def test_different_users_isolated(self):
        self.diary.write("user1", "assistant", "user1 entry")
        self.diary.write("user2", "assistant", "user2 entry")

        results_1 = self.diary.read("user1", "assistant")
        results_2 = self.diary.read("user2", "assistant")

        assert len(results_1) == 1
        assert len(results_2) == 1
        assert "user1" in results_1[0]["text"]
        assert "user2" in results_2[0]["text"]


if __name__ == "__main__":
    unittest.main()
