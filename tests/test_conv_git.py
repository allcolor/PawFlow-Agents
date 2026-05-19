"""Tests for ConversationStore git versioning — branch, switch, fork, rollback, tag."""

import subprocess
import time
import uuid
import json
import pytest

from core.conversation_store import ConversationStore


def _msg(role="user", content="hello", **kw):
    m = {
        "role": role, "content": content,
        "msg_id": uuid.uuid4().hex[:12], "ts": time.time(), "seq": 1,
    }
    if role == "user" and "source" not in kw:
        m["source"] = {
            "type": "user", "name": "testuser",
            "target_agent": "assistant",
        }
    m.update(kw)
    return m


@pytest.fixture(autouse=True)
def reset_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def conv(store):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser")
    return store, cid


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not available")


# ── Basic git state ──


def test_git_wrapper_disables_auto_maintenance(store, monkeypatch):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    store._git(cid, "status")

    cmd = calls[0]
    assert "safe.directory=*" in cmd
    assert "gc.auto=0" in cmd
    assert "maintenance.auto=false" in cmd



def test_git_init_creates_repo(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    assert (conv_dir / ".git").is_dir()


def test_git_current_branch_default(conv):
    store, cid = conv
    branch = store.git_current_branch(cid)
    assert branch == "live"


def test_git_log_has_init_commit(conv):
    store, cid = conv
    log = store.git_log(cid)
    assert len(log) >= 1
    assert log[-1]["message"] == "init"


def test_git_snapshot_tracks_only_durable_conversation_state(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    store.save_agent_context(cid, "assistant", [_msg(content="derived")])
    bucket_dir = conv_dir / "summaries" / "_shared"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "meta.json").write_text(json.dumps({"objects": []}), encoding="utf-8")

    store.git_snapshot(cid, "durable only")

    tracked = store._git(cid, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert "transcript.jsonl" in tracked or any(p.startswith("transcript/") for p in tracked)
    assert not any(p.startswith("assistant/") for p in tracked)
    assert not any(p.startswith("summaries/") for p in tracked)


def test_rollback_purges_agent_contexts_and_buckets(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    store.git_snapshot(cid, "base")
    base_hash = store.git_log(cid)[0]["hash"]

    store.append_message(cid, _msg(content="after base"))
    store.save_agent_context(cid, "assistant", [_msg(content="derived")])
    bucket_dir = conv_dir / "summaries" / "_shared"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "meta.json").write_text(json.dumps({"objects": []}), encoding="utf-8")
    store.git_snapshot(cid, "with derived")

    assert (conv_dir / "assistant").exists()
    assert (conv_dir / "summaries").exists()
    assert store.git_rollback(cid, base_hash)
    assert not (conv_dir / "assistant").exists()
    assert not (conv_dir / "summaries").exists()


def test_rollback_rebuild_source_comes_from_restored_shared_context(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    store.append_message(cid, _msg(content="base shared"))
    store.git_snapshot(cid, "base")
    base_hash = store.git_log(cid)[0]["hash"]

    store.append_message(cid, _msg(content="after base"))
    store.save_agent_context(cid, "assistant", [_msg(content="stale derived")])
    bucket_dir = conv_dir / "summaries" / "_shared"
    bucket_dir.mkdir(parents=True)
    (bucket_dir / "meta.json").write_text(json.dumps({"objects": []}), encoding="utf-8")
    store.git_snapshot(cid, "with derived")

    assert store.git_rollback(cid, base_hash)
    assert store.load_agent_context(cid, "assistant") is None

    rebuilt_source = store.load_shared_for_agent(cid, "assistant")
    assert rebuilt_source is not None
    assert [m["content"] for m in rebuilt_source] == ["base shared"]
    assert all(m["content"] != "after base" for m in rebuilt_source)


# ── Branch ──

def test_branch_create(conv):
    store, cid = conv
    ok = store.git_branch(cid, "experiment")
    assert ok
    assert store.git_current_branch(cid) == "experiment"


def test_branch_list(conv):
    store, cid = conv
    store.git_branch(cid, "feat-a")
    branches = store.git_list_branches(cid)
    names = [b["name"] for b in branches]
    assert "feat-a" in names
    current = [b for b in branches if b["current"]]
    assert len(current) == 1
    assert current[0]["name"] == "feat-a"


def test_branch_switch(conv):
    store, cid = conv
    store.git_branch(cid, "alt")
    # Add a message on 'alt'
    store.append_message(cid, _msg(content="on alt"))
    store.git_snapshot(cid, "alt msg")
    # Switch back to main
    default_branch = store.git_list_branches(cid)
    main = [b["name"] for b in default_branch if not b["current"]][0]
    ok = store.git_switch(cid, main)
    assert ok
    assert store.git_current_branch(cid) == main


def test_branch_switch_reloads_cache(conv):
    store, cid = conv
    store.set_extra(cid, "title", "original", user_id="testuser")
    store.git_snapshot(cid, "titled")
    default_branch = store.git_current_branch(cid)
    store.git_branch(cid, "branch2")
    store.set_extra(cid, "title", "on branch2", user_id="testuser")
    store.git_snapshot(cid, "branch2 title")
    store.git_switch(cid, default_branch)
    assert store.get_extra(cid, "title") == "original"


def test_branch_delete(conv):
    store, cid = conv
    store.git_branch(cid, "to-delete")
    default = [b["name"] for b in store.git_list_branches(cid) if not b["current"]][0]
    store.git_switch(cid, default)
    ok = store.git_delete_branch(cid, "to-delete")
    assert ok
    names = [b["name"] for b in store.git_list_branches(cid)]
    assert "to-delete" not in names


def test_cannot_delete_current_branch(conv):
    store, cid = conv
    current = store.git_current_branch(cid)
    with pytest.raises(ValueError, match="current branch"):
        store.git_delete_branch(cid, current)


# ── Rollback ──

def test_rollback(conv):
    store, cid = conv
    store.git_snapshot(cid, "snap1")
    log_before = store.git_log(cid)
    first_hash = log_before[-1]["hash"]
    store.append_message(cid, _msg(content="extra"))
    store.git_snapshot(cid, "snap2")
    ok = store.git_rollback(cid, first_hash)
    assert ok
    # Rollback creates a new commit
    log_after = store.git_log(cid)
    assert len(log_after) > len(log_before)


# ── Tag ──

def test_tag_create_and_list(conv):
    store, cid = conv
    store.git_snapshot(cid, "before tag")
    ok = store.git_tag(cid, "v1.0")
    assert ok
    tags = store.git_list_tags(cid)
    assert any(t["name"] == "v1.0" for t in tags)


def test_tag_delete(conv):
    store, cid = conv
    store.git_tag(cid, "temp")
    ok = store.git_delete_tag(cid, "temp")
    assert ok
    tags = store.git_list_tags(cid)
    assert not any(t["name"] == "temp" for t in tags)


# ── Fork ──

def test_fork_creates_new_conv(conv):
    store, cid = conv
    store.set_extra(cid, "title", "Original", user_id="testuser")
    store.git_snapshot(cid, "before fork")
    new_cid = store.fork(cid, "testuser")
    assert new_cid != cid
    assert store.exists(new_cid)
    title = store.get_extra(new_cid, "title")
    assert "fork" in title.lower()
    assert store.get_extra(new_cid, "forked_from") == cid


def test_fork_preserves_history(conv):
    store, cid = conv
    store.append_message(cid, _msg(content="msg1"))
    store.append_message(cid, _msg(content="msg2"))
    store.git_snapshot(cid, "2 messages")
    new_cid = store.fork(cid, "testuser")
    new_log = store.git_log(new_cid)
    assert len(new_log) >= 2  # at least init + snapshot + forked


# ── Idle check ──

def test_require_idle_blocks_active(store):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser", status="active")
    with pytest.raises(RuntimeError, match="active"):
        store._require_idle(cid)


def test_require_idle_allows_idle(conv):
    store, cid = conv
    store._require_idle(cid)  # should not raise


def test_branch_blocked_when_active(store):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser", status="active")
    with pytest.raises(RuntimeError):
        store.git_branch(cid, "nope")


def test_switch_blocked_when_active(store):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser", status="active")
    with pytest.raises(RuntimeError):
        store.git_switch(cid, "main")


def test_fork_blocked_when_active(store):
    cid = store.generate_id()
    store.save(cid, [_msg()], user_id="testuser", status="active")
    with pytest.raises(RuntimeError):
        store.fork(cid, "testuser")


# ── Compare ──

def test_compare_branches(conv):
    store, cid = conv
    store.git_snapshot(cid, "snap")
    store.git_branch(cid, "branch-b")
    store.append_message(cid, _msg(content="b msg"))
    store.git_snapshot(cid, "b snap")
    default = [b["name"] for b in store.git_list_branches(cid) if not b["current"]][0]
    result = store.git_compare_branches(cid, default, "branch-b")
    assert "commits_ahead" in result
    assert "commits_behind" in result
    assert "messages_a" in result
    assert "messages_b" in result
