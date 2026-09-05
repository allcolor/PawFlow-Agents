"""Native questions keep user selections distinct from tool approvals."""

import threading
from unittest.mock import Mock

import pytest

from core.confirmation_store import validate_interaction_answer
from core.native_user_input import NativeInputRequests, collect_native_questions, native_question_fields
from core.native_hook_input import answer_claude_hook


def question(**extra):
    return {"id": "native/q", "question": "Choose", "options": [
        {"value": "opaque-option", "label": "Same label"},
        {"value": "other-id", "label": "Same label"}], **extra}


def collect(questions, store, cancel=None):
    return collect_native_questions(
        questions, provider="test-provider", user_id="u", conversation_id="c",
        agent_name="a", request_id="native-request", store=store,
        cancel_event=cancel or threading.Event())


def test_native_selection_preserves_option_id_and_requester_kind():
    store = Mock()
    store.create_interaction.return_value = {"request_id": "req"}
    store.get_confirmation.return_value = {
        "status": "answered", "answer": {"question_0": "other-id"}}
    assert collect([question()], store) == {"native/q": "other-id"}
    kwargs = store.create_interaction.call_args.kwargs
    assert kwargs["requester_kind"] == "provider"
    assert kwargs["response_schema"]["fields"][0]["no_default"] is True
    assert kwargs["continuation"]["native_request_id"] == "native-request"


def test_cancel_before_create_does_not_publish():
    cancel = threading.Event()
    cancel.set()
    store = Mock()
    assert collect([question()], store, cancel) is None
    store.create_interaction.assert_not_called()


def test_cancel_during_wait_closes_interaction():
    cancel = threading.Event()
    store = Mock()
    store.create_interaction.return_value = {"request_id": "req"}
    def pending(_):
        cancel.set()
        return {"status": "pending"}
    store.get_confirmation.side_effect = pending
    assert collect([question()], store, cancel) is None
    store.cancel.assert_called_once_with("req", cancelled_by="a")


@pytest.mark.parametrize("invalid", [None, {}, [], [question(options={})],
                                    [question(), question()]])
def test_invalid_questions_rejected_before_publishing(invalid):
    store = Mock()
    with pytest.raises(ValueError):
        collect(invalid, store)
    store.create_interaction.assert_not_called()


@pytest.mark.parametrize("kind,answer", [
    ("choice", "free text\nwith newline"),
    ("multi", ["opaque-option", "free text"]),
])
def test_custom_answers_require_explicit_schema_opt_in(kind, answer):
    options = question()["options"]
    with pytest.raises(ValueError):
        validate_interaction_answer(kind, answer, {}, options)
    assert validate_interaction_answer(
        kind, answer, {"allow_other": True, "max_length": 80}, options) == answer


@pytest.mark.parametrize("answer", ["", " ", "x" * 81])
def test_custom_answer_bounds(answer):
    with pytest.raises(ValueError):
        validate_interaction_answer("choice", answer, {
            "allow_other": True, "min_length": 1, "max_length": 80}, [])


def test_claude_hook_returns_original_input_and_multiple_answers(monkeypatch):
    monkeypatch.setattr("core.native_hook_input.collect_native_questions",
                        lambda *a, **kw: {"0": ["One", "Two"]})
    original = {"questions": [{"question": "Which?", "multiSelect": True,
                               "options": [{"label": "One"}, {"label": "Two"}]}],
                "metadata": {"preserved": True}}
    result = answer_claude_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
         "tool_input": original, "tool_use_id": "tool-1"},
        provider="claude-code-interactive", user_id="u", conversation_id="c",
        agent_name="a", cancel_event=threading.Event())
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"] == {**original, "answers": {"Which?": "One, Two"}}
    assert "answers" not in original


@pytest.mark.parametrize("event,tool", [
    ("PreToolUse", "AskUserQuestion"), ("PermissionRequest", "Bash")])
def test_cancelled_hook_never_approves(monkeypatch, event, tool):
    monkeypatch.setattr("core.native_hook_input.collect_native_questions",
                        lambda *a, **kw: None)
    result = answer_claude_hook(
        {"hook_event_name": event, "tool_name": tool,
         "tool_input": {"questions": [{"question": "Which?", "options": []}]}},
        provider="claude-code-interactive", user_id="u", conversation_id="c",
        agent_name="a", cancel_event=threading.Event())["hookSpecificOutput"]
    assert result.get("permissionDecision", result.get("decision", {}).get("behavior")) == "deny"


