import json

import core.paths as paths
from core.todo_store import TodoStore
from services.cc_interactive_event_service import (
    CCInteractiveEventService, _native_task_id)


def _service(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "TODOLISTS_DIR", tmp_path / "todolists")
    TodoStore._instance = None
    service = CCInteractiveEventService(
        {"token": "tok", "_service_id": "events"})
    service.register_session(
        "sess", user_id="u", conversation_id="c", agent_name="assistant",
        provider="claude-code-interactive")
    return service


def _publish(service, event):
    service.publish_event("sess", event, block=False)


def test_native_task_id_parses_json_and_text():
    assert _native_task_id('{"task": {"taskId": "42"}}') == "42"
    assert _native_task_id("Task #17 created successfully") == "17"
    assert _native_task_id("Created task id: td-native") == "td-native"


def test_successful_native_create_is_mirrored_once(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    use = {
        "type": "tool_use", "tool_use_id": "call-1", "name": "TaskCreate",
        "arguments": {
            "subject": "Implement helper avatar",
            "description": "Expose semantic controls",
            "activeForm": "Implementing helper avatar",
        },
    }
    result = {
        "type": "tool_result", "tool_use_id": "call-1",
        "content": "Task #7 created successfully", "is_error": False,
    }
    _publish(service, use)
    assert TodoStore.instance().list_tasks("u", "c", "assistant") == []
    _publish(service, result)
    _publish(service, use)
    _publish(service, result)

    tasks = TodoStore.instance().list_tasks("u", "c", "assistant")
    assert len(tasks) == 1
    assert tasks[0]["external_id"] == "7"
    assert tasks[0]["source_call_id"] == "call-1"
    assert tasks[0]["active_form"] == "Implementing helper avatar"


def test_failed_native_create_does_not_mutate_store(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    _publish(service, {
        "type": "tool_use", "tool_use_id": "call-bad", "name": "TaskCreate",
        "arguments": {"subject": "must not exist"},
    })
    _publish(service, {
        "type": "tool_result", "tool_use_id": "call-bad",
        "content": "permission denied", "is_error": True,
    })
    assert TodoStore.instance().list_tasks("u", "c", "assistant") == []


def test_native_update_resolves_external_id_and_merges_dependencies(
        tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    store = TodoStore.instance()
    task = store.create(
        "u", "c", "assistant", subject="work", external_id="9",
        blocks=["1"])
    _publish(service, {
        "type": "tool_use", "tool_use_id": "call-update",
        "name": "TaskUpdate", "arguments": {
            "taskId": "9", "status": "in_progress",
            "addBlocks": ["1", "2"], "metadata": {"source": "claude"},
        },
    })
    _publish(service, {
        "type": "tool_result", "tool_use_id": "call-update",
        "content": json.dumps({"ok": True}), "is_error": False,
    })
    updated = store.get("u", "c", "assistant", task["id"])
    assert updated["status"] == "in_progress"
    assert updated["blocks"] == ["1", "2"]
    assert updated["metadata"] == {"source": "claude"}


def test_non_claude_provider_is_not_mirrored(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.session_state("sess").provider = "codex-interactive"
    _publish(service, {
        "type": "tool_use", "tool_use_id": "call-1", "name": "TaskCreate",
        "arguments": {"subject": "no"},
    })
    _publish(service, {
        "type": "tool_result", "tool_use_id": "call-1",
        "content": "Task #1 created", "is_error": False,
    })
    assert TodoStore.instance().list_tasks("u", "c", "assistant") == []
