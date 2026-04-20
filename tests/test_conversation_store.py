"""Tests for ConversationStore — directory-based conversation storage.

Tests cover:
- save / create conversation
- append_messages — add messages to transcript
- load_page — pagination
- message_count — returns int
- get_extra / set_extra — key-value metadata
- list_conversations — filtered by user
- delete — removes conversation
- save_agent_context / load_agent_context — per-agent context
- agent_flush — flush agent messages to shared
"""

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


# ── append_messages ──────────────────────────────────────────────────

class TestAppendMessages:

    def test_append_increases_count(self, conv):
        store, cid, uid = conv
        assert store.message_count(cid) == 0
        store.append_messages(cid, [_msg(content="hi")], user_id=uid)
        assert store.message_count(cid) == 1

    def test_append_multiple(self, conv):
        store, cid, uid = conv
        msgs = [_msg(content=f"msg{i}") for i in range(5)]
        store.append_messages(cid, msgs, user_id=uid)
        assert store.message_count(cid) == 5

    def test_append_deduplicates(self, conv):
        store, cid, uid = conv
        m = _msg(content="dup")
        store.append_messages(cid, [m], user_id=uid)
        store.append_messages(cid, [m], user_id=uid)  # same msg_id
        assert store.message_count(cid) == 1

    def test_append_to_nonexistent_creates(self, store):
        cid = store.generate_id()
        store.append_messages(cid, [_msg(content="x")], user_id="bob")
        assert store.exists(cid)
        assert store.message_count(cid) == 1


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
        msgs = [_msg(content=f"m{i}") for i in range(10)]
        store.append_messages(cid, msgs, user_id=uid)
        result = store.load_page(cid, limit=5, offset=0)
        assert result is not None
        assert len(result["messages"]) == 5
        assert result["total_count"] == 10
        assert result["has_more"] is True

    def test_load_page_offset(self, conv):
        store, cid, uid = conv
        msgs = [_msg(content=f"m{i}") for i in range(10)]
        store.append_messages(cid, msgs, user_id=uid)
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
        store.append_messages(cid, [_msg(), _msg(), _msg()], user_id=uid)
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

    def test_save_replaces_context(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="old")])
        store.save_agent_context(cid, "agent1", [_msg(content="new")])
        loaded = store.load_agent_context(cid, "agent1")
        assert len(loaded) == 1
        assert loaded[0]["content"] == "new"

    def test_save_on_nonexistent_conv_returns_false(self, store):
        assert store.save_agent_context("fake", "a", [_msg()]) is False


# ── agent_flush ──────────────────────────────────────────────────────

class TestAgentFlush:

    @patch.object(ConversationStore, "_git_init")
    @patch.object(ConversationStore, "git_snapshot")
    def test_flush_adds_to_transcript(self, _snap, _git, conv):
        store, cid, uid = conv
        pub = [_msg(role="assistant", content="answer",
                     source={"type": "agent", "name": "bot"})]
        store.agent_flush(cid, "bot", public_messages=pub,
                          private_messages=[], user_id=uid)
        assert store.message_count(cid) == 1

    @patch.object(ConversationStore, "_git_init")
    @patch.object(ConversationStore, "git_snapshot")
    def test_flush_writes_agent_context(self, _snap, _git, conv):
        store, cid, uid = conv
        pub = [_msg(role="assistant", content="ctx msg",
                     source={"type": "agent", "name": "bot"})]
        store.agent_flush(cid, "bot", public_messages=pub,
                          private_messages=[], user_id=uid)
        ctx = store.load_agent_context(cid, "bot")
        assert ctx is not None
        assert any("ctx msg" in m.get("content", "") for m in ctx)

    @patch.object(ConversationStore, "_git_init")
    @patch.object(ConversationStore, "git_snapshot")
    def test_flush_private_not_in_shared(self, _snap, _git, conv):
        store, cid, uid = conv
        priv = [_msg(role="assistant", content="secret",
                      source={"type": "agent", "name": "bot"})]
        store.agent_flush(cid, "bot", public_messages=[],
                          private_messages=priv, user_id=uid)
        shared = store.load_agent_context(cid, "")  # shared context
        # Private messages should not appear in shared
        if shared:
            assert not any("secret" in m.get("content", "") for m in shared)

    @patch.object(ConversationStore, "_git_init")
    @patch.object(ConversationStore, "git_snapshot")
    def test_flush_creates_conv_if_needed(self, _snap, _git, store):
        cid = store.generate_id()
        pub = [_msg(role="assistant", content="hi",
                     source={"type": "agent", "name": "bot"})]
        store.agent_flush(cid, "bot", public_messages=pub,
                          private_messages=[], user_id="newuser")
        assert store.exists(cid)


# ── append_message (unified router) ────────────────────────────────

class TestAppendMessage:
    """append_message is the single write path. Same semantics as
    agent_flush (same transform helpers) but per-message instead of
    grouped, and without dedup / git_snapshot.
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

    def test_assistant_plain_text_goes_to_shared(self, conv):
        store, cid, uid = conv
        m = _msg(role="assistant", content="done",
                 source={"type": "agent", "name": "bot"})
        store.append_message(cid, m, agent_name="bot", user_id=uid)
        shared = store.load_agent_context(cid, "")
        assert shared and any(
            "[Agent bot]" in str(m.get("content", ""))
            for m in shared)

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
        # current (fresh mtime) + stale companion dir
        current_jf = self._mk_jsonl(sess_dir, current_sid)
        stale_jf = self._mk_jsonl(sess_dir, stale_sid,
                                    mtime=time.time() - 3600)
        stale_companion = stale_jf.with_suffix("")
        stale_companion.mkdir()
        store.invalidate_claude_sessions(cid)
        # extras cleared
        assert store.get_extra(cid, "claude_session:claude") == ""
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
        claude_jf = self._mk_jsonl(sess_dir, claude_sid)
        other_jf = self._mk_jsonl(sess_dir, other_sid)
        # Invalidate ONLY 'claude'.
        store.invalidate_claude_session_for_agent(cid, "claude")
        # 'claude' extra cleared, 'other' untouched.
        assert store.get_extra(cid, "claude_session:claude") == ""
        assert store.get_extra(cid, "claude_session:other") == other_sid
        # 'claude' jsonl gone (no longer in live_sids), 'other' still
        # present because extras still names it as live.
        assert not claude_jf.exists()
        assert other_jf.exists()

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
