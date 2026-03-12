"""Tests for conversation history feature.

Tests cover:
- ConversationStore user_id support (save, load, list_by_user, delete)
- AgentLoopTask action-based requests (list, load_history, delete)
- Access control (user isolation — user A cannot see user B's conversations)
- Chat UI sidebar elements
- i18n keys
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
from core.conversation_store import ConversationStore


# ── ConversationStore user_id ───────────────────────────────────────


class TestConversationStoreUserId(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        # Force store to use temp dir
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_save_with_user_id(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        loaded = store.load("c1")
        assert loaded is not None
        assert loaded[0]["content"] == "hi"

    def test_load_same_user(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        loaded = store.load("c1", user_id="alice")
        assert loaded is not None

    def test_load_different_user_denied(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        loaded = store.load("c1", user_id="bob")
        assert loaded is None

    def test_load_no_user_id_allows_all(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        # Load without user_id = no restriction
        loaded = store.load("c1")
        assert loaded is not None

    def test_load_conv_without_owner_allows_all(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}], ttl=60)
        loaded = store.load("c1", user_id="anyone")
        assert loaded is not None

    def test_list_by_user(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "alice msg"}],
                   ttl=60, user_id="alice")
        store.save("c2", [{"role": "user", "content": "bob msg"}],
                   ttl=60, user_id="bob")
        store.save("c3", [{"role": "user", "content": "alice msg 2"}],
                   ttl=60, user_id="alice")

        alice_convs = store.list_conversations(user_id="alice")
        assert len(alice_convs) == 2
        assert all(c["conversation_id"] in ("c1", "c3") for c in alice_convs)

        bob_convs = store.list_conversations(user_id="bob")
        assert len(bob_convs) == 1
        assert bob_convs[0]["conversation_id"] == "c2"

    def test_list_all_no_filter(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "a"}], ttl=60, user_id="alice")
        store.save("c2", [{"role": "user", "content": "b"}], ttl=60, user_id="bob")
        all_convs = store.list_conversations()
        assert len(all_convs) == 2

    def test_list_sorted_by_updated_at(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "first"}], ttl=60)
        time.sleep(0.01)
        store.save("c2", [{"role": "user", "content": "second"}], ttl=60)
        convs = store.list_conversations()
        assert convs[0]["conversation_id"] == "c2"  # most recent first

    def test_list_has_preview(self):
        store = ConversationStore.instance()
        store.save("c1", [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "What is 2+2?"},
        ], ttl=60)
        convs = store.list_conversations()
        assert convs[0]["preview"] == "What is 2+2?"

    def test_list_preview_truncated(self):
        store = ConversationStore.instance()
        long_msg = "x" * 200
        store.save("c1", [{"role": "user", "content": long_msg}], ttl=60)
        convs = store.list_conversations()
        assert len(convs[0]["preview"]) == 80

    def test_list_has_created_at(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}], ttl=60)
        convs = store.list_conversations()
        assert "created_at" in convs[0]

    def test_save_preserves_user_id_on_update(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "v1"}],
                   ttl=60, user_id="alice")
        store.save("c1", [
            {"role": "user", "content": "v1"},
            {"role": "assistant", "content": "reply"},
        ], ttl=60)  # no user_id — should preserve "alice"
        loaded = store.load("c1", user_id="alice")
        assert loaded is not None
        assert len(loaded) == 2

    def test_delete_same_user(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        deleted = store.delete("c1", user_id="alice")
        assert deleted is True
        assert store.load("c1") is None

    def test_delete_different_user_denied(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        deleted = store.delete("c1", user_id="bob")
        assert deleted is False
        assert store.load("c1") is not None

    def test_delete_no_user_restriction(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice")
        deleted = store.delete("c1")
        assert deleted is True

    def test_delete_nonexistent(self):
        store = ConversationStore.instance()
        deleted = store.delete("nonexistent")
        assert deleted is False


# ── Disk Persistence ────────────────────────────────────────────────


class TestConversationPersistence(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_store(self):
        ConversationStore.reset()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        return store

    def test_save_creates_file(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "hello"}], ttl=60)
        files = list(Path(self._tmpdir).glob("*.json"))
        assert len(files) == 1
        assert files[0].stem == "c1"

    def test_survives_restart(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "hello"}],
                   ttl=3600, user_id="alice")
        store.save("c2", [{"role": "user", "content": "world"}],
                   ttl=3600, user_id="bob")
        # Simulate restart
        store2 = self._make_store()
        assert store2.count() == 2
        msgs = store2.load("c1", user_id="alice")
        assert msgs is not None
        assert msgs[0]["content"] == "hello"
        msgs2 = store2.load("c2", user_id="bob")
        assert msgs2 is not None
        assert msgs2[0]["content"] == "world"

    def test_list_survives_restart(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=3600, user_id="alice")
        store2 = self._make_store()
        convs = store2.list_conversations(user_id="alice")
        assert len(convs) == 1
        assert convs[0]["conversation_id"] == "c1"

    def test_delete_removes_file(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "hi"}], ttl=60)
        assert len(list(Path(self._tmpdir).glob("*.json"))) == 1
        store.delete("c1")
        assert len(list(Path(self._tmpdir).glob("*.json"))) == 0

    def test_expired_cleaned_on_load(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "old"}], ttl=1)
        time.sleep(1.1)
        store2 = self._make_store()
        assert store2.count() == 0
        assert len(list(Path(self._tmpdir).glob("*.json"))) == 0

    def test_user_isolation_after_restart(self):
        store = self._make_store()
        store.save("c1", [{"role": "user", "content": "secret"}],
                   ttl=3600, user_id="alice")
        store2 = self._make_store()
        assert store2.load("c1", user_id="bob") is None
        assert store2.load("c1", user_id="alice") is not None


# ── AgentLoopTask actions ───────────────────────────────────────────


class TestAgentLoopActions(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_task(self):
        from tasks.ai.agent_loop import AgentLoopTask
        return AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
        })

    def test_list_conversations_action(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({"action": "list_conversations"}).encode())
        ff.set_attribute("http.auth.principal", "alice@test.com")
        results = task.execute(ff)

        body = json.loads(results[0].get_content().decode())
        assert "conversations" in body
        assert len(body["conversations"]) == 1
        assert body["conversations"][0]["conversation_id"] == "c1"

    def test_list_conversations_user_isolation(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "alice"}],
                   ttl=60, user_id="alice@test.com")
        store.save("c2", [{"role": "user", "content": "bob"}],
                   ttl=60, user_id="bob@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({"action": "list_conversations"}).encode())
        ff.set_attribute("http.auth.principal", "alice@test.com")
        results = task.execute(ff)

        body = json.loads(results[0].get_content().decode())
        assert len(body["conversations"]) == 1
        assert body["conversations"][0]["conversation_id"] == "c1"

    def test_load_history_action(self):
        store = ConversationStore.instance()
        store.save("c1", [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Good!", "tool_calls": [{"id": "t1", "name": "calc", "arguments": {}}]},
        ], ttl=60, user_id="alice@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({
            "action": "load_history",
            "conversation_id": "c1",
        }).encode())
        ff.set_attribute("http.auth.principal", "alice@test.com")
        results = task.execute(ff)

        body = json.loads(results[0].get_content().decode())
        assert body["conversation_id"] == "c1"
        # All display-relevant messages with type classification (no system)
        assert all(m.get("type") in ("user", "assistant", "tool_call", "tool_result")
                   for m in body["messages"])
        # 2 user + 2 assistant + 1 tool_call = 5 (system is excluded)
        assert len(body["messages"]) == 5
        assert body["message_count"] == 5  # raw count from store
        types = [m["type"] for m in body["messages"]]
        assert types == ["user", "assistant", "user", "assistant", "tool_call"]

    def test_load_history_wrong_user(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({
            "action": "load_history",
            "conversation_id": "c1",
        }).encode())
        ff.set_attribute("http.auth.principal", "bob@test.com")
        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "404"

    def test_load_history_missing_conv_id(self):
        task = self._make_task()
        ff = FlowFile(content=json.dumps({"action": "load_history"}).encode())
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "400"

    def test_delete_conversation_action(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({
            "action": "delete_conversation",
            "conversation_id": "c1",
        }).encode())
        ff.set_attribute("http.auth.principal", "alice@test.com")
        results = task.execute(ff)

        body = json.loads(results[0].get_content().decode())
        assert body["deleted"] is True
        assert store.load("c1") is None

    def test_delete_conversation_wrong_user(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   ttl=60, user_id="alice@test.com")

        task = self._make_task()
        ff = FlowFile(content=json.dumps({
            "action": "delete_conversation",
            "conversation_id": "c1",
        }).encode())
        ff.set_attribute("http.auth.principal", "bob@test.com")
        results = task.execute(ff)

        body = json.loads(results[0].get_content().decode())
        assert body["deleted"] is False
        assert store.load("c1") is not None

    def test_normal_message_not_intercepted(self):
        task = self._make_task()
        task._execute_sync = MagicMock(return_value=[FlowFile(content=b"ok")])
        ff = FlowFile(content=json.dumps({"message": "hello"}).encode())
        task.execute(ff)
        task._execute_sync.assert_called_once()

    def test_unknown_action_not_intercepted(self):
        task = self._make_task()
        task._execute_sync = MagicMock(return_value=[FlowFile(content=b"ok")])
        ff = FlowFile(content=json.dumps({"action": "unknown", "message": "hi"}).encode())
        task.execute(ff)
        task._execute_sync.assert_called_once()

    def test_save_stores_user_id(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import LLMResponse

        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
            "conversation_ttl": 60,
        })

        mock_response = LLMResponse(
            content="Hello!", model="test-model", finish_reason="stop",
            tokens_in=10, tokens_out=5, tool_calls=[],
        )
        mock_client = MagicMock()
        mock_client.complete.return_value = mock_response

        task._prepare_agent_context = MagicMock(return_value={
            "client": mock_client,
            "registry": MagicMock(),
            "tool_defs": [],
            "messages": [{"role": "user", "content": "hi"}],
            "model": "test",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_iterations": 10,
            "use_conv_store": True,
            "conv_ttl": 60,
            "conv_attr": "",
            "conversation_id": "test-conv",
            "user_id": "alice@test.com",
        })

        # Override _prepare_agent_context to return LLMMessage list
        from core.llm_client import LLMMessage
        def patched_prepare(ff):
            ctx = task._prepare_agent_context.return_value.copy()
            ctx["messages"] = [LLMMessage(role="user", content="hi")]
            return ctx
        task._prepare_agent_context.side_effect = patched_prepare

        ff = FlowFile(content=json.dumps({"message": "hi"}).encode())
        ff.set_attribute("http.auth.principal", "alice@test.com")
        task._execute_sync(ff)

        store = ConversationStore.instance()
        # Check the conversation was saved with user_id
        entry = store._conversations.get("test-conv")
        assert entry is not None
        assert entry["user_id"] == "alice@test.com"


# ── Chat UI sidebar ────────────────────────────────────────────────


class TestChatUISidebar(unittest.TestCase):

    def test_html_has_sidebar(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({"agent_path": "/api/agent"})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        html = results[0].get_content().decode()
        assert 'class="sidebar"' in html
        assert 'id="convList"' in html
        assert 'loadConversations' in html
        assert 'resumeConv' in html
        assert 'deleteConv' in html

    def test_html_has_new_chat_in_sidebar(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        html = results[0].get_content().decode()
        assert 'btn-new' in html
        assert 'Conversations' in html

    def test_html_has_mobile_toggle(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        html = results[0].get_content().decode()
        assert 'sidebar-toggle' in html
        assert 'toggleSidebar' in html

    def test_list_conversations_action_in_js(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        html = results[0].get_content().decode()
        assert "list_conversations" in html
        assert "load_history" in html
        assert "delete_conversation" in html


# ── i18n keys ───────────────────────────────────────────────────────


class TestConversationI18n(unittest.TestCase):

    def test_conversation_keys_exist(self):
        for lang in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{lang}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            assert "conversations.title" in data, f"Missing conversations.title in {lang}"
            assert "conversations.new" in data, f"Missing conversations.new in {lang}"
            assert "conversations.delete" in data, f"Missing conversations.delete in {lang}"
            assert "conversations.resume" in data, f"Missing conversations.resume in {lang}"
            assert "conversations.history" in data, f"Missing conversations.history in {lang}"
            assert "conversations.empty" in data, f"Missing conversations.empty in {lang}"

    def test_keys_consistent_across_languages(self):
        en = json.loads(Path("gui/i18n/en.json").read_text(encoding="utf-8"))
        fr = json.loads(Path("gui/i18n/fr.json").read_text(encoding="utf-8"))
        es = json.loads(Path("gui/i18n/es.json").read_text(encoding="utf-8"))
        conv_keys = [k for k in en if k.startswith("conversations.")]
        for key in conv_keys:
            assert key in fr, f"Missing {key} in fr.json"
            assert key in es, f"Missing {key} in es.json"


if __name__ == "__main__":
    unittest.main()
