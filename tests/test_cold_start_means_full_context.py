"""Launching a process is a cold start, and a cold start gets the full context.

Two cases, no third one:

    1. no process running -> we launch -> cold start -> FULL context
    2. a process is running -> delta

The context phase decides which one applies, but the PROVIDER is what actually
launches, and only it can find the process gone -- it crashed, its container
was stopped -- after the context was already built as a delta. Sending that
delta to a fresh process is case 1 carrying case 2's context: a process that
knows nothing, handed a bare question with no transcript, no persona, no
skills and no tool configuration.

The provider therefore refuses to launch. The turn goes back to the context
phase with force_cold=True, which is case 1 built by the ordinary cold path --
not reassembled by hand, which is what the previous mechanism did and why it
only ever restored the transcript.
"""
import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core._llm_types import ColdStartRequired
from core.llm_client import LLMMessage
from core.llm_providers.cli_shared import LLMCliSharedMixin


class _Client(LLMCliSharedMixin):
    pass


# -- the refusal itself ------------------------------------------------------

def test_an_ordinary_cold_start_launches_without_complaint():
    """No marker means the context phase built this turn as a cold start."""
    assert _Client()._cli_require_cold_context("codex-app") is None


def test_launching_with_a_resume_delta_is_refused():
    client = _Client()
    client._pawflow_context_is_delta = True

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("codex-app")


def test_the_refusal_fires_at_most_once():
    """The rebuilt context is a real cold context; a stale marker must not
    bounce a turn that is already correct."""
    client = _Client()
    client._pawflow_context_is_delta = True

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("codex-app")
    assert client._cli_require_cold_context("codex-app") is None


# -- the marker has to reach the provider -----------------------------------

def test_the_context_phase_marks_the_context_and_not_the_service():
    """The client the context phase holds comes from the service registry and
    is shared by every conversation using that service. A marker left on it is
    read -- or cleared -- by whichever turn reaches it next, and the isolated
    clone is only made later, in _alc_setup."""
    from tasks.ai._agentctx_p2 import _PACPhase2Mixin

    class _Phase(_PACPhase2Mixin):
        pass

    class _St:
        client = _Client()

    st = _St()
    _Phase()._mark_context_as_delta(st)

    assert st._context_is_delta is True
    assert getattr(st.client, "_pawflow_context_is_delta", False) is False


def test_the_marker_survives_clone_for_call():
    """The loop clones the client after the context phase (`_alc_setup`), and
    clone_for_call copies an explicit whitelist. Anything left off that list
    is a no-op in production while every same-instance unit test passes --
    which is exactly what happened to the mechanism this one replaces.
    """
    from core.llm_client import LLMClient

    client = LLMClient(provider="codex-app-server", config={})
    client._pawflow_context_is_delta = True

    assert getattr(client.clone_for_call(),
                   "_pawflow_context_is_delta", False) is True


def test_a_cold_context_is_not_marked():
    from core.llm_client import LLMClient

    client = LLMClient(provider="codex-app-server", config={})

    assert getattr(client.clone_for_call(),
                   "_pawflow_context_is_delta", False) is False


# -- the refusal must not be swallowed by the retry driver ------------------

def test_the_stream_driver_does_not_retry_a_cold_start():
    """The driver retries almost everything. Retrying here would re-send the
    same delta to the same launch, max_retries times, and then wrap it in an
    LLMClientError the loop cannot recognise."""
    from pathlib import Path

    src = Path("core/_llm_client_driver.py").read_text(encoding="utf-8")
    assert "isinstance(e, (_AC, CCCompactDetected, ColdStartRequired))" in src


# -- both launch sites ask ---------------------------------------------------

# Every CLI that DECIDES to launch a process. There is no per-provider
# variation in the rule -- a new CLI belongs on this list, and the test below
# is what makes forgetting it fail.
CLI_LAUNCH_SITES = [
    "core/llm_providers/_codex_app_stream.py",
    "core/llm_providers/_gemini_stream.py",
    "core/llm_providers/claude_code_interactive.py",
    "core/llm_providers/antigravity_interactive.py",
    "core/llm_providers/_cc_stream.py",
]

# Modules that only carry out a launch someone else already decided on.
# Listed by name so a new launcher cannot slip in unclassified.
LAUNCH_HELPERS = {
    "_gemini_acp.py": "spawn helper; _gemini_stream asks before calling it",
    "codex_app_server.py": "defines _codex_pool_popen",
    "gemini.py": "defines _gemini_pool_popen",
    "claude_code.py": "_spawn_cc_stream; _cc_stream asks before calling it",
}


