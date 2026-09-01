"""AG-UI per-conversation tools: frontend tools, shared state, interrupts."""

import json

import pytest

import core.agui_tools as agui_tools
from core.agui_tools import (
    AguiFrontendToolHandler, AguiInterruptHandler, AguiStateHandler,
    apply_json_patch, register_agui_conversation_tools)
from core.tool_registry import ToolRegistry


# ── JSON Patch (RFC 6902) ────────────────────────────────────────

def test_apply_json_patch_add_replace_remove_test():
    state = {"items": [1, 2], "name": "a"}
    result = apply_json_patch(state, [
        {"op": "replace", "path": "/name", "value": "b"},
        {"op": "add", "path": "/items/-", "value": 3},
        {"op": "add", "path": "/items/0", "value": 0},
        {"op": "test", "path": "/name", "value": "b"},
        {"op": "remove", "path": "/items/1"},
        {"op": "add", "path": "/nested", "value": {"x": 1}},
    ])
    assert result == {"items": [0, 2, 3], "name": "b", "nested": {"x": 1}}
    # The input state is never mutated.
    assert state == {"items": [1, 2], "name": "a"}


def test_apply_json_patch_root_and_errors():
    assert apply_json_patch(None, [
        {"op": "add", "path": "", "value": {"fresh": True}}]) == {"fresh": True}
    with pytest.raises(ValueError, match="non-empty"):
        apply_json_patch({}, [])
    with pytest.raises(ValueError, match="unsupported op"):
        apply_json_patch({}, [{"op": "move", "path": "/a", "from": "/b"}])
    with pytest.raises(ValueError, match="path not found"):
        apply_json_patch({}, [{"op": "replace", "path": "/missing",
                               "value": 1}])
    with pytest.raises(ValueError, match="test failed"):
        apply_json_patch({"a": 1}, [{"op": "test", "path": "/a",
                                     "value": 2}])
    with pytest.raises(ValueError, match="Invalid JSON Pointer"):
        apply_json_patch({}, [{"op": "add", "path": "noslash", "value": 1}])


# ── Handlers ─────────────────────────────────────────────────────

@pytest.fixture
def doc_store(monkeypatch):
    """In-memory replacement for the conversation's `agui` extra."""
    docs = {"conv1": {"tools": [], "state": {"count": 1}, "interrupts": []}}
    published = []
    monkeypatch.setattr(agui_tools, "load_agui_doc",
                        lambda cid: docs.get(cid))
    monkeypatch.setattr(agui_tools, "save_agui_doc",
                        lambda cid, doc: docs.__setitem__(cid, doc))
    monkeypatch.setattr(
        agui_tools._AguiHandlerBase, "_publish",
        lambda self, event_type, data: published.append((event_type, data)))
    return docs, published


def test_frontend_tool_handler_returns_placeholder():
    handler = AguiFrontendToolHandler("conv1", "confirm", "ask the user",
                                      {"type": "object", "properties": {}})
    assert handler.name == "confirm"
    assert "frontend tool" in handler.description
    assert "untrusted" in handler.description
    # Multi-call batching: the agent may emit several frontend calls in
    # one turn; execute() must not demand ending the turn after one call.
    assert "batch" in handler.description
    result = handler.execute({"question": "sure?"})
    assert "NEXT message" in result
    assert "batch further frontend tool calls" in result
    assert handler._origin == "agui"
    assert handler._origin_scope == "agui:conv1"


def test_frontend_tool_annotations_are_unverified_scalar_hints():
    handler = AguiFrontendToolHandler(
        "conv1", "confirm", "ask the user", None,
        annotations={"readOnlyHint": True, "title": "Confirm",
                     "nested": {"drop": "me"},
                     "huge": "x" * 500})
    description = handler.description
    assert 'readOnlyHint=true' in description
    assert 'title="Confirm"' in description
    assert "unverified, presentation only, never a permission" in description
    # Non-scalar and oversized string hints are dropped (injection cap).
    assert "nested" not in description
    assert "huge" not in description


