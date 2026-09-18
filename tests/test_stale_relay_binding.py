"""A linked relay whose definition is gone must not be offered as available.

This conversation kept 'Ultima7' linked while no relay of that name existed any
more (proved live: naming it answered "filesystem not found: 'Ultima7'.
Available: MyWorkspace, Ultima7" -- listing the very name it had just rejected
-- while the Relay panel said "not connected (def=missing)"). One binding, two
contradictory answers, and neither of them said the binding was the problem.
"""

import types

from core.agent_group_tools import GroupReadOnlyToolRuntime
from core.handlers._fs_base import BaseFsHandler


class _Runtime(GroupReadOnlyToolRuntime):
    """The handler wiring without the group/member plumbing."""

    def __init__(self, context):
        self.context = context


class _Registry:
    def __init__(self, handlers):
        self._handlers = list(handlers)

    def fork(self):
        return self

    def list_tools(self):
        return list(self._handlers)

    def unregister(self, name):
        self._handlers = [h for h in self._handlers if h.name != name]


class _Handler(BaseFsHandler):
    name = "read"
    description = "test handler"
    parameters_schema = {}

    def execute(self, arguments):
        return ""


def _wire(monkeypatch, linked, resolvable):
    from core.handlers import _fs_helpers

    monkeypatch.setattr(
        _fs_helpers, "find_fs_service",
        lambda user, name, conv: object() if name in resolvable else None)
    runtime = _Runtime(types.SimpleNamespace(
        user_id="allcolor", conversation_id="conv-1", agent_name="dev"))
    monkeypatch.setattr(runtime, "_linked_relays", lambda: tuple(linked))
    handler = _Handler()
    runtime._configure_handlers(_Registry([handler]))
    return handler


def test_only_defined_relays_are_offered(monkeypatch):
    handler = _wire(monkeypatch, ["MyWorkspace", "Ultima7"], {"MyWorkspace"})
    assert [s["id"] for s in handler._available_services] == ["MyWorkspace"]
    assert handler._stale_linked == ("Ultima7",)


def test_a_stale_name_says_the_binding_is_the_problem(monkeypatch):
    handler = _wire(monkeypatch, ["MyWorkspace", "Ultima7"], {"MyWorkspace"})
    message = handler._no_target_error("Ultima7")
    assert "no relay of that name is defined" in message
    assert "Relay panel" in message
    assert "Available: MyWorkspace" in message
    assert "Available: MyWorkspace, Ultima7" not in message


def test_an_unknown_name_keeps_the_plain_message(monkeypatch):
    handler = _wire(monkeypatch, ["MyWorkspace"], {"MyWorkspace"})
    message = handler._no_target_error("SomeOtherRelay")
    assert "filesystem not found: 'SomeOtherRelay'" in message
    assert "no relay of that name is defined" not in message


def test_a_stale_only_conversation_still_enforces_the_scope(monkeypatch):
    """Nothing resolvable must not mean "no filter"."""
    handler = _wire(monkeypatch, ["Ultima7"], set())
    assert handler._filesystem_scope_enforced is True
    assert handler._available_services == []
    assert handler._find_service("Ultima7") is None