@pytest.mark.parametrize("path", CLI_LAUNCH_SITES)
def test_every_cli_provider_asks_before_launching(path):
    assert "_cli_require_cold_context(" in Path(path).read_text(encoding="utf-8")


def test_no_cli_provider_launches_without_asking():
    """The rule is one rule: process not running -> full context; process
    running -> delta. A provider that starts a process without asking is the
    original bug, whatever its name. Every module that can launch is either a
    decision site that asks, or a named helper acting on a decision already
    taken -- there is no third category."""
    known = {Path(p).name for p in CLI_LAUNCH_SITES} | set(LAUNCH_HELPERS)
    unclassified = []
    for path in sorted(Path("core/llm_providers").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if not ("ensure_started(" in src or "_pool_popen(" in src):
            continue
        if path.name not in known:
            unclassified.append(path.name)

    assert not unclassified, (
        "these modules can launch a CLI process and are classified neither as "
        f"a decision site that asks, nor as a helper: {unclassified}")


# -- the pools ask at the launch, and only at the launch ---------------------

def test_a_pool_asks_only_when_it_is_really_going_to_launch():
    """Reuse must never be bounced: the process is there, the delta is
    correct, and a refusal would restart a turn that had nothing wrong with
    it."""
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool

    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    pool._lock = threading.RLock()
    pool._sessions = {}
    pool._reserved_slots = set()
    pool.ensure_sweeper = lambda **_kw: None
    pool._is_alive = lambda _name: True

    live = SimpleNamespace(name="cci-live", last_used=0.0)
    pool._sessions[("u", "c", "a", "svc")] = live
    client = SimpleNamespace(timeout=None, _agent_service="svc")
    asked = []

    got = pool.ensure_started(client, "m", "u", "c", "a",
                              before_launch=lambda: asked.append(True))

    assert got is live
    assert asked == [], "a reused session must not be asked to restart"


def test_a_pool_refusal_stops_the_launch_before_anything_is_claimed():
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool

    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    pool._lock = threading.RLock()
    pool._sessions = {}
    pool._reserved_slots = set()
    pool.ensure_sweeper = lambda **_kw: None
    pool._is_alive = lambda _name: False
    claimed = []
    pool._claim_pool_slot_locked = lambda *a, **k: claimed.append(True)
    pool._start_new = lambda *a, **k: pytest.fail("launched after refusing")
    client = SimpleNamespace(timeout=None, _agent_service="svc", api_key="")

    def _refuse():
        raise ColdStartRequired("claude-code-interactive: cold start required")

    with pytest.raises(ColdStartRequired):
        pool.ensure_started(client, "m", "u", "c", "a", before_launch=_refuse)

    assert claimed == [], "a credential slot was claimed for a turn that stopped"


# -- claude-code (-p) has no third path either -------------------------------

def test_claude_code_never_resumes_from_disk():
    """There were three paths, and the third one was the bug: no live process
    but a persisted session id, so CC was launched with `--resume` and a
    delta. Whether that jsonl still meant anything was decided by re-deriving
    CC's project-key algorithm and trusting a file only CC can validate --
    and when it did not resume, the SESSION MISMATCH check merely logged it
    while the agent silently lost its history."""
    spawn = Path("core/llm_providers/claude_code.py").read_text(encoding="utf-8")
    stream = Path("core/llm_providers/_cc_stream.py").read_text(encoding="utf-8")

    # The quoted literal, as it would appear in code -- prose and docstrings
    # are free to name the flag they no longer pass.
    assert '"--resume"' not in spawn and "'--resume'" not in spawn, (
        "the spawn path resumes again")
    assert "_effective_session_id" not in spawn
    # The launch path clears the stored id: it describes a process that is
    # gone, and keeping it would replay CC's jsonl on top of the full context
    # we are about to send.
    launch = stream.split("st._owns_turn_lock = False")[1].split("catchup")[0]
    assert 'st.session_id = ""' in launch
    assert "_cli_require_cold_context(" in launch


def test_the_context_phase_asks_claude_code_the_live_question():
    """It was the last provider still deciding on a persisted session id --
    the exact divergence removed from codex and gemini in beta.58."""
    tree = ast.parse(Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8"))
    branches = [n for n in ast.walk(tree)
                if isinstance(n, ast.If)
                and "_is_claude_code" in ast.dump(n.test)
                and "interactive" not in ast.dump(n.test)]

    assert branches, "the claude-code branch of the live probe is gone"
    decided_on = ast.dump(branches[0])
    assert "find_for_agent" in decided_on, (
        "claude-code decides warm/cold on something other than a live process")


# -- the rebuild is the ordinary cold path ----------------------------------

def test_force_cold_skips_the_live_probe():
    """force_cold is a third CALLER, not a third state: the turn already knows
    it is going to launch, so asking again could only answer 'warm' and strip
    the context that launch needs."""
    from pathlib import Path

    src = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
    assert 'and not getattr(st, "force_cold", False)' in src


def test_the_marker_never_outlives_its_turn():
    """The marker rides in the turn's context and is stamped on the turn's own
    clone. Nothing writes it on the shared service client, so nothing has to
    remember to clear it either."""
    p1 = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
    p2 = Path("tasks/ai/_agentctx_p2.py").read_text(encoding="utf-8")
    ctx = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
    setup = Path("tasks/ai/_alc_setup.py").read_text(encoding="utf-8")

    assert "_pawflow_context_is_delta" not in p1
    assert "_pawflow_context_is_delta" not in p2
    assert '"_context_is_delta": bool(getattr(st, "_context_is_delta", False))' in ctx
    assert 'st.ctx.get("_context_is_delta", False))' in setup


def test_the_turn_rebuilds_at_most_once():
    """Twice means the process dies as fast as we start it; a third attempt
    would only spin."""
    from pathlib import Path

    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("except ColdStartRequired:")[1].split("except Exception")[0]
    assert '_cold_restart_done' in body
    assert 'force_cold=True' in body
    assert 'raise' in body


def test_the_rebuild_carries_the_cancel_checkpoint():
    """The checkpoint is consumed on injection and is NOT cold-gated, so the
    first pass eats it. Without carrying it, a rebuilt turn silently loses
    its 'continue where you left off' instruction."""
    from pathlib import Path

    p2 = Path("tasks/ai/_agentctx_p2.py").read_text(encoding="utf-8")
    turn = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    assert 'getattr(st, "resume_checkpoint", None)' in p2
    assert "if not st._cp_carried:" in p2
    assert 'resume_checkpoint=st.ctx.get("_consumed_cancel_checkpoint")' in turn


# -- the refusal happens with the turn's hands full --------------------------

def test_the_refusal_gives_back_what_the_caller_took():
    client = _Client()
    client._pawflow_context_is_delta = True
    given_back = []

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context(
            "codex-app", release=lambda: given_back.append(True))

    assert given_back == [True]


def test_an_ordinary_cold_start_gives_nothing_back():
    """Nothing was taken on the strength of a delta, so nothing is released --
    releasing a lock this turn legitimately holds would be worse than the bug."""
    client = _Client()
    given_back = []

    assert client._cli_require_cold_context(
        "codex-app", release=lambda: given_back.append(True)) is None
    assert given_back == []


def test_a_failing_release_hook_still_refuses_to_launch():
    client = _Client()
    client._pawflow_context_is_delta = True

    def _boom():
        raise RuntimeError("lock already released")

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("codex-app", release=_boom)


def test_another_thread_can_take_the_live_lock_after_a_refusal():
    """The real damage: the turn lock is an RLock and the retry runs on the
    SAME thread, so a leaked level is invisible until another thread asks --
    and then it waits for a turn that ended long ago."""
    turn_lock = threading.RLock()
    turn_lock.acquire()          # the provider takes it before looking
    client = _Client()
    client._pawflow_context_is_delta = True

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("gemini-acp", release=turn_lock.release)

    taken = []
    other = threading.Thread(
        target=lambda: taken.append(turn_lock.acquire(timeout=2)))
    other.start()
    other.join()
    assert taken == [True]


@pytest.mark.parametrize("path", [
    "core/llm_providers/_codex_app_stream.py",
    "core/llm_providers/_gemini_stream.py",
])
def test_every_launch_site_hands_back_the_live_lock(path):
    """Both providers hold the live session's turn lock when they ask, and
    their own try/finally has not started yet."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    asks = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_cli_require_cold_context"]

    assert asks, f"{path} never asks whether it may launch"
    for call in asks:
        assert any(kw.arg == "release" for kw in call.keywords), (
            f"{path}: the refusal escapes without giving the turn lock back")


@pytest.mark.parametrize("path", CLI_LAUNCH_SITES)
def test_an_ephemeral_call_is_never_bounced(path):
    """An ephemeral call builds its own full text, but it clones a client that
    may carry the marker. Bouncing it would restart a compact or a memory
    extraction as if it were the agent's own turn."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    asks = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_cli_require_cold_context"]
    assert asks, f"{path} never asks whether it may launch"

    for ask in asks:
        node, exempt = ask, False
        while node in parents and not exempt:
            node = parents[node]
            if isinstance(node, (ast.If, ast.IfExp)) and "ephemeral" in ast.dump(node.test):
                exempt = True
        assert exempt, (
            f"{path}: the launch check is not exempt for ephemeral calls")


# -- the rebuilt context is adopted whole ------------------------------------

def _rebind_state(client, registry, messages, ctx):
    import types

    return types.SimpleNamespace(
        ctx=ctx, conversation_id="conv1234", user_id="u1",
        _agent_name_key="conv1234:main", client=client, registry=registry,
        tool_defs=[{"name": "warm_tool"}], model="warm-model",
        messages=messages, new_messages=[LLMMessage(role="assistant", content="x", conversation_id="conv1234")],
        base_count=0, llm_context=[], use_conv_store=False, _cp_id="")


class _Registry:
    def __init__(self, name):
        self.name = name

    def list_tools(self):
        return []


def _rebind_host():
    from tasks.ai._alc_setup import _ALCSetupMixin

    class _Host(_ALCSetupMixin):
        def __init__(self):
            self._active_contexts_lock = threading.Lock()
            self._active_claude_client = {}

    return _Host()


def test_the_rebuild_rebinds_the_whole_loop_not_four_fields():
    """A rebuilt context brings its own client, registry, tool list and
    messages. A loop left half on the old one executes tools through a
    registry the new context never configured, and cancel still reaches the
    clone of the context that was abandoned."""
    from core.llm_client import LLMClient

    warm_client = LLMClient(provider="codex-app-server", config={})
    cold_client = LLMClient(provider="codex-app-server", config={})
    warm_messages = [LLMMessage(role="user", content="delta only", conversation_id="conv1234")]
    ctx = {"client": warm_client, "registry": _Registry("warm"),
           "tool_defs": [{"name": "warm_tool"}], "model": "warm-model",
           "messages": warm_messages, "active_agent_name": "main",
           "_context_is_delta": True}
    st = _rebind_state(warm_client, ctx["registry"], warm_messages, ctx)
    host = _rebind_host()

    cold_messages = [LLMMessage(role="system", content="full persona", conversation_id="conv1234"),
                     LLMMessage(role="user", content="delta only", conversation_id="conv1234")]
    host._alc_rebind_context(st, {
        "client": cold_client, "registry": _Registry("cold"),
        "tool_defs": [{"name": "cold_tool"}], "model": "cold-model",
        "messages": cold_messages, "active_agent_name": "main",
        "_context_is_delta": False, "_base_message_count": 2,
    })

    # the tools the loop EXECUTES through, not just the ones it advertises
    assert st.registry.name == "cold"
    assert st.tool_defs == [{"name": "cold_tool"}]
    assert st.ctx["tool_defs"] == [{"name": "cold_tool"}]
    assert st.model == "cold-model"
    # a clone of the new client, never the resolver's singleton
    assert st.client is not cold_client
    assert st.client is not warm_client
    assert st.client._agent_name == "main"
    # cancel/preempt reaches the client that is actually running
    assert host._active_claude_client["conv1234:main"] is st.client
    # the rebuilt context is a real cold start: the marker is gone
    assert st.client._pawflow_context_is_delta is False


def test_the_rebuild_keeps_the_message_list_everyone_else_holds():
    """ctx, the emitter and every closure built at setup hold this exact list
    object. Replacing it with a new one splits the loop's state from the one
    the stream reports."""
    from core.llm_client import LLMClient

    warm_messages = [LLMMessage(role="user", content="delta only", conversation_id="conv1234")]
    ctx = {"client": LLMClient(provider="codex-app-server", config={}),
           "registry": _Registry("warm"), "tool_defs": [], "model": "m",
           "messages": warm_messages, "active_agent_name": "main",
           "_context_is_delta": True}
    st = _rebind_state(ctx["client"], ctx["registry"], warm_messages, ctx)
    host = _rebind_host()

    host._alc_rebind_context(st, {
        "client": LLMClient(provider="codex-app-server", config={}),
        "registry": _Registry("cold"), "tool_defs": [], "model": "m",
        "messages": [LLMMessage(role="system", content="full persona", conversation_id="conv1234"),
                     LLMMessage(role="user", content="delta only", conversation_id="conv1234")],
        "active_agent_name": "main", "_context_is_delta": False,
    })

    assert st.messages is warm_messages          # same object, new content
    assert [m.role for m in warm_messages] == ["system", "user"]
    assert st.ctx["messages"] is st.messages
    assert st.base_count == len(st.messages)
    assert st.ctx["_base_message_count"] == len(st.messages)
    # everything produced so far is persisted and was just loaded back
    assert st.new_messages == []


# -- the restart is control flow, not work -----------------------------------

def test_the_restart_does_not_spend_an_iteration():
    """The iteration is counted before the provider is called. With
    max_iterations=1 a restart that keeps it ends the turn having never called
    the model -- and CLI providers deliberately synthesize no empty answer."""
    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("except ColdStartRequired:")[1].split("except Exception")[0]

    assert "st.iteration = max(0, st.iteration - 1)" in body
    assert '_alc_rebind_context' in body


# -- the gauge tells the truth about a cold start ----------------------------

def test_the_gauge_is_zeroed_on_the_pass_that_launches():
    """force_cold skips the live probe, and the reset used to live inside that
    same block -- so the one pass that KNOWS it is launching kept showing the
    dead session's percentage."""
    tree = ast.parse(Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8"))
    probe_blocks = [n for n in ast.walk(tree)
                    if isinstance(n, ast.If) and "force_cold" in ast.dump(n.test)]

    assert probe_blocks, "the live probe is no longer gated on force_cold"
    for block in probe_blocks:
        assert "reset_cli_context_usage" not in ast.dump(block), (
            "the gauge reset is skipped exactly when the turn is launching")
    assert "reset_cli_context_usage" in ast.dump(tree)


# -- the LAUNCH decides the serialisation, not a persisted id ----------------

class _StoreWithAStaleSession:
    """A conversation store that still holds yesterday's claude_session id.

    This is the ordinary state after a server restart: the process is gone,
    the extras are not.
    """

    def __init__(self):
        self.written = {}

    def get_extra(self, _conv_id, key, *_a, **_kw):
        if key.startswith("claude_session:"):
            return "11111111-2222-3333-4444-555555555555"
        return None

    def set_extra(self, _conv_id, key, value, *_a, **_kw):
        self.written[key] = value


def test_a_launch_ignores_the_stored_session_id_and_sends_everything():
    """The high one. The context phase asks the live registry and correctly
    loads the whole transcript; the provider then read the persisted id,
    decided "resume", cut the messages down to the system prompt and the last
    user line -- and only afterwards discovered there was no process, launched
    a fresh one, and handed it that delta. The new CC held nothing.

    Nothing here mocks the decision: a real turn runs against a mocked popen
    and we look at what the process actually receives."""
    from unittest.mock import MagicMock, patch
    import json as _json

    from core.cc_live_registry import LiveSessionRegistry
    from core.llm_client import LLMClient

    LiveSessionRegistry.instance()._sessions.clear()
    client = LLMClient(provider="claude-code",
                       config={"api_key": "k", "default_model": "sonnet"})
    client._conversation_id = "conv-stale"
    client._agent_name = "agent"
    client._user_id = "user"

    ctx_file = (Path(client._get_session_workdir("conv-stale", "agent", "user"))
                / ".pawflow_cli" / "initial_context.md")
    if ctx_file.exists():
        ctx_file.unlink()

    events = [
        _json.dumps({"type": "assistant",
                     "message": {"content": [{"type": "text", "text": "ok"}]}}),
        _json.dumps({"type": "result", "result": "", "model": "sonnet",
                     "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]
    stdout = MagicMock()
    stdout.__iter__ = MagicMock(return_value=iter([e + "\n" for e in events]))
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = 0

    cid = "conv-stale"
    messages = [
        LLMMessage(role="system", content="PERSONA-MARKER", conversation_id=cid),
        LLMMessage(role="user", content="ANCIENT-HISTORY", conversation_id=cid),
        LLMMessage(role="assistant", content="earlier answer", conversation_id=cid),
        LLMMessage(role="user", content="the newest question", conversation_id=cid),
    ]

    try:
        with patch.object(client, "_setup_credentials"), \
                patch.object(client, "_pool_popen", return_value=(proc, None)), \
                patch("core.conversation_store.ConversationStore.instance",
                      return_value=_StoreWithAStaleSession()):
            client.complete_stream(messages, callback=lambda _t: None)

        sent = "".join(str(c.args[0]) for c in proc.stdin.write.call_args_list)
        assert "cold-session bootstrap" in sent, (
            "a launched process was handed a delta, not a cold start")
        assert ctx_file.exists(), "the cold context file was never written"
        body = ctx_file.read_text(encoding="utf-8")
        assert "ANCIENT-HISTORY" in body, "the launched process lost the transcript"
        assert "PERSONA-MARKER" in body, "the launched process lost its persona"
    finally:
        if ctx_file.exists():
            ctx_file.unlink()
        LiveSessionRegistry.instance()._sessions.clear()


def test_the_serialisation_is_decided_after_the_live_lookup():
    """Order is the whole bug: whatever is built before the registry is asked
    is built on a guess."""
    src = Path("core/llm_providers/_cc_stream.py").read_text(encoding="utf-8")

    lookup = src.index("st._is_reuse = st._live_session is not None")
    build = src.index("_build_cli_initial_context_prompt(")
    assert lookup < build, (
        "the prompt is built before the provider knows whether a process is "
        "alive -- which is how a launch got a delta")

    decision = src[lookup:build]
    assert "if st._is_reuse:" in decision.split("# Session-aware")[-1], (
        "the serialisation branches on something other than the live lookup")


def test_the_provider_takes_the_live_session_whatever_slot_it_is_on():
    """Second divergence, same order. The context phase asks without a
    credential slot -- it cannot know which one _setup_credentials will pick
    -- while the provider asked for one exact slot. A stored slot that is
    missing or stale then answered "cold" against the context phase's "warm",
    orphaned the live process and paid a full cold retry."""
    from unittest.mock import MagicMock

    from core.cc_live_registry import CCLiveSession, LiveSessionRegistry

    reg = LiveSessionRegistry.instance()
    reg._sessions.clear()
    try:
        proc = MagicMock()
        proc.poll.return_value = None
        live = CCLiveSession(
            proc=proc, event_q=MagicMock(), reader_thread=MagicMock(),
            stop_event=MagicMock(), pool_container=None, workdir="/w",
            service_id="svc", svc_pool_idx=7, user_id="u", conv_id="c",
            session_id="sess")
        reg._sessions[("u", "c", "a", "svc", 7)] = live

        assert reg.get(("u", "c", "a", "svc", 0)) is None
        found = reg.get_compatible("u", "c", "a", "svc")
        assert found is not None, "the live process is invisible to the provider"
        key, session = found
        assert session is live
        assert key == ("u", "c", "a", "svc", 7), (
            "the key must come back too -- touch/evict/register address it")
    finally:
        reg._sessions.clear()

    src = Path("core/llm_providers/_cc_stream.py").read_text(encoding="utf-8")
    assert "get_compatible(" in src, (
        "the provider still asks a different question than the context phase")
    assert "st._live_key, st._live_session = _compat" in src, (
        "an adopted session must be adopted with its own key")


# -- a refusal must not leave a container nobody tracks ----------------------

def test_a_refused_antigravity_launch_still_kills_the_stale_session():
    """find_session() calls a session warm on the container alone;
    ensure_started() also wants the proxy journal ready. So a session can be
    unusable here while its container is very much alive -- and it has already
    left the registry by the time the callback speaks. A refusal that skipped
    the kill left that container orphaned, and the cold retry started a second
    one beside it."""
    import threading as _threading

    from core.antigravity_observer_pool import AntigravityObserverPool

    pool = AntigravityObserverPool.__new__(AntigravityObserverPool)
    pool._lock = _threading.RLock()
    pool._sessions = {}
    pool._is_usable = lambda _s: False
    killed = []
    pool.kill = lambda s: killed.append(s)
    pool._start_new = lambda *a, **k: pytest.fail("launched after refusing")

    stale = SimpleNamespace(name="antigravity-stale", last_used=0.0)
    pool._sessions[("u", "c", "a", "svc")] = stale
    client = SimpleNamespace(_agent_service="svc")

    def _refuse():
        raise ColdStartRequired("antigravity-interactive: cold start required")

    with pytest.raises(ColdStartRequired):
        pool.ensure_started(client, "m", "u", "c", "a", before_launch=_refuse)

    assert killed == [stale], (
        "the stale container survived a refusal with nothing tracking it")
    assert pool._sessions == {}
