"""Tests for the agent-facing PawFlow variable manager."""

import json

import pytest

from core.conversation_store import ConversationStore
from core.handlers.variables import ManageVariableHandler


@pytest.fixture
def handler(tmp_path, monkeypatch):
    from core import paths

    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    ConversationStore.reset()
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    ConversationStore._instance = store
    store.save("conv-1", [{"role": "user", "content": "hi"}], user_id="alice")

    value = ManageVariableHandler()
    value.set_user_id("alice")
    value.set_conversation_id("conv-1")
    yield value
    ConversationStore.reset()


def call(handler, **arguments):
    return json.loads(handler.execute(arguments))


def test_user_variable_lifecycle_and_empty_string(handler):
    result = call(handler, action="set", name="comfyui.default_relay", value="")
    assert result["value"] == ""
    assert call(handler, action="get", name="comfyui.default_relay") == {
        "action": "get",
        "found": True,
        "name": "comfyui.default_relay",
        "scope": "user",
        "value": "",
    }
    assert call(handler, action="list")["variables"] == {
        "comfyui.default_relay": "",
    }
    assert call(handler, action="delete", name="comfyui.default_relay")[
        "deleted"] is True
    assert call(handler, action="delete", name="comfyui.default_relay")[
        "deleted"] is False


def test_non_string_values_are_stored_as_compact_json(handler):
    value = {"relay-1": {"base_url": "http://127.0.0.1:8188"}}
    result = call(handler, action="set", name="comfyui.targets", value=value)
    assert result["value"] == (
        '{"relay-1":{"base_url":"http://127.0.0.1:8188"}}')
    assert call(handler, action="get", name="comfyui.targets")["value"] == result[
        "value"]


def test_conversation_variables_use_conversation_metadata(handler):
    call(handler, action="set", scope="conversation", name="comfyui.mode",
         value="manual")
    assert ConversationStore.instance().get_extra(
        "conv-1", "conv_parameters") == {"comfyui.mode": "manual"}
    assert call(handler, action="get", scope="conversation",
                name="comfyui.mode")["value"] == "manual"


@pytest.mark.parametrize("name", ["", "_reserved", "bad name", "a/b"])
def test_invalid_names_are_rejected(handler, name):
    with pytest.raises(ValueError, match="name must start"):
        handler.execute({"action": "get", "name": name})


def test_set_requires_value_but_allows_null(handler):
    with pytest.raises(ValueError, match="value is required"):
        handler.execute({"action": "set", "name": "comfyui.value"})
    assert call(handler, action="set", name="comfyui.value", value=None)[
        "value"] == "null"


def test_missing_runtime_context_is_rejected():
    handler = ManageVariableHandler()
    with pytest.raises(ValueError, match="current user context"):
        handler.execute({"action": "list"})
    with pytest.raises(ValueError, match="current conversation context"):
        handler.execute({"action": "list", "scope": "conversation"})


def test_registered_and_schema_exposes_actions():
    from core.tool_registry import create_default_registry

    handler = create_default_registry().get("manage_variable")
    assert handler is not None
    assert handler.parameters_schema["properties"]["action"]["enum"] == [
        "get", "list", "set", "delete"]
