"""Silent control-flow handling for obsolete agent workers."""

from types import SimpleNamespace

import pytest

from core.llm_client import AgentSuperseded
from services.cc_interactive_event_service import CCIConsumerEvicted
from tasks.ai.agent_core import AgentCoreMixin


def test_consumer_eviction_never_reaches_error_emitter(monkeypatch):
    core = object.__new__(AgentCoreMixin)
    errors = []
    emitter = SimpleNamespace(on_error=errors.append)

    def _setup(state):
        state.max_rounds = 1
        state.iteration = 0

    def _evict(_state):
        raise CCIConsumerEvicted(
            "CC interactive session taken over by a newer consumer")

    monkeypatch.setattr(core, "_alc_setup", _setup)
    monkeypatch.setattr(core, "_alc_iteration", _evict)

    with pytest.raises(AgentSuperseded, match="newer consumer"):
        core._run_agent_loop_inner({"max_iterations": 1}, emitter)

    assert errors == []


def test_real_agent_failure_still_reaches_error_emitter(monkeypatch):
    core = object.__new__(AgentCoreMixin)
    errors = []
    emitter = SimpleNamespace(on_error=errors.append)
    failure = RuntimeError("provider failed")

    def _setup(state):
        state.max_rounds = 1
        state.iteration = 0

    def _fail(_state):
        raise failure

    monkeypatch.setattr(core, "_alc_setup", _setup)
    monkeypatch.setattr(core, "_alc_iteration", _fail)

    with pytest.raises(RuntimeError, match="provider failed"):
        core._run_agent_loop_inner({"max_iterations": 1}, emitter)

    assert errors == [failure]
