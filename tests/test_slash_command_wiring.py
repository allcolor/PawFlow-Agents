import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import FlowFile
from tasks.ai.actions._cmd_help import HELP
from tasks.ai.actions._command_result import (
    decorate_command_flowfiles,
    format_command_payload,
)
from tasks.ai.actions.command_dispatch import (
    _handle_command_dispatch,
    _parse_command,
)
from tasks.ai.actions._agentres_k1 import _handle_agentres_k1
from tasks.ai.actions._conv_core import _handle_conv_core
from tasks.ai.actions.memory_prompts import _handle_memory_prompts
from tasks.ai._agent_actions_conv import _AgentActionsConvMixin
from tasks.io._telegram_client_helpers import _format_telegram_command_result


@pytest.mark.parametrize(
    ("text", "action", "expected"),
    [
        ("/rename New title", "set_conv_title", {"title": "New title"}),
        ("/search needle", "search_messages", {"query": "needle"}),
        ("/setname assistant Helper", "set_agent_nickname", {"nickname": "Helper"}),
        ("/setname assistant", "set_agent_nickname", {"nickname": ""}),
        ("/agent setname assistant Helper", "set_agent_nickname", {"agent_name": "assistant"}),
        ("/agent reviewer", "select_agent", {"name": "reviewer"}),
        ("/agent delete assistant", "delete_agent", {"name": "assistant"}),
        ("/msg reviewer inspect this", "agent_msg", {"target_agent": "reviewer", "message": "inspect this"}),
        ("/stop reviewer", "cancel", {"agent_name": "reviewer"}),
        ("/stop reviewer -f", "cancel", {"agent_name": "reviewer"}),
        ("/llm openai reviewer", "set_llm_service", {"llm_service": "openai", "agent_name": "reviewer"}),
        ("/cost reviewer", "cost", {"agent": "reviewer"}),
        ("/add-secret TOKEN value", "add_secret", {"key": "TOKEN"}),
        ("/add-variable REGION eu", "add_variable", {"key": "REGION"}),
        ("/activate agent reviewer", "activate_resource", {"resource_type": "agent"}),
        ("/share skill audit target-conv", "share_resource", {"target_conversation_id": "target-conv"}),
        ("/link telegram 123", "link_account", {"provider_id": "123"}),
        ("/uninstall old_tool", "uninstall_tool", {"tool_name": "old_tool"}),
        ("/view README.md", "fs_read_file", {"path": "README.md"}),
        ("/rebuild-full reviewer", "rebuild_full", {"agent_name": "reviewer"}),
        ("/memory add release checklist ready", "add_memory", {"text": "release checklist ready"}),
        ("/memory search release checklist", "search_memories", {"query": "release checklist"}),
        ("/service enable svc", "service_enable", {"service_id": "svc"}),
        ("/flow start flow-1 key=value", "start_flow", {"parameters": {"key": "value"}}),
        ("/autoconv on @reviewer 6/1m", "random_thought", {"sub": "on", "agent": "reviewer"}),
        ("/loop 5m check deploy", "loop_start", {"interval_seconds": 300}),
        ("/encrypt status", "conv_encrypt_status", {}),
        ("/clear-store ALL", "clear_store", {"scope": "all_agents"}),
    ],
)
def test_repaired_slash_command_routes_match_handler_contract(text, action, expected):
    parsed = _parse_command(text, "conv1", "alice", "assistant")

    assert parsed["action"] == action
    assert parsed["conversation_id"] == "conv1"
    for key, value in expected.items():
        assert parsed[key] == value


def test_every_help_registry_command_has_a_parse_route():
    # Empty/default invocation may produce a usage display, a client-only
    # directive, or a real action.  It must never disappear or raise.
    missing = []
    for command in HELP:
        parsed = _parse_command(command, "conv1", "alice", "assistant")
        if not parsed or not (
            parsed.get("action")
            or parsed.get("display")
            or parsed.get("_client_only")
        ):
            missing.append(command)
    assert missing == []


def test_structured_tool_call_syntax_is_parsed_without_eval():
    parsed = _parse_command(
        '/call read(path="README.md", offset=2)',
        "conv1", "alice", "assistant",
    )
    assert parsed["action"] == "call_tool"
    assert parsed["tool_name"] == "read"
    assert parsed["arguments"] == {"path": "README.md", "offset": 2}

    rejected = _parse_command(
        '/call read(path=__import__("os").getcwd())',
        "conv1", "alice", "assistant",
    )
    assert "Invalid /call arguments" in rejected["display"]


def test_display_only_parse_result_returns_normally():
    ff = FlowFile(content=b"")
    body = {
        "action": "command", "text": "/hooks add pre cmd",
        "conversation_id": "conv1",
    }
    result = _handle_command_dispatch(None, "command", body, None, "alice", ff)
    payload = json.loads(result[0].get_content())
    assert "signed agent-hook resources" in payload["display"]


def test_command_result_decorator_keeps_machine_json_and_adds_display():
    ff = FlowFile(content=json.dumps({
        "tasks": [{"task_id": "t_1", "status": "active", "agent": "reviewer"}],
        "count": 1,
    }).encode())
    decorate_command_flowfiles([ff])
    payload = json.loads(ff.get_content())
    assert payload["tasks"][0]["task_id"] == "t_1"
    assert "t_1" in payload["display"]
    assert payload["display"].startswith("Tasks (1):")


