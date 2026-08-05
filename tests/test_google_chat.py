import json

import pytest

from core import FlowFile


def test_google_chat_space_store_defaults_denied_and_deduplicates(tmp_path, monkeypatch):
    from core import paths
    from core.google_chat_store import GoogleChatSpaceStore

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    store = GoogleChatSpaceStore("owner", "chat-service")
    row = store.observe_space("spaces/AAA", "Support")
    assert row["status"] == "pending"
    assert row["permission_mode"] == "read_only"
    assert store.claim_event("messages/1") is True
    assert store.claim_event("messages/1") is False
    assert store.release_event("messages/1") is True
    assert store.release_event("messages/1") is False
    assert store.claim_event("messages/1") is True
    allowed = store.allow_space("spaces/AAA", "conv-1")
    assert allowed["status"] == "allowed"
    assert allowed["conversation_id"] == "conv-1"
    with pytest.raises(ValueError, match="only support read_only"):
        store.allow_space("spaces/AAA", "conv-1", "default")


def test_google_chat_webhook_acks_before_downstream_processing():
    from tasks.io.google_chat import GoogleChatWebhookTask

    class Chat:
        def verify_request(self, auth):
            assert auth == "Bearer signed"

    class Listener:
        def __init__(self):
            self.response = None

        def submit_response(self, *args):
            self.response = args
            return True

    listener = Listener()
    task = GoogleChatWebhookTask({
        "service_id": "chat", "http_service_id": "http"})
    task.set_services({"chat": Chat(), "http": listener})
    event = {"type": "MESSAGE", "space": {"name": "spaces/A"}}
    ff = FlowFile(content=json.dumps(event).encode())
    ff.set_attribute("http.request.id", "req-1")
    ff.set_attribute("http.header.authorization", "Bearer signed")

    out = task.execute(ff)[0]

    assert listener.response[0:2] == ("req-1", 200)
    assert json.loads(out.get_attribute("google_chat.event_json")) == event
    assert out.get_attribute("http.response.sent") == "true"


def test_google_chat_service_requires_chat_issuer_claims(monkeypatch):
    from services.google_chat_service import GoogleChatService
    from google.oauth2 import id_token

    service = GoogleChatService({
        "service_account_json": json.dumps({
            "client_email": "app@example.iam.gserviceaccount.com",
            "private_key": "unused-in-this-test",
        }),
        "audience": "https://example.test/chat",
    })
    monkeypatch.setattr(
        id_token, "verify_oauth2_token",
        lambda *_: {"email": "attacker@example.com", "email_verified": True})
    with pytest.raises(ValueError, match="token email"):
        service.verify_request("Bearer signed")

    monkeypatch.setattr(
        id_token, "verify_oauth2_token",
        lambda *_: {"email": "chat@system.gserviceaccount.com",
                    "email_verified": True})
    assert service.verify_request("Bearer signed")["email"].startswith("chat@")


def test_google_chat_non_owner_cannot_allow_space(tmp_path, monkeypatch):
    from core import paths
    from core.google_chat_store import GoogleChatSpaceStore
    from tasks.io.google_chat import GoogleChatAgentClientTask

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    task = GoogleChatAgentClientTask({
        "service_id": "chat", "owner_google_user_id": "users/owner"})
    task._owner_user_id = "owner"
    store = GoogleChatSpaceStore("owner", "chat")
    reply = task._handle_admin(
        "/gchat allow conv-1", store, "spaces/A", "users/intruder", "users/owner")
    assert "only the configured bot owner" in reply
    assert store.get_space("spaces/A") == {}


def test_google_chat_group_rejects_default_permission_mode(tmp_path, monkeypatch):
    from core import paths
    from core.google_chat_store import GoogleChatSpaceStore
    from tasks.io.google_chat import GoogleChatAgentClientTask

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    task = GoogleChatAgentClientTask({
        "service_id": "chat", "owner_google_user_id": "users/owner"})
    task._owner_user_id = "owner"
    monkeypatch.setattr(
        task, "_require_owned_conversation", lambda _conversation_id: None)
    store = GoogleChatSpaceStore("owner", "chat")

    reply = task._handle_admin(
        "/gchat allow conv-1 default", store, "spaces/A",
        "users/owner", "users/owner")

    assert "only support read_only" in reply
    assert store.get_space("spaces/A") == {}


