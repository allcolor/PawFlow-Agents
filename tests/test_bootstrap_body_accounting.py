"""A serialized context is charged once, on every surface that handles it.

A cold CLI start does not discard PawFlow's messages: it renders them into
initial_context.md and hands the provider a path. The agent reads that file
back -- page by page when it exceeds the native read ceiling -- so the same
conversation exists twice in the stored context, as messages and as read
bodies.

Two surfaces must therefore agree on which copy is authoritative, and both
pick the messages:

  * the gauge, which otherwise reports a size that depends on whether a
    native read happened to land, on a provider that happens to report a
    measurement;
  * compaction, which otherwise summarizes the same conversation twice and
    grows the duplicate by one layer on every cold start.
"""

from pathlib import Path

from core.llm_client import LLMMessage, LLMToolCall

BOOTSTRAP = "/cc_sessions/c/a/.pawflow_cci/initial_context.md"


def _read_call(call_id, path=BOOTSTRAP, **kwargs):
    return LLMMessage(
        role="assistant",
        content="",
        conversation_id="cid",
        tool_calls=[LLMToolCall(
            id=call_id,
            name="Read",
            arguments=dict({"file_path": path}, **kwargs),
            tool_origin="native",
        )],
    )


def _read_result(call_id, body, flagged=True):
    return LLMMessage(
        role="tool",
        conversation_id="cid",
        tool_call_id=call_id,
        source={"context_usage_bootstrap_body": True} if flagged else None,
        content=body,
    )


# -- The gauge --------------------------------------------------------------

def test_every_page_of_a_paginated_bootstrap_read_is_excluded():
    """A file past the read ceiling is consumed in several calls.

    Only the first read carries the boundary marker, so an accounting keyed
    on that marker charges every later page as if it were fresh context.
    """
    from tasks.ai.context_usage_cache import context_usage_from_cache

    history = LLMMessage(
        role="user", conversation_id="cid", content="history " * 4000)
    baseline = context_usage_from_cache([history], 200000, source="baseline")

    paginated = [history]
    for page in range(4):
        call_id = f"page-{page}"
        paginated.append(_read_call(call_id, offset=page * 500, limit=500))
        paginated.append(_read_result(call_id, "history " * 1000))
    usage = context_usage_from_cache(paginated, 200000, source="paginated")

    # Eight extra messages, none of them carrying countable content: the four
    # calls are empty assistant messages and the four results are the file.
    assert usage["message_count"] == 9
    assert usage["used"] - baseline["used"] <= 8 * 8


def test_a_read_of_any_other_file_is_still_charged():
    """The exclusion is about one specific file, not about native reads."""
    from tasks.ai.context_usage_cache import context_usage_from_cache

    body = "real file content " * 2000
    excluded = context_usage_from_cache(
        [_read_call("c1"), _read_result("c1", body, flagged=False)],
        200000, source="bootstrap")
    charged = context_usage_from_cache(
        [_read_call("c1", path="/workspace/notes.md"),
         _read_result("c1", body, flagged=False)],
        200000, source="ordinary")

    assert charged["used"] > excluded["used"] * 10


def test_the_gauge_needs_no_provider_measurement_and_no_active_turn():
    """The number exists from PawFlow's own data, for every provider.

    Nothing here reports a prompt size, and nothing has read the bootstrap
    file. The gauge is still the real size of the context PawFlow holds --
    which is what makes it live on providers that only report their usage
    when a turn ends, and on those that never report at all.
    """
    from tasks.ai.context_usage_cache import context_usage_from_cache

    messages = [
        LLMMessage(role="user", conversation_id="cid", content="q " * 500),
        LLMMessage(role="assistant", conversation_id="cid", content="a " * 500),
    ]
    usage = context_usage_from_cache(messages, 200000, source="cold")

    assert usage["used"] > 100
    assert usage["pct"] > 0


def test_the_serialized_copy_never_doubles_the_total():
    """messages + their own serialization must not read as twice the context."""
    from tasks.ai.context_usage_cache import context_usage_from_cache

    serialized = "conversation line " * 4000
    history = LLMMessage(
        role="user", conversation_id="cid", content=serialized)
    before = context_usage_from_cache([history], 200000, source="before")
    after = context_usage_from_cache(
        [history, _read_call("c1"), _read_result("c1", serialized)],
        200000, source="after")

    assert after["used"] < before["used"] * 1.1


# -- Compaction -------------------------------------------------------------

def _compact_harness(monkeypatch):
    from core import bucket_store as _bs_mod
    from core.conversation_store import ConversationStore
    from tasks.ai.agent_loop import AgentLoopTask

    class _StubCS:
        def _conv_dir(self, conversation_id):
            return Path("/tmp/pawflow-test-bootstrap-compact")

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        def assemble_summary_header(self):
            return ""

    monkeypatch.setattr(
        ConversationStore, "instance", classmethod(lambda cls: _StubCS()))
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)
    monkeypatch.setattr(
        AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        AgentLoopTask, "_cleanup_orphan_files",
        staticmethod(lambda *a, **kw: None))

    class _C:
        api_key = ""
        base_url = ""

    return AgentLoopTask.__new__(AgentLoopTask), _C()


def _run_compact(instance, client, msgs):
    return instance._compact(
        list(msgs), client,
        max_tokens=200_000,
        force=True,
        conversation_id="cid_test",
        agent_name="assistant",
        user_id="uid",
        budget_config={"compact_target_tokens": 25_000},
    )


def test_compact_drops_the_bootstrap_read_body(monkeypatch):
    """Its body is the conversation the summarizer is already being handed."""
    instance, client = _compact_harness(monkeypatch)
    msgs = [
        LLMMessage(role="system", content="sys",
                   conversation_id="cid", seq=1),
        LLMMessage(role="user", content="FRESH USER TURN",
                   conversation_id="cid", seq=10),
        _read_call("c1"),
        _read_result(
            "c1",
            "SERIALIZED COPY of everything said before, " * 200),
        LLMMessage(role="assistant", content="FRESH ASSISTANT TURN",
                   conversation_id="cid", seq=21),
    ]

    joined = "\n".join(
        (m.content or "") for m in _run_compact(instance, client, msgs))

    assert "FRESH USER TURN" in joined
    assert "FRESH ASSISTANT TURN" in joined
    assert "SERIALIZED COPY" not in joined


def test_compact_keeps_an_ordinary_native_read_result(monkeypatch):
    """The filter targets the bootstrap file, not native reads in general."""
    instance, client = _compact_harness(monkeypatch)
    msgs = [
        LLMMessage(role="system", content="sys",
                   conversation_id="cid", seq=1),
        LLMMessage(role="user", content="FRESH USER TURN",
                   conversation_id="cid", seq=10),
        _read_call("c1", path="/workspace/notes.md"),
        _read_result("c1", "GENUINE FILE CONTENT", flagged=False),
    ]

    joined = "\n".join(
        (m.content or "") for m in _run_compact(instance, client, msgs))

    assert "GENUINE FILE CONTENT" in joined
