"""Regression tests for maintenance-only summarizer routing."""

import inspect
from unittest.mock import MagicMock

from core.memory_auto_extract import auto_extract_memories
from core.post_compaction_learning import process_post_compaction_learning
from core.skill_loop import propose_skill_draft_from_summary
from core.summarizer_bindings import resolve_llm_client
from tasks.ai.actions._conv_core import _handle_conv_core


def test_shared_resolver_forwards_user_and_conversation(monkeypatch):
    client = object()
    summarizer = MagicMock()
    summarizer.resolve_llm_service.return_value = (
        client, 128000, "summarizer-llm")
    monkeypatch.setattr(
        "core.summarizer_bindings.resolve_service",
        lambda user_id, conversation_id: (summarizer, object(), True))

    assert resolve_llm_client("alice", "conv1") == (
        client, 128000, "summarizer-llm")
    summarizer.resolve_llm_service.assert_called_once_with(
        user_id="alice", conversation_id="conv1")


def test_maintenance_operations_expose_no_client_override():
    assert "llm_client" not in inspect.signature(
        auto_extract_memories).parameters
    assert "llm_client" not in inspect.signature(
        propose_skill_draft_from_summary).parameters
    assert "llm_client" not in inspect.signature(
        process_post_compaction_learning).parameters


def test_resume_compaction_has_no_main_llm_fallback():
    source = inspect.getsource(_handle_conv_core)
    block = source[source.index('if action == "resume_conversation":'):]
    block = block[:block.index('if action == "ping":')]

    assert "_get_summarizer_client" in block
    assert "_resolve_client" not in block
    assert "No summarizer_service LLM configured" in block
