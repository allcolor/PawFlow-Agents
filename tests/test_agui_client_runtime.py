"""Inbound AG-UI runtime parsing and routing tests."""

import json
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core import FlowFile
from core.agui_client_runtime import (
    _connection_config,
    _current_content,
    _events,
    _execute_tool,
    _one_run,
    _resume_entries,
    _run,
    cancel,
)
from services.agui_connection_service import AguiConnectionService
from services.external_agent_runtime_router import route_external_agent_prompt
from tasks.ai.actions._agentres_k5 import _handle_agentres_k5


class _Response:
    def __init__(self, lines):
        self.lines = lines

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        return iter(self.lines)

    status_code = 200

    def close(self):
        self.closed = True


def test_sse_parser_supports_comments_multiline_and_final_event():
    first = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"}
    response = _Response([
        ": heartbeat", "data: " + json.dumps(first), "",
        "data: {\"type\":", "data: \"RUN_FINISHED\"}", "",
        "data: {\"type\":\"RUN_STARTED\"}",
    ])
    assert list(_events(response)) == [
        first, {"type": "RUN_FINISHED"}, {"type": "RUN_STARTED"}]


def test_sse_parser_ignores_malformed_events():
    response = _Response(["data: not-json", "", "data: {\"type\":\"RUN_FINISHED\"}", ""])
    assert list(_events(response)) == [{"type": "RUN_FINISHED"}]


def test_one_run_enforces_started_and_finished_lifecycle():
    job = {"conversation_id": "c", "agent_name": "Remote",
           "message_id": "q", "config": {"agui_timeout": 10}}
    missing_start = _Response([
        'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"bad"}', ''])
    missing_finish = _Response(['data: {"type":"RUN_STARTED"}', ''])
    with patch("core.agui_client_runtime.requests.post",
               side_effect=[missing_start, missing_finish]):
        first = _one_run(job, "https://example/agui", {}, {}, {})
        second = _one_run(job, "https://example/agui", {}, {}, {})
    assert "first event is not RUN_STARTED" in first["error"]
    assert "without RUN_FINISHED" in second["error"]


def test_scoped_connection_service_resolves_runtime_settings():
    from core import ServiceFactory

    service = AguiConnectionService({
        "endpoint": "https://example/agui", "auth_secret": "token_ref",
        "allow_private": True, "timeout": 45, "max_tool_rounds": 4})
    job = {"conversation_id": "conv", "user_id": "user",
           "config": {"agui_service": "remote", "tools": ["read"]}}
    with patch("core.service_registry.ServiceRegistry.get_instance") as get_registry:
        get_registry.return_value.resolve.return_value = service
        resolved = _connection_config(job)
    assert resolved == {
        "agui_url": "https://example/agui",
        "agui_auth_secret": "token_ref", "agui_allow_private": True,
        "agui_timeout": 45, "agui_max_tool_rounds": 4,
        "tools": ["read"], "agui_service": "remote"}
    assert ServiceFactory.get("aguiConnection") is AguiConnectionService
    assert AguiConnectionService.CATEGORY == "ai"


def test_pending_interrupts_become_resume_entries_once():
    doc = {"pending_interrupts": [
        {"id": "i1", "question": "Continue?"}, {"id": "i2"}]}
    assert _resume_entries(doc, "yes") == [
        {"interruptId": "i1", "status": "resolved",
         "payload": {"answer": "yes"}},
        {"interruptId": "i2", "status": "resolved",
         "payload": {"answer": "yes"}},
    ]
    assert doc["pending_interrupts"] == []


def test_wrapped_tool_must_also_be_in_hard_allowlist():
    registry = SimpleNamespace(
        get=lambda _name: object(),
        prepare=lambda _name, _args: SimpleNamespace(
            name="use_tool", arguments={"tool_name": "delete"}),
    )
    job = {"conversation_id": "c", "user_id": "u", "agent_name": "Remote",
           "config": {"tools": ["use_tool"]}}
    with patch("core.llm_client.unwrap_mcp_tool", return_value=("delete", {"path": "x"})):
        assert "not in this agent's allowlist" in _execute_tool(
            job, registry, {"name": "use_tool", "arguments": {}, "id": "tc"})


def test_cancel_closes_active_response_and_discards_queued_runs():
    import core.agui_client_runtime as runtime
    class Response:
        closed = False
        def close(self):
            self.closed = True
    response = Response()
    jobs = queue.Queue()
    jobs.put({"message_id": "queued", "conversation_id": "conv",
              "agent_name": "Remote", "user_id": "user"})
    key = "conv:remote"
    with runtime._LOCK:
        runtime._ACTIVE[key] = {"cancel": False, "response": response}
        runtime._QUEUES[key] = jobs
    try:
        with patch("core.agui_client_runtime._finish") as finish:
            assert cancel("conv", "Remote") is True
            finish.assert_called_once()
        assert response.closed is True
        assert jobs.empty()
        with runtime._LOCK:
            assert runtime._ACTIVE[key]["cancel"] is True
    finally:
        with runtime._LOCK:
            runtime._ACTIVE.pop(key, None)
            runtime._QUEUES.pop(key, None)


