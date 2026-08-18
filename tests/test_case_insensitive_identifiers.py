"""Regression coverage for case-insensitive user-defined identifiers."""

import pytest

from core import Flow
from core._service_defs import ServiceDef
from core.handlers._fs_base import BaseFsHandler
from core.identifier import resolve_identifier
from core.service_registry import ServiceRegistry
from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry
from services import mcp_server_endpoint


class _Handler(ToolHandler):
    name = "MyTool"
    description = "test"
    parameters_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "ok"


class _FsHandler(BaseFsHandler):
    name = "read"
    description = "test"
    parameters_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "ok"


def test_identifier_resolution_preserves_canonical_spelling_and_rejects_ambiguity():
    assert resolve_identifier(["MyWorkspace"], "myworkspace") == "MyWorkspace"
    with pytest.raises(ValueError, match="Ambiguous identifier"):
        resolve_identifier(["Name", "name"], "NAME")


def test_service_registry_lookups_are_case_insensitive():
    registry = ServiceRegistry()
    definition = ServiceDef(
        "MyWorkspace", "relay", scope="user", scope_id="alice")

    class _Live:
        def is_connected(self):
            return True

    live = _Live()
    registry._definitions["alice"] = {"MyWorkspace": definition}
    registry._live_instances["alice"] = {"MyWorkspace": live}
    registry._loaded.add("alice")

    assert registry.get_definition("user", "alice", "myworkspace") is definition
    assert registry.get_live_instance_cached(
        "user", "alice", "MYWORKSPACE") is live
    assert registry.is_connected("user", "alice", "myworkspace") is True


def test_filesystem_selector_uses_the_linked_service_canonical_spelling():
    class _Live:
        _service_id = "MyWorkspace"

    live = _Live()
    handler = _FsHandler()
    handler.set_available_services(
        [{"id": "MyWorkspace"}], default_service_id="MyWorkspace")
    handler.set_fs_service(live)

    assert handler._find_service("myworkspace") is live


def test_relay_binding_mutations_accept_any_case(monkeypatch):
    from core import relay_bindings

    class _Store:
        def __init__(self):
            self.value = {
                "linked": {"*": ["MyWorkspace"]},
                "default": {"*": "MyWorkspace"},
            }

        def get_extra_cached(self, *_args, **_kwargs):
            return self.value

        def set_extra(self, _cid, _key, value):
            self.value = value

    store = _Store()
    monkeypatch.setattr(relay_bindings, "_get_store", lambda: store)
    monkeypatch.setattr(
        relay_bindings, "_invalidate_cli_after_mount_change", lambda *_args: None)

    assert relay_bindings.set_default_relay(
        "conv", "myworkspace") is True
    assert store.value["default"]["*"] == "MyWorkspace"
    assert relay_bindings.unlink_relay("conv", "MYWORKSPACE") is True


def test_tool_and_flow_ids_are_case_insensitive_and_case_collisions_fail():
    registry = ToolRegistry()
    handler = _Handler()
    registry.register(handler)
    assert registry.get("mytool") is handler
    assert registry.execute("MYTOOL", {}) == "ok"

    class _Duplicate(_Handler):
        name = "mytool"

    with pytest.raises(ValueError, match="conflicts"):
        registry.register(_Duplicate())

    flow = Flow({"id": "flow"})
    task = object()
    service = object()
    flow.add_task("ExtractData", task)
    flow.add_service("MainDB", service)
    assert flow.get_task("extractdata") is task
    assert flow.get_service("MAINDB") is service


def test_published_mcp_schema_listing_and_lookup_are_case_insensitive():
    class _Registry:
        def get_tool_definitions(self):
            return [{
                "name": "ReadFile",
                "description": "read",
                "parameters": {"type": "object", "properties": {}},
            }]

    listed = mcp_server_endpoint._tool_schema(_Registry(), "")
    assert [row["name"] for row in listed] == ["ReadFile"]
    assert mcp_server_endpoint._tool_schema(
        _Registry(), "readfile")["name"] == "ReadFile"
