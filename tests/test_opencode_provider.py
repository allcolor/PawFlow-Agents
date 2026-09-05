"""Protocol-level tests for the native OpenCode provider, without Docker/API keys."""

from __future__ import annotations

import copy
import inspect
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from core._llm_types import ColdStartRequired, LLMClientError, LLMMessage
from core.llm_providers.opencode import (
    LLMOpenCodeMixin, _OpenCodeLiveSession, _OpenCodeTurn,
    validate_opencode_config,
)
from core.opencode_pool import OpenCodeCancelled, OpenCodeHTTPError


class ProtocolServer:
    def __init__(self, scope, revision, env, ephemeral):
        self.scope, self.revision, self.env, self.ephemeral = scope, revision, env, ephemeral
        self.is_running = False
        self.closed = False
        self.requests = []
        self.events = queue.Queue()
        self.sessions = {}
        self.messages = []
        self.failure = None
        self.block_prompt = False
        self.prompt_started = threading.Event()
        self.prefix_events = []

    def start(self):
        self.is_running = True

    def close(self):
        self.closed = True
        self.is_running = False

    def drain_events(self):
        while not self.events.empty():
            self.events.get_nowait()

    def next_event(self):
        if self.closed:
            raise OpenCodeCancelled()
        try:
            return self.events.get(timeout=0.02)
        except queue.Empty:
            return None

    def request(self, method, path, body=None, **kwargs):
        if self.closed:
            raise OpenCodeCancelled()
        self.requests.append((method, path, copy.deepcopy(body)))
        if self.failure and self.failure(method, path):
            raise OpenCodeHTTPError(method, path, 503)
        if method == "GET" and path.endswith("/message"):
            return copy.deepcopy(self.messages)
        if method == "GET" and "/message/" in path:
            return copy.deepcopy(next(x for x in self.messages if x["info"]["id"] == path.rsplit("/", 1)[1]))
        if method == "GET" and path.startswith("/session/"):
            sid = path.split("/")[2]
            if sid not in self.sessions:
                raise OpenCodeHTTPError(method, path, 404)
            return {"id": sid}
        if path == "/session":
            sid = "ses_" + str(len(self.sessions))
            self.sessions[sid] = {}
            return {"id": sid}
        if path.endswith("/prompt_async"):
            self.prompt_started.set()
            if self.block_prompt:
                return None
            sid = path.split("/")[2]
            pid = body["messageID"]
            info = {"id": "msg_assistant_" + pid, "parentID": pid, "sessionID": sid,
                    "role": "assistant", "time": {"created": 1}, "tokens": {
                        "input": 7, "output": 5, "reasoning": 2, "cache": {"read": 11, "write": 3}}}
            def part(ident, kind, **extra):
                return {"id": ident, "sessionID": sid, "messageID": info["id"], "type": kind, **extra}
            def event(kind, props):
                self.events.put({"type": kind, "properties": props})
            for evt in self.prefix_events:
                self.events.put(copy.deepcopy(evt))
            # Stale idle and unrelated session data must not finish/leak.
            event("session.status", {"sessionID": sid, "status": {"type": "idle"}})
            event("message.updated", {"info": {**info, "id": "msg_foreign", "sessionID": "ses_foreign"}})
            event("message.updated", {"info": copy.deepcopy(info)})
            event("message.part.updated", {"part": part("reason", "reasoning", text="Think")})
            event("message.part.updated", {"part": part("text", "text", text="Hello")})
            event("message.part.delta", {"sessionID": sid, "messageID": info["id"],
                                        "partID": "text", "field": "text", "delta": " world"})
            text_part = part("text", "text", text="Hello world")
            event("message.part.updated", {"part": text_part})
            tool = part("tool", "tool", callID="call_1", tool="pawflow_use_tool",
                        state={"status": "running", "input": {"tool_name": "read"}})
            event("message.part.updated", {"part": copy.deepcopy(tool)})
            tool["state"].update(status="completed", output="file content")
            event("message.part.updated", {"part": copy.deepcopy(tool)})
            event("message.part.updated", {"part": copy.deepcopy(tool)})
            step = part("usage", "step-finish", tokens=info["tokens"])
            event("message.part.updated", {"part": step})
            info.update(time={"created": 1, "completed": 2}, finish="stop")
            event("message.updated", {"info": info})
            self.messages = [{"info": copy.deepcopy(info), "parts": [
                part("reason", "reasoning", text="Think"), text_part, tool, step]}]
            event("session.status", {"sessionID": sid, "status": {"type": "idle"}})
        return None