def test_setname_without_nickname_removes_existing_mapping():
    store = MagicMock()
    store.get_extra.return_value = {"assistant": "Helper"}
    owner = SimpleNamespace(_resolve_agent_name=lambda name, _conv_id: name)
    flowfile = FlowFile(content=b"")

    result = _handle_agentres_k1(
        owner,
        "set_agent_nickname",
        {"conversation_id": "conv1", "agent_name": "assistant", "nickname": ""},
        store,
        "alice",
        flowfile,
    )

    payload = json.loads(result[0].get_content())
    assert payload["reset"] is True
    store.set_extra.assert_called_once_with("conv1", "agent_nicknames", {})


def test_conversation_title_and_message_search_handlers():
    store = MagicMock()
    flowfile = FlowFile(content=b"")

    renamed = _handle_conv_core(
        None, "set_conv_title",
        {"conversation_id": "conv1", "title": "Release notes"},
        store, "alice", flowfile,
    )
    assert json.loads(renamed[0].get_content())["title"] == "Release notes"
    store.set_extra.assert_called_once_with(
        "conv1", "title", "Release notes", user_id="alice")

    store.load.return_value = [
        {"id": "m1", "role": "user", "content": "Ship the release"},
        {"id": "m2", "role": "assistant", "content": "Tests are green"},
    ]
    searched = _handle_conv_core(
        None, "search_messages",
        {"conversation_id": "conv1", "query": "release"},
        store, "alice", FlowFile(content=b""),
    )
    payload = json.loads(searched[0].get_content())
    assert payload["count"] == 1
    assert payload["matches"][0]["msg_id"] == "m1"


def test_memory_search_handler_returns_machine_readable_matches(monkeypatch):
    entry = SimpleNamespace(
        id="mem1", text="Release checklist", tags=["release"],
        agent="assistant", conversation_id="conv1", category="project",
    )
    memory_store = MagicMock()
    memory_store.recall.return_value = [entry]
    from core.memory_store import MemoryStore
    monkeypatch.setattr(MemoryStore, "instance", lambda: memory_store)

    result = _handle_memory_prompts(
        None, "search_memories",
        {"conversation_id": "conv1", "agent_name": "assistant",
         "query": "release"},
        MagicMock(), "alice", FlowFile(content=b""),
    )

    payload = json.loads(result[0].get_content())
    assert payload["count"] == 1
    assert payload["memories"][0]["id"] == "mem1"
    memory_store.recall.assert_called_once_with(
        "alice", query="release", limit=50,
        agent_name="assistant", conversation_id="conv1")


def test_autoconv_second_range_matches_random_thought_frequency_contract():
    assert _AgentActionsConvMixin._parse_thought_frequency(
        "60-240s") == (60, 240)


def test_telegram_never_falls_back_to_raw_json_for_command_results():
    raw = json.dumps({
        "tasks": [{"task_id": "t_1", "status": "active", "agent": "reviewer"}],
        "count": 1,
    })
    rendered = _format_telegram_command_result(raw)
    assert rendered.startswith("Tasks (1):")
    assert "t_1" in rendered
    assert not rendered.lstrip().startswith("{")


def test_generic_command_formatter_handles_mapping_results():
    rendered = format_command_payload({"ok": True, "title": "Renamed"})
    assert rendered == "Title: Renamed"


def test_pawcode_authenticated_help_uses_server_registry():
    from pawflow_cli._app_commands import _PawCodeCommandsMixin

    api = SimpleNamespace(send_action=MagicMock(return_value={
        "help": "## Available Commands\n`/help` — Help",
    }))
    renderer = SimpleNamespace(
        print_markdown=MagicMock(), print_system=MagicMock(), print_error=MagicMock())
    app = SimpleNamespace(
        session_token="token", selected_agent="assistant",
        conversation_id="conv1", api=api, renderer=renderer,
        _handle_agent_stream_command=lambda *_: False,
    )

    _PawCodeCommandsMixin._handle_command(app, "/help")

    api.send_action.assert_called_once_with(
        "command", text="/help", agent_name="assistant",
        conversation_id="conv1")
    renderer.print_markdown.assert_called_once()


def test_webchat_help_and_vscode_domain_commands_delegate_to_server():
    webchat = Path("tasks/io/chat_ui/commands.js").read_text(encoding="utf-8")
    vscode_commands = Path(
        "pawflow-vscode/media/webview/commands.js").read_text(encoding="utf-8")
    vscode_chat = Path(
        "pawflow-vscode/media/webview/chat.js").read_text(encoding="utf-8")

    assert "if (!_LOCAL_COMMANDS.has(resolved))" in webchat
    assert "'/task'" not in webchat.split("const _LOCAL_COMMANDS", 1)[1].split("]);", 1)[0]
    assert "'/audio'" not in webchat.split("const _LOCAL_COMMANDS", 1)[1].split("]);", 1)[0]
    assert "'/relay-audio'" in webchat.split("const _LOCAL_COMMANDS", 1)[1].split("]);", 1)[0]
    assert "if (data.display) {\n      addMsg('system', data.display);" in webchat
    assert "if (data.display) { addMsg('system', data.display); return; }" not in webchat
    assert "sendCmd('command', JSON.stringify({" in vscode_commands
    assert "if (!localCommands[cmd])" in vscode_commands
    assert "else if (d.display) addMsg('system', d.display);" in vscode_chat
