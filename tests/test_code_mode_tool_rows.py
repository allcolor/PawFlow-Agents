"""Code-mode tool rows come from the relay, never from the script's source.

A code-mode harness (GPT-5.x "sol") runs one freeform `exec` item and calls
every tool from inside its JavaScript. The provider stream therefore shows one
opaque call where several tools ran, and reading their names back out of the
source does not hold: property shorthand, variables, loops and `.filter()` are
ordinary code-mode. PawFlow executes those calls itself, so the relay is what
reports them -- with the tool's real name, arguments and result.
"""

import pytest

from services._tool_relay_execute import _ToolRelayExecuteMixin
from services.cc_interactive_event_service import CCInteractiveEventService


@pytest.fixture
def service():
    svc = CCInteractiveEventService({"_service_id": "events-test",
                                     "token": "t"})
    with CCInteractiveEventService._instances_lock:
        CCInteractiveEventService._instances["events-test"] = svc
    yield svc
    with CCInteractiveEventService._instances_lock:
        CCInteractiveEventService._instances.pop("events-test", None)


def _row(service, session="sess"):
    return service.wait_event(session, timeout=0.01)


def test_a_call_reaches_the_session_that_is_in_code_mode(service):
    service.register_session("sess", conversation_id="conv",
                             agent_name="assistant")
    service.mark_code_mode("sess")

    published = CCInteractiveEventService.publish_agent_event(
        "conv", "assistant",
        {"type": "tool_use", "tool_use_id": "pawflow-relay-r1",
         "name": "read", "arguments": {"path": "/workspace/a.py"},
         "tool_origin": "mcp"})

    assert published is True
    event = _row(service)
    assert (event["name"], event["tool_origin"]) == ("read", "mcp")
    assert event["arguments"] == {"path": "/workspace/a.py"}


def test_an_ordinary_session_gets_no_relay_row(service):
    # Every provider that calls its tools directly already draws the row from
    # its own stream. Publishing here too would show each of those calls twice.
    service.register_session("sess", conversation_id="conv",
                             agent_name="assistant")

    published = CCInteractiveEventService.publish_agent_event(
        "conv", "assistant", {"type": "tool_use", "tool_use_id": "r1",
                              "name": "read", "arguments": {}})

    assert published is False
    assert _row(service) == {}


def test_code_mode_is_a_property_of_the_turn_not_the_session(service):
    # The next turn may call its tools directly. A flag left standing would
    # have the relay add a row beside the one the provider already drew.
    service.register_session("sess", conversation_id="conv",
                             agent_name="assistant")
    service.mark_code_mode("sess")

    service.claim_consumer("sess", kind="request")

    assert CCInteractiveEventService.publish_agent_event(
        "conv", "assistant", {"type": "tool_use", "tool_use_id": "r1",
                              "name": "read", "arguments": {}}) is False


def test_another_agent_never_receives_the_row(service):
    service.register_session("sess", conversation_id="conv",
                             agent_name="assistant")
    service.mark_code_mode("sess")

    assert CCInteractiveEventService.publish_agent_event(
        "conv", "reviewer", {"type": "tool_use", "tool_use_id": "r1",
                             "name": "read", "arguments": {}}) is False
    assert CCInteractiveEventService.publish_agent_event(
        "other-conv", "assistant", {"type": "tool_use", "tool_use_id": "r1",
                                    "name": "read", "arguments": {}}) is False


def test_the_relay_row_is_keyed_on_the_id_its_buttons_act_on(service):
    # Background and Kill address a call by the id the row carries. Keyed on
    # anything else the buttons are drawn on a row they cannot reach.
    service.register_session("sess", conversation_id="conv",
                             agent_name="assistant")
    service.mark_code_mode("sess")

    row_id = _ToolRelayExecuteMixin._publish_code_mode_call(
        "conv", "assistant", "req-42", "mcp__pawflow__use_tool",
        {"tool_name": "read", "arguments_json": '{"path": "/workspace/a.py"}'})

    assert row_id == "req-42"
    event = _row(service)
    # The wrapper is never what the user reads: the row names the real tool.
    assert (event["tool_use_id"], event["name"]) == ("req-42", "read")
    assert event["arguments"] == {"path": "/workspace/a.py"}

    _ToolRelayExecuteMixin._publish_code_mode_result(
        "conv", "assistant", row_id,
        {"type": "result", "data": "line one"})
    assert _row(service)["content"] == "line one"