class Harness(LLMOpenCodeMixin):
    def __init__(self, **config):
        self._config_ref = {"_service_id": "svc", "opencode_mcp_mode": "none", **config}
        self.created = []
        self.runtime_created = threading.Event()
        self.stored = {}
        self.runtime_setup = lambda runtime: None
        self.serialized = 0

    def _serialize_messages_for_cli(self, messages, tools):
        self.serialized += 1
        return "System instructions", "Full history\n" + messages[-1].text_content

    def _opencode_get_stored_session(self, scope, revision):
        return self.stored.get((scope, revision), "")

    def _opencode_set_stored_session(self, scope, revision, session_id):
        self.stored[(scope, revision)] = session_id

    def _opencode_new_runtime(self, scope, revision, env, ephemeral):
        runtime = ProtocolServer(scope, revision, env, ephemeral)
        self.runtime_setup(runtime)
        self.created.append(runtime)
        self.runtime_created.set()
        return runtime


def call(client, text="hello", **kwargs):
    params = {"call_user_id": "user", "call_conversation_id": "conv",
              "call_agent_name": "agent"}
    params.update(kwargs)
    return client._stream_opencode(
        [LLMMessage(role="user", content=text, conversation_id=params["call_conversation_id"])],
        "vendor/model", 0.2, 100, None, **params)


def test_signature_matches_acp():
    from core.llm_providers.acp import LLMAcpMixin
    expected = inspect.signature(LLMAcpMixin._stream_acp)
    actual = inspect.signature(LLMOpenCodeMixin._stream_opencode)
    assert list(actual.parameters) == list(expected.parameters)
    for name, parameter in expected.parameters.items():
        assert actual.parameters[name].kind == parameter.kind
        assert actual.parameters[name].default == parameter.default


@pytest.mark.parametrize("overrides", [
    {"opencode_mode": "external"}, {"opencode_base_url": "http://example.test"},
    {"auth_mode": "oauth"}, {"api_key": "secret"}, {"credential_service_id": "cred"},
    {"opencode_env": '["invalid"]'}, {"opencode_env": {"HOME": "/evil"}},
    {"opencode_env": {"OPENCODE_CONFIG_CONTENT": "{}"}},
    {"opencode_env": {"PAWFLOW_USER_ID": "other"}}, {"opencode_env": {"NODE_OPTIONS": "--require evil"}},
    {"opencode_env": {"KEY": 1}}, {"opencode_mcp_mode": "unknown"},
    {"opencode_load_session": "maybe"},
])
def test_config_rejects_unsafe_or_invalid_fields(overrides):
    with pytest.raises(ValueError):
        validate_opencode_config(overrides)


def test_stream_deduplicates_snapshots_tools_and_usage():
    client = Harness()
    text, reasoning, blocks = [], [], []
    response = call(client, callback=text.append, thinking_callback=reasoning.append,
                    block_callback=lambda kind, data: blocks.append((kind, data)))
    assert response.content == "".join(text) == "Hello world"
    assert response.thinking == "".join(reasoning) == "Think"
    assert [kind for kind, _ in blocks] == ["tool_use", "tool_result"]
    assert blocks[0][1]["name"] == "mcp__pawflow__use_tool"
    assert blocks[1][1]["result"] == "file content"
    assert (response.tokens_in, response.tokens_out, response.cache_read_tokens,
            response.cache_creation_tokens) == (7, 7, 11, 3)
    assert response.input_usage_native
    assert response.finish_reason == "stop"
    assert client.serialized == 1


def test_warm_session_sends_current_prompt_and_cold_sends_full_context():
    client = Harness(opencode_agent="build", opencode_variant="high")
    call(client, "first")
    client._pawflow_context_is_delta = True
    call(client, "second")
    assert len(client.created) == 1
    prompts = [r[2] for r in client.created[0].requests if r[1].endswith("/prompt_async")]
    assert prompts[0]["system"] == "System instructions"
    assert prompts[0]["parts"][0]["text"] == "Full history\nfirst"
    assert prompts[1]["parts"] == [{"type": "text", "text": "second"}]
    assert "system" not in prompts[1]
    assert prompts[0]["model"] == {"providerID": "vendor", "modelID": "model"}
    assert prompts[0]["agent"] == "build" and prompts[0]["variant"] == "high"
    assert prompts[0]["messageID"] != prompts[1]["messageID"]


