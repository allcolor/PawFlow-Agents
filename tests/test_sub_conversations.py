"""Tests for sub-conversation task contexts."""
import json
import time
import pytest
from core.conversation_store import ConversationStore


class TestSubConversations:
    """Test sub-conversation creation, persistence, and cleanup."""

    def setup_method(self):
        self.store = ConversationStore.instance()
        self.parent_id = f"test_parent_{int(time.time()*1000)}"
        self.task_id = "t_test123"
        self.sub_id = f"{self.parent_id}::task::{self.task_id}"
        # Create parent conversation
        self.store.save(self.parent_id, [
            {"role": "system", "content": "You are a helper"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ], user_id="test_user")

    def teardown_method(self):
        try:
            self.store.delete(self.parent_id)
        except Exception:
            pass
        try:
            self.store.delete(self.sub_id)
        except Exception:
            pass

    def test_sub_conv_hidden_from_listing(self):
        """Sub-conversations should not appear in list_conversations."""
        self.store.save(self.sub_id, [
            {"role": "user", "content": "task message"},
        ], user_id="test_user")
        convs = self.store.list_conversations(user_id="test_user")
        conv_ids = [c["conversation_id"] for c in convs]
        assert self.parent_id in conv_ids
        assert self.sub_id not in conv_ids

    def test_sub_conv_persistence(self):
        """Sub-conversation messages should be persistable and loadable."""
        msgs = [
            {"role": "system", "content": "Task prompt"},
            {"role": "user", "content": "Do the task"},
            {"role": "assistant", "content": "Working on it"},
        ]
        self.store.save(self.sub_id, msgs, user_id="test_user")
        loaded = self.store.load(self.sub_id, user_id="test_user")
        assert len(loaded) == 3
        assert loaded[0]["content"] == "Task prompt"

    def test_sub_conv_append_message(self):
        """append_message should work on sub-conversations."""
        self.store.save(self.sub_id, [
            {"role": "user", "content": "Start"},
        ], user_id="test_user")
        self.store.append_message(self.sub_id,
            {"role": "assistant", "content": "Done", "msg_id": "m1", "ts": 1000},
            user_id="test_user")
        loaded = self.store.load(self.sub_id, user_id="test_user")
        assert len(loaded) == 2

    def test_sub_conv_delete(self):
        """Sub-conversations should be deletable."""
        self.store.save(self.sub_id, [
            {"role": "user", "content": "temp"},
        ], user_id="test_user")
        self.store.delete(self.sub_id)
        loaded = self.store.load(self.sub_id)
        assert loaded is None

    def test_sub_conv_user_isolation(self):
        """Sub-conversations should respect user isolation."""
        self.store.save(self.sub_id, [
            {"role": "user", "content": "private"},
        ], user_id="test_user")
        loaded = self.store.load(self.sub_id, user_id="other_user")
        assert loaded is None

    def test_multiple_sub_convs_hidden(self):
        """Multiple sub-conversations for different tasks all hidden."""
        for i in range(3):
            sid = f"{self.parent_id}::task::task_{i}"
            self.store.save(sid, [
                {"role": "user", "content": f"task {i}"},
            ], user_id="test_user")
        convs = self.store.list_conversations(user_id="test_user")
        conv_ids = [c["conversation_id"] for c in convs]
        for i in range(3):
            assert f"{self.parent_id}::task::task_{i}" not in conv_ids
        # Cleanup
        for i in range(3):
            self.store.delete(f"{self.parent_id}::task::task_{i}")

    def test_sub_conv_independent_from_parent(self):
        """Sub-conversation messages are independent of parent messages."""
        self.store.save(self.sub_id, [
            {"role": "user", "content": "sub only"},
        ], user_id="test_user")
        parent_msgs = self.store.load(self.parent_id, user_id="test_user")
        sub_msgs = self.store.load(self.sub_id, user_id="test_user")
        # Parent should still have original 3 messages
        assert len(parent_msgs) == 3
        # Sub should have only 1
        assert len(sub_msgs) == 1
        assert sub_msgs[0]["content"] == "sub only"


class TestLoadPage:
    """Test paginated message loading."""

    def setup_method(self):
        self.store = ConversationStore.instance()
        self.conv_id = f"test_page_{int(time.time()*1000)}"
        msgs = [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"Message {i}"} for i in range(100)]
        self.store.save(self.conv_id, msgs, user_id="test_user")

    def teardown_method(self):
        try:
            self.store.delete(self.conv_id)
        except Exception:
            pass

    def test_load_page_default(self):
        """Default load_page returns last 50 messages."""
        page = self.store.load_page(self.conv_id, limit=50, user_id="test_user")
        assert page is not None
        assert len(page["messages"]) == 50
        assert page["total_count"] == 100
        assert page["has_more"] is True

    def test_load_page_with_offset(self):
        """load_page with offset skips from end."""
        page = self.store.load_page(self.conv_id, limit=20, offset=80, user_id="test_user")
        assert page is not None
        assert len(page["messages"]) == 20
        assert page["messages"][0]["content"] == "Message 0"

    def test_load_page_no_more(self):
        """has_more is False when all messages loaded."""
        page = self.store.load_page(self.conv_id, limit=200, user_id="test_user")
        assert page["has_more"] is False
        assert len(page["messages"]) == 100

    def test_load_page_returns_none_for_missing(self):
        """load_page returns None for nonexistent conversation."""
        page = self.store.load_page("nonexistent_conv_xyz", user_id="test_user")
        assert page is None

    def test_load_page_user_access_denied(self):
        """load_page returns None when user doesn't match owner."""
        page = self.store.load_page(self.conv_id, user_id="wrong_user")
        assert page is None

    def test_load_page_boundary_safety(self):
        """Boundary safety: tool role messages not split from assistant."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "let me check", "tool_calls": [{"id": "tc1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "here you go"},
        ]
        cid = f"test_boundary_{int(time.time()*1000)}"
        self.store.save(cid, msgs, user_id="test_user")
        page = self.store.load_page(cid, limit=2, user_id="test_user")
        # Should not start with a tool message
        assert page["messages"][0]["role"] != "tool"
        self.store.delete(cid)

    def test_load_page_offset_zero_is_most_recent(self):
        """offset=0 returns the most recent messages."""
        page = self.store.load_page(self.conv_id, limit=10, offset=0, user_id="test_user")
        assert page is not None
        # Last message should be Message 99
        assert page["messages"][-1]["content"] == "Message 99"

    def test_load_page_metadata(self):
        """load_page response includes correct metadata fields."""
        page = self.store.load_page(self.conv_id, limit=25, offset=10, user_id="test_user")
        assert page is not None
        assert "total_count" in page
        assert "offset" in page
        assert "limit" in page
        assert "has_more" in page
        assert page["offset"] == 10
        assert page["limit"] == 25
