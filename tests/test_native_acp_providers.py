"""Native ACP interaction contracts, provider defaults, and completion ownership."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from acp import RequestError
from acp.schema import PermissionOption, PromptResponse

from core.acp.grok_session import GrokAcpProcessSession
from core.acp.native_extensions import NativeAcpExtensions
from core.acp.process_session import AcpProcessSession
from core.acp.session_state import AcpEventChannel
from core.llm_providers.acp import LLMAcpMixin
from core.llm_providers.cursor_acp import LLMCursorAcpMixin, validate_cursor_acp_config
from core.llm_providers.grok_build_acp import (
    LLMGrokBuildAcpMixin,
    validate_grok_build_acp_config,
)


def live_session():
    return SimpleNamespace(
        session_id="session",
        registry=None,
        cancel_event=threading.Event(),
        process=SimpleNamespace(events=AcpEventChannel(), generation=1),
        grant_lock=threading.RLock(),
        write_grants=set(),
    )


def native(provider="cursor-acp"):
    return NativeAcpExtensions(
        provider=provider,
        live=live_session(),
        user_id="user",
        conversation_id="conversation",
        agent_name="assistant",
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_interaction_options_bypass_automatic_authorization(monkeypatch, enabled):
    client = LLMAcpMixin()
    client.provider = "antigravity-acp"
    live = live_session()
    seen = []

    def answer(questions, **kwargs):
        seen.append((questions, kwargs))
        return {"native-question": "interaction_2"}

    monkeypatch.setattr("core.acp.native_extensions.collect_native_questions", answer)
    monkeypatch.setattr(
        "core.tool_approval.ToolApprovalGate.get_mode",
        lambda *_: pytest.fail("Questions must bypass the approval gate"),
    )
    handlers = client._acp_client_handlers(
        live,
        enabled,
        user_id="user",
        conversation_id="conversation",
        agent_name="assistant",
    )
    options = [
        PermissionOption(option_id=f"interaction_{i}", name=f"Choice {i}", kind="allow_once")
        for i in (1, 2)
    ]
    tool = SimpleNamespace(tool_call_id="native-question", title="Which choice?", kind="edit")
    result = handlers.permission("session", tool, options)
    assert result.outcome.option_id == "interaction_2"
    assert [o["value"] for o in seen[0][0][0]["options"]] == ["interaction_1", "interaction_2"]
    assert not live.write_grants
    assert seen[0][1]["provider"] == "antigravity-acp"
    with pytest.raises(RequestError):
        handlers.permission("stale", tool, options)


@pytest.mark.parametrize("answer", [None, {"q": "allow_once"}, {"q": "not-supplied"}])
def test_interaction_does_not_guess_when_cancelled_or_invalid(monkeypatch, answer):
    client = LLMAcpMixin()
    client.provider = "antigravity-acp"
    monkeypatch.setattr(
        "core.acp.native_extensions.collect_native_questions", lambda *_a, **_k: answer
    )
    handlers = client._acp_client_handlers(
        live_session(),
        True,
        user_id="u",
        conversation_id="c",
        agent_name="a",
    )
    result = handlers.permission(
        "session",
        SimpleNamespace(tool_call_id="q", title="Choose"),
        [PermissionOption(option_id="interaction_exact", name="Exact", kind="allow_once")],
    )
    assert result.outcome.outcome == "cancelled"


def test_cursor_answers_use_ids_even_when_labels_repeat(monkeypatch):
    extension = native()
    monkeypatch.setattr(extension, "ask", lambda questions, _: {"q": ["two", "one"]})
    result = extension.request(
        "_cursor/ask_question",
        {
            "toolCallId": "call",
            "questions": [
                {
                    "id": "q",
                    "prompt": "Choose",
                    "allowMultiple": True,
                    "options": [{"id": "one", "label": "Same"}, {"id": "two", "label": "Same"}],
                }
            ],
        },
    )
    assert result == {
        "outcome": {
            "outcome": "answered",
            "answers": [
                {"questionId": "q", "selectedOptionIds": ["two", "one"]},
            ],
        }
    }


def test_cursor_rejects_label_instead_of_native_id(monkeypatch):
    extension = native()
    monkeypatch.setattr(extension, "ask", lambda *_: {"q": "Label"})
    with pytest.raises(RequestError):
        extension.request(
            "cursor/ask_question",
            {
                "toolCallId": "call",
                "questions": [
                    {
                        "id": "q",
                        "prompt": "Choose",
                        "options": [{"id": "opaque", "label": "Label"}],
                    }
                ],
            },
        )


def test_cursor_plan_requires_explicit_decision(monkeypatch):
    extension = native()
    asked = []

    def answer(questions, _):
        asked.extend(questions)
        return {"plan": "rejected"}

    monkeypatch.setattr(extension, "ask", answer)
    assert extension.request(
        "cursor/create_plan",
        {
            "toolCallId": "plan",
            "plan": "# Full plan\nDo the work",
            "todos": [],
        },
    ) == {"outcome": {"outcome": "rejected"}}
    assert asked[0]["question"] == "# Full plan\nDo the work"
    monkeypatch.setattr(extension, "ask", lambda *_: None)
    assert extension.request(
        "cursor/create_plan",
        {
            "toolCallId": "plan",
            "plan": "Work",
            "todos": [],
        },
    ) == {"outcome": {"outcome": "cancelled"}}


def test_cursor_todos_merge_and_replace_emit_full_snapshots():
    extension = native()
    for merge, todos in (
        (False, [{"id": "1", "content": "First", "status": "pending"}]),
        (
            True,
            [
                {"id": "1", "content": "First", "status": "completed"},
                {"id": "2", "content": "Second", "status": "in_progress"},
            ],
        ),
        (False, [{"id": "3", "content": "Replacement", "status": "pending"}]),
    ):
        extension.notification(
            "_cursor/update_todos", {"toolCallId": "todos", "merge": merge, "todos": todos}
        )
    updates = extension.live.process.events.drain()
    assert len(updates) == 3
    assert [t["id"] for t in updates[1].payload.raw_output["todos"]] == ["1", "2"]
    assert updates[1].payload.raw_output["todos"][0]["status"] == "completed"
    assert list(extension.todos) == ["3"]
    assert len({event.payload.tool_call_id for event in updates}) == 3


def test_grok_wrapped_questions_return_labels_and_annotations(monkeypatch):
    extension = native("grok-build-acp")
    seen = []

    def answer(questions, request_id):
        seen.extend(questions)
        assert request_id == "call"
        return {"qid": ["Choice", "Use these details"]}

    monkeypatch.setattr(extension, "ask", answer)
    result = extension.request(
        "_x.ai/ask_user_question",
        {
            "method": "x.ai/ask_user_question",
            "params": {
                "sessionId": "session",
                "toolCallId": "call",
                "mode": "plan",
                "questions": [
                    {
                        "id": "qid",
                        "question": "Question text",
                        "options": [{"id": "opaque", "label": "Choice", "preview": "Preview"}],
                    }
                ],
            },
        },
    )
    assert result == {
        "outcome": "accepted",
        "answers": {"Question text": ["Choice"]},
        "annotations": {"Question text": {"preview": "Preview", "notes": "Use these details"}},
    }
    assert seen[0]["allow_other"] is True
    assert seen[0]["options"] == [{"value": "Choice", "label": "Choice"}]


def test_grok_free_text_and_plan_changes(monkeypatch):
    extension = native("grok-build-acp")
    monkeypatch.setattr(extension, "ask", lambda *_: {"Text": "Free answer"})
    result = extension.request(
        "x.ai/ask_user_question",
        {
            "sessionId": "session",
            "toolCallId": "question",
            "mode": "default",
            "questions": [{"question": "Text", "options": []}],
        },
    )
    assert result["answers"] == {"Text": ["Other"]}
    assert result["annotations"] == {"Text": {"notes": "Free answer"}}
    answers = iter([{"plan": "request_changes"}, {"feedback": "Add tests"}])
    monkeypatch.setattr(extension, "ask", lambda *_: next(answers))
    assert extension.request(
        "x.ai/exit_plan_mode",
        {
            "sessionId": "session",
            "toolCallId": "plan",
            "planContent": "The plan",
        },
    ) == {"outcome": "request_changes", "feedback": "Add tests"}


@pytest.mark.parametrize(
    "provider,method",
    [
        ("cursor-acp", "cursor/ask_question"),
        ("grok-build-acp", "x.ai/ask_user_question"),
    ],
)
def test_cancelled_questions_and_stale_sessions(monkeypatch, provider, method):
    extension = native(provider)
    extension.live.cancel_event.set()
    params = {"sessionId": "session", "toolCallId": "call", "questions": []}
    expected = (
        {"outcome": {"outcome": "cancelled"}}
        if provider == "cursor-acp"
        else {"outcome": "cancelled"}
    )
    assert extension.request(method, params) == expected
    params["sessionId"] = "other"
    with pytest.raises(RequestError):
        extension.request(method, params)


@pytest.mark.parametrize("args", [None, "", [], "[]", " [ ] "])
def test_provider_defaults_use_managed_cli(tmp_path, monkeypatch, args):
    monkeypatch.setattr("core.llm_providers.acp.shutil.which", lambda command: "/bin/" + command)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    config = {"acp_cwd": str(tmp_path), "acp_args": args}
    cursor = validate_cursor_acp_config(config)
    grok = validate_grok_build_acp_config(config)
    assert (cursor["command"], cursor["args"], cursor["auth_method_id"]) == (
        "cursor-agent",
        ("acp",),
        "cursor_login",
    )
    assert (grok["command"], grok["args"], grok["auth_method_id"]) == (
        "grok",
        ("agent", "stdio"),
        "cached_token",
    )
    assert (
        validate_grok_build_acp_config({**config, "acp_env": {"XAI_API_KEY": "key"}})[
            "auth_method_id"
        ]
        == "xai.api_key"
    )
    with pytest.raises(ValueError, match="acp_cwd"):
        validate_cursor_acp_config({})
    with pytest.raises(ValueError, match="array of strings"):
        validate_grok_build_acp_config({**config, "acp_args": [1]})


def test_provider_mro_and_grok_headless_auth(tmp_path, monkeypatch):
    class Client(LLMCursorAcpMixin, LLMGrokBuildAcpMixin, LLMAcpMixin):
        pass

    client = Client()
    client.provider = "grok-build-acp"
    client._config_ref = {"acp_command": sys.executable, "acp_cwd": str(tmp_path)}
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    config = client._acp_config()
    assert client._acp_process_class() is GrokAcpProcessSession
    calls = []
    client._acp_authenticate(
        SimpleNamespace(call=lambda *args, **kwargs: calls.append((args, kwargs))),
        SimpleNamespace(auth_methods=[SimpleNamespace(id="cached_token")]),
        config,
    )
    assert calls == [(("authenticate",), {"method_id": "cached_token", "headless": True})]


@pytest.mark.asyncio
async def test_grok_completion_ids_isolate_late_notifications_and_sessions():
    session = GrokAcpProcessSession(sys.executable, [])
    requests = asyncio.Queue()

    class Connection:
        async def prompt(self, **kwargs):
            await requests.put(kwargs)
            await asyncio.Future()

    first = asyncio.create_task(session._prompt_response(Connection(), "s", []))
    request = await requests.get()
    prompt_id = request["promptId"]
    assert request["requestId"] == prompt_id
    for params in (
        {"sessionId": "wrong", "promptId": prompt_id},
        {"sessionId": "s", "promptId": "wrong"},
        {"sessionId": "s"},
    ):
        await session._completion_notification(
            "_x.ai/session/prompt_complete", {**params, "stopReason": "end_turn"}
        )
        assert not first.done()
    await session._completion_notification(
        "_x.ai/session/prompt_complete",
        {
            "sessionId": "s",
            "promptId": prompt_id,
            "stopReason": "end_turn",
        },
    )
    assert (await first).stop_reason == "end_turn"
    second = asyncio.create_task(session._prompt_response(Connection(), "s", []))
    next_request = await requests.get()
    await session._completion_notification(
        "x.ai/session/prompt_complete",
        {
            "sessionId": "s",
            "promptId": prompt_id,
            "stopReason": "end_turn",
        },
    )
    assert not second.done()
    await session._completion_notification(
        "x.ai/session/prompt_complete",
        {
            "sessionId": "s",
            "promptId": next_request["promptId"],
            "stopReason": "cancelled",
        },
    )
    assert (await second).stop_reason == "cancelled"
    assert not session._pending_completions


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["rate_limit", "error", None, "unknown"])
async def test_grok_completion_failures_do_not_report_success(reason):
    session = GrokAcpProcessSession(sys.executable, [])

    class Connection:
        async def prompt(self, **kwargs):
            await session._completion_notification(
                "x.ai/session/prompt_complete",
                {
                    "sessionId": "s",
                    "promptId": kwargs["promptId"],
                    "stopReason": reason,
                },
            )
            await asyncio.Future()

    with pytest.raises(RequestError):
        await session._prompt_response(Connection(), "s", [])
    assert not session._pending_completions


@pytest.mark.asyncio
async def test_standard_grok_response_cleans_up_fallback_and_cancellation():
    session = GrokAcpProcessSession(sys.executable, [])

    class Connection:
        async def prompt(self, **kwargs):
            return PromptResponse(stop_reason="max_tokens")

    assert (await session._prompt_response(Connection(), "s", [])).stop_reason == "max_tokens"
    assert not session._pending_completions


@pytest.mark.parametrize(
    "provider,process_type",
    [
        ("cursor", AcpProcessSession),
        ("grok", GrokAcpProcessSession),
    ],
)
@pytest.mark.parametrize("prefix", ["underscore", "plain"])
def test_native_questions_round_trip_over_stdio(monkeypatch, provider, process_type, prefix):
    fixture = Path(__file__).parent / "fixtures" / "native_acp_agent.py"
    live = live_session()
    client = LLMAcpMixin()
    client.provider = "cursor-acp" if provider == "cursor" else "grok-build-acp"
    monkeypatch.setattr(
        "core.acp.native_extensions.collect_native_questions",
        lambda *_a, **_k: {"q": "second" if provider == "cursor" else "Second"},
    )
    handlers = client._acp_client_handlers(
        live,
        False,
        user_id="user",
        conversation_id="conversation",
        agent_name="assistant",
    )
    process = process_type(sys.executable, [str(fixture), provider, prefix], handlers=handlers)
    live.process = process
    try:
        process.start()
        created = process.call("new_session", cwd=str(fixture.parent), mcp_servers=[])
        live.session_id = created.session_id
        for _ in range(2):
            handle = process.begin_prompt(live.session_id, [])
            assert handle.result(timeout=3).stop_reason == "end_turn"
            events = process.events.drain()
            update = next(event.payload for event in events if event.kind == "update")
            answer = json.loads(update.content.text)
            if provider == "cursor":
                assert answer["outcome"]["answers"][0]["selectedOptionIds"] == ["second"]
            else:
                assert answer == {"outcome": "accepted", "answers": {"Choose": ["Second"]}}
    finally:
        process.close(force=True)


@pytest.mark.asyncio
async def test_grok_cancel_cleans_up_completion_waiter():
    session = GrokAcpProcessSession(sys.executable, [])
    ready = asyncio.Event()

    class WaitingConnection:
        async def prompt(self, **kwargs):
            ready.set()
            await asyncio.Future()

    waiting = asyncio.create_task(session._prompt_response(WaitingConnection(), "s", []))
    await ready.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not session._pending_completions