def test_state_handler_get_set_patch(doc_store):
    docs, published = doc_store
    handler = AguiStateHandler("conv1")
    assert json.loads(handler.execute({"action": "get"})) == {"count": 1}

    out = handler.execute({"action": "set", "state": {"count": 5}})
    assert "replaced" in out
    assert docs["conv1"]["state"] == {"count": 5}
    assert published[-1] == ("agui_state_snapshot", {"state": {"count": 5}})

    patch = [{"op": "replace", "path": "/count", "value": 6}]
    out = handler.execute({"action": "patch", "patch": patch})
    assert "Patch applied" in out
    assert docs["conv1"]["state"] == {"count": 6}
    assert published[-1] == ("agui_state_delta", {"delta": patch})

    assert handler.execute({"action": "patch", "patch": [
        {"op": "replace", "path": "/missing", "value": 1}]}).startswith(
        "Error: invalid JSON Patch")
    assert handler.execute({"action": "set"}).startswith("Error")
    assert AguiStateHandler("other").execute(
        {"action": "get"}).startswith("Error")


def test_interrupt_handler_persists_and_publishes(doc_store):
    docs, published = doc_store
    handler = AguiInterruptHandler("conv1")
    out = handler.execute({"reason": "approval_required",
                           "message": "Deploy?",
                           "response_schema": {"type": "object"}})
    assert "End your turn now" in out
    assert len(docs["conv1"]["interrupts"]) == 1
    interrupt = docs["conv1"]["interrupts"][0]
    assert interrupt["reason"] == "approval_required"
    assert interrupt["message"] == "Deploy?"
    assert interrupt["responseSchema"] == {"type": "object"}
    assert interrupt["id"].startswith("int_")
    assert published == [("agui_interrupt", {"interrupt": interrupt})]
    assert handler.execute({}).startswith("Error")


# ── Per-turn registration ────────────────────────────────────────

def test_register_is_a_noop_without_agui_doc(monkeypatch):
    monkeypatch.setattr(agui_tools, "load_agui_doc", lambda cid: None)
    registry = ToolRegistry()
    assert register_agui_conversation_tools(registry, "conv1") == 0
    assert registry.list_tools() == []


def test_register_declares_prunes_and_never_shadows(monkeypatch):
    docs = {"conv1": {"tools": [
        {"name": "confirm", "description": "ask", "parameters": None},
        {"name": "read", "description": "shadow attempt",
         "parameters": None},
    ], "state": None, "interrupts": []}}
    monkeypatch.setattr(agui_tools, "load_agui_doc",
                        lambda cid: docs.get(cid))

    class _Builtin:
        name = "read"
        description = "builtin read"
        parameters_schema = {"type": "object"}

        def execute(self, arguments):
            return "builtin"

    registry = ToolRegistry()
    registry.register(_Builtin())
    count = register_agui_conversation_tools(registry, "conv1")
    # agui_state + agui_interrupt + confirm; 'read' is skipped (builtin wins)
    assert count == 3
    assert registry.get("read").description == "builtin read"
    assert isinstance(registry.get("confirm"), AguiFrontendToolHandler)
    assert registry.get("agui_state") is not None
    assert registry.get("agui_interrupt") is not None

    # The client stops declaring 'confirm' → pruned on the next turn.
    docs["conv1"]["tools"] = []
    register_agui_conversation_tools(registry, "conv1")
    assert registry.get("confirm") is None
    assert registry.get("read") is not None


def test_agui_handlers_are_invisible_outside_their_conversation():
    from core.tool_mcp_filters import is_tool_enabled
    assert is_tool_enabled("conv1", "confirm", origin="agui",
                           origin_scope="agui:conv1") is True
    assert is_tool_enabled("conv2", "confirm", origin="agui",
                           origin_scope="agui:conv1") is False
    assert is_tool_enabled("", "confirm", origin="agui",
                           origin_scope="agui:conv1") is False