def test_router_selects_agui_adapter():
    config = {"runtime_kind": "external_agui", "agui_url": "https://example/agui"}
    with patch("core.conv_agent_config.get_agent_config", return_value=config), \
         patch("core.agui_client_runtime.submit", return_value=True) as submit:
        kind, routed = route_external_agent_prompt(
            "conv", "Remote", "hello", "msg1", channel="web")
    assert (kind, routed) == ("external_agui", True)
    submit.assert_called_once_with(
        "conv", "Remote", "msg1", "hello", config, attachments=None)


def test_add_agent_handler_forwards_external_agui_connection():
    store = MagicMock()
    store.get_extra.return_value = {}
    resource_store = MagicMock()
    resource_store.get_any.return_value = {"assigned_skills": []}
    flowfile = FlowFile()
    body = {"conversation_id": "conv", "instance_name": "Remote",
            "definition": "external", "runtime_kind": "external_agui",
            "agui_service": "remote_agui", "agui_timeout": 45,
            "agui_max_tool_rounds": 3, "tools": ["search"]}
    with patch("core.resource_store.ResourceStore.instance",
               return_value=resource_store), \
         patch("core.conv_agent_config.add_agent_to_conv") as add:
        result = _handle_agentres_k5(
            None, "add_agent_to_conv", body, store, "user", flowfile)
    assert result == [flowfile]
    assert json.loads(flowfile.content)["ok"] is True
    assert add.call_args.kwargs["llm_service"] == ""
    assert add.call_args.kwargs["runtime_kind"] == "external_agui"
    assert add.call_args.kwargs["agui_service"] == "remote_agui"
    assert add.call_args.kwargs["agui_timeout"] == 45
    assert add.call_args.kwargs["agui_max_tool_rounds"] == 3


def test_update_agent_handler_rejects_agui_without_connection():
    flowfile = FlowFile()
    store = MagicMock()
    with patch("core.conv_agent_config.get_all_agent_configs", return_value={
            "Remote": {"runtime_kind": "llm", "llm_service": "svc",
                       "agui_url": "", "agui_service": ""}}):
        result = _handle_agentres_k5(None, "update_agent_conv_config", {
            "conversation_id": "conv", "name": "Remote",
            "config": {"runtime_kind": "external_agui", "llm_service": ""},
        }, store, "user", flowfile)
    assert result == [flowfile]
    assert flowfile.get_attribute("http.response.status") == "400"
    assert json.loads(flowfile.content)["error"] == (
        "agui_url or agui_service is required")


def test_router_forwards_agui_attachments():
    config = {"runtime_kind": "external_agui", "agui_url": "https://example/agui"}
    attachments = [{"filename": "a.png", "mime_type": "image/png", "data": "abc"}]
    with patch("core.conv_agent_config.get_agent_config", return_value=config), \
         patch("core.agui_client_runtime.submit", return_value=True) as submit:
        assert route_external_agent_prompt(
            "conv", "Remote", "hello", "msg1", channel="web",
            attachments=attachments) == ("external_agui", True)
    submit.assert_called_once_with(
        "conv", "Remote", "msg1", "hello", config, attachments=attachments)
    content = _current_content("hello", attachments)
    assert content[0] == {"type": "text", "text": "hello"}
    assert content[1]["source"]["type"] == "data"
    assert content[1]["source"]["mimeType"] == "image/png"
    assert "mime_type" not in content[1]["source"]


def test_multimodal_parts_use_current_agui_types():
    content = _current_content("", [
        {"mime_type": "video/mp4", "url": "https://example/video.mp4"},
        {"mime_type": "application/pdf", "data": "cGRm"},
        {"mime_type": "application/zip", "data": "emlw"},
    ])
    assert [part["type"] for part in content] == [
        "video", "document", "document"]
    assert content[0]["source"] == {
        "type": "url", "value": "https://example/video.mp4",
        "mimeType": "video/mp4"}


