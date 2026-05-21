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
import inspect
import subprocess
import threading
import time
import uuid
import pytest
from unittest.mock import patch

from core.conversation_store import ConversationStore


def test_seed_persisted_seq_zero_initializes_cache(monkeypatch):
    import core.llm_client as lc_mod

    cid = f"cid_zero_seed_{uuid.uuid4().hex[:8]}"
    with lc_mod._msg_seq_lock:
        lc_mod._msg_seq_persisted.pop(cid, None)

    lc_mod._seed_persisted_seq(cid, 0)
    monkeypatch.setattr(
        lc_mod, "_bootstrap_seq_for",
        lambda _cid: (_ for _ in ()).throw(
            AssertionError("zero seed must avoid bootstrap")),
    )

    assert lc_mod._has_persisted_seq(cid) is True
    assert lc_mod._peek_persisted_seq(cid) == 0


def test_seq_bootstrap_does_not_hold_global_seq_lock(monkeypatch):
    import core.llm_client as lc_mod

    slow_cid = f"cid_slow_boot_{uuid.uuid4().hex[:8]}"
    fast_cid = f"cid_fast_boot_{uuid.uuid4().hex[:8]}"
    entered_bootstrap = threading.Event()
    release_bootstrap = threading.Event()

    with lc_mod._msg_seq_lock:
        lc_mod._msg_seq_persisted.pop(slow_cid, None)
        lc_mod._msg_seq_persisted[fast_cid] = 10

    def _bootstrap(cid):
        if cid == slow_cid:
            entered_bootstrap.set()
            release_bootstrap.wait(timeout=5.0)
            return 40
        return 0

    monkeypatch.setattr(lc_mod, "_bootstrap_seq_for", _bootstrap)

    result = {}
    t = threading.Thread(
        target=lambda: result.setdefault("slow", lc_mod._peek_persisted_seq(slow_cid)))
    t.start()
    assert entered_bootstrap.wait(timeout=1.0)

    assert lc_mod._next_persisted_seq(fast_cid) == 11

    release_bootstrap.set()
    t.join(timeout=5.0)
    assert result["slow"] == 40


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

    def test_boot_cache_uses_metadata_without_transcript_scan(self, store, monkeypatch):
        cid = store.generate_id()
        user_id = "alice"
        store.save(cid, [_msg(source={"type": "user", "target_agent": "bot"})],
                   user_id=user_id)
        ConversationStore.reset()
        warmed = ConversationStore(store_dir=str(store._store_dir))

        def fail_iter_rows(_self):
            raise AssertionError("startup cache must not scan transcript rows")

        monkeypatch.setattr("core.segmented_jsonl.SegmentedJsonl.iter_rows", fail_iter_rows)
        warmed._ensure_loaded()

        meta = warmed.get_metadata(cid)
        assert meta["user_id"] == user_id
        assert meta["message_count"] == 1

    def test_first_append_after_restart_seeds_seq_without_full_bootstrap_scan(
            self, store, monkeypatch):
        cid = store.generate_id()
        user_id = "alice"
        store.save(cid, [_msg(source={"type": "user", "target_agent": "bot"})],
                   user_id=user_id)

        import core.llm_client as lc_mod
        with lc_mod._msg_seq_lock:
            lc_mod._msg_seq_persisted.pop(cid, None)

        def fail_bootstrap(_cid):
            raise AssertionError("append must seed seq from store metadata/tail")

        monkeypatch.setattr(lc_mod, "_bootstrap_seq_for", fail_bootstrap)
        ConversationStore.reset()
        warmed = ConversationStore(store_dir=str(store._store_dir))

        warmed.append_message(
            cid,
            _msg(content="after restart", source={"type": "user", "target_agent": "bot"}),
            agent_name="bot",
            user_id=user_id,
        )

        rows = warmed.load(cid)
        assert rows[-1]["content"] == "after restart"
        assert rows[-1]["seq"] > rows[-2]["seq"]

    def test_append_persists_hot_metadata_for_fast_restart(self, conv):
        store, cid, uid = conv
        store.append_message(
            cid,
            _msg(content="first", source={"type": "user", "target_agent": "bot"}),
            agent_name="bot",
            user_id=uid,
        )

        extras = store.get_extras(cid)
        assert extras["_meta_msg_count"] == 1
        assert extras["_meta_preview"] == "first"
        assert extras["_meta_max_seq"] >= 1

    def test_append_messages_batches_transcript_and_context(self, conv):
        store, cid, uid = conv
        messages = [
            _msg(role="assistant", content="one",
                 source={"type": "agent", "name": "bot"}),
            _msg(role="assistant", content="two",
                 source={"type": "agent", "name": "bot"}),
        ]

        store.append_messages(cid, [
            {"msg": messages[0], "agent_name": "bot", "user_id": uid},
            {"msg": messages[1], "agent_name": "bot", "user_id": uid},
        ])

        transcript = store.load(cid)
        assert [m["content"] for m in transcript] == ["one", "two"]
        ctx = store.load_agent_context(cid, "bot")
        assert [m["content"] for m in ctx] == ["one", "two"]
        shared = store.load_context(cid)
        assert [m["content"] for m in shared] == [
            "[Agent bot]:\none", "[Agent bot]:\ntwo"]

    def test_append_coalesces_hot_metadata_writes(self, conv, monkeypatch):
        store, cid, uid = conv
        import core.conversation_store as cs_mod

        monkeypatch.setattr(cs_mod, "_HOT_METADATA_FLUSH_INTERVAL_SEC", 3600.0)
        monkeypatch.setattr(cs_mod, "_HOT_METADATA_FLUSH_MSG_DELTA", 1000)
        original_write = store._write_extras
        writes = {"count": 0}
        first_write = threading.Event()

        def count_write(*args, **kwargs):
            writes["count"] += 1
            first_write.set()
            return original_write(*args, **kwargs)

        monkeypatch.setattr(store, "_write_extras", count_write)

        for i in range(3):
            store.append_message(
                cid,
                _msg(content=f"m{i}", source={"type": "user", "target_agent": "bot"}),
                agent_name="bot",
                user_id=uid,
            )

        assert first_write.wait(timeout=2)
        assert writes["count"] == 1
        assert store.get_metadata(cid)["message_count"] == 3

        assert store.set_extra(cid, "title", "Updated") is True
        extras = store.get_extras(cid)
        assert writes["count"] == 2
        assert extras["_meta_msg_count"] == 3
        assert extras["title"] == "Updated"

    def test_append_schedules_hot_metadata_write_off_hot_path(self, conv, monkeypatch):
        store, cid, uid = conv
        import core.conversation_store as cs_mod

        scheduled = []

        class FakeExecutor:
            def submit(self, target, *args):
                scheduled.append({"target": target, "args": args})

        def fail_write_extras(*_args, **_kwargs):
            raise AssertionError("append hot path must not write extras.json")

        monkeypatch.setattr(cs_mod, "_HOT_METADATA_EXECUTOR", FakeExecutor())
        monkeypatch.setattr(store, "_write_extras", fail_write_extras)

        store.append_message(
            cid,
            _msg(content="first", source={"type": "user", "target_agent": "bot"}),
            agent_name="bot",
            user_id=uid,
        )

        assert len(scheduled) == 1
        assert scheduled[0]["target"] == store._persist_hot_metadata_worker

    def test_exists_uses_loaded_conversation_cache_without_disk_stat(self, conv, monkeypatch):
        store, cid, _uid = conv

        def fail_is_dir(_self):
            raise AssertionError("hot exists() must not stat known conversations")

        monkeypatch.setattr("pathlib.Path.is_dir", fail_is_dir)

        assert store.exists(cid) is True

    def test_git_history_retention_rewrites_to_sliding_window(self, store, monkeypatch):
        try:
            subprocess.run(["git", "--version"], check=True,
                           capture_output=True, text=True, timeout=5)
        except Exception:
            pytest.skip("git unavailable")
        import core.conversation_store as cs_mod

        monkeypatch.setattr(cs_mod, "_GIT_RETENTION_DAYS", 0)
        monkeypatch.setattr(cs_mod, "_GIT_RETENTION_COMMITS", 2)
        monkeypatch.setattr(cs_mod, "_GIT_RETENTION_INTERVAL_SEC", 0)

        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        for i in range(4):
            assert store.set_extra(cid, "title", f"title {i}")
            store.git_snapshot(cid, f"snapshot {i}")

        store.prune_git_history_now(cid)

        commits = store._git(cid, "rev-list", "--count", "live").stdout.strip()
        assert int(commits) <= 2

    def test_git_snapshot_does_not_run_retention_on_hot_path(self, conv, monkeypatch):
        store, cid, _uid = conv
        called = {"schedule": False, "untrack": False}

        monkeypatch.setattr(
            store, "_maybe_schedule_git_retention",
            lambda *_args, **_kwargs: called.__setitem__("schedule", True))
        monkeypatch.setattr(
            store, "_git_untrack_derived_state",
            lambda *_args, **_kwargs: called.__setitem__("untrack", True))

        store.set_extra(cid, "title", "hot snapshot")
        store.git_snapshot(cid, "hot snapshot")

        assert called == {"schedule": True, "untrack": False}

    def test_git_retention_is_scheduled_not_run_inline(self, conv, monkeypatch):
        import core.conversation_store as cs_mod

        store, cid, _uid = conv
        submitted = []

        class _Executor:
            def submit(self, fn, *args):
                submitted.append((fn, args))

        monkeypatch.setattr(cs_mod, "_GIT_RETENTION_INTERVAL_SEC", 1)
        monkeypatch.setattr(cs_mod, "_GIT_RETENTION_EXECUTOR", _Executor())
        monkeypatch.setattr(store, "_maybe_prune_git_history", lambda *_a, **_k: None)

        store.set_extra(cid, "title", "schedule retention")
        store.git_snapshot(cid, "schedule retention")

        assert submitted == [(store._git_retention_worker, (cid,))]
        with cs_mod._GIT_RETENTION_RUNNING_LOCK:
            cs_mod._GIT_RETENTION_RUNNING.discard(cid)

    def test_generate_id_is_string(self, store):
        cid = store.generate_id()
        assert isinstance(cid, str)
        assert len(cid) == 16

    def test_load_transcript_seq_range_reads_only_requested_window(self, conv):
        store, cid, uid = conv
        for i in range(8):
            store.append_message(
                cid,
                _msg(content=f"m{i}", source={"type": "user", "target_agent": "bot"}),
                agent_name="bot",
                user_id=uid,
            )

        all_msgs = store.load(cid)
        first_seq = all_msgs[2]["seq"]
        last_seq = all_msgs[5]["seq"]

        window = store.load_transcript_seq_range(cid, first_seq, last_seq)

        expected = all_msgs[2:6]
        assert [m["content"] for m in window] == [m["content"] for m in expected]
        assert [m["seq"] for m in window] == [m["seq"] for m in expected]

    def test_load_transcript_tail_for_agent_reads_recent_window(self, conv):
        store, cid, uid = conv
        for i in range(12):
            store.append_message(
                cid,
                _msg(content=f"m{i}", source={"type": "user", "target_agent": "bot"}),
                agent_name="bot",
                user_id=uid,
            )

        tail = store.load_transcript_tail_for_agent(cid, "bot", limit=4)

        assert [m["content"] for m in tail] == ["m8", "m9", "m10", "m11"]
        assert len(store.load(cid)) > len(tail)

    def test_patch_message_keeps_post_write_hooks_outside_append_lock(
            self, conv, monkeypatch):
        store, cid, uid = conv
        store.append_message(
            cid,
            _msg(content="original", source={"type": "user", "target_agent": "bot"}),
            agent_name="bot",
            user_id=uid,
        )
        msg_id = store.load(cid)[-1]["msg_id"]

        raw_lock = store._get_conv_lock(cid)
        state = {"locked": False, "notify_locked": None, "usage_locked": None}

        class _ProbeLock:
            def __enter__(self):
                state["locked"] = True
                return raw_lock.__enter__()

            def __exit__(self, exc_type, exc, tb):
                try:
                    return raw_lock.__exit__(exc_type, exc, tb)
                finally:
                    state["locked"] = False

        monkeypatch.setattr(store, "_get_conv_lock", lambda _cid: _ProbeLock())
        monkeypatch.setattr(
            store, "_notify_bg_transcript_chars",
            lambda *_args: state.__setitem__("notify_locked", state["locked"]))
        monkeypatch.setattr(
            store, "_maybe_persist_context_usage_from_patch",
            lambda *_args: state.__setitem__("usage_locked", state["locked"]))

        store.patch_message(cid, msg_id, content="patched")

        assert state["notify_locked"] is False
        assert state["usage_locked"] is False

    def test_set_extra_does_not_take_conversation_lock(self, conv, monkeypatch):
        store, cid, _uid = conv

        def _fail_conv_lock(_cid):
            raise AssertionError("set_extra must use the extras lock")

        monkeypatch.setattr(store, "_get_conv_lock", _fail_conv_lock)

        assert store.set_extra(cid, "title", "Updated") is True
        assert store.get_extra(cid, "title") == "Updated"

    def test_get_extra_serializes_with_extras_writer_lock(self, conv):
        store, cid, _uid = conv
        assert store.set_extra(cid, "title", "Initial") is True
        lock = store._get_extras_lock(cid)
        started = threading.Event()
        finished = threading.Event()
        seen = {}

        def reader():
            started.set()
            seen["value"] = store.get_extra(cid, "title")
            finished.set()

        lock.acquire()
        try:
            t = threading.Thread(target=reader)
            t.start()
            assert started.wait(1)
            assert not finished.wait(0.05)
        finally:
            lock.release()

        assert finished.wait(1)
        t.join(1)
        assert seen["value"] == "Initial"

    def test_agent_session_invalidation_does_not_wait_for_cache_lock(
            self, conv, monkeypatch):
        store, cid, _uid = conv
        agent = "assistant"
        session_keys = (
            f"claude_session:{agent}",
            f"codex_session:{agent}",
            f"codex_app_server_thread:{agent}",
            f"codex_app_pool_idx:{agent}",
            f"gemini_acp_session:{agent}",
            f"gemini_acp_pool_idx:{agent}",
            f"gemini_acp_session_version:{agent}",
        )
        for key in session_keys:
            assert store.set_extra(cid, key, "stale") is True

        monkeypatch.setattr(
            store, "_delete_cli_runtime_session_dirs", lambda *a, **k: 0)
        monkeypatch.setattr(
            store, "set_extra",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("session invalidation must batch extras writes")))

        store._cache_lock.acquire()
        try:
            store.invalidate_claude_session_for_agent(cid, agent)
        finally:
            store._cache_lock.release()

        extras = store.get_extras(cid)
        for key in session_keys:
            assert extras[key] == ""

    def test_load_agent_context_does_not_take_conversation_lock(self, conv, monkeypatch):
        store, cid, uid = conv
        store.append_message(
            cid,
            _msg(content="hello", source={"type": "user", "target_agent": "bot"}),
            agent_name="bot",
            user_id=uid,
        )
        store._invalidate_ctx_cache(cid)

        def _fail_conv_lock(_cid):
            raise AssertionError("load_agent_context must not block append_message")

        monkeypatch.setattr(store, "_get_conv_lock", _fail_conv_lock)

        ctx = store.load_agent_context(cid, "bot")
        assert ctx
        assert ctx[-1]["content"] == "hello"

    def test_patch_context_usage_does_not_take_conversation_lock(
            self, conv, monkeypatch):
        store, cid, _uid = conv

        def _fail_conv_lock(_cid):
            raise AssertionError("context_usage extras write must not use conv lock")

        monkeypatch.setattr(store, "_get_conv_lock", _fail_conv_lock)

        store._maybe_persist_context_usage_from_patch(cid, {
            "ts": 123.0,
            "source": {
                "name": "assistant",
                "context_used": 42,
                "context_max": 100,
                "context_pct": 0.42,
            },
        })

        usage = store.get_extra(cid, "context_usage")
        assert usage["assistant"]["used"] == 42

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

    def test_append_message_updates_hot_cache_without_reload(self, conv):
        store, cid, uid = conv
        def _unexpected_reload(_cid):
            raise AssertionError("append_message must not rescan transcript.jsonl")
        store._reload_cache = _unexpected_reload

        for i in range(3):
            store.append_message(
                cid,
                _msg(content=f"m{i}", source={
                    "type": "user", "name": uid, "target_agent": "bot"}),
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

    def test_extra_reads_do_not_take_conversation_lock(self, conv, monkeypatch):
        store, cid, uid = conv
        store.set_extra(cid, "title", "My Chat")

        def _fail_lock(_cid):
            raise AssertionError("extra reads must not take the conv lock")

        monkeypatch.setattr(store, "_get_conv_lock", _fail_lock)

        assert store.get_extra(cid, "title") == "My Chat"
        assert store.get_extra_cached(cid, "title") == "My Chat"
        assert store.get_extras(cid)["title"] == "My Chat"

    def test_write_extras_uses_unique_tmp_and_retries_permission_error(self, conv, monkeypatch):
        from pathlib import Path

        store, cid, uid = conv
        original_replace = Path.replace
        attempted = []

        def flaky_replace(self, target):
            if self.name.startswith("extras.json.") and self.name.endswith(".tmp"):
                attempted.append(self.name)
                assert self.name != "extras.tmp"
                if len(attempted) == 1:
                    raise PermissionError("transient Windows lock")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)

        store.set_extra(cid, "title", "Recovered")

        assert store.get_extra(cid, "title") == "Recovered"
        assert len(attempted) == 2

    def test_hot_metadata_write_failure_does_not_abort_append(self, conv, monkeypatch):
        from pathlib import Path

        store, cid, uid = conv
        original_replace = Path.replace

        def locked_extras_replace(self, target):
            if self.name.startswith("extras.json.") and self.name.endswith(".tmp"):
                raise PermissionError("persistent Windows lock")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", locked_extras_replace)

        store.append_message(cid, _msg(role="assistant", content="persisted"),
                             agent_name="bot", user_id=uid)

        messages = store.load(cid)
        assert messages[-1]["content"] == "persisted"


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

    def test_delete_removes_directory_under_extras_lock(self, conv, monkeypatch):
        store, cid, _uid = conv
        raw_lock = store._get_extras_lock(cid)
        state = {"held": False, "rmtree_saw_held": None}

        class _ProbeLock:
            def __enter__(self):
                raw_lock.__enter__()
                state["held"] = True
                return self

            def __exit__(self, exc_type, exc, tb):
                try:
                    return raw_lock.__exit__(exc_type, exc, tb)
                finally:
                    state["held"] = False

        monkeypatch.setattr(store, "_get_extras_lock", lambda _cid: _ProbeLock())

        import shutil as _shutil
        real_rmtree = _shutil.rmtree
        conv_dir = str(store._conv_dir(cid))

        def _rmtree_probe(path, *args, **kwargs):
            if str(path) == conv_dir:
                state["rmtree_saw_held"] = state["held"]
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(_shutil, "rmtree", _rmtree_probe)

        assert store.delete(cid) is True
        assert state["rmtree_saw_held"] is True


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

    def test_compacted_sized_context_is_cached(self, conv):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="x" * 390457)])
        assert store.load_agent_context(cid, "agent1") is not None
        assert "agent1" in store._ctx_cache.get(cid, {})

    def test_oversized_context_is_not_cached(self, conv, caplog):
        store, cid, uid = conv
        store.save_agent_context(cid, "agent1", [_msg(content="x" * 1000001)])
        assert store.load_agent_context(cid, "agent1") is not None
        assert "agent1" not in store._ctx_cache.get(cid, {})
        assert "skipped ctx cache" not in caplog.text

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

    def test_reload_prunes_context_dirs_not_declared_as_agents(self, conv):
        store, cid, uid = conv
        store.set_extra(cid, "conv_agents", {
            "assistant": {"definition": "assistant", "llm_service": "llm"},
        }, user_id=uid)
        store.save_agent_context(cid, "assistant", [_msg(content="valid")])
        store.save_agent_context(cid, "background", [_msg(content="invalid")])
        store.save_agent_context(cid, uid, [_msg(content="invalid user")])

        store._reload_cache(cid)

        contexts = store.list_agent_contexts(cid)
        assert "assistant" in contexts
        assert "background" not in contexts
        assert uid not in contexts
        assert not store._agent_ctx_path(cid, "background").parent.exists()
        assert not store._agent_ctx_path(cid, uid).parent.exists()


