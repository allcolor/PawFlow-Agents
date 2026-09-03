"""Current/next-turn modes around the end-of-turn pending drain.

Regression for the webchat starvation bug: while an agent answered a
delegator (delegate_reply turn), every human message was queued with
"mode mismatch" and, at the drain, the newest queued delegate broadcast
re-selected delegate_reply. The agent's replies then went privately to the
delegator and the user's next messages were queued again, until a force
stop. A human message in the batch must win and yield a visible user turn.

The drain selects the *next* turn. It must not retroactively change ownership
of the turn that just completed, or an interleaved human/tmux message makes a
shared delegate finish as a normal agent turn and its task_id never receives a
terminal reply.
"""
import inspect
from types import SimpleNamespace

from core.llm_client import LLMMessage
from tasks.ai._alc_closures2 import _DELEGATE_HINT_RE, _ALCClosures2Mixin
from tasks.ai.agent_core import AgentCoreMixin


COLD_HINT = (
    "\n\nDELEGATE MODE: Agent 'assistant' is waiting for your answer. Write "
    "your response as normal text — it will be routed back to 'assistant' "
    "automatically as a private reply. Do NOT call delegate() yourself to "
    "answer — that would open a new thread instead of replying. Use "
    "delegate() only if you need to ASK a DIFFERENT agent before answering "
    "'assistant'."
)
HOT_HINT = (
    "\n\nDELEGATE MODE: 'assistant' is waiting for your answer. Write your "
    "response as normal text; it will be routed back to 'assistant' "
    "automatically as a private reply. Do NOT call delegate() yourself to "
    "answer."
)


def _msg(content, source):
    return LLMMessage(role="user", content=content, source=source,
                      conversation_id="conv12345678")


def _human(content="hello"):
    return _msg(content, {"type": "user", "name": "alice",
                          "target_agent": "claude"})


def _delegate(kind="request", content="status"):
    return _msg(content, {"type": "agent_delegate", "from": "assistant",
                          "to": "claude", "kind": kind, "task_id": ""})


def _external(content="ext"):
    return _msg(content, {"type": "a2a", "task_id": "t_ext"})


def _state(turn_mode=None, system="You are claude."):
    ctx = {"_turn_mode": turn_mode or {"type": "user", "source_agent": None}}
    messages = [LLMMessage(role="system", content=system,
                           conversation_id="conv12345678")]
    return SimpleNamespace(ctx=ctx, messages=messages,
                           conversation_id="conv12345678")


def _apply(st, batch):
    mixin = _ALCClosures2Mixin()
    return mixin._alc_apply_queued_delegate_turn_mode(st, batch)


def _system(st):
    return st.messages[0].content


class TestHumanMessageWins:
    def test_human_after_delegate_broadcasts_resets_to_user_turn(self):
        st = _state(turn_mode={"type": "delegate_reply",
                               "source_agent": "assistant"},
                    system="You are claude." + COLD_HINT)
        batch = [_delegate(), _human("BUG"), _delegate()]
        assert _apply(st, batch) is False
        assert st.ctx["_turn_mode"] == {"type": "user", "source_agent": None}
        assert _system(st) == "You are claude."

    def test_human_older_than_newest_delegate_still_wins(self):
        st = _state(turn_mode={"type": "delegate_reply",
                               "source_agent": "assistant"})
        batch = [_human("first"), _delegate(), _delegate()]
        _apply(st, batch)
        assert st.ctx["_turn_mode"]["type"] == "user"

    def test_human_beats_external_request(self):
        st = _state()
        _apply(st, [_external(), _human()])
        assert st.ctx["_turn_mode"]["type"] == "user"

    def test_hot_path_hint_is_stripped_on_user_turn(self):
        st = _state(turn_mode={"type": "delegate_reply",
                               "source_agent": "assistant"},
                    system="You are claude." + HOT_HINT)
        _apply(st, [_human()])
        assert _system(st) == "You are claude."
        assert "DELEGATE MODE" not in _system(st)


class TestCompletedTurnOwnership:
    def test_delegate_mode_is_snapshotted_before_next_turn_reselection(self):
        src = inspect.getsource(AgentCoreMixin._run_agent_loop_inner)
        snapshot = (
            'st._completed_turn_mode = dict('
            'st.ctx.get("_turn_mode") or {})'
        )
        drain = "# Final drain:"
        assert snapshot in src
        assert src.index(snapshot) < src.index(drain)

        done = src[src.index("# If this was a delegate-reply turn"):]
        assert "st._tm_end = st._completed_turn_mode" in done

    def test_idle_delegate_wake_uses_cli_fallback_reply_text(self):
        src = inspect.getsource(AgentCoreMixin._run_agent_loop_inner)
        done = src[src.index("# If this was a delegate-reply turn"):]
        wake = done[done.index("SpawnAgentsHandler._wake_caller("):]
        wake = wake[:wake.index("source=st._reply_src") + len("source=st._reply_src")]
        assert "st._reply_text" in wake
        assert "st.response_content" not in wake


class TestDelegateOnlyBatches:
    def test_delegate_request_sets_delegate_reply_and_hint(self):
        st = _state()
        assert _apply(st, [_delegate()]) is True
        mode = st.ctx["_turn_mode"]
        assert mode["type"] == "delegate_reply"
        assert mode["source_agent"] == "assistant"
        assert "DELEGATE MODE: 'assistant'" in _system(st)

    def test_hint_is_not_duplicated_across_drains(self):
        st = _state()
        _apply(st, [_delegate()])
        _apply(st, [_delegate()])
        assert _system(st).count("DELEGATE MODE:") == 1

    def test_delegate_replies_only_reset_to_user_turn(self):
        st = _state(turn_mode={"type": "delegate_reply",
                               "source_agent": "assistant"},
                    system="You are claude." + HOT_HINT)
        assert _apply(st, [_delegate(kind="reply")]) is False
        assert st.ctx["_turn_mode"]["type"] == "user"
        assert _system(st) == "You are claude."

    def test_external_request_wins_over_delegate_request(self):
        st = _state()
        assert _apply(st, [_external(), _delegate()]) is True
        mode = st.ctx["_turn_mode"]
        assert mode["type"] == "external_request"
        assert mode["source_agent"] == "t_ext"

    def test_empty_batch_keeps_user_mode(self):
        st = _state()
        assert _apply(st, []) is False
        assert st.ctx["_turn_mode"]["type"] == "user"


class TestHintRegex:
    def test_matches_both_wordings_only(self):
        assert _DELEGATE_HINT_RE.sub("", "base" + COLD_HINT) == "base"
        assert _DELEGATE_HINT_RE.sub("", "base" + HOT_HINT) == "base"
        assert _DELEGATE_HINT_RE.sub("", "base\n\nTail.") == "base\n\nTail."
