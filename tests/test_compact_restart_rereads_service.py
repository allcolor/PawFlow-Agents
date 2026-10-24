"""A provider compaction restarts the agent on its service's current settings.

The agent loop resolves its LLM service once, when it starts. A compaction in
the middle of a loop used to restart the provider on that same client: the
loop kept the context size, the compact threshold and the CLI environment it
started with, so a service edit never reached an agent that kept working,
however many times it compacted (an agent compacted twice at 95% of 380K after
its service had moved to 600K). The restart now goes through the ordinary cold
path, which resolves the service again.
"""
import threading
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_client import CCCompactDetected, LLMMessage
from tasks.ai._alc_base import _ALC_CONTINUE
from tasks.ai._alc_llm_turn import _ALCLlmTurnMixin
from tasks.ai._alc_setup import _ALCSetupMixin
from tasks.ai.agent_exceptions import AgentCancelled


class _Client:
    """Just enough of an LLM client for the loop to clone and register."""

    provider = "claude-code-interactive"

    def __init__(self, max_context_size):
        self.config = {"max_context_size": max_context_size,
                       "compact_threshold_pct": 95}

    def clone_for_call(self):
        clone = _Client(self.config["max_context_size"])
        clone.cloned_from = self
        return clone

    def send_user_message(self, *_args, **_kwargs):
        pass


class _Registry:
    def list_tools(self):
        return []


def _msg(role, content):
    return LLMMessage(role=role, content=content, conversation_id="conv1234")


COMPACTED = [_msg("user", "[summary of the work so far]"),
             _msg("assistant", "latest tail message")]


class _Harness(_ALCLlmTurnMixin, _ALCSetupMixin):
    def __init__(self):
        self._active_contexts_lock = threading.Lock()
        self._active_claude_client = {}
        self._active_client_owners = {}
        self.prepare_calls = []
        # The service as the operator edited it after the loop started.
        self.service = _Client(600_000)

    def _compact_context_from_store(self, *_args, **_kwargs):
        return list(COMPACTED)

    def _prepare_agent_context(self, flowfile, **kwargs):
        self.prepare_calls.append((flowfile, kwargs))
        return {
            "client": self.service, "registry": _Registry(), "tool_defs": [],
            "messages": list(COMPACTED), "model": "",
            "max_context_size": 600_000,
            "resolved_svc": SimpleNamespace(config=dict(self.service.config)),
            "active_agent_name": "GameDev5", "active_llm_service": "cc",
            "_context_is_delta": False,
            "_context_rebuild_args": {"flowfile": flowfile},
        }

    def _alc_compact_restart_ms(self, st):
        return 0.0

    def _alc_persist_post_compact_usage(self, st):
        pass


def _state(**ctx_overrides):
    started_client = _Client(380_000)
    messages = [_msg("user", "start the job"), _msg("assistant", "working")]
    ctx = {
        "active_agent_name": "GameDev5",
        "max_context_size": 380_000,
        "resolved_svc": SimpleNamespace(config=dict(started_client.config)),
        "_is_cli_provider": True,
        "messages": messages,
        "_context_rebuild_args": {
            "flowfile": "wake-flowfile",
            "preloaded_messages": [{"role": "user", "content": "stale"}],
            "preloaded_conversation_id": "conv1234",
            "independent_context": False,
            "skip_current_user_inject": False,
        },
        "_consumed_cancel_checkpoint": {"tools_called": ["bash"]},
    }
    ctx.update(ctx_overrides)
    st = SimpleNamespace(
        _budget_precheck_done=True, total_tokens_in=0, total_tokens_out=0,
        total_cache_read=0, total_cache_write=0,
        conversation_id="conv1234", user_id="u1", iteration=3,
        _agent_name_key="conv1234:GameDev5",
        ctx=ctx, client=started_client, registry=_Registry(), tool_defs=[],
        model="", messages=messages, new_messages=[], base_count=2,
        llm_context=list(messages), use_conv_store=False, _cp_id="",
        compact_client=object(), _call_context=None,
        _llm_call=lambda _ctx: (_ for _ in ()).throw(
            CCCompactDetected("PawFlow post-append compact threshold crossed")),
        emitter=SimpleNamespace(check_cancelled=lambda: None,
                                check_interrupt=lambda: False,
                                on_fatal_error=lambda _msg: None),
        _set_provider_compact_barrier=lambda _reason: None,
        _clear_provider_compact_barrier=lambda: None,
    )

    def _adopt(compacted, **_kwargs):
        st.messages[:] = list(compacted)

    st._adopt_compacted_context = _adopt
    return st