def test_google_chat_dm_requires_owned_configured_conversation(
        tmp_path, monkeypatch):
    from core import paths
    from core.agent_runtime_api import AgentRuntimeAPI
    from tasks.io.google_chat import GoogleChatAgentClientTask

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    submitted = []
    monkeypatch.setattr(
        AgentRuntimeAPI, "submit_message",
        lambda request: submitted.append(request))
    sent = []

    class Chat:
        def send_message(self, space_id, text, thread_name=""):
            sent.append(text)

    task = GoogleChatAgentClientTask({
        "service_id": "chat",
        "owner_google_user_id": "users/owner",
        "direct_conversation_id": "foreign-conversation",
    })
    task.set_runtime_context(user_id="paw-owner")
    task.set_services({"chat": Chat()})
    monkeypatch.setattr(
        task, "_require_owned_conversation",
        lambda _conversation_id: (_ for _ in ()).throw(
            ValueError("Conversation not found for the configured PawFlow owner.")))
    event = {
        "type": "MESSAGE",
        "space": {"name": "spaces/DM", "type": "DIRECT_MESSAGE"},
        "message": {
            "name": "spaces/DM/messages/1",
            "text": "hello",
            "sender": {"name": "users/owner", "type": "HUMAN"},
        },
    }
    ff = FlowFile(attributes={
        "google_chat.event_json": json.dumps(event),
    })

    assert task.execute(ff) == []
    assert submitted == []
    assert sent == [
        "Conversation not found for the configured PawFlow owner."]


def test_google_chat_allowed_group_runs_as_owner_with_actor_provenance(
        tmp_path, monkeypatch):
    from core import paths
    from core.agent_runtime_api import (
        AgentFinalResult, AgentRuntimeAPI, AgentSubmission)
    from core.google_chat_store import GoogleChatSpaceStore
    from tasks.io.google_chat import GoogleChatAgentClientTask

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    GoogleChatSpaceStore("paw-owner", "chat").allow_space(
        "spaces/A", "conv-1", "read_only")
    sent = []

    class Chat:
        def send_message(self, space_id, text, thread_name=""):
            sent.append((space_id, text, thread_name))

    captured = {}

    def submit(request):
        captured["request"] = request
        request.live_callback("conv-1", "new_message", {
            "role": "assistant", "content": "live answer", "msg_id": "a1"})
        return AgentSubmission("accepted", "conv-1", request.msg_id)

    monkeypatch.setattr(AgentRuntimeAPI, "submit_message", submit)
    monkeypatch.setattr(
        AgentRuntimeAPI, "wait_for_done",
        lambda *_: AgentFinalResult("conv-1", "turn", response="live answer"))
    monkeypatch.setattr(
        GoogleChatAgentClientTask, "_selected_agent", staticmethod(lambda _: "assistant"))

    task = GoogleChatAgentClientTask({
        "service_id": "chat", "owner_google_user_id": "users/owner"})
    task.set_runtime_context(user_id="paw-owner")
    task.set_services({"chat": Chat()})
    event = {
        "type": "MESSAGE",
        "space": {"name": "spaces/A", "type": "ROOM"},
        "message": {
            "name": "spaces/A/messages/1",
            "argumentText": "hello",
            "sender": {"name": "users/member", "displayName": "Member", "type": "HUMAN"},
            "thread": {"name": "spaces/A/threads/T"},
        },
    }
    ff = FlowFile()
    ff.set_attribute("google_chat.event_json", json.dumps(event))

    assert task.execute(ff) == []
    request = captured["request"]
    assert request.user_id == "paw-owner"
    assert request.permission_mode == "read_only"
    assert request.source_attributes["google_chat.actor_id"] == "users/member"
    assert sent == [("spaces/A", "live answer", "spaces/A/threads/T")]


