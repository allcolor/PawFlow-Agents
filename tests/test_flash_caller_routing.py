"""Results addressed to a flash agent never start a conversation turn for it.

A flash agent is a sub-agent run with no conversation agent config. Waking it
by its runtime name failed with "No LLM service resolved for agent
'GameDev4::flash::...'" when a delegate it had called replied an hour after
it finished (2026-09-30).
"""

import threading
from types import SimpleNamespace

import pytest

from core import _agent_executor_base as live
from core.handlers._spawn_delivery import _SpawnDeliveryMixin, route_flash_caller

FLASH = "GameDev4::flash::bench-review"


@pytest.fixture
def live_slots():
    registered = []

    def register(conv, caller, target):
        live.register_live_delegate(conv, caller, target, "t1", None, None)
        registered.append((conv, caller, target))

    yield register
    for conv, caller, target in registered:
        live.unregister_live_delegate(conv, caller, target)


def test_conversation_agent_caller_is_unchanged():
    assert route_flash_caller("conv1", "GameDev4", "reply") == (
        "GameDev4", "reply")
    assert route_flash_caller("conv1", "", "reply") == ("", "reply")


def test_running_flash_agent_gets_the_result_in_its_live_queue(live_slots):
    live_slots("conv1", "GameDev4", FLASH)

    assert route_flash_caller("conv1::task::abc", FLASH, "reply") is None
    assert live.drain_live_delegate_messages(
        "conv1", "GameDev4", FLASH) == ["reply"]


def test_live_slot_names_the_exact_creator(live_slots):
    """The runtime name holds a sanitized creator; the slot holds the real one."""
    flash = "Game_Dev::flash::x"
    live_slots("conv1", "Game Dev", flash)

    assert route_flash_caller("conv1", flash, "reply") is None
    assert live.drain_live_delegate_messages(
        "conv1", "Game Dev", flash) == ["reply"]


def test_finished_flash_agent_forwards_the_result_to_its_creator():
    agent, text = route_flash_caller("conv1", FLASH, "the answer")

    assert agent == "GameDev4"
    assert text.startswith("[Result addressed to your flash agent "
                           "'bench-review', which has already finished]")
    assert text.endswith("the answer")


def test_delivery_to_a_finished_flash_agent_wakes_its_creator(monkeypatch):
    from tasks.ai.agent_loop import AgentLoopTask

    written = []
    woken = []
    writer = SimpleNamespace(enqueue_message=lambda msg, **kw: written.append(
        (msg, kw["agent_name"])))
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: writer)
    inst = SimpleNamespace(_active_contexts_lock=threading.Lock(),
                           _active_contexts={})
    monkeypatch.setattr(AgentLoopTask, "_live_instance", inst)
    monkeypatch.setattr(
        _SpawnDeliveryMixin, "_wake_caller",
        staticmethod(lambda _inst, conv, agent, *_a, **_k: woken.append(
            (conv, agent))))

    _SpawnDeliveryMixin()._deliver_to_caller(
        conv_id="conv1", caller_agent=FLASH, user_id="alice",
        text="result", msg_id="m1", task_id="t9", delegate_agent="GameDev",
        file_id="")

    assert woken == [("conv1", "GameDev4")]
    assert written[0][1] == "GameDev4"
    assert written[0][0]["source"]["target_agent"] == "GameDev4"


def test_delegate_reply_turn_routes_its_caller_before_waking():
    """agent_core's delegate-reply delivery goes through the same routing."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "tasks" / "ai" / "agent_core.py").read_text(encoding="utf-8")
    route_at = src.index("st._routed = route_flash_caller(")
    assert route_at < src.index("elif st._routed is not None:")
    assert route_at < src.index("SpawnAgentsHandler._wake_caller(")
    assert route_at < src.index("SpawnAgentsHandler._preempt_caller(")


def test_delivery_to_a_running_flash_agent_starts_no_turn(monkeypatch, live_slots):
    live_slots("conv1", "GameDev4", FLASH)
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: pytest.fail("nothing is persisted for the parent agents"))
    monkeypatch.setattr(
        _SpawnDeliveryMixin, "_wake_caller",
        staticmethod(lambda *_a, **_k: pytest.fail("no turn may start")))

    _SpawnDeliveryMixin()._deliver_to_caller(
        conv_id="conv1", caller_agent=FLASH, user_id="alice",
        text="result", msg_id="m1", task_id="t9", delegate_agent="GameDev",
        file_id="")

    assert live.drain_live_delegate_messages(
        "conv1", "GameDev4", FLASH) == ["result"]
