"""Tests for one-pass post-compaction memory and skill learning."""

import json
from unittest.mock import MagicMock

import pytest

from core.memory_store import MemoryStore
from core.post_compaction_learning import process_post_compaction_learning


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def clone_for_call(self):
        return self

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return MagicMock(content=json.dumps(self.payload))


@pytest.fixture()
def learning_env(tmp_path, monkeypatch):
    MemoryStore.reset()
    store = MemoryStore(store_dir=str(tmp_path / "memories"))
    MemoryStore._instance = store
    monkeypatch.setattr(
        "core.memory_auto_extract._memory_extraction_allowed",
        lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "core.skill_loop._existing_skill_lines",
        lambda user_id, conversation_id: "(none)")
    yield store
    MemoryStore.reset()


def _memory():
    return {
        "text": "User requires exact-SHA CI success before every PawFlow release.",
        "category": "preferences",
        "importance": "high",
        "durability": "durable",
        "scope": "global",
        "ttl_days": 0,
        "tags": ["release"],
    }


def _skill():
    return {
        "name": "release-pawflow",
        "description": "Release PawFlow after exact-SHA validation",
        "steps": ["run the full suite", "wait for CI", "push the tag"],
        "trigger": "a PawFlow release is requested",
    }


def test_shared_client_reads_summary_once_and_stores_both_outputs(
    learning_env, monkeypatch
):
    summary = "UNIQUE SUMMARY SENTINEL"
    client = _FakeClient({"memories": [_memory()], "skill": _skill()})
    monkeypatch.setattr(
        "core.post_compaction_learning._resolve_client",
        lambda role, user_id, conversation_id: client)

    result = process_post_compaction_learning(
        user_id="alice", summary=summary, conversation_id="conv-1")

    assert result == {
        "mode": "combined",
        "llm_calls": 1,
        "memory_count": 1,
        "memory_outcome": "stored",
        "skill_outcome": "created",
    }
    assert len(client.calls) == 1
    assert client.calls[0]["messages"][0].content.count(summary) == 1
    entries = learning_env.list_all("alice")
    assert len(entries) == 2
    assert any("skill-draft" in entry.tags for entry in entries)
    assert any(entry.category == "preferences" for entry in entries)


def test_invalid_memory_output_does_not_block_skill(
    learning_env, monkeypatch
):
    client = _FakeClient({"memories": {"bad": "shape"}, "skill": _skill()})
    monkeypatch.setattr(
        "core.post_compaction_learning._resolve_client",
        lambda role, user_id, conversation_id: client)

    result = process_post_compaction_learning(
        user_id="alice", summary="summary", conversation_id="conv-1")

    assert result["llm_calls"] == 1
    assert result["memory_outcome"] == "invalid"
    assert result["skill_outcome"] == "created"
    assert len(learning_env.list_all("alice")) == 1


def test_invalid_skill_output_does_not_block_memory(
    learning_env, monkeypatch
):
    client = _FakeClient({
        "memories": [_memory()],
        "skill": {"name": "missing-required-fields"},
    })
    monkeypatch.setattr(
        "core.post_compaction_learning._resolve_client",
        lambda role, user_id, conversation_id: client)

    result = process_post_compaction_learning(
        user_id="alice", summary="summary", conversation_id="conv-1")

    assert result["llm_calls"] == 1
    assert result["memory_count"] == 1
    assert result["skill_outcome"] == "invalid"
    assert len(learning_env.list_all("alice")) == 1


def test_memory_storage_failure_does_not_block_skill(
    learning_env, monkeypatch
):
    client = _FakeClient({"memories": [_memory()], "skill": _skill()})
    monkeypatch.setattr(
        "core.post_compaction_learning._resolve_client",
        lambda role, user_id, conversation_id: client)
    monkeypatch.setattr(
        "core.memory_auto_extract._store_extracted_memories",
        MagicMock(side_effect=RuntimeError("memory store unavailable")))

    result = process_post_compaction_learning(
        user_id="alice", summary="summary", conversation_id="conv-1")

    assert result["memory_outcome"] == "error"
    assert result["skill_outcome"] == "created"
    assert len(learning_env.list_all("alice")) == 1


def test_distinct_role_clients_keep_independent_calls(
    learning_env, monkeypatch
):
    memory_client = _FakeClient([_memory()])
    skill_client = _FakeClient({"skill": _skill()})

    def resolve(role, user_id, conversation_id):
        return memory_client if role == "auto_memory" else skill_client

    monkeypatch.setattr(
        "core.post_compaction_learning._resolve_client", resolve)

    result = process_post_compaction_learning(
        user_id="alice", summary="summary", conversation_id="conv-1")

    assert result["mode"] == "separate"
    assert result["llm_calls"] == 2
    assert result["memory_count"] == 1
    assert result["skill_outcome"] == "created"
    assert len(memory_client.calls) == 1
    assert len(skill_client.calls) == 1
    assert len(learning_env.list_all("alice")) == 2
