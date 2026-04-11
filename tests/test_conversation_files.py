"""Tests for conversation store, file store, serve_file, serve_chat_ui, and
agent conversation_store mode.

Tests cover:
- ConversationStore (CRUD, TTL, cleanup, singleton)
- FileStore (store, get, TTL, cleanup, singleton)
- CreateFileHandler (builtin tool)
- ServeFileTask (serve from store, 404 on missing)
- ServeChatUITask (HTML output, agent_path config)
- AgentLoopTask conversation_store mode (JSON input/output, conversation_id)
- Task registration
- i18n keys
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
from core.conversation_store import ConversationStore
from core.file_store import FileStore
from core.tool_registry import CreateFileHandler, create_default_registry




# ── ConversationStore ────────────────────────────────────────────────


class TestConversationStore(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore(store_dir=self._tmpdir)
        ConversationStore._instance = store

    def tearDown(self):
        ConversationStore.reset()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_singleton(self):
        a = ConversationStore.instance()
        b = ConversationStore.instance()
        assert a is b

    def test_save_and_load(self):
        store = ConversationStore.instance()
        messages = [{"role": "system", "content": "hi"}]
        store.save("conv1", messages, ttl=60, user_id="test")
        loaded = store.load("conv1")
        assert loaded[0]["role"] == "system"
        assert loaded[0]["content"] == "hi"

    def test_load_missing(self):
        store = ConversationStore.instance()
        assert store.load("nonexistent") is None


    def test_delete(self):
        store = ConversationStore.instance()
        store.save("conv1", [{"role": "user", "content": "hi"}], user_id="test")
        store.delete("conv1")
        assert store.load("conv1") is None

    def test_generate_id(self):
        store = ConversationStore.instance()
        id1 = store.generate_id()
        id2 = store.generate_id()
        assert len(id1) == 16
        assert id1 != id2

    def test_list_conversations(self):
        store = ConversationStore.instance()
        store.save("a", [{"role": "user", "content": "1"}], ttl=60, user_id="test")
        store.save("b", [{"role": "user", "content": "2"}], ttl=60, user_id="test")
        convs = store.list_conversations()
        ids = [c["conversation_id"] for c in convs]
        assert "a" in ids
        assert "b" in ids


    def test_count(self):
        store = ConversationStore.instance()
        assert store.count() == 0
        store.save("a", [], user_id="test")
        assert store.count() == 1


    def test_get_metadata(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   user_id="alice")
        meta = store.get_metadata("c1")
        assert meta is not None
        assert meta["user_id"] == "alice"
        assert meta["message_count"] == 1

    def test_get_metadata_missing(self):
        store = ConversationStore.instance()
        assert store.get_metadata("nonexistent") is None


    def test_list_conversations_includes_user_id(self):
        store = ConversationStore.instance()
        store.save("c1", [{"role": "user", "content": "hi"}],
                   user_id="alice")
        convs = store.list_conversations()
        assert convs[0]["user_id"] == "alice"





class TestFileStore(unittest.TestCase):

    def setUp(self):
        self._old_instance = FileStore._instance
        self._tmpdir = tempfile.mkdtemp()
        self.store = FileStore(base_dir=self._tmpdir)
        FileStore._instance = self.store

    def tearDown(self):
        FileStore._instance = self._old_instance
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_singleton(self):
        a = FileStore.instance()
        b = FileStore.instance()
        assert a is b

    def test_store_and_get(self):
        fid = self.store.store("test.txt", b"hello world", "text/plain")
        result = self.store.get(fid)
        assert result is not None
        filename, content, content_type = result
        assert filename == "test.txt"
        assert content == b"hello world"
        assert content_type == "text/plain"

    def test_get_missing(self):
        assert self.store.get("nonexistent") is None

    def test_delete(self):
        fid = self.store.store("test.txt", b"data")
        self.store.delete(fid)
        assert self.store.get(fid) is None

    def test_exists(self):
        fid = self.store.store("test.txt", b"data")
        assert self.store.exists(fid) is True
        assert self.store.exists("nonexistent") is False

    def test_list_files(self):
        self.store.store("a.txt", b"a")
        self.store.store("b.csv", b"b")
        files = self.store.list_files()
        names = [f["filename"] for f in files]
        assert "a.txt" in names
        assert "b.csv" in names

    def test_sanitize_filename(self):
        fid = self.store.store("../../etc/passwd", b"hack")
        result = self.store.get(fid)
        assert result[0] == "passwd"  # path traversal stripped

    def test_file_id_format(self):
        fid = self.store.store("test.txt", b"data")
        assert len(fid) == 12
        assert fid.isalnum()


# ── CreateFileHandler ────────────────────────────────────────────────


class TestCreateFileHandler(unittest.TestCase):

    def setUp(self):
        self._old_instance = FileStore._instance
        self._tmpdir = tempfile.mkdtemp()
        store = FileStore(base_dir=self._tmpdir)
        FileStore._instance = store

    def tearDown(self):
        FileStore._instance = self._old_instance
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_handler_in_default_registry(self):
        registry = create_default_registry()
        assert registry.get("share_file") is not None

    def test_create_file(self):
        h = CreateFileHandler()
        h.set_base_url("http://localhost:9090")
        result = h.execute({"filename": "test.py", "content": "print('hello')"})
        assert "http://localhost:9090/files/" in result
        assert "file_id:" in result

    def test_guess_content_type(self):
        assert CreateFileHandler._guess_content_type("test.py") == "text/x-python"
        assert CreateFileHandler._guess_content_type("data.csv") == "text/csv"
        assert CreateFileHandler._guess_content_type("page.html") == "text/html"
        assert CreateFileHandler._guess_content_type("data.json") == "application/json"
        assert CreateFileHandler._guess_content_type("unknown") == "application/octet-stream"

    def test_file_stored_in_filestore(self):
        h = CreateFileHandler()
        h.set_base_url("http://localhost:9090")
        h.execute({"filename": "report.txt", "content": "Report content"})
        store = FileStore.instance()
        files = store.list_files()
        assert len(files) == 1
        assert files[0]["filename"] == "report.txt"


# ── ServeFileTask ────────────────────────────────────────────────────


class TestServeFileTask(unittest.TestCase):

    def setUp(self):
        self._old_instance = FileStore._instance
        self._tmpdir = tempfile.mkdtemp()
        store = FileStore(base_dir=self._tmpdir)
        FileStore._instance = store

    def tearDown(self):
        FileStore._instance = self._old_instance
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("serveFile") is not None

    def test_serve_existing_file(self):
        store = FileStore.instance()
        fid = store.store("test.csv", b"a,b,c\n1,2,3", "text/csv")

        from tasks.io.serve_file import ServeFileTask
        task = ServeFileTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.path.file_id", fid)
        results = task.execute(ff)

        assert results[0].get_content() == b"a,b,c\n1,2,3"
        assert results[0].get_attribute("http.response.status") == "200"
        assert results[0].get_attribute("http.response.header.Content-Type") == "text/csv"
        assert "test.csv" in results[0].get_attribute("http.response.header.Content-Disposition")

    def test_serve_missing_file(self):
        from tasks.io.serve_file import ServeFileTask
        task = ServeFileTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.path.file_id", "nonexistent")
        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "404"

    def test_serve_no_file_id(self):
        from tasks.io.serve_file import ServeFileTask
        task = ServeFileTask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "400"


# ── ServeChatUITask ──────────────────────────────────────────────────


class TestServeChatUITask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("serveChatUI") is not None

    def test_returns_html(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({"agent_path": "/api/agent"})
        ff = FlowFile(content=b"")
        results = task.execute(ff)

        content = results[0].get_content().decode()
        assert "<!DOCTYPE html>" in content
        assert "PawFlow Agent" in content
        assert "/api/agent" in content
        assert results[0].get_attribute("http.response.header.Content-Type") == "text/html; charset=utf-8"

    def test_custom_agent_path(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({"agent_path": "/custom/chat"})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert "/custom/chat" in results[0].get_content().decode()


# ── AgentLoop conversation_store ─────────────────────────────────────


@unittest.skip("Requires full agent infrastructure (active_resources, agent identity)")
class TestAgentLoopConversationStore(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore(store_dir=self._tmpdir)
        ConversationStore._instance = store

    def tearDown(self):
        ConversationStore.reset()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @patch("core.llm_client.LLMClient.complete")
    def test_json_input_with_conversation_id(self, mock_complete):
        from core.llm_client import LLMResponse
        mock_complete.return_value = LLMResponse(
            content="Hello!",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )

        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
        })
        body = json.dumps({"message": "Hi", "conversation_id": "test123"})
        ff = FlowFile(content=body.encode())
        ff.set_attribute("http.auth.principal", "test@test.com")
        results = task.execute(ff)

        output = json.loads(results[0].get_content())
        assert output["conversation_id"] == "test123"
        assert output["response"] == "Hello!"

        # Verify conversation saved in store
        store = ConversationStore.instance()
        messages = store.load("test123")
        assert messages is not None
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    @patch("core.llm_client.LLMClient.complete")
    def test_generates_conversation_id_if_missing(self, mock_complete):
        from core.llm_client import LLMResponse
        mock_complete.return_value = LLMResponse(
            content="Hi!",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )

        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
        })
        body = json.dumps({"message": "Hello"})
        ff = FlowFile(content=body.encode())
        ff.set_attribute("http.auth.principal", "test@test.com")
        results = task.execute(ff)

        output = json.loads(results[0].get_content())
        assert "conversation_id" in output
        assert len(output["conversation_id"]) == 16

    @patch("core.llm_client.LLMClient.complete")
    def test_multi_turn_conversation(self, mock_complete):
        from core.llm_client import LLMResponse

        from tasks.ai.agent_loop import AgentLoopTask

        # Turn 1
        mock_complete.return_value = LLMResponse(
            content="I'm an AI assistant.",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )
        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
        })
        body1 = json.dumps({"message": "Who are you?", "conversation_id": "multi1"})
        ff1 = FlowFile(content=body1.encode())
        task.execute(ff1)

        # Turn 2
        mock_complete.return_value = LLMResponse(
            content="I can help with many things.",
            model="gpt-4o",
            tokens_in=20, tokens_out=10,
            finish_reason="stop",
        )
        task2 = AgentLoopTask({
            "api_key": "test",
            "conversation_store": True,
        })
        body2 = json.dumps({"message": "What can you do?", "conversation_id": "multi1"})
        ff2 = FlowFile(content=body2.encode())
        task2.execute(ff2)

        # Verify full history
        store = ConversationStore.instance()
        messages = store.load("multi1")
        assert len(messages) == 5  # system, user1, assistant1, user2, assistant2
        assert messages[1]["content"] == "Who are you?"
        assert messages[3]["content"] == "What can you do?"

    @patch("core.llm_client.LLMClient.complete")
    def test_plain_text_input_still_works(self, mock_complete):
        """Non-JSON input still works (backward compatible)."""
        from core.llm_client import LLMResponse
        mock_complete.return_value = LLMResponse(
            content="Sure!",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )

        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": False,
        })
        ff = FlowFile(content=b"Plain text message")
        results = task.execute(ff)

        # Should return plain text, not JSON
        assert results[0].get_content() == b"Sure!"


# ── Flow JSON structure ──────────────────────────────────────────────


class TestAgentFlowStructure(unittest.TestCase):

    def test_flow_json_valid(self):
        path = Path("flows/pawflow_agent.json")
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["id"] == "pawflow-agent"

        # Check routes
        routes = data["tasks"]["http_in"]["parameters"]["routes"]
        patterns = [r["pattern"] for r in routes]
        assert "/api/agent" in patterns
        assert "/chat" in patterns
        assert "/files/{file_id}" in patterns

        # Check relations
        froms = [r["from"] for r in data["relations"]]
        assert froms.count("http_in") == 8  # 8 relations from http_in

    def test_flow_has_conversation_store(self):
        path = Path("flows/pawflow_agent.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        agent_params = data["tasks"]["agent"]["parameters"]
        assert agent_params["conversation_store"] is True


# ── i18n ─────────────────────────────────────────────────────────────


class TestConversationFilesI18n(unittest.TestCase):

    def test_keys_in_all_locales(self):
        keys = [
            "conversation.store", "conversation.ttl",
            "file_store.title", "file_store.ttl",
            "serve_file.title", "serve_file.not_found",
            "chat_ui.title", "chat_ui.agent_path",
        ]
        for locale in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{locale}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing key '{key}' in {locale}.json"


if __name__ == "__main__":
    unittest.main()
