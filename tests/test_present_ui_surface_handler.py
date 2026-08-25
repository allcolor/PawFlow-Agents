import json

import pytest

from core.handlers.ui_surfaces import PresentUiSurfaceHandler
from core.ui_surface_store import UiSurfaceStore


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    UiSurfaceStore.reset()
    yield
    UiSurfaceStore.reset()


def test_context_bound_tool_publishes_custom_task_surface():
    handler = PresentUiSurfaceHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv")
    handler.set_agent_name("workflow-agent")
    result = json.loads(handler.execute({
        "producer_kind": "workflow_task",
        "producer_id": "package.example:review",
        "semantic": {
            "role": "review", "title": "Custom review",
            "fields": [], "actions": [],
        },
    }))
    surface = result["surface"]
    assert surface["user_id"] == "alice"
    assert surface["conversation_id"] == "conv"
    assert UiSurfaceStore.instance().list(
        user_id="alice", conversation_id="conv") == [surface]


def test_tool_fails_closed_without_runtime_scope():
    result = PresentUiSurfaceHandler().execute({
        "producer_kind": "task", "producer_id": "custom",
        "semantic": {"role": "notice", "title": "No scope"},
    })
    assert result.startswith("Error: present_ui_surface requires")
