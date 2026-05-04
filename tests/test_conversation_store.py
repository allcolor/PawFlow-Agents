"""Tests for ConversationStore — directory-based conversation storage.

Tests cover:
- save / create conversation
- append_message — unified single-message router (transcript / shared / context)
- load_page — pagination
- message_count — returns int
- get_extra / set_extra — key-value metadata
- list_conversations — filtered by user
- delete — removes conversation
- save_agent_context / load_agent_context — per-agent context
"""

import json
import time
import uuid
import pytest
from unittest.mock import patch

from core.conversation_store import ConversationStore


def _msg(role="user", content="hello", source=None, **kw):
    """Build a minimal valid message dict."""
    m = {
        "role": role,
        "content": content,
        "msg_id": uuid.uuid4().hex[:12],
        "ts": time.time(),
    }
    if source:
        m["source"] = source
    m.update(kw)
    return m


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset ConversationStore singleton before and after each test."""
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    """Create a ConversationStore backed by a temp directory."""
    s = ConversationStore(store_dir=str(tmp_path / "conversations"))
    return s


@pytest.fixture
def conv(store):
    """Create a conversation and return (store, conv_id, user_id)."""
    cid = store.generate_id()
    user_id = "testuser"
    store.save(cid, [], user_id=user_id)
    return store, cid, user_id


# ── Create / Save ────────────────────────────────────────────────────

class TestCreateConversation:

    def test_save_returns_working_cid(self, store):
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        assert store.exists(cid)

    def test_generate_id_is_string(self, store):
        cid = store.generate_id()
        assert isinstance(cid, str)
        assert len(cid) == 16

    def test_save_requires_user_id(self, store):
        cid = store.generate_id()
        with pytest.raises(ValueError, match="user_id"):
            store.save(cid, [], user_id="")

    def test_save_with_initial_messages(self, store):
        cid = store.generate_id()
        msgs = [_msg(content="first"), _msg(role="assistant", content="reply")]
        store.save(cid, msgs, user_id="alice")
        assert store.message_count(cid) == 2


# ── load_page (pagination) ──────────────────────────────────────────

class TestLoadPage:

    def test_load_page_empty(self, conv):
        store, cid, uid = conv
        result = store.load_page(cid, limit=10, offset=0)
        assert result is not None
        assert result["messages"] == []
        assert result["total_count"] == 0

    def test_load_page_returns_messages(self, conv):
        store, cid, uid = conv
        for i in range(10):
            store.append_message(cid, _msg(content=f"m{i}",
                                           source={"type": "user", "name": uid,
                                                   "target_agent": "bot"}),
                                 user_id=uid)
        result = store.load_page(cid, limit=5, offset=0)
        assert result is not None
        assert len(result["messages"]) == 5
        assert result["total_count"] == 10
        assert result["has_more"] is True

    def test_load_page_offset(self, conv):
        store, cid, uid = conv
        for i in range(10):
            store.append_message(cid, _msg(content=f"m{i}",
                                           source={"type": "user", "name": uid,
                                                   "target_agent": "bot"}),
                                 user_id=uid)
        result = store.load_page(cid, limit=5, offset=5)
        assert result is not None
        assert len(result["messages"]) == 5
        assert result["has_more"] is False

    def test_load_page_nonexistent(self, store):
        result = store.load_page("nonexistent", limit=10, offset=0)
        assert result is None


# ── message_count ────────────────────────────────────────────────────

class TestMessageCount:

    def test_count_zero_initially(self, conv):
        store, cid, uid = conv
        assert store.message_count(cid) == 0

    def test_count_after_append(self, conv):
        store, cid, uid = conv
        for _ in range(3):
            store.append_message(cid, _msg(source={"type": "user", "name": uid,
                                                   "target_agent": "bot"}),
                                 user_id=uid)
        assert store.message_count(cid) == 3


# ── get_extra / set_extra ────────────────────────────────────────────

class TestExtras:

    def test_set_and_get(self, conv):
        store, cid, uid = conv
        store.set_extra(cid, "title", "My Chat")
        assert store.get_extra(cid, "title") == "My Chat"

    def test_get_default(self, conv):
        store, cid, uid = conv
        assert store.get_extra(cid, "missing", default="nope") == "nope"

    def test_overwrite_extra(self, conv):
        store, cid, uid = conv
        store.set_extra(cid, "key", "v1")
        store.set_extra(cid, "key", "v2")
        assert store.get_extra(cid, "key") == "v2"

    def test_set_extra_on_nonexistent_returns_false(self, store):
        assert store.set_extra("fake_id", "key", "val") is False

    def test_complex_value(self, conv):
        store, cid, uid = conv
        val = {"nested": [1, 2, 3], "flag": True}
        store.set_extra(cid, "data", val)
        assert store.get_extra(cid, "data") == val


# ── list_conversations ───────────────────────────────────────────────

class TestListConversations:

    def test_list_empty(self, store):
        assert store.list_conversations(user_id="nobody") == []

    def test_list_returns_created(self, store):
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        convs = store.list_conversations(user_id="alice")
        assert len(convs) == 1
        assert convs[0]["conversation_id"] == cid

    def test_list_filters_by_user(self, store):
        cid1 = store.generate_id()
        cid2 = store.generate_id()
        store.save(cid1, [], user_id="alice")
        store.save(cid2, [], user_id="bob")
        alice_convs = store.list_conversations(user_id="alice")
        assert len(alice_convs) == 1
        assert alice_convs[0]["conversation_id"] == cid1

    def test_list_includes_title(self, store):
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.set_extra(cid, "title", "Chat Title")
        convs = store.list_conversations(user_id="alice")
        assert convs[0]["title"] == "Chat Title"


# ── delete ───────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_returns_true(self, conv):
        store, cid, uid = conv
        assert store.exists(cid)
        result = store.delete(cid)
        assert result is True

    def test_delete_nonexistent_raises(self, store):
        # _conv_dir raises ValueError when conv is unknown and no user_id given
        with pytest.raises(ValueError):
            store.delete("no_such_conv")

    def test_delete_clears_from_list(self, store):
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.delete(cid)
        assert store.list_conversations(user_id="alice") == []


# ── save_agent_context / load_agent_context ──────────────────────────

class TestAgentContext:

    def test_save_and_load(self, conv):
        store, cid, uid = conv
        ctx = [{"role": "system", "content": "You are helpful."},
               _msg(role="user", content="hi")]
        store.save_agent_context(cid, "agent1", ctx)
        loaded = store.load_agent_context(cid, "agent1")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["content"] == "You are helpful."

    def test_load_nonexistent_returns_none(self, conv):
        store, cid, uid = conv
        assert store.load_agent_context(cid, "ghost") is None

    def test_small_context_is_cached(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="small")])
        assert store.load_agent_context(cid, "agent1") is not None
        assert "agent1" in store._ctx_cache.get(cid, {})

    def test_large_context_is_not_cached(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="x" * 250001)])
        assert store.load_agent_context(cid, "agent1") is not None
        assert "agent1" not in store._ctx_cache.get(cid, {})

    def test_many_message_context_is_not_cached(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content=str(i)) for i in range(501)])
        assert store.load_agent_context(cid, "agent1") is not None
        assert "agent1" not in store._ctx_cache.get(cid, {})

    def test_save_replaces_context(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="old")])
        store.save_agent_context(cid, "agent1", [_msg(content="new")])
        loaded = store.load_agent_context(cid, "agent1")
        assert len(loaded) == 1
        assert loaded[0]["content"] == "new"

    def test_save_on_nonexistent_conv_returns_false(self, store):
        assert store.save_agent_context("fake", "a", [_msg()]) is False


# ── append_message (unified router) ────────────────────────────────

class TestAppendMessage:
    """append_message is the single write path: per-message routing to
    transcript / shared / own ctx / other agents' ctx / delegate A<->B
    based on role + source + tool_calls + display_only.
    """

    def test_user_message_goes_to_transcript_shared_and_target_ctx(
            self, conv):
        store, cid, uid = conv
        m = _msg(role="user", content="hi bot",
                 source={"type": "user", "name": uid,
                         "target_agent": "bot"})
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        # Transcript
        assert store.message_count(cid) == 1
        # Shared contains prefixed copy
        shared = store.load_agent_context(cid, "")
        assert shared and any(
            "[User to agent bot]" in str(m.get("content", ""))
            for m in shared)
        # Target ctx contains raw copy
        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and any("hi bot" in str(m.get("content", ""))
                               for m in bot_ctx)

    def test_user_message_routes_by_target_agent_without_agent_param(
            self, conv):
        store, cid, uid = conv
        msg = _msg(role="user", content="targeted request",
                   source={"type": "user", "name": uid,
                           "target_agent": "bot"})

        store.append_message(cid, msg, user_id=uid)

        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and bot_ctx[-1]["content"] == "targeted request"
        shared = store.load_agent_context(cid, "")
        assert shared and "[User to agent bot]" in shared[-1]["content"]

    def test_user_message_without_target_agent_is_rejected(self, conv):
        store, cid, uid = conv
        msg = _msg(role="user", content="orphan",
                   source={"type": "user", "name": uid})

        with pytest.raises(ValueError, match="source.target_agent"):
            store.append_message(cid, msg, user_id=uid)

    def test_first_target_message_seeds_missing_agent_context_from_shared(
            self, conv):
        store, cid, uid = conv
        seed = _msg(role="assistant", content="previous analysis",
                    source={"type": "agent", "name": "other"})
        store.append_message(cid, seed, agent_name="other", user_id=uid)

        current = _msg(role="user", content="new request",
                       source={"type": "user", "name": uid,
                               "target_agent": "bot"})
        store.append_message(cid, current, agent_name="bot", user_id=uid)

        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx
        contents = [str(m.get("content", "")) for m in bot_ctx]
        assert any("previous analysis" in c for c in contents)
        assert any("new request" in c for c in contents)
        assert len(bot_ctx) >= 2

    def test_assistant_plain_text_goes_to_shared(self, conv):
        store, cid, uid = conv
        m = _msg(role="assistant", content="done",
                 source={"type": "agent", "name": "bot"})
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        shared = store.load_agent_context(cid, "")
        assert shared and any(
            "[Agent bot]" in str(m.get("content", ""))
            for m in shared)
        assert shared[-1]["content"].count("[Agent bot]") == 1

    def test_shared_prefixing_is_idempotent(self, conv):
        store, cid, uid = conv
        msg = _msg(role="assistant", content="[Agent bot]:\nalready tagged",
                   source={"type": "agent", "name": "bot"})
        store.append_message(cid, msg, agent_name="bot", user_id=uid)

        shared = store.load_agent_context(cid, "")
        assert shared
        assert shared[-1]["content"].count("[Agent bot]:") == 1

    def test_assistant_with_tool_calls_writes_raw_to_own_ctx(
            self, conv):
        store, cid, uid = conv
        m = _msg(role="assistant", content="calling",
                 source={"type": "agent", "name": "bot"},
                 tool_calls=[{"id": "tc1", "name": "read",
                              "arguments": {}}])
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        # Own ctx keeps tool_calls intact
        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and any(msg.get("tool_calls")
                               for msg in bot_ctx)
        # Shared keeps text but strips tool_calls
        shared = store.load_agent_context(cid, "")
        assert shared
        for s in shared:
            assert not s.get("tool_calls")

    def test_tool_result_stays_private_to_own_ctx(self, conv):
        store, cid, uid = conv
        m = _msg(role="tool", content="[result]",
                 source={"type": "agent", "name": "bot"},
                 tool_call_id="tc1")
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and any(msg.get("role") == "tool"
                               for msg in bot_ctx)
        shared = store.load_agent_context(cid, "")
        if shared:
            assert not any(msg.get("role") == "tool"
                           for msg in shared)

    def test_display_only_transcript_only(self, conv):
        store, cid, uid = conv
        m = _msg(role="assistant", content="narrate",
                 source={"type": "agent", "name": "bot"},
                 display_only=True)
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        assert store.message_count(cid) == 1
        bot_ctx = store.load_agent_context(cid, "bot")
        # display_only must not appear in any ctx
        assert not bot_ctx or not any(
            "narrate" in str(msg.get("content", ""))
            for msg in bot_ctx)
        shared = store.load_agent_context(cid, "")
        assert not shared or not any(
            "narrate" in str(msg.get("content", ""))
            for msg in shared)

    def test_context_injection_skips_transcript_and_shared(self, conv):
        store, cid, uid = conv
        m = _msg(role="user", content="[System: resumed]",
                 source={"type": "context"})
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        # NOT in transcript
        assert store.message_count(cid) == 0
        # NOT in shared
        shared = store.load_agent_context(cid, "")
        assert not shared or not any(
            "resumed" in str(msg.get("content", ""))
            for msg in shared)
        # In target agent ctx only
        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and any(
            "resumed" in str(msg.get("content", ""))
            for msg in bot_ctx)

    def test_delegate_request_routes_to_from_to_and_shared(self, conv):
        store, cid, uid = conv
        # Seed cache: ensure both 'alice' and 'bob' are known agents
        store.save_agent_context(cid, "alice", [])
        store.save_agent_context(cid, "bob", [])
        store._reload_cache(cid)
        m = _msg(role="assistant", content="do X",
                 source={"type": "agent_delegate",
                         "from": "alice", "to": "bob",
                         "kind": "request"})
        store.append_message(cid, m, agent_name="alice", user_id=uid)
        alice_ctx = store.load_agent_context(cid, "alice")
        bob_ctx = store.load_agent_context(cid, "bob")
        shared = store.load_agent_context(cid, "")
        # alice sees [delegate alice → bob]:
        assert alice_ctx and any(
            "[delegate alice" in str(msg.get("content", ""))
            for msg in alice_ctx)
        # bob receives with explicit attribution
        assert bob_ctx and any(
            "Here is a message from agent 'alice'" in
            str(msg.get("content", ""))
            for msg in bob_ctx)
        # shared visible with [alice to agent bob]: prefix
        assert shared and any(
            "[alice to agent bob]" in str(msg.get("content", ""))
            for msg in shared)

    def test_delegate_reply_stays_private_no_shared(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "alice", [])
        store.save_agent_context(cid, "bob", [])
        store._reload_cache(cid)
        m = _msg(role="assistant", content="answer",
                 source={"type": "agent_delegate",
                         "from": "bob", "to": "alice",
                         "kind": "reply"})
        store.append_message(cid, m, agent_name="bob", user_id=uid)
        alice_ctx = store.load_agent_context(cid, "alice")
        bob_ctx = store.load_agent_context(cid, "bob")
        shared = store.load_agent_context(cid, "")
        # Both parties receive their tailored copy
        assert alice_ctx and any(
            "reply to your delegate" in str(msg.get("content", ""))
            for msg in alice_ctx)
        assert bob_ctx and any(
            "[delegate bob" in str(msg.get("content", ""))
            for msg in bob_ctx)
        # Shared must NOT contain the reply
        if shared:
            assert not any(
                "answer" in str(msg.get("content", ""))
                for msg in shared)

    def test_broadcast_to_other_agents(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "alice", [])
        store.save_agent_context(cid, "bob", [])
        store._reload_cache(cid)
        m = _msg(role="assistant", content="hello world",
                 source={"type": "agent", "name": "alice"})
        store.append_message(cid, m, agent_name="alice", user_id=uid)
        bob_ctx = store.load_agent_context(cid, "bob")
        assert bob_ctx and any(
            "[Agent alice]" in str(msg.get("content", ""))
            for msg in bob_ctx)

    def test_creates_conv_if_needed(self, store):
        cid = store.generate_id()
        m = _msg(role="assistant", content="hi",
                 source={"type": "agent", "name": "bot"})
        store.append_message(cid, m, agent_name="bot",
                             user_id="newuser")
        assert store.exists(cid)

    def test_does_not_git_snapshot(self, conv):
        store, cid, uid = conv
        with patch.object(store, "git_snapshot") as gs:
            m = _msg(role="assistant", content="x",
                     source={"type": "agent", "name": "bot"})
            store.append_message(cid, m, agent_name="bot",
                                 user_id=uid)
            gs.assert_not_called()


# ── cleanup_orphan_claude_sessions / _prune_stale_cc_sessions ────────

class TestCleanupOrphanClaudeSessions:
    """CC writes a new <uuid>.jsonl per turn under
    sessions/claude/<user>/<sanitized_cid>/claude/projects/-workspace/;
    only the one recorded in extras[claude_session:<agent>] is current.
    cleanup_orphan_claude_sessions must:
      (a) wipe whole dirs whose conv is dead / one-shot (_compact etc.)
      (b) for live convs, prune every jsonl whose stem isn't in extras
          (extras is the single source of truth - no mtime heuristic)
      (c) leave the current-session jsonl alone.
    """

    def _setup(self, tmp_path, monkeypatch):
        from core import paths as _paths
        base = tmp_path / "sessions" / "claude"
        base.mkdir(parents=True)
        monkeypatch.setattr(_paths, "CLAUDE_SESSIONS_DIR", base)
        return base

    def _mk_jsonl(self, sess_dir, session_id, agent="claude",
                  mtime=None):
        proj = sess_dir / agent / "projects" / "-workspace"
        proj.mkdir(parents=True, exist_ok=True)
        jf = proj / f"{session_id}.jsonl"
        jf.write_text("{}\n")
        if mtime is not None:
            import os
            os.utime(jf, (mtime, mtime))
        return jf

    def test_removes_dir_for_dead_conv(self, store, tmp_path,
                                        monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        orphan = base / "alice" / "deadcid0000000000"
        orphan.mkdir(parents=True)
        (orphan / "creds.json").write_text("{}")
        removed = store.cleanup_orphan_claude_sessions()
        assert removed >= 1
        assert not orphan.exists()

    def test_removes_one_shot_dirs(self, store, tmp_path, monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        one_shot = base / "alice" / "_compact"
        one_shot.mkdir(parents=True)
        (one_shot / "x").write_text("")
        removed = store.cleanup_orphan_claude_sessions()
        assert removed >= 1
        assert not one_shot.exists()

    def test_preserves_live_conv_dir(self, store, tmp_path, monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        sess_dir.mkdir(parents=True)
        store.cleanup_orphan_claude_sessions()
        assert sess_dir.exists()

    def test_prunes_stale_jsonl_in_live_conv(self, store, tmp_path,
                                              monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        current_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        store.set_extra(cid, "claude_session:claude", current_sid)
        old_ts = time.time() - 3600  # 1h old
        current = self._mk_jsonl(sess_dir, current_sid, mtime=old_ts)
        stale = self._mk_jsonl(sess_dir, "bbbbbbbb-bbbb-bbbb-bbbb-"
                                         "bbbbbbbbbbbb", mtime=old_ts)
        store.cleanup_orphan_claude_sessions()
        assert current.exists(), "current session jsonl must survive"
        assert not stale.exists(), "stale session jsonl must be pruned"

    def test_prunes_unregistered_jsonl_regardless_of_mtime(
            self, store, tmp_path, monkeypatch):
        """extras is the single source of truth. A jsonl whose stem is
        not in extras[claude_session:*] is pruned - we do NOT use an
        mtime grace window. Mtime-based sparing previously resurrected
        orphans and could wipe live sessions whose stall timeout was
        larger than the hardcoded grace window."""
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        store.set_extra(cid, "claude_session:claude",
                        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        fresh = self._mk_jsonl(sess_dir, "cccccccc-cccc-cccc-cccc-"
                                         "cccccccccccc")  # mtime=now
        store.cleanup_orphan_claude_sessions()
        assert not fresh.exists(), (
            "jsonl not registered in extras must be pruned "
            "(extras is authoritative)")

    def test_no_extras_keeps_everything(self, store, tmp_path,
                                         monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        # No claude_session:* extras recorded.
        old_ts = time.time() - 3600
        jf = self._mk_jsonl(sess_dir, "dddddddd-dddd-dddd-dddd-"
                                      "dddddddddddd", mtime=old_ts)
        store.cleanup_orphan_claude_sessions()
        assert jf.exists(), "no known current sid → don't guess"

    def test_prunes_companion_dir_alongside_jsonl(self, store, tmp_path,
                                                    monkeypatch):
        """Each CC session leaves a <sid>.jsonl AND a <sid>/ workdir
        next to it. Both must be reclaimed when the jsonl is pruned."""
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        store.set_extra(cid, "claude_session:claude",
                        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        old_ts = time.time() - 3600
        stale_sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        jf = self._mk_jsonl(sess_dir, stale_sid, mtime=old_ts)
        companion = jf.with_suffix("")
        companion.mkdir()
        (companion / "scratch.txt").write_text("x")
        store.cleanup_orphan_claude_sessions()
        assert not jf.exists(), "stale jsonl must be pruned"
        assert not companion.exists(), "companion dir must be pruned"

    def test_invalidate_claude_sessions_wipes_disk(self, store, tmp_path,
                                                     monkeypatch):
        """invalidate_claude_sessions must clear extras AND wipe all
        jsonls + companion dirs on disk — including the "current" one,
        since it was just invalidated."""
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        current_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        stale_sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        store.set_extra(cid, "claude_session:claude", current_sid)
        store.set_extra(cid, "codex_session:assistant", "codex-old")
        store.set_extra(cid, "gemini_acp_session:gemini", "gemini-old")
        store.set_extra(cid, "gemini_acp_pool_idx:gemini", "0")
        store.set_extra(cid, "gemini_acp_session_version:gemini", "2")
        # current (fresh mtime) + stale companion dir
        current_jf = self._mk_jsonl(sess_dir, current_sid)
        stale_jf = self._mk_jsonl(sess_dir, stale_sid,
                                    mtime=time.time() - 3600)
        stale_companion = stale_jf.with_suffix("")
        stale_companion.mkdir()
        store.invalidate_claude_sessions(cid)
        # extras cleared
        assert store.get_extra(cid, "claude_session:claude") == ""
        assert store.get_extra(cid, "codex_session:assistant") == ""
        assert store.get_extra(cid, "gemini_acp_session:gemini") == ""
        assert store.get_extra(cid, "gemini_acp_pool_idx:gemini") == ""
        assert store.get_extra(cid, "gemini_acp_session_version:gemini") == ""
        # disk wiped (wipe_all bypasses mtime guard and live_sids)
        assert not current_jf.exists()
        assert not stale_jf.exists()
        assert not stale_companion.exists()

    def test_invalidate_noop_if_no_owner(self, store, tmp_path,
                                           monkeypatch):
        """If _cid_user has no owner for cid, skip disk wipe silently."""
        self._setup(tmp_path, monkeypatch)
        # No store.save → _cid_user has no entry for this cid
        cid = "orphan_cid_0000000000"
        # Should not raise
        store.invalidate_claude_sessions(cid)

    def test_invalidate_per_agent_clears_extra_and_prunes_its_jsonl(
            self, store, tmp_path, monkeypatch):
        """invalidate_claude_session_for_agent clears ONE agent's extra
        and prunes ITS stale jsonl, but leaves other agents' live
        sessions intact (extras authoritative via wipe_all=False).
        Used after PawFlow compact to avoid orphan jsonls piling up.
        """
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        claude_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        other_sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        # Two live agents, each with its own jsonl.
        store.set_extra(cid, "claude_session:claude", claude_sid)
        store.set_extra(cid, "claude_session:other", other_sid)
        store.set_extra(cid, "gemini_acp_session:claude", "gemini-claude")
        store.set_extra(cid, "gemini_acp_session:other", "gemini-other")
        store.set_extra(cid, "gemini_acp_pool_idx:claude", "1")
        store.set_extra(cid, "gemini_acp_session_version:claude", "2")
        store.set_extra(cid, "gemini_acp_session_version:other", "2")
        claude_jf = self._mk_jsonl(sess_dir, claude_sid)
        other_jf = self._mk_jsonl(sess_dir, other_sid)
        # Invalidate ONLY 'claude'.
        store.invalidate_claude_session_for_agent(cid, "claude")
        # 'claude' extra cleared, 'other' untouched.
        assert store.get_extra(cid, "claude_session:claude") == ""
        assert store.get_extra(cid, "claude_session:other") == other_sid
        assert store.get_extra(cid, "gemini_acp_session:claude") == ""
        assert store.get_extra(cid, "gemini_acp_session:other") == "gemini-other"
        assert store.get_extra(cid, "gemini_acp_pool_idx:claude") == ""
        assert store.get_extra(cid, "gemini_acp_session_version:claude") == ""
        assert store.get_extra(cid, "gemini_acp_session_version:other") == "2"
        # 'claude' jsonl gone (no longer in live_sids), 'other' still
        # present because extras still names it as live.
        assert not claude_jf.exists()
        assert other_jf.exists()

    def test_invalidate_per_agent_clears_gemini_without_claude_sid(
            self, store, tmp_path, monkeypatch):
        """Context edits invalidate Gemini ACP even without a CC session."""
        self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.set_extra(cid, "gemini_acp_session:gemini", "gemini-old")
        store.set_extra(cid, "gemini_acp_pool_idx:gemini", "0")
        store.set_extra(cid, "gemini_acp_session_version:gemini", "2")

        store.invalidate_claude_session_for_agent(cid, "gemini")

        assert store.get_extra(cid, "gemini_acp_session:gemini") == ""
        assert store.get_extra(cid, "gemini_acp_pool_idx:gemini") == ""
        assert store.get_extra(cid, "gemini_acp_session_version:gemini") == ""

    def test_invalidate_per_agent_prunes_companion_dir(
            self, store, tmp_path, monkeypatch):
        """After compact, both <sid>.jsonl AND the <sid>/ workdir must
        be reclaimed so the session dir stops growing."""
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        store.set_extra(cid, "claude_session:claude", sid)
        jf = self._mk_jsonl(sess_dir, sid)
        companion = jf.with_suffix("")
        companion.mkdir()
        (companion / "scratch.txt").write_text("x")
        store.invalidate_claude_session_for_agent(cid, "claude")
        assert not jf.exists()
        assert not companion.exists()


class TestCleanupCliRuntimeSessions:
    def _setup(self, tmp_path, monkeypatch):
        from core import paths as _paths
        codex = tmp_path / "sessions" / "codex"
        gemini = tmp_path / "sessions" / "gemini"
        codex.mkdir(parents=True)
        gemini.mkdir(parents=True)
        monkeypatch.setattr(_paths, "CODEX_SESSIONS_DIR", codex)
        monkeypatch.setattr(_paths, "GEMINI_SESSIONS_DIR", gemini)
        return codex, gemini

    def _codex_jsonl(self, base, cid, thread_id, agent="assistant", user="alice"):
        d = base / user / cid.replace(":", "_") / agent / ".codex" / "sessions" / "2026" / "04" / "30"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"rollout-2026-04-30T00-00-00-{thread_id}.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        return p

    def _gemini_jsonl(self, base, cid, session_id, agent="gemini", user="alice"):
        d = base / user / cid.replace(":", "_") / agent / ".gemini" / "tmp" / "gemini" / "chats"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"session-{session_id}.jsonl"
        p.write_text(json.dumps({"sessionId": session_id}) + "\n", encoding="utf-8")
        return p

    def test_cleanup_prunes_non_current_codex_and_gemini_jsonls(self, store, tmp_path, monkeypatch):
        codex, gemini = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.set_extra(cid, "codex_app_server_thread:assistant", "thread-live")
        store.set_extra(cid, "gemini_acp_session:gemini", "gemini-live")

        codex_live = self._codex_jsonl(codex, cid, "thread-live")
        codex_old = self._codex_jsonl(codex, cid, "thread-old")
        gemini_live = self._gemini_jsonl(gemini, cid, "gemini-live")
        gemini_old = self._gemini_jsonl(gemini, cid, "gemini-old")

        removed = store.cleanup_orphan_cli_sessions()

        assert removed >= 2
        assert codex_live.exists()
        assert not codex_old.exists()
        assert gemini_live.exists()
        assert not gemini_old.exists()

    def test_invalidate_all_removes_codex_and_gemini_agent_dirs(self, store, tmp_path, monkeypatch):
        codex, gemini = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.set_extra(cid, "codex_app_server_thread:assistant", "thread-live")
        store.set_extra(cid, "codex_app_pool_idx:assistant", "0")
        store.set_extra(cid, "gemini_acp_session:gemini", "gemini-live")
        store.set_extra(cid, "gemini_acp_pool_idx:gemini", "0")

        codex_file = self._codex_jsonl(codex, cid, "thread-live")
        gemini_file = self._gemini_jsonl(gemini, cid, "gemini-live")
        codex_agent = codex_file.parents[5]
        gemini_agent = gemini_file.parents[4]

        store.invalidate_claude_sessions(cid)

        assert store.get_extra(cid, "codex_app_server_thread:assistant") == ""
        assert store.get_extra(cid, "codex_app_pool_idx:assistant") == ""
        assert store.get_extra(cid, "gemini_acp_session:gemini") == ""
        assert store.get_extra(cid, "gemini_acp_pool_idx:gemini") == ""
        assert not codex_agent.exists()
        assert not gemini_agent.exists()

    def test_invalidate_one_agent_removes_only_matching_cli_dirs(self, store, tmp_path, monkeypatch):
        codex, gemini = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        store.set_extra(cid, "codex_app_server_thread:assistant", "thread-live")
        store.set_extra(cid, "codex_app_server_thread:other", "thread-other")
        store.set_extra(cid, "gemini_acp_session:assistant", "gemini-live")
        store.set_extra(cid, "gemini_acp_session:other", "gemini-other")

        codex_file = self._codex_jsonl(codex, cid, "thread-live", agent="assistant")
        codex_other = self._codex_jsonl(codex, cid, "thread-other", agent="other")
        gemini_file = self._gemini_jsonl(gemini, cid, "gemini-live", agent="assistant")
        gemini_other = self._gemini_jsonl(gemini, cid, "gemini-other", agent="other")

        store.invalidate_claude_session_for_agent(cid, "assistant")

        assert not codex_file.parents[5].exists()
        assert codex_other.parents[5].exists()
        assert not gemini_file.parents[4].exists()
        assert gemini_other.parents[4].exists()
        assert store.get_extra(cid, "codex_app_server_thread:assistant") == ""
        assert store.get_extra(cid, "codex_app_server_thread:other") == "thread-other"
        assert store.get_extra(cid, "gemini_acp_session:assistant") == ""
        assert store.get_extra(cid, "gemini_acp_session:other") == "gemini-other"
