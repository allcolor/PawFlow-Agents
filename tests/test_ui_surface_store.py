import json

import pytest

from core import FlowFile
from core.ui_surface import make_ui_surface
from core.ui_surface_store import UiSurfaceConflict, UiSurfaceStore
from tasks.ai.actions.ui_surfaces import _handle_ui_surfaces


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    UiSurfaceStore.reset()
    yield
    UiSurfaceStore.reset()


def _surface(revision=1, title="Review", **overrides):
    values = {
        "user_id": "alice", "conversation_id": "conv",
        "producer_kind": "task", "producer_id": "custom-review",
        "surface_id": "uis_one", "revision": revision,
        "semantic": {
            "role": "review", "title": title, "fields": [], "actions": [],
        },
    }
    values.update(overrides)
    return make_ui_surface(**values)


def test_store_is_scoped_durable_and_monotone():
    store = UiSurfaceStore.instance()
    store.upsert(_surface(), user_id="alice", conversation_id="conv")
    store.upsert(
        _surface(revision=2, title="Revised"),
        user_id="alice", conversation_id="conv")
    assert store.list(user_id="alice", conversation_id="conv")[0][
        "semantic"]["title"] == "Revised"
    assert store.list(user_id="bob", conversation_id="conv") == []
    with pytest.raises(UiSurfaceConflict, match="moved forward"):
        store.upsert(
            _surface(), user_id="alice", conversation_id="conv")
    with pytest.raises(UiSurfaceConflict, match="reused"):
        store.upsert(
            _surface(revision=2, title="Different"),
            user_id="alice", conversation_id="conv")


def test_store_rejects_publisher_scope_mismatch():
    with pytest.raises(ValueError, match="user_id"):
        UiSurfaceStore.instance().upsert(
            _surface(), user_id="bob", conversation_id="conv")


def test_authenticated_list_action_is_producer_agnostic():
    UiSurfaceStore.instance().upsert(
        _surface(), user_id="alice", conversation_id="conv")
    flowfile = FlowFile()
    result = _handle_ui_surfaces(
        None, "ui_surface_list", {"conversation_id": "conv"},
        None, "alice", flowfile)
    payload = json.loads(result[0].get_content())
    assert payload["surfaces"][0]["producer"] == {
        "kind": "task", "id": "custom-review"}
    assert "workflow_proposal" not in json.dumps(payload)
