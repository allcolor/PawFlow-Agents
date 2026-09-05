"""Codex native questions use correlated replies and durable async delivery."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.confirmation_store import ConfirmationStore
from core.llm_providers._codex_app_rpc import _CodexAppRpcMixin
from core.llm_providers._codex_native_input import (
    codex_questions, handle_server_request, publish_async_questions,
)
from core.native_user_input import NativeInputRequests


class ImmediateRequests:
    def submit(self, request_id, collect, reply):
        reply(collect(threading.Event()))


def client_and_proc(manager=None):
    client = _CodexAppRpcMixin()
    client._codex_native_context = {
        "user_id": "u", "conversation_id": "c", "agent_name": "a"}
    client._codex_native_inputs = manager or ImmediateRequests()
    proc = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
    return client, proc


def request(**extra):
    return {"id": "rpc-question", "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread", "questions": [{
                "id": "opaque/id", "question": "Which?", "isOther": True,
                "options": [{"label": "First"}, {"label": "Second"}]}]}, **extra}


@pytest.mark.parametrize("method", ["item/tool/requestUserInput", "tool/requestUserInput"])
def test_blocking_reply_keeps_rpc_and_question_identifiers(monkeypatch, method):
    collect = Mock(return_value={"opaque/id": "custom user text"})
    monkeypatch.setattr("core.llm_providers._codex_native_input.collect_native_questions", collect)
    client, proc = client_and_proc()
    handle_server_request(client, proc, request(method=method))
    assert json.loads(proc.stdin.getvalue()) == {
        "id": "rpc-question", "result": {"answers": {
            "opaque/id": {"answers": ["custom user text"]}}}}
    assert collect.call_args.kwargs["request_id"] == "rpc-question"
    assert collect.call_args.args[0][0]["allow_other"] is True


def test_cancelled_question_returns_no_selected_answer(monkeypatch):
    monkeypatch.setattr("core.llm_providers._codex_native_input.collect_native_questions", lambda *a, **kw: None)
    client, proc = client_and_proc()
    handle_server_request(client, proc, request())
    assert json.loads(proc.stdin.getvalue())["result"] == {
        "answers": {"opaque/id": {"answers": []}}}


@pytest.mark.parametrize("params", [[], "bad", {"questions": []},
                                     {"questions": [{"id": "q", "question": "?", "options": "bad"}]}])
def test_malformed_requests_receive_rpc_error(params):
    client, proc = client_and_proc()
    handle_server_request(client, proc, request(params=params))
    frame = json.loads(proc.stdin.getvalue())
    assert frame["id"] == "rpc-question"
    assert frame["error"]["code"] == -32602


def test_unknown_server_request_is_explicitly_rejected():
    client, proc = client_and_proc()
    handle_server_request(client, proc, request(method="unknown/request"))
    assert json.loads(proc.stdin.getvalue())["error"]["code"] == -32601


def test_resolved_question_during_rpc_wait_is_cancelled():
    manager = Mock()
    client, proc = client_and_proc(manager)
    proc.stdout = io.StringIO("\n".join(json.dumps(frame) for frame in [
        {"method": "serverRequest/resolved", "params": {"threadId": "thread", "requestId": "rpc-question"}},
        {"id": 1, "result": {"turn": {"id": "turn"}}},
    ]) + "\n")
    assert client._codex_app_request(proc, "turn/start") == {"turn": {"id": "turn"}}
    manager.cancel.assert_called_once_with("rpc-question")


@pytest.mark.parametrize("operation", ["cancel", "close"])
def test_request_manager_fences_late_answers(monkeypatch, operation):
    started = threading.Event()
    release = threading.Event()
    workers = []
    real_thread = threading.Thread
    def thread(*args, **kwargs):
        worker = real_thread(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr("core.native_user_input.threading.Thread", thread)
    def collect(cancel):
        started.set()
        assert release.wait(2)
        assert cancel.is_set()
        return "late answer"
    reply = Mock()
    manager = NativeInputRequests()
    try:
        manager.submit("question", collect, reply)
        assert started.wait(2)
        if operation == "cancel":
            manager.cancel("question")
        else:
            manager.close()
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()
    assert not manager.pending
    reply.assert_not_called()


def test_async_questions_survive_store_reopen_and_resume_requester(tmp_path, monkeypatch):
    database = tmp_path / "questions.db"
    monkeypatch.setattr(ConfirmationStore, "ensure_sweeper", lambda self: None)
    monkeypatch.setattr(ConfirmationStore, "_publish", staticmethod(lambda *args: None))
    scheduler = Mock()
    monkeypatch.setattr("core.poll_scheduler.PollScheduler.instance", lambda: scheduler)
    monkeypatch.setattr("tasks.ai.agent_loop.AgentLoopTask.wake_poller", lambda: None)
    item = {"id": "item-1", "questions": [{"title": "Where?", "options": ["Here", "There"]}]}
    context = {"user_id": "u", "conversation_id": "c", "agent_name": "a", "thread_id": "thread"}
    first = publish_async_questions(item, store=ConfirmationStore(database_path=database), **context)
    reopened = ConfirmationStore(database_path=database)
    duplicate = publish_async_questions(item, store=reopened, **context)
    assert first["request_id"] == duplicate["request_id"]
    assert duplicate["continuation"]["thread_id"] == "thread"
    assert duplicate["continuation"]["item_id"] == "item-1"
    assert duplicate["response_schema"]["fields"][0]["no_default"] is True
    reopened.respond(first["request_id"], {"question_0": "User-written place"}, answered_by="u")
    kwargs = scheduler.schedule_delay.call_args.kwargs
    assert kwargs["user_id"] == "u"
    assert "[scheduled:a]" in kwargs["reason"]
    assert "Where?" in kwargs["reason"]
    assert "User-written place" in kwargs["reason"]
    scheduler.schedule_delay.assert_called_once()


@pytest.mark.parametrize("question", [
    {"title": "?", "options": [5]},
    {"title": "?", "options": ["same", "same"]},
    {"title": "", "options": []},
])
def test_invalid_async_questions_do_not_publish(question):
    with pytest.raises(ValueError):
        codex_questions([question], asynchronous=True)
