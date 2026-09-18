"""The compacted snapshot must never keep a tool result without its owner.

Observed live on 2026-09-18 23:28:19 (conv 80c37670, agent ``assistant``): the
mid-turn auto-compaction re-adopted a context whose window kept the tool result
for ``call_01_df3S5Z1kFcYYzOac0Pu39326`` but dropped the assistant turn holding
its ``tool_use``. The next provider call died with

    messages.79.content.1: `tool_use_id` found in `tool_result` blocks ...
    must have a corresponding `tool_use` block in the previous message

and the whole turn was lost.
"""

from core.llm_client import LLMMessage, LLMToolCall
from core.conversation_store import ConversationStore
from tasks.ai.agent_compaction import AgentCompactionMixin
from tasks.ai.agent_serialization import AgentSerializationMixin


class _Compactor(AgentSerializationMixin, AgentCompactionMixin):
    """Minimal host for the two mixins under test."""


class _RecordingStore:
    def __init__(self):
        self.writes = []

    def save_agent_context(self, cid, agent_name, rows):
        self.writes.append((cid, agent_name, rows))
        return True


def _persist(monkeypatch, messages):
    store = _RecordingStore()
    monkeypatch.setattr(ConversationStore, "instance", lambda: store)
    _Compactor()._persist_context(messages, "conv-1", "assistant")
    assert store.writes, "nothing was persisted"
    _cid, _agent, rows = store.writes[-1]
    return rows


def _assert_pairing_intact(rows):
    """Every tool result answers a declared tool_call whose parent exists."""
    declared = {}
    for row in rows:
        if row.get("role") == "tool_call" and row.get("tool_call_id"):
            declared[row["tool_call_id"]] = row.get("parent_message_id")
    present = {row.get("msg_id") for row in rows}
    for call_id, parent_id in declared.items():
        assert parent_id in present, (
            f"tool_call {call_id!r} lost its assistant parent")
    for row in rows:
        if row.get("role") != "tool":
            continue
        call_id = row.get("tool_call_id")
        assert call_id in declared, (
            f"persisted an orphan tool result for {call_id!r} — the provider "
            "rejects the whole call")


def test_orphan_tool_result_is_dropped_from_the_snapshot(monkeypatch):
    messages = [
        LLMMessage(role="user", content="go", conversation_id="conv-1"),
        LLMMessage(role="assistant", content="",
                   tool_calls=[LLMToolCall(
                       id="call_kept", name="bash", arguments={})],
                   conversation_id="conv-1"),
        LLMMessage(role="tool", content="ok", tool_call_id="call_kept",
                   conversation_id="conv-1"),
        # Window cut between the assistant turn and this result: the assistant
        # holding `call_gone` was dropped, the result survived.
        LLMMessage(role="tool", content="orphaned output",
                   tool_call_id="call_gone", conversation_id="conv-1"),
    ]

    rows = _persist(monkeypatch, messages)

    assert [r.get("tool_call_id") for r in rows if r.get("role") == "tool"] \
        == ["call_kept"]
    _assert_pairing_intact(rows)


def test_snapshot_stays_historical_when_a_call_has_no_result(monkeypatch):
    messages = [
        LLMMessage(role="user", content="go", conversation_id="conv-1"),
        LLMMessage(role="assistant", content="",
                   tool_calls=[LLMToolCall(
                       id="call_preempted", name="bash", arguments={})],
                   conversation_id="conv-1"),
    ]

    rows = _persist(monkeypatch, messages)

    # The wire-level repair answers this call for the provider; the snapshot
    # itself must not invent a result row stamped `now` (the (ts, seq) reader
    # would sort it away from the assistant turn it answers).
    assert not [r for r in rows if r.get("role") == "tool"]
    assert not any("Result unavailable" in str(r.get("content"))
                   for r in rows)
    _assert_pairing_intact(rows)


def test_intact_pairing_is_left_untouched(monkeypatch):
    messages = [
        LLMMessage(role="user", content="go", conversation_id="conv-1"),
        LLMMessage(role="assistant", content="",
                   tool_calls=[
                       LLMToolCall(id="call_a", name="bash", arguments={}),
                       LLMToolCall(id="call_b", name="read", arguments={}),
                   ], conversation_id="conv-1"),
        LLMMessage(role="tool", content="a", tool_call_id="call_a",
                   conversation_id="conv-1"),
        LLMMessage(role="tool", content="b", tool_call_id="call_b",
                   conversation_id="conv-1"),
    ]

    rows = _persist(monkeypatch, messages)

    assert [r.get("tool_call_id") for r in rows if r.get("role") == "tool"] \
        == ["call_a", "call_b"]
    assert len([r for r in rows if r.get("role") == "tool_call"]) == 2
    _assert_pairing_intact(rows)