def test_run_posts_current_prompt_streams_and_finishes_once():
    response = _Response([
        'data: {"type":"RUN_STARTED"}', '',
        'data: {"type":"TEXT_MESSAGE_START","messageId":"answer-1"}', '',
        'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hi "}', '',
        'data: {"type":"REASONING_MESSAGE_CONTENT","delta":"checked"}', '',
        'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"there"}', '',
        'data: {"type":"RUN_FINISHED"}', '',
    ])
    job = {"conversation_id": "conv", "agent_name": "Remote",
           "message_id": "question-1", "content": "Hello",
           "user_id": "user", "config": {
               "agui_url": "https://example/agui", "agui_timeout": 30}}
    with patch("core.agui_client_runtime.resolve_relay_aware_url",
               return_value="https://example/agui"), \
         patch("core.agui_client_runtime._messages", return_value=[
             {"id": "question-1", "role": "user", "content": "Hello"}]), \
         patch("core.agui_client_runtime.requests.post", return_value=response) as post, \
         patch("core.agui_client_runtime._publish") as publish, \
         patch("core.agui_client_runtime._finish") as finish:
        _run(job)
    payload = json.loads(post.call_args.kwargs["data"])
    assert payload["messages"][-1]["id"] == "question-1"
    assert [call.args[1] for call in publish.call_args_list].count("token") == 2
    finish.assert_called_once_with(
        job, "Hi there", message_id="answer-1", thinking="checked")
    assert response.closed is True


def test_one_run_applies_state_activity_usage_and_interrupt_outcome():
    response = _Response([
        'data: {"type":"RUN_STARTED"}', '',
        'data: {"type":"STATE_SNAPSHOT","snapshot":{"count":1}}', '',
        'data: {"type":"STATE_DELTA","delta":[{"op":"replace","path":"/count","value":2}]}', '',
        'data: {"type":"ACTIVITY_SNAPSHOT","messageId":"act1","activityType":"SEARCH","content":{"label":"research","done":false}}', '',
        'data: {"type":"ACTIVITY_DELTA","messageId":"act1","activityType":"SEARCH","patch":[{"op":"replace","path":"/done","value":true}]}', '',
        'data: {"type":"STEP_STARTED","stepName":"search"}', '',
        'data: {"type":"RUN_FINISHED","usage":{"inputTokens":3},"outcome":{"type":"interrupt","interrupts":[{"id":"i1","question":"Continue?"}]}}', '',
    ])
    job = {"conversation_id": "conv", "agent_name": "Remote",
           "message_id": "q1", "user_id": "u",
           "config": {"agui_timeout": 30}}
    doc = {"state": {}}
    with patch("core.agui_client_runtime.requests.post", return_value=response), \
         patch("core.agui_client_runtime._save_doc"), \
         patch("core.agui_client_runtime._publish") as publish:
        result = _one_run(job, "https://example/agui", {}, {}, doc)
    assert doc["state"] == {"count": 2}
    assert doc["activities"]["act1"] == {
        "id": "act1", "activityType": "SEARCH",
        "content": {"label": "research", "done": True}}
    assert doc["usage"] == {"inputTokens": 3}
    assert result["outcome"]["interrupts"][0]["id"] == "i1"
    names = [call.args[1] for call in publish.call_args_list]
    assert "agui_state_snapshot" in names
    assert "agui_state_delta" in names
    assert "agui_activity" in names
    assert "agui_step" in names
    assert "agui_usage" in names


def test_one_run_uses_chunk_ids_and_persists_event_metadata_and_encrypted_reasoning():
    response = _Response([
        'data: {"type":"RUN_STARTED","threadId":"t","runId":"r"}', '',
        'data: {"type":"TEXT_MESSAGE_CHUNK","messageId":"answer-chunk","role":"assistant","delta":"hello","metadata":{"model":"m1"}}', '',
        'data: {"type":"REASONING_MESSAGE_CHUNK","messageId":"reasoning-1","delta":"why","metadata":{"trace":"x"}}', '',
        'data: {"type":"REASONING_ENCRYPTED_VALUE","subtype":"message","entityId":"reasoning-1","encryptedValue":"opaque"}', '',
        'data: {"type":"RUN_FINISHED","threadId":"t","runId":"r","metadata":{"finishReason":"stop"}}', '',
    ])
    job = {"conversation_id": "conv", "agent_name": "Remote",
           "message_id": "q1", "user_id": "u",
           "config": {"agui_timeout": 30}}
    doc = {"state": {}}
    with patch("core.agui_client_runtime.requests.post", return_value=response), \
         patch("core.agui_client_runtime._save_doc"):
        result = _one_run(job, "https://example/agui", {}, {}, doc)
    assert result["message_id"] == "answer-chunk"
    assert result["content"] == "hello"
    assert result["thinking"] == "why"
    assert result["metadata"] == {"model": "m1"}
    assert result["reasoning_metadata"] == {"trace": "x"}
    assert result["run_metadata"] == {"finishReason": "stop"}
    assert doc["encrypted_values"]["reasoning-1"] == {
        "subtype": "message", "encryptedValue": "opaque"}