def test_google_chat_delivery_failure_releases_event_for_retry(
        tmp_path, monkeypatch):
    from core import paths
    from core.agent_runtime_api import (
        AgentFinalResult, AgentRuntimeAPI, AgentSubmission)
    from core.google_chat_store import GoogleChatSpaceStore
    from tasks.io.google_chat import GoogleChatAgentClientTask

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path)
    GoogleChatSpaceStore("paw-owner", "chat").allow_space(
        "spaces/A", "conv-1", "read_only")
    attempts = []
    delivered = []
    fail_live_delivery = [True]

    class Chat:
        def send_message(self, _space_id, text, thread_name=""):
            if text == "live answer" and fail_live_delivery[0]:
                fail_live_delivery[0] = False
                raise RuntimeError("transient send failure")
            delivered.append((text, thread_name))

    def submit(request):
        attempts.append(request.msg_id)
        request.live_callback("conv-1", "new_message", {
            "role": "assistant", "content": "live answer", "msg_id": "a1"})
        return AgentSubmission("accepted", "conv-1", request.msg_id)

    monkeypatch.setattr(AgentRuntimeAPI, "submit_message", submit)
    monkeypatch.setattr(
        AgentRuntimeAPI, "wait_for_done",
        lambda *_: AgentFinalResult("conv-1", "turn", response="live answer"))
    monkeypatch.setattr(
        GoogleChatAgentClientTask, "_selected_agent",
        staticmethod(lambda _: "assistant"))

    task = GoogleChatAgentClientTask({
        "service_id": "chat", "owner_google_user_id": "users/owner"})
    task.set_runtime_context(user_id="paw-owner")
    task.set_services({"chat": Chat()})
    event = {
        "type": "MESSAGE",
        "space": {"name": "spaces/A", "type": "ROOM"},
        "message": {
            "name": "spaces/A/messages/retry",
            "argumentText": "hello",
            "sender": {"name": "users/member", "type": "HUMAN"},
            "thread": {"name": "spaces/A/threads/T"},
        },
    }
    flowfile = FlowFile(attributes={
        "google_chat.event_json": json.dumps(event),
    })

    assert task.execute(flowfile) == []
    assert task.execute(flowfile) == []

    assert attempts.count("google_chat:spaces/A/messages/retry") == 2
    assert ("live answer", "spaces/A/threads/T") in delivered


def test_agent_runtime_permission_override_is_reserved(monkeypatch):
    from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
    from tasks.ai.agent_loop import AgentLoopTask

    captured = {}

    class Runtime:
        def execute(self, flowfile):
            captured["ff"] = flowfile
            flowfile.set_content(b'{"status":"queued","wait_for_done":false}')
            return [flowfile]

    monkeypatch.setattr(AgentLoopTask, "_live_instance", Runtime())
    AgentRuntimeAPI.submit_message(AgentRequest(
        user_id="owner", message="hello", channel="google_chat",
        permission_mode="read_only",
        source_attributes={"agent.permission_mode": "default"}))
    assert captured["ff"].get_attribute("agent.permission_mode") == "read_only"


def test_turn_read_only_override_beats_persistent_tool_allow(monkeypatch):
    from core.llm_client import LLMToolCall
    from tasks.ai.agent_tool_exec import AgentToolExecMixin

    class Registry:
        def list_tools(self):
            return []

        def execute(self, *_args, **_kwargs):
            pytest.fail("blocked tool must not execute")

    result = AgentToolExecMixin()._execute_tool_calls(
        [LLMToolCall(id="1", name="write", arguments={"path": "x", "content": "y"})],
        Registry(), {}, 100, conversation_id="conv", user_id="owner",
        permission_mode_override="read_only", parallel=False)
    assert "blocked for this read-only external turn" in result[0][1]