def test_saved_session_resumes_after_clean_runtime_close():
    client = Harness(opencode_reuse_process=False)
    call(client)
    assert client.created[0].closed
    client.runtime_setup = lambda r: r.sessions.update({"ses_0": {}})
    client._pawflow_context_is_delta = True
    call(client, "next")
    assert ("GET", "/session/ses_0", None) in client.created[1].requests
    assert not any(path == "/session" for _, path, _ in client.created[1].requests)
    assert client.serialized == 1


def test_missing_saved_session_with_delta_requires_cold_without_submission():
    client = Harness()
    scope = ("user", "conv", "agent", "svc")
    revision = client._opencode_revision(validate_opencode_config(client._config_ref), "vendor/model")
    client.stored[(scope, revision)] = "ses_missing"
    client._pawflow_context_is_delta = True
    with pytest.raises(ColdStartRequired):
        call(client)
    runtime = client.created[0]
    assert runtime.closed
    assert not any(p.endswith("/prompt_async") or p == "/session" for _, p, _ in runtime.requests)
    assert client.stored[(scope, revision)] == ""
    client._pawflow_context_is_delta = False
    assert call(client).content == "Hello world"


def test_resume_non404_error_never_creates_a_new_session():
    client = Harness()
    scope = ("user", "conv", "agent", "svc")
    revision = client._opencode_revision(validate_opencode_config(client._config_ref), "vendor/model")
    client.stored[(scope, revision)] = "ses_saved"
    client.runtime_setup = lambda r: setattr(r, "failure", lambda method, path: path == "/session/ses_saved")
    with pytest.raises(OpenCodeHTTPError) as exc:
        call(client)
    assert exc.value.status == 503
    assert client.created[0].requests == [("GET", "/session/ses_saved", None)]
    assert client.stored[(scope, revision)] == "ses_saved"


@pytest.mark.parametrize("field,value", [
    ("opencode_variant", "high"), ("opencode_agent", "plan"),
    ("opencode_env", {"ANTHROPIC_API_KEY": "new-key"}),
    ("model", "different/model"),
])
def test_config_changes_replace_runtime_and_require_full_context(field, value):
    client = Harness()
    call(client)
    old = client.created[0]
    client._config_ref[field] = value
    assert not client._opencode_has_live_session("svc", "user", "conv", "agent")
    client._pawflow_context_is_delta = True
    with pytest.raises(ColdStartRequired):
        call(client)
    assert old.closed


def test_identity_and_ephemeral_calls_are_isolated():
    client = Harness()
    call(client)
    call(client, call_user_id="other")
    call(client, call_conversation_id="other")
    call(client, call_agent_name="other")
    client._config_ref["_service_id"] = "other"
    call(client)
    count = len(client.stored)
    call(client, call_ephemeral_stream=True)
    call(client, call_ephemeral_stream=True)
    assert len({r.scope for r in client.created}) == 7
    assert len(client.stored) == count
    assert all(r.closed for r in client.created[-2:])


def test_failed_prompt_invalidates_resume_and_next_loop_recovers():
    client = Harness()
    call(client)
    old = client.created[0]
    old.failure = lambda method, path: path.endswith("/prompt_async")
    with pytest.raises(OpenCodeHTTPError):
        call(client)
    assert old.closed
    assert not any(client.stored.values())
    assert call(client).content == "Hello world"


@pytest.mark.parametrize("force", [True, False])
def test_cancel_is_not_an_error_and_does_not_poison_next_loop(force):
    client = Harness()
    client.runtime_setup = lambda r: setattr(r, "block_prompt", True)
    output = []
    worker = threading.Thread(target=lambda: output.append(call(client)))
    worker.start()
    # Event-based synchronization, no sleep/polling race.
    assert client.runtime_created.wait(2)
    runtime = client.created[0]
    assert runtime.prompt_started.wait(2)
    client._opencode_abort_active(force=force)
    worker.join(2)
    assert not worker.is_alive()
    assert output[0].finish_reason == "cancelled"
    assert runtime.closed
    client.runtime_setup = lambda r: None
    assert call(client).content == "Hello world"