def test_tool_chunks_keep_current_id_and_server_results_are_not_reexecuted():
    response = _Response([
        'data: {"type":"RUN_STARTED"}', '',
        'data: {"type":"TOOL_CALL_CHUNK","toolCallId":"server-tc","toolCallName":"remote_search","delta":"{\\"q\\":"}', '',
        'data: {"type":"TOOL_CALL_CHUNK","delta":"\\"x\\"}"}', '',
        'data: {"type":"REASONING_ENCRYPTED_VALUE","subtype":"tool-call","entityId":"server-tc","encryptedValue":"opaque-call"}', '',
        'data: {"type":"TOOL_CALL_RESULT","messageId":"tr1","toolCallId":"server-tc","content":"remote result"}', '',
        'data: {"type":"RUN_FINISHED"}', '',
    ])
    job = {"conversation_id": "conv", "agent_name": "Remote",
           "message_id": "q1", "user_id": "u",
           "config": {"agui_timeout": 30}}
    doc = {"state": {}}
    with patch("core.agui_client_runtime.requests.post", return_value=response), \
         patch("core.agui_client_runtime._save_doc"), \
         patch("core.agui_client_runtime._publish"):
        result = _one_run(job, "https://example/agui", {}, {}, doc)
    assert result["calls"] == []
    assert result["remote_calls"] == [{
        "id": "server-tc", "name": "remote_search", "arguments": {"q": "x"},
        "metadata": {}, "encryptedValue": "opaque-call",
        "result": "remote result"}]


def test_run_executes_allowed_tool_and_posts_result_followup():
    job = {"conversation_id": "conv", "agent_name": "Remote",
           "message_id": "q1", "content": "find it", "attachments": [],
           "user_id": "u", "config": {"agui_url": "https://example/agui",
               "tools": ["search"], "agui_max_tool_rounds": 2}}
    payloads = []
    outcomes = [
        {"content": "", "thinking": "", "message_id": "a1", "error": "", "outcome": {},
         "calls": [{"id": "tc1", "name": "search", "arguments": {"q": "x"}}]},
        {"content": "done", "thinking": "", "message_id": "a2", "error": "", "outcome": {},
         "calls": []},
    ]
    def fake_run(_job, _endpoint, _headers, payload, _doc):
        payloads.append(payload)
        return outcomes.pop(0)
    with patch("core.agui_client_runtime.resolve_relay_aware_url", return_value="https://example/agui"), \
         patch("core.agui_client_runtime._load_doc", return_value={"thread_id": "t1", "state": {}, "pending_interrupts": []}), \
         patch("core.agui_client_runtime._save_doc"), \
         patch("core.agui_client_runtime._messages", return_value=[{"id": "q1", "role": "user", "content": "find it"}]), \
         patch("core.agui_client_runtime._registry", return_value=object()), \
         patch("core.agui_client_runtime._tool_definitions", return_value=[{"name": "search"}]), \
         patch("core.agui_client_runtime._one_run", side_effect=fake_run), \
         patch("core.agui_client_runtime._persist_block"), \
         patch("core.agui_client_runtime._execute_tool", return_value="result"), \
         patch("core.agui_client_runtime._persist_tool_result"), \
         patch("core.agui_client_runtime._publish"), \
         patch("core.agui_client_runtime._finish") as finish:
        _run(job)
    assert payloads[0]["tools"] == [{"name": "search"}]
    assert payloads[1]["messages"][-1]["role"] == "tool"
    assert payloads[1]["messages"][-1]["content"] == "result"
    finish.assert_called_once_with(job, "done", message_id="a2", thinking="")


def test_webchat_and_openspace_expose_external_agui_runtime():
    root = "tasks/io/chat_ui/"
    with open(root + "resources_create_dialogs.js", encoding="utf-8") as handle:
        create = handle.read()
    with open(root + "resources_menus.js", encoding="utf-8") as handle:
        configure = handle.read()
    with open(root + "resources_render.js", encoding="utf-8") as handle:
        resources = handle.read()
    with open(root + "openspace_agents.js", encoding="utf-8") as handle:
        openspace = handle.read()
    with open(root + "openspace_runtime.js", encoding="utf-8") as handle:
        runtime = handle.read()
    with open(root + "sse_handlers_a.js", encoding="utf-8") as handle:
        webchat = handle.read()
    assert 'value="external_agui"' in create
    assert 'value="external_agui"' in configure
    assert "aRuntime === 'external_agui'" in resources
    assert "rec.runtimeKind === 'external_agui'" in openspace
    assert "activity.content" in runtime
    assert "activity.content" in webchat
    for event in ("agui_activity", "agui_step", "agui_state_snapshot",
                  "agui_state_delta", "agui_usage", "agui_custom"):
        assert "on('" + event + "'" in runtime