def _run_compaction(host, st):
    """Drive one provider turn that ends in a compaction; return the events."""
    published = []
    bus = SimpleNamespace(publish_event=lambda _cid, kind, payload: published.append((kind, payload)))
    with patch("core.conversation_event_bus.ConversationEventBus.instance",
               return_value=bus), \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=SimpleNamespace(flush=lambda timeout: True)), \
            patch("core.conversation_store.ConversationStore.instance",
                  return_value=object()), \
            patch("core.pending_queue.PendingQueue.for_agent",
                  return_value=SimpleNamespace(discard_msg_ids=lambda _ids: None)), \
            patch("tasks.ai.context_usage.reset_cli_context_usage",
                  return_value=None), \
            patch("tasks.ai.context_usage.context_usage_for_messages",
                  side_effect=lambda _cid, _agent, _msgs, svc_cfg=None, **_kw: {
                      "used": 15_000, "max": svc_cfg["max_context_size"]}), \
            patch("tasks.ai.context_usage.usage_event_payload",
                  side_effect=lambda usage: usage):
        result = host._alc_llm_turn(st)
    return result, published


def test_a_compaction_restarts_on_the_service_as_it_is_now():
    host, st = _Harness(), _state()
    started_client = st.client

    result, published = _run_compaction(host, st)

    assert result is _ALC_CONTINUE
    # rebuilt once, by the ordinary cold path
    assert len(host.prepare_calls) == 1
    flowfile, kwargs = host.prepare_calls[0]
    assert flowfile == "wake-flowfile"
    assert kwargs["force_cold"] is True
    # the loop now runs on the service's current settings
    assert st.ctx["max_context_size"] == 600_000
    assert st.client is not started_client
    assert st.client.cloned_from is host.service
    assert host._active_claude_client["conv1234:GameDev5"] is st.client
    # and the gauge shown right after the compaction says so
    gauges = [payload for kind, payload in published if kind == "message_meta"]
    assert gauges and gauges[-1]["max"] == 600_000


def test_the_rebuild_loads_the_compacted_context_and_nothing_else():
    """The compacted context is the conversation now. The request that started
    the loop, preloaded or retrigger input and the cancel checkpoint were all
    consumed before the compaction: replaying them would hand the agent its
    request again in the middle of the work."""
    host, st = _Harness(), _state()

    _run_compaction(host, st)

    _, kwargs = host.prepare_calls[0]
    assert kwargs["preloaded_messages"] is None
    assert kwargs["preloaded_conversation_id"] == ""
    assert kwargs["skip_current_user_inject"] is True
    assert kwargs.get("resume_checkpoint") is None
    assert [m.content for m in st.messages] == [m.content for m in COMPACTED]
    assert st.llm_context == st.messages


def test_a_task_sub_conversation_keeps_its_loop():
    """The poller builds a task's context on the parent conversation and points
    it at the task afterwards: a rebuild would load the parent's context."""
    host, st = _Harness(), _state(_independent_context=True)
    started_client = st.client

    result, _ = _run_compaction(host, st)

    assert result is _ALC_CONTINUE
    assert host.prepare_calls == []
    assert st.client is started_client
    assert st.ctx["max_context_size"] == 380_000
    assert [m.content for m in st.llm_context] == [m.content for m in COMPACTED]


def test_a_newer_generation_keeps_its_client_registration():
    """The rebuild takes a moment. When the agent is restarted meanwhile, the
    stale loop must not register its client over the newer one, or cancel and
    preempt would reach the wrong process."""
    host, st = _Harness(), _state()
    newer = object()
    host._active_claude_client["conv1234:GameDev5"] = newer
    checks = []

    def _check_cancelled():
        checks.append(True)
        if len(checks) > 1:   # the restart lands once the compaction is adopted
            raise AgentCancelled()

    st.emitter.check_cancelled = _check_cancelled

    try:
        _run_compaction(host, st)
    except AgentCancelled:
        pass

    assert host._active_claude_client["conv1234:GameDev5"] is newer
