"""Tests for conservative auto memory extraction and GC."""

import json
import shutil
import tempfile
import time
from unittest.mock import MagicMock

from core.memory_auto_extract import auto_extract_memories
from core.memory_gc import apply_memory_gc, memory_gc_plan
from core.memory_store import MemoryEntry, MemoryStore


class _FakeClient:
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def clone_for_call(self):
        return self

    def complete(self, **_kwargs):
        self.calls.append(_kwargs)
        return MagicMock(content=json.dumps(self.facts))


class TestMemoryAutoExtract:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        MemoryStore.reset()
        self.store = MemoryStore(store_dir=self.tmpdir)
        MemoryStore._instance = self.store

    def teardown_method(self):
        MemoryStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_skips_temporary_conversation(self, monkeypatch):
        fake_store = MagicMock()
        fake_store.is_temporary.return_value = True
        monkeypatch.setattr(
            "core.conversation_store.ConversationStore.instance",
            staticmethod(lambda: fake_store))
        client = _FakeClient([{
            "text": "Durable preference that must NOT be stored.",
            "category": "facts",
            "importance": "high",
            "durability": "durable",
            "scope": "global",
        }])
        count = auto_extract_memories(
            "u1", "summary", llm_client=client,
            conversation_id="temp1")
        assert count == 0
        assert client.calls == []  # LLM never invoked for temporary convs
        assert self.store.list_all("u1") == []

    def test_skips_ephemeral_and_stores_conversation_ttl(self):
        facts = [
            {
                "text": "Latest validation passed with 3471 tests after compact.",
                "category": "events",
                "importance": "medium",
                "durability": "ephemeral",
                "scope": "global",
            },
            {
                "text": "PawFlow compact restart must save the compacted context before launching a new CLI session.",
                "category": "facts",
                "importance": "high",
                "durability": "project",
                "scope": "global",
                "tags": ["compact"],
            },
        ]

        client = _FakeClient(facts)
        count = auto_extract_memories(
            "u1", "summary", llm_client=client,
            conversation_id="conv1")

        assert count == 1
        entries = self.store.list_all("u1")
        assert len(entries) == 1
        assert entries[0].conversation_id == "conv1"
        assert entries[0].agent == ""
        assert entries[0].expires_at > time.time()
        assert "auto-extracted" in entries[0].tags
        scope = client.calls[0]["call_conversation_id"]
        assert scope.startswith("_memory_extract_conv1_")
        assert scope != "_memory_extract"
        assert client.calls[0]["call_ephemeral_stream"] is True
        assert client.calls[0]["messages"][0].conversation_id == scope

    def test_durable_preference_can_be_global_without_ttl(self):
        facts = [{
            "text": "User expects agents to answer in French when reporting PawFlow bugs.",
            "category": "preferences",
            "importance": "high",
            "durability": "durable",
            "scope": "global",
            "ttl_days": 0,
            "tags": ["communication"],
        }]

        count = auto_extract_memories(
            "u1", "summary", llm_client=_FakeClient(facts),
            conversation_id="conv1")

        assert count == 1
        entry = self.store.list_all("u1")[0]
        assert entry.agent == ""
        assert entry.conversation_id == ""
        assert entry.expires_at == 0

    def test_limits_each_extract_to_two_memories(self):
        facts = [{
            "text": f"Durable user preference number {i} should be retained for future PawFlow conversations.",
            "category": "preferences",
            "importance": "high",
            "durability": "durable",
            "scope": "global",
        } for i in range(4)]

        count = auto_extract_memories("u1", "summary", llm_client=_FakeClient(facts))

        assert count == 2
        assert self.store.count("u1") == 2


class TestMemoryGc:
    def test_marks_volatile_and_family_overflow_as_ended(self):
        entries = [
            MemoryEntry(
                text="Latest validation passed with 3471 tests after compact.",
                tags=["auto-extracted", "compaction"],
                source="compaction",
                category="events",
                updated_at=10,
            )
        ]
        for i in range(5):
            entries.append(MemoryEntry(
                text=f"User communicates in French and prefers concise bug reports variant {i}.",
                tags=["auto-extracted", "compaction"],
                source="compaction",
                category="preferences",
                updated_at=100 + i,
            ))

        plan = memory_gc_plan(entries, now=1000)

        assert len(plan["end_ids"]) == 3
        assert plan["stats"]["volatile-compaction"] == 1
        assert plan["stats"]["family-overflow"] == 2

    def test_apply_marks_ended_without_deleting(self):
        tmpdir = tempfile.mkdtemp()
        try:
            MemoryStore.reset()
            store = MemoryStore(store_dir=tmpdir)
            MemoryStore._instance = store
            store.remember(
                "u1",
                "Latest validation passed with 3471 tests after compact.",
                ["auto-extracted", "compaction"],
                source="compaction",
                category="events",
            )

            plan = apply_memory_gc("u1", dry_run=False, now=1000)

            assert plan["applied"] == 1
            all_entries = store.list_all("u1")
            assert len(all_entries) == 1
            assert all_entries[0].ended == 1000
            assert store.recall("u1") == []
        finally:
            MemoryStore.reset()
            shutil.rmtree(tmpdir, ignore_errors=True)