# ── append_message (unified router) ────────────────────────────────

class TestAppendMessage:
    """append_message is the single write path: per-message routing to
    transcript / shared / own ctx / other agents' ctx / delegate A<->B
    based on role + source + tool_calls + display_only.
    """

    def test_canonical_rows_mint_anchor_before_child_parent_links(self, conv):
        store, cid, _uid = conv
        rows = store._canonical_message_rows(cid, {
            "role": "assistant",
            "content": "answer",
            "thinking": "plan",
            "tool_calls": [{"id": "tc1", "name": "read", "arguments": {}}],
        })

        assert [row["role"] for row in rows] == ["assistant", "thinking", "tool_call"]
        anchor_id = rows[0]["msg_id"]
        assert anchor_id
        assert rows[1]["parent_message_id"] == anchor_id
        assert rows[2]["parent_message_id"] == anchor_id

    def test_system_context_rows_do_not_receive_msg_id(self, conv):
        store, cid, _uid = conv
        row = store._stamp_line(cid, {"role": "system", "content": "summary"})

        assert "msg_id" not in row

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
        # Own ctx stores canonical assistant + tool_call rows.
        bot_ctx = store.load_agent_context(cid, "bot")
        assert bot_ctx and any(msg.get("role") == "tool_call"
                               and msg.get("tool_call_id") == "tc1"
                               and msg.get("parent_message_id") == m["msg_id"]
                               for msg in bot_ctx)
        # Shared keeps text but skips tool/detail rows.
        shared = store.load_agent_context(cid, "")
        assert shared
        for s in shared:
            assert s.get("role") not in ("tool_call", "tool", "thinking")

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
        store.set_extra(cid, "conv_agents", {
            "alice": {"definition": "alice", "llm_service": "llm"},
            "bob": {"definition": "bob", "llm_service": "llm"},
        }, user_id=uid)
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
        store.set_extra(cid, "conv_agents", {
            "alice": {"definition": "alice", "llm_service": "llm"},
            "bob": {"definition": "bob", "llm_service": "llm"},
        }, user_id=uid)
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
        store.set_extra(cid, "conv_agents", {
            "alice": {"definition": "alice", "llm_service": "llm"},
            "bob": {"definition": "bob", "llm_service": "llm"},
        }, user_id=uid)
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

    def test_append_agent_cache_miss_does_not_reload_transcript(self, conv, monkeypatch):
        store, cid, uid = conv
        store.set_extra(cid, "conv_agents", {
            "alice": {"definition": "alice", "llm_service": "llm"},
            "bob": {"definition": "bob", "llm_service": "llm"},
        }, user_id=uid)
        with store._cache_lock:
            store._cache.pop(cid, None)

        def _forbidden_reload(_cid):
            raise AssertionError("append hot path must not scan transcript")

        monkeypatch.setattr(store, "_reload_cache", _forbidden_reload)
        m = _msg(role="assistant", content="hello world",
                 source={"type": "agent", "name": "alice"})
        store.append_message(cid, m, agent_name="alice", user_id=uid)
        bob_ctx = store.load_agent_context(cid, "bob")
        assert bob_ctx and any(
            "[Agent alice]" in str(msg.get("content", ""))
            for msg in bob_ctx)

    def test_append_legacy_agent_dirs_fanout_without_conv_agents(
            self, conv, monkeypatch):
        store, cid, uid = conv
        store.save_agent_context(cid, "alice", [])
        store.save_agent_context(cid, "bob", [])
        with store._cache_lock:
            store._cache.pop(cid, None)

        def _forbidden_reload(_cid):
            raise AssertionError("legacy fanout must not scan transcript")

        monkeypatch.setattr(store, "_reload_cache", _forbidden_reload)
        m = _msg(role="assistant", content="hello legacy",
                 source={"type": "agent", "name": "alice"})
        store.append_message(cid, m, agent_name="alice", user_id=uid)

        bob_ctx = store.load_agent_context(cid, "bob")
        assert bob_ctx and any(
            "[Agent alice]" in str(msg.get("content", ""))
            for msg in bob_ctx)

    def test_save_agent_context_updates_agent_cache_without_reload(self, conv, monkeypatch):
        store, cid, uid = conv
        store.set_extra(cid, "conv_agents", {
            "alice": {"definition": "alice", "llm_service": "llm"},
        }, user_id=uid)
        store._reload_cache(cid)

        def _forbidden_reload(_cid):
            raise AssertionError("save_agent_context must not scan transcript")

        monkeypatch.setattr(store, "_reload_cache", _forbidden_reload)
        assert store.save_agent_context(cid, "alice", []) is True
        with store._cache_lock:
            assert "alice" in store._cache[cid]["agents"]

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

    def _setup_all_providers(self, tmp_path, monkeypatch):
        from core import paths as _paths
        roots = {}
        for provider, attr in (
                ("claude", "CLAUDE_SESSIONS_DIR"),
                ("codex", "CODEX_SESSIONS_DIR"),
                ("gemini", "GEMINI_SESSIONS_DIR")):
            root = tmp_path / "sessions" / provider
            root.mkdir(parents=True)
            monkeypatch.setattr(_paths, attr, root)
            roots[provider] = root
        return roots

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

    def test_cli_cleanup_removes_orphans_for_all_providers(
            self, store, tmp_path, monkeypatch):
        roots = self._setup_all_providers(tmp_path, monkeypatch)
        for provider, root in roots.items():
            orphan = root / "alice" / f"dead-{provider}"
            orphan.mkdir(parents=True)
            (orphan / "state.txt").write_text("x")

        removed = store.cleanup_orphan_cli_sessions()

        assert removed == 3
        for provider, root in roots.items():
            assert not (root / "alice" / f"dead-{provider}").exists()

    def test_cli_cleanup_renames_orphan_before_recursive_delete(
            self, store, tmp_path, monkeypatch):
        roots = self._setup_all_providers(tmp_path, monkeypatch)
        orphan = roots["codex"] / "alice" / "dead-codex"
        deep = orphan / "assistant" / ".codex" / "sessions" / "2026" / "05"
        deep.mkdir(parents=True)
        (deep / "rollout.jsonl").write_text("{}\n")
        deleted = []
        monkeypatch.setattr(
            store, "_delete_cli_runtime_session_dir_worker",
            lambda target, provider, cid, agent_name="": deleted.append(target))

        removed = store.cleanup_orphan_cli_sessions()

        assert removed == 1
        assert not orphan.exists()
        assert deleted and deleted[0].name.startswith(".stale-codex-dead-codex-")

    def test_cli_cleanup_keeps_live_provider_dirs_without_deep_scan(
            self, store, tmp_path, monkeypatch):
        roots = self._setup_all_providers(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        deep_file = (roots["codex"] / "alice" / sanitized / "assistant" /
                     ".codex" / "sessions" / "rollout.jsonl")
        deep_file.parent.mkdir(parents=True)
        deep_file.write_text("{}\n")

        removed = store.cleanup_orphan_cli_sessions()

        assert removed == 0
        assert deep_file.exists()

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

    def test_boot_mode_skips_deep_prune_for_live_conv(self, store, tmp_path,
                                                       monkeypatch):
        base = self._setup(tmp_path, monkeypatch)
        cid = store.generate_id()
        store.save(cid, [], user_id="alice")
        sanitized = cid.replace(":", "_")
        sess_dir = base / "alice" / sanitized
        current_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        store.set_extra(cid, "claude_session:claude", current_sid)
        stale = self._mk_jsonl(sess_dir, "bbbbbbbb-bbbb-bbbb-bbbb-"
                                        "bbbbbbbbbbbb")

        removed = store.cleanup_orphan_claude_sessions(prune_live=False)

        assert removed == 0
        assert stale.exists(), "boot cleanup must not rglob live session trees"

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

    def test_cleanup_keeps_live_codex_and_gemini_dirs_without_jsonl_prune(
            self, store, tmp_path, monkeypatch):
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

        assert removed == 0
        assert codex_live.exists()
        assert codex_old.exists()
        assert gemini_live.exists()
        assert gemini_old.exists()

    def test_cleanup_uses_codex_app_server_provider_name(self):
        from core.conversation_store import ConversationStore

        src = inspect.getsource(ConversationStore._cli_session_roots)
        assert '"codex"' in src
        assert '"gemini"' in src

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


def test_task_lifecycle_cleanup_deletes_task_and_verify_contexts(store, monkeypatch):
    from core.task_lifecycle import cleanup_agent_task_context

    parent = store.generate_id()
    task_id = "t_cleanup"
    task_cid = f"{parent}::task::{task_id}"
    verify_cid = f"{parent}::task_verify::{task_id}"
    store.save(parent, [], user_id="alice")
    store.save(task_cid, [_msg("user", "task context")], user_id="alice")
    store.save(verify_cid, [_msg("user", "verify context")], user_id="alice")

    invalidated = []
    original_invalidate = store.invalidate_claude_sessions

    def _invalidate(cid):
        invalidated.append(cid)
        original_invalidate(cid)

    class _Scheduler:
        def __init__(self):
            self.cancelled = []

        def cancel(self, key):
            self.cancelled.append(key)
            return True

    scheduler = _Scheduler()
    relay_cancelled = []
    monkeypatch.setattr(store, "invalidate_claude_sessions", _invalidate)
    monkeypatch.setattr("core.poll_scheduler.PollScheduler.instance",
                        lambda: scheduler)
    monkeypatch.setattr(
        "services.tool_relay_service.ToolRelayService.cancel_agent",
        lambda cid, agent: relay_cancelled.append((cid, agent)))

    result = cleanup_agent_task_context(parent, task_id, "worker", store)

    assert result["deleted"] == 2
    assert not store.exists(task_cid)
    assert not store.exists(verify_cid)
    assert invalidated == [task_cid, verify_cid]
    assert scheduler.cancelled == [task_cid, verify_cid]
    assert relay_cancelled == [(task_cid, "worker"), (verify_cid, "worker")]


def test_complete_task_final_cleanup_runs_for_terminal_task(store, monkeypatch):
    from core.conversation_store import ConversationStore
    from core.handlers.task_management import CompleteTaskHandler

    ConversationStore._instance = store
    parent = store.generate_id()
    task_id = "t_done"
    store.save(parent, [], user_id="alice")
    store.set_extra(parent, "agent_tasks", {
        task_id: {
            "task_id": task_id,
            "agent": "worker",
            "status": "active",
            "completion_criteria": "done",
            "iterations_done": 0,
            "reschedule_count": 3,
        }
    })

    cleaned = []
    monkeypatch.setattr(
        "core.handlers.task_management.cleanup_agent_task_context",
        lambda *args, **kwargs: cleaned.append((args, kwargs)) or {"deleted": 1})
    monkeypatch.setattr("core.poll_scheduler.PollScheduler.instance",
                        lambda: type("S", (), {"cancel": lambda self, key: True})())

    handler = CompleteTaskHandler()
    handler.set_conversation_id(f"{parent}::task::{task_id}")
    handler.set_agent_name("worker")

    result = handler.execute({
        "task_id": task_id,
        "done": True,
        "progress": "done",
        "result": "finished",
    })

    assert result == f"Task {task_id} completed."
    assert store.get_extra(parent, "agent_tasks") == {}
    assert cleaned[0][0][:4] == (parent, task_id, "worker", store)
    assert cleaned[0][1]["reason"] == "task_completed"