def test_permission_routes_actual_arguments_through_shared_gate(monkeypatch):
    from core import tool_authorization, tool_approval
    calls = []
    monkeypatch.setattr(tool_approval.ToolApprovalGate, "get_mode", lambda _: "default")
    monkeypatch.setattr(tool_authorization, "gate_for_runtime",
                        lambda **kw: calls.append(kw) or "denied")
    monkeypatch.setattr(tool_approval.ToolApprovalGate, "check",
                        lambda *a, **k: pytest.fail("denied gate must not be bypassed"))
    live = _OpenCodeLiveSession("rev", "vendor/model")
    result = Harness()._opencode_permission(live, ("u", "c", "a", "s"), {
        "permission": "bash", "patterns": ["git status"], "metadata": {}})
    assert result == "reject"
    assert calls[0]["arguments"]["command"] == "git status"
    assert calls[0]["runtime"] == "opencode"
    assert calls[0]["cancel_event"] is live.cancel_event


def test_question_waits_for_real_store_answer_and_uses_provider_kind(monkeypatch):
    from core.confirmation_store import UserInteractionStore
    ready = threading.Event()
    record = {"request_id": "req_one", "status": "pending"}
    captured = {}
    fake = SimpleNamespace(
        create_interaction=lambda **kw: (captured.update(kw), ready.set(), record)[-1],
        get_interaction=lambda _: dict(record),
        cancel_interaction=lambda *a, **k: None)
    monkeypatch.setattr(UserInteractionStore, "instance", classmethod(lambda cls: fake))
    live = _OpenCodeLiveSession("rev", "vendor/model", session_id="ses_1")
    answers = []
    questions = [{"question": "Choose", "header": "Choice", "custom": False,
                  "options": [{"label": "One"}, {"label": "Two"}]},
                 {"question": "Details", "header": "Details", "options": []}]
    worker = threading.Thread(target=lambda: answers.append(Harness()._opencode_question(
        live, ("u", "c", "a", "s"), {"id": "q1", "questions": questions})))
    worker.start()
    assert ready.wait(2)
    assert not answers and worker.is_alive()
    assert captured["requester_kind"] == "provider"
    record.update(status="answered", answer={"q0": "Two", "q1": "User's answer"})
    worker.join(2)
    assert answers == [[["Two"], ["User's answer"]]]
    assert captured["response_schema"]["fields"][0]["no_default"] is True


def test_question_cancellation_does_not_invent_answers(monkeypatch):
    from core.confirmation_store import UserInteractionStore
    captured = []
    live = _OpenCodeLiveSession("rev", "vendor/model", session_id="ses_1")
    fake = SimpleNamespace(
        create_interaction=lambda **kw: {"request_id": "req"},
        get_interaction=lambda _: {"status": "cancelled"},
        cancel_interaction=lambda *args, **kw: captured.append(args))
    monkeypatch.setattr(UserInteractionStore, "instance", classmethod(lambda cls: fake))
    assert Harness()._opencode_question(live, ("u", "c", "a", "s"), {
        "id": "q1", "questions": [{"question": "Why?", "options": []}]}) is None
    assert captured == [("req",)]


def test_mcp_tokens_are_per_scope_and_revoked(monkeypatch):
    from core import internal_auth, docker_utils
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
    minted, revoked = [], []
    def mint():
        token = "token-" + str(len(minted))
        minted.append(token)
        return token
    monkeypatch.setattr(internal_auth, "mint_token", mint)
    monkeypatch.setattr(internal_auth, "revoke_token", revoked.append)
    monkeypatch.setattr(docker_utils, "get_host_ip", lambda: "host.docker.internal")
    monkeypatch.setattr(ClaudeCodeSessionMixin, "_get_tool_relay_info",
                        staticmethod(lambda: ("http://localhost:1234", "relay-secret")))
    client = Harness(opencode_mcp_mode="pawflow")
    call(client)
    call(client, call_conversation_id="second")
    configs = [json.loads(r.env["OPENCODE_CONFIG_CONTENT"]) for r in client.created]
    envs = [c["mcp"]["pawflow"]["environment"] for c in configs]
    assert envs[0]["PAWFLOW_INTERNAL_TOKEN"] != envs[1]["PAWFLOW_INTERNAL_TOKEN"]
    assert envs[0]["PAWFLOW_CONVERSATION_ID"] == "conv"
    assert envs[1]["PAWFLOW_CONVERSATION_ID"] == "second"
    client._opencode_close_all()
    assert sorted(revoked) == sorted(minted)