@pytest.mark.parametrize("field", ["multi_select", "allow_other"])
@pytest.mark.parametrize("value", ["false", 0, 1, None, []])
def test_native_question_flags_require_booleans(field, value):
    with pytest.raises(ValueError, match="boolean"):
        native_question_fields([question(**{field: value})])


def test_native_labels_are_preserved_or_rejected_without_truncation():
    label = "é" * 2000
    fields, _ = native_question_fields([question(options=[{"value": "id", "label": label}])])
    assert fields[0]["options"][0]["label"] == label
    with pytest.raises(ValueError, match="2000"):
        native_question_fields([question(options=[{"value": "id", "label": label + "x"}])])


@pytest.mark.parametrize("tool", [None, "", " ", 1])
def test_permission_hook_requires_tool_name_before_publishing(monkeypatch, tool):
    collect_mock = Mock()
    monkeypatch.setattr("core.native_hook_input.collect_native_questions", collect_mock)
    with pytest.raises(ValueError, match="tool_name"):
        answer_claude_hook(
            {"hook_event_name": "PermissionRequest", "tool_name": tool, "tool_input": {}},
            provider="claude-code", user_id="u", conversation_id="c",
            agent_name="a", cancel_event=threading.Event())
    collect_mock.assert_not_called()


@pytest.mark.parametrize("multiple", ["false", 0, 1, None, []])
def test_claude_hook_rejects_malformed_multiselect_before_publishing(monkeypatch, multiple):
    collect_mock = Mock()
    monkeypatch.setattr("core.native_hook_input.collect_native_questions", collect_mock)
    with pytest.raises(ValueError, match="boolean"):
        answer_claude_hook(
            {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
             "tool_input": {"questions": [{"question": "Choose", "multiSelect": multiple}]}},
            provider="claude-code", user_id="u", conversation_id="c",
            agent_name="a", cancel_event=threading.Event())
    collect_mock.assert_not_called()


def test_cancelled_workers_still_count_until_they_exit():
    manager = NativeInputRequests()
    release = threading.Event()
    started = [threading.Event() for _ in range(16)]
    workers = []
    reply = Mock()
    def collect_blocked(cancel, index):
        workers.append(threading.current_thread())
        started[index].set()
        assert release.wait(3)
        return "late answer"
    try:
        for index in range(16):
            manager.submit(index, lambda cancel, i=index: collect_blocked(cancel, i), reply)
            assert started[index].wait(1)
            manager.cancel(index)
        assert not manager.pending
        with pytest.raises(ValueError, match="Too many"):
            manager.submit("overflow", lambda cancel: None, reply)
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
        manager.close()
    reply.assert_not_called()


def test_blocked_reply_does_not_block_cancel_or_close():
    manager = NativeInputRequests()
    writing = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    waiting = threading.Event()
    workers = []
    def collect_first(cancel):
        workers.append(threading.current_thread())
        return "answer"
    def reply(_):
        writing.set()
        assert release.wait(3)
    def collect_second(cancel):
        workers.append(threading.current_thread())
        waiting.set()
        cancel.wait(3)
    manager.submit("second", collect_second, Mock())
    assert waiting.wait(1)
    manager.submit("first", collect_first, reply)
    assert writing.wait(1)
    def stop():
        manager.cancel("second")
        manager.close()
        cancelled.set()
    stopper = threading.Thread(target=stop)
    stopper.start()
    try:
        assert cancelled.wait(1), "Blocked stdin must not hold the manager state lock"
        assert not manager.pending
    finally:
        release.set()
        stopper.join(2)
        for worker in workers:
            worker.join(2)


def test_cancelled_request_cannot_reply_after_id_is_reused():
    manager = NativeInputRequests()
    release = threading.Event()
    started = threading.Event()
    done = threading.Event()
    workers = []
    old_reply = Mock()
    replies = []
    def old_collect(cancel):
        workers.append(threading.current_thread())
        started.set()
        assert release.wait(3)
        return "old"
    try:
        manager.submit("same", old_collect, old_reply)
        assert started.wait(1)
        manager.cancel("same")
        manager.submit("same", lambda cancel: "new", lambda value: (replies.append(value), done.set()))
        assert done.wait(1)
        assert replies == ["new"]
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
        manager.close()
    old_reply.assert_not_called()
