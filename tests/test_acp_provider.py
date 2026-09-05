"""Generic outbound ACP provider integration and lifecycle tests."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from acp import RequestError
from acp.schema import PermissionOption
from acp.schema import (
    ContentToolCallContent,
    EmbeddedResourceContentBlock,
    TextContentBlock,
    ToolCallProgress,
)

from core import ServiceError
from core.llm_auth_modes import NONE, default_mode
from core.llm_client import (
    LLMClient,
    LLMMessage,
    LLMToolDefinition,
)
from core.llm_providers.acp import LLMAcpMixin, validate_acp_config
from services.llm_connection import LLMConnectionService


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "acp_runtime_agent.py"


@pytest.fixture
def stored_sessions(monkeypatch):
    values = {}

    def get_session(_self, conversation_id, service_id, agent_name):
        key = _self._acp_session_store_key(service_id, agent_name)
        return values.get((conversation_id, key), "")

    def set_session(
        _self, conversation_id, service_id, agent_name, session_id
    ):
        key = _self._acp_session_store_key(service_id, agent_name)
        values[(conversation_id, key)] = session_id

    monkeypatch.setattr(LLMAcpMixin, "_acp_get_stored_session", get_session)
    monkeypatch.setattr(LLMAcpMixin, "_acp_set_stored_session", set_session)
    return values


def _config(*, emit_tool=False, stale_session="", **overrides):
    env = {"PAWFLOW_ACP_FIXTURE_MODE": "provider"}
    if emit_tool:
        env["PAWFLOW_ACP_FIXTURE_EMIT_TOOL"] = "1"
    if stale_session:
        env["PAWFLOW_ACP_FIXTURE_STALE_SESSION"] = stale_session
    config = {
        "provider": "acp",
        "auth_mode": "none",
        "acp_command": sys.executable,
        "acp_args": [str(FIXTURE)],
        "acp_cwd": str(ROOT),
        "acp_env": env,
        "acp_mcp_mode": "none",
        "acp_use_client_io": False,
        "acp_reuse_process": True,
        "acp_load_session": True,
        "_service_id": "acp-service",
        "max_retries": 3,
        "max_context_size": 100,
    }
    config.update(overrides)
    return config


def _call(service, text="hello", *, content=None, **kwargs):
    message = LLMMessage(
        role="user",
        content=text if content is None else content,
        conversation_id="conv-acp",
    )
    return service.complete_stream(
        [message],
        call_user_id="user-acp",
        call_conversation_id="conv-acp",
        call_agent_name="assistant",
        call_event_cid="conv-acp",
        **kwargs,
    )


def _payload(response):
    return json.loads(response.content)


def _close(service):
    close = getattr(service._client, "_acp_close_all", None)
    if close is not None:
        close()


def _wait_for_active_prompt(service):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        sessions = getattr(service._client, "_acp_live_sessions", {})
        if any(live.active_handle is not None for live in sessions.values()):
            return
        time.sleep(0.01)
    raise AssertionError("ACP prompt did not become active")


def test_acp_is_registered_with_explicit_service_schema_and_none_auth():
    assert "acp" in LLMClient.PROVIDERS
    assert default_mode("acp", {}) == NONE
    service = LLMConnectionService(_config())
    schema = service.get_parameter_schema()
    for field in (
        "acp_command",
        "acp_args",
        "acp_cwd",
        "acp_env",
        "acp_auth_method_id",
        "acp_reuse_process",
        "acp_load_session",
        "acp_additional_directories",
        "acp_mcp_mode",
        "acp_use_client_io",
        "acp_title_override",
    ):
        assert field in schema
    settings = {}
    for rule in service.get_parameter_rules():
        if "acp" in rule.get("when", {}).get("provider", []):
            for field, attributes in rule.get("set", {}).items():
                settings.setdefault(field, {}).update(attributes)
    assert settings["auth_mode"]["default"] == "none"
    assert settings["acp_command"]["visible"] is True
    assert settings["acp_command"]["required"] is True
    service.connect()
    service.disconnect()


def test_acp_rejects_non_none_service_auth_mode():
    service = LLMConnectionService(
        _config(auth_mode="api_key", api_key="must-not-be-used")
    )
    with pytest.raises(ServiceError, match="auth_mode=none"):
        service.connect()


def test_acp_config_is_argv_only_and_validates_json_shapes(tmp_path):
    with pytest.raises(ValueError, match="acp_command is required"):
        validate_acp_config({"acp_cwd": str(tmp_path)})
    with pytest.raises(ValueError, match="array of strings"):
        validate_acp_config(
            {
                "acp_command": sys.executable,
                "acp_args": ["ok", 1],
                "acp_cwd": str(tmp_path),
            }
        )
    with pytest.raises(ValueError, match="variable name"):
        validate_acp_config(
            {
                "acp_command": sys.executable,
                "acp_args": [],
                "acp_cwd": str(tmp_path),
                "acp_env": {"BAD-NAME": "value"},
            }
        )


def test_service_clones_share_live_state_but_copy_request_registry():
    client = LLMClient(provider="acp", config=_config())
    registry = object()
    client.set_tool_registry(registry)
    clone = client.clone_for_call()
    assert clone._acp_live_sessions is client._acp_live_sessions
    assert clone._acp_live_lock is client._acp_live_lock
    assert clone._tool_registry is registry


def test_client_writes_require_matching_consumable_permission(monkeypatch):
    class Registry:
        def __init__(self):
            self.calls = []

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return ""

    registry = Registry()
    live = SimpleNamespace(
        session_id="session-1",
        registry=registry,
        grant_lock=threading.RLock(),
        write_grants=set(),
        cancel_event=threading.Event(),
    )
    client = LLMClient(provider="acp", config=_config())
    handlers = client._acp_client_handlers(
        live,
        True,
        user_id="user-acp",
        conversation_id="conv-acp",
        agent_name="assistant",
    )
    monkeypatch.setattr(
        "core.tool_approval.ToolApprovalGate.get_mode", lambda *_args: "auto"
    )
    monkeypatch.setattr(
        "core.tool_authorization.gate_for_runtime", lambda **_kwargs: None
    )

    with pytest.raises(RequestError):
        handlers.write_text_file("session-1", "/workspace/approved.txt", "one")

    tool_call = SimpleNamespace(
        kind="edit",
        title="Edit approved file",
        raw_input={"path": "/workspace/approved.txt"},
        locations=[],
        content=[],
    )
    options = [
        PermissionOption(
            option_id="allow", name="Allow once", kind="allow_once"
        )
    ]
    response = handlers.permission("session-1", tool_call, options)
    assert response.outcome.option_id == "allow"

    with pytest.raises(RequestError):
        handlers.write_text_file("session-1", "/workspace/other.txt", "wrong")
    handlers.write_text_file("session-1", "/workspace/approved.txt", "accepted")
    assert registry.calls == [
        (
            "write",
            {"path": "/workspace/approved.txt", "content": "accepted"},
        )
    ]
    with pytest.raises(RequestError):
        handlers.write_text_file("session-1", "/workspace/approved.txt", "twice")


def test_real_provider_inherits_environment_ignores_tool_definitions_and_streams(
    monkeypatch, stored_sessions
):
    monkeypatch.setenv("PAWFLOW_ACP_INHERITED", "present")
    service = LLMConnectionService(_config(emit_tool=True))
    text_chunks = []
    blocks = []
    try:
        response = _call(
            service,
            tools=[LLMToolDefinition("normal_tool", "normal", {})],
            callback=text_chunks.append,
            block_callback=lambda kind, payload: blocks.append((kind, payload)),
        )
        payload = _payload(response)
        assert payload["inherited"] == "present"
        assert "".join(text_chunks) == response.content
        assert [kind for kind, _payload_value in blocks] == [
            "tool_use",
            "tool_result",
        ]
        assert blocks[0][1]["id"].startswith("acp-")
        assert blocks[1][1]["tc_id"] == blocks[0][1]["id"]
        assert response.tokens_in == 0
        assert response.tokens_out == 0
        assert service.get_token_stats() == {
            "tokens_in": 0,
            "tokens_out": 0,
            "calls": 0,
        }
        assert response.finish_reason == "end_turn"
    finally:
        _close(service)


def test_service_call_clones_reuse_one_warm_process(stored_sessions):
    service = LLMConnectionService(_config())
    try:
        first = _payload(_call(service, "first"))
        second = _payload(_call(service, "second"))
        assert second["pid"] == first["pid"]
        assert second["session_id"] == first["session_id"]
        assert "second" in second["text"]
        assert "first" not in second["text"]
        assert service._client._acp_has_live_session(
            "acp-service", "user-acp", "conv-acp", "assistant"
        )
    finally:
        _close(service)


def test_ephemeral_call_isolated_from_foreground_process_and_session(
    stored_sessions,
):
    service = LLMConnectionService(_config())
    try:
        foreground = _payload(_call(service, "foreground"))
        ephemeral = _payload(
            _call(service, "ephemeral", call_ephemeral_stream=True)
        )
        resumed = _payload(_call(service, "foreground again"))

        assert ephemeral["pid"] != foreground["pid"]
        assert ephemeral["loaded"] is False
        assert resumed["pid"] == foreground["pid"]
        assert resumed["session_id"] == foreground["session_id"]
    finally:
        _close(service)


def test_failed_fresh_prompt_does_not_persist_session(
    monkeypatch, stored_sessions
):
    service = LLMConnectionService(_config())

    def fail_prompt(*_args, **_kwargs):
        raise RuntimeError("prompt submission failed")

    monkeypatch.setattr(
        "core.llm_providers.acp.AcpProcessSession.begin_prompt", fail_prompt
    )
    try:
        with pytest.raises(ServiceError, match="prompt submission failed"):
            _call(service)
        assert stored_sessions == {}
    finally:
        _close(service)


def test_persisted_session_key_changes_with_runtime_config():
    first = LLMClient(
        provider="acp", config=_config(acp_env={"ACP_REVISION": "one"})
    )
    second = LLMClient(
        provider="acp", config=_config(acp_env={"ACP_REVISION": "two"})
    )

    assert first._acp_session_store_key(
        "acp-service", "assistant"
    ) != second._acp_session_store_key("acp-service", "assistant")


def test_runtime_config_change_closes_replaced_process(stored_sessions):
    service = LLMConnectionService(_config())
    old_process = None
    try:
        first = _payload(_call(service, "first"))
        old_live = next(iter(service._client._acp_live_sessions.values()))
        old_process = old_live.process
        service._client._config_ref["acp_env"] = {
            "PAWFLOW_ACP_FIXTURE_MODE": "provider",
            "ACP_REVISION": "two",
        }

        second = _payload(_call(service, "second"))
        assert second["pid"] != first["pid"]
        assert second["loaded"] is False
        assert old_process is not None
        assert not old_process.is_running
    finally:
        _close(service)
        if old_process is not None and old_process.is_running:
            old_process.close(force=True)


def test_prompt_response_usage_is_per_turn_not_cumulative():
    client = LLMClient(provider="acp", config=_config())
    live = SimpleNamespace(last_usage={})
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            cached_read_tokens=3,
            cached_write_tokens=2,
        )
    )

    assert client._acp_usage_delta(live, response) == (11, 7, 3, 2)
    assert client._acp_usage_delta(live, response) == (11, 7, 3, 2)


def test_acp_uses_session_context_observation():
    client = LLMClient(provider="acp", config=_config())

    assert "acp" in client._SESSION_CONTEXT_PROVIDERS


def test_close_releases_internal_token_even_when_process_close_fails(
    monkeypatch,
):
    class FailingProcess:
        def close(self, *, force):
            del force
            raise RuntimeError("close failed")

    client = LLMClient(provider="acp", config=_config())
    key = client._acp_stream_key(
        "acp-service", "user-acp", "conv-acp", "assistant"
    )
    live = SimpleNamespace(
        process=FailingProcess(),
        active_handle=object(),
        session_id="session-1",
        internal_token="secret-token",
        cancel_event=threading.Event(),
        force_stop_event=threading.Event(),
        grant_lock=threading.RLock(),
        write_grants=set(),
    )
    sessions, _lock = client._acp_shared_state()
    sessions[key] = live
    revoked = []
    monkeypatch.setattr(
        "core.internal_auth.revoke_token", revoked.append
    )

    with pytest.raises(RuntimeError, match="close failed"):
        client._acp_close_entry(key, live, force=True)
    assert revoked == ["secret-token"]
    assert live.internal_token == ""


def test_process_restart_loads_the_persisted_session(stored_sessions):
    service = LLMConnectionService(_config())
    try:
        first = _payload(_call(service, "first"))
        service._client._acp_close_all()
        second = _payload(_call(service, "after restart"))
        assert second["pid"] != first["pid"]
        assert second["loaded"] is True
        assert second["session_id"] == first["session_id"]
    finally:
        _close(service)


def test_stale_load_falls_back_once_to_new_session(stored_sessions):
    service = LLMConnectionService(
        _config(stale_session="runtime-session")
    )
    try:
        _call(service, "first")
        service._client._acp_close_all()
        recovered = _payload(_call(service, "recover"))
        assert recovered["stale_load_seen"] is True
        assert recovered["loaded"] is False
        assert recovered["new_session_calls"] == 1
    finally:
        _close(service)


def test_image_content_uses_typed_acp_blocks(stored_sessions):
    service = LLMConnectionService(_config())
    try:
        payload = _payload(
            _call(
                service,
                content=[
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,aGVsbG8="
                        },
                    },
                ],
            )
        )
        assert payload["types"] == ["text", "image"]
        assert payload["mime_types"] == ["", "image/png"]
    finally:
        _close(service)


def test_http_image_url_is_safely_materialized(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        headers = {"Content-Type": "image/png", "Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_content(self, chunk_size):
            captured["chunk_size"] = chunk_size
            return iter((b"hello",))

    def resolve(url, **kwargs):
        captured["resolve"] = (url, kwargs)
        return "https://safe.example/image.png"

    def get(url, **kwargs):
        captured["get"] = (url, kwargs)
        return Response()

    monkeypatch.setattr("core.relay_proxy_url.resolve_relay_aware_url", resolve)
    monkeypatch.setattr("requests.get", get)
    client = LLMClient(provider="acp", config=_config())
    block = client._acp_media_block(
        {
            "type": "image_url",
            "image_url": {"url": "https://example.test/image.png"},
        },
        user_id="user-acp",
        conversation_id="conv-acp",
        capabilities=SimpleNamespace(image=True, audio=False),
    )

    assert block.mime_type == "image/png"
    assert base64.b64decode(block.data) == b"hello"
    assert captured["resolve"][1]["allow_private"] is False
    assert captured["get"][1]["allow_redirects"] is False
    assert captured["get"][1]["stream"] is True


def test_filestore_document_is_embedded_when_supported(monkeypatch):
    class Store:
        def get_required(self, file_id, *, user_id, conversation_id):
            assert (file_id, user_id, conversation_id) == (
                "file-1",
                "user-acp",
                "conv-acp",
            )
            return "note.txt", b"embedded text", "text/plain"

    monkeypatch.setattr("core.file_store.FileStore.instance", lambda: Store())
    client = LLMClient(provider="acp", config=_config())
    block = client._acp_media_block(
        {"type": "file_ref", "file_id": "file-1"},
        user_id="user-acp",
        conversation_id="conv-acp",
        capabilities=SimpleNamespace(
            image=False, audio=False, embedded_context=True
        ),
    )

    assert isinstance(block, EmbeddedResourceContentBlock)
    assert block.resource.text == "embedded text"
    assert block.resource.uri == "fs://filestore/file-1/note.txt"


def test_structured_tool_result_is_not_dropped():
    update = ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id="tool-1",
        status="completed",
        content=[
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(
                    type="text", text="structured result"
                ),
            )
        ],
    )

    assert "structured result" in LLMClient._acp_tool_output(update)


def test_soft_cancel_returns_cancelled_and_closes_the_live_process(stored_sessions):
    service = LLMConnectionService(_config())
    outcome = {}

    def run():
        try:
            outcome["response"] = _call(service, "provider-wait")
        except BaseException as exc:  # noqa: BLE001 - test thread boundary
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    try:
        _wait_for_active_prompt(service)
        service._client._acp_abort_active(force=False)
        thread.join(5)
        assert not thread.is_alive()
        assert "error" not in outcome
        assert outcome["response"].finish_reason == "cancelled"
        assert not service._client._acp_live_sessions
    finally:
        _close(service)


def test_force_stop_targets_call_clone_and_next_turn_recovers(stored_sessions):
    service = LLMConnectionService(_config())
    outcome = {}

    def run():
        try:
            outcome["response"] = _call(service, "provider-wait")
        except BaseException as exc:  # noqa: BLE001 - test thread boundary
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    try:
        _wait_for_active_prompt(service)
        service._client.abort()
        thread.join(5)
        assert not thread.is_alive()
        assert "error" in outcome
        assert not service._client._acp_live_sessions
        service._client.reset_abort()
        assert _payload(_call(service, "next"))["text"].endswith("next")
    finally:
        _close(service)


def test_consumed_acp_prompt_is_never_retried(monkeypatch):
    calls = []
    client = LLMClient(provider="acp", config=_config())

    def fail(*_args, **_kwargs):
        calls.append("prompt")
        raise RuntimeError("429 after prompt accepted")

    monkeypatch.setattr(client, "_stream_acp", fail)
    monkeypatch.setattr("core._llm_client_driver.time.sleep", lambda *_: None)
    with pytest.raises(RuntimeError, match="429"):
        client.complete_stream([
            LLMMessage(
                role="user", content="hello", conversation_id="conv-acp"
            )
        ])
    assert calls == ["prompt"]