def test_unrelated_message_does_not_emit_or_count_usage():
    turn = _OpenCodeTurn("ses_1", "msg_current")
    turn.message({"id": "msg_old", "sessionID": "ses_1", "parentID": "msg_old_prompt",
                  "role": "assistant", "tokens": {"input": 999}})
    turn.part({"id": "p", "sessionID": "ses_1", "messageID": "msg_old",
               "type": "text", "text": "not this turn"})
    result = turn.response("vendor/model")
    assert not result.content and result.tokens_in == 0


def test_changed_stream_prefix_fails_instead_of_silently_corrupting_output():
    turn = _OpenCodeTurn("ses", "msg")
    turn.message({"id": "answer", "sessionID": "ses", "parentID": "msg", "role": "assistant"})
    part = {"id": "part", "sessionID": "ses", "messageID": "answer", "type": "text"}
    turn.part({**part, "text": "first"})
    with pytest.raises(LLMClientError, match="prefix"):
        turn.part({**part, "text": "replacement"})


def test_late_short_snapshot_does_not_rewind_next_delta():
    emitted = []
    turn = _OpenCodeTurn("ses", "msg", callback=emitted.append)
    turn.message({"id": "answer", "sessionID": "ses", "parentID": "msg", "role": "assistant"})
    part = {"id": "part", "sessionID": "ses", "messageID": "answer", "type": "text"}
    turn.part({**part, "text": "Hello"})
    turn.delta({"sessionID": "ses", "messageID": "answer", "partID": "part", "field": "text", "delta": " world"})
    turn.part({**part, "text": "Hello"})
    turn.delta({"sessionID": "ses", "messageID": "answer", "partID": "part", "field": "text", "delta": "!"})
    assert emitted == ["Hello", " world", "!"]


def test_permission_and_question_protocol_replies_are_scoped_and_deduplicated(monkeypatch):
    client = Harness()
    permissions, questions = [], []
    monkeypatch.setattr(client, "_opencode_permission", lambda live, scope, props: permissions.append(props["id"]) or "reject")
    monkeypatch.setattr(client, "_opencode_question", lambda live, scope, props: questions.append(props["id"]) or [["Actual answer"]])
    permission = {"type": "permission.asked", "properties": {"sessionID": "ses_0", "id": "per_1"}}
    question = {"type": "question.asked", "properties": {"sessionID": "ses_0", "id": "q_1"}}
    foreign = {"type": "question.asked", "properties": {"sessionID": "ses_foreign", "id": "q_foreign"}}
    client.runtime_setup = lambda r: r.prefix_events.extend([foreign, permission, permission, question, question])
    call(client)
    requests = client.created[0].requests
    assert permissions == ["per_1"] and questions == ["q_1"]
    assert ("POST", "/permission/per_1/reply", {"reply": "reject"}) in requests
    assert ("POST", "/question/q_1/reply", {"answers": [["Actual answer"]]}) in requests


def test_env_key_order_does_not_change_session_revision():
    client = Harness(opencode_env={"API_KEY": "one", "OTHER_API_KEY": "two"})
    before = client._opencode_revision(validate_opencode_config(client._config_ref), "vendor/model")
    client._config_ref["opencode_env"] = {"OTHER_API_KEY": "two", "API_KEY": "one"}
    assert before == client._opencode_revision(validate_opencode_config(client._config_ref), "vendor/model")


def test_cancel_one_clone_leaves_other_active_scope_running():
    first, second = Harness(), Harness()
    sessions, lock = first._opencode_shared_state()
    second._opencode_live_sessions, second._opencode_live_lock = sessions, lock
    for client in (first, second):
        client.runtime_setup = lambda r: setattr(r, "block_prompt", True)
    outputs = []
    workers = [threading.Thread(target=lambda client=client: outputs.append(call(
        client, call_agent_name="first" if client is first else "second")))
        for client in (first, second)]
    for worker in workers:
        worker.start()
    for client in (first, second):
        assert client.runtime_created.wait(2)
        assert client.created[0].prompt_started.wait(2)
    first._opencode_abort_active(force=True)
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert workers[1].is_alive() and not second.created[0].closed
    second._opencode_abort_active(force=True)
    workers[1].join(2)
    assert not workers[1].is_alive()
    assert [result.finish_reason for result in outputs] == ["cancelled", "cancelled"]
