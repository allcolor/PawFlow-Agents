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


import core.paths as _paths
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

    def test_patch_message_persists_context_usage_extra(self):
        store = ConversationStore.instance()
        store.save("conv1", [{
            "role": "assistant",
            "content": "hello",
            "msg_id": "m1",
            "source": {"type": "agent", "name": "assistant"},
        }], user_id="test")

        store.patch_message("conv1", "m1", source={
            "type": "agent",
            "name": "assistant",
            "provider": "codex-app-server",
            "context_used": 573886,
            "context_max": 1000000,
            "context_pct": 0.573886,
        })

        usage = store.get_extra("conv1", "context_usage")
        assert usage["assistant"]["used"] == 573886
        assert usage["assistant"]["max"] == 1000000
        assert usage["assistant"]["pct"] == 0.573886

    def test_get_context_usage_repairs_stale_extra_from_transcript(self):
        store = ConversationStore.instance()
        store.save("conv1", [{
            "role": "assistant",
            "content": "old",
            "msg_id": "m1",
            "source": {"type": "agent", "name": "assistant"},
        }], user_id="test")
        store.set_extra("conv1", "context_usage", {
            "assistant": {
                "used": 573886,
                "max": 1000000,
                "pct": 0.573886,
                "updated_at": 1,
            }
        })
        store.patch_message("conv1", "m1", source={
            "type": "agent",
            "name": "assistant",
            "provider": "codex-app-server",
            "context_used": 800184,
            "context_max": 1000000,
            "context_pct": 0.800184,
        })
        # Simulate a stale extras file left by an older runtime: get_extra should
        # repair from transcript rows on read, so restart hydration cannot fall
        # back to an older gauge value.
        store.set_extra("conv1", "context_usage", {
            "assistant": {
                "used": 573886,
                "max": 1000000,
                "pct": 0.573886,
                "updated_at": 1,
            }
        })

        usage = store.get_extra("conv1", "context_usage")
        assert usage["assistant"]["used"] == 800184
        assert usage["assistant"]["pct"] == 0.800184

        cached = store.get_extra_cached("conv1", "context_usage")
        assert cached["assistant"]["used"] == 800184

        extras = store.get_extras("conv1")
        assert extras["context_usage"]["assistant"]["used"] == 800184


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

    def test_save_index_retries_transient_permission_error(self):
        file_path = Path(self._tmpdir) / "alice" / "conv" / "0000" / "abc123abc123_test.txt"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"data")
        self.store._entries["abc123abc123"] = {
            "filename": "test.txt",
            "path": str(file_path),
            "content_type": "text/plain",
            "size": 4,
            "created_at": time.time(),
            "conversation_id": "conv",
            "user_id": "alice",
            "access": "private",
            "shared_with": [],
            "ttl": 0,
            "agent_name": "assistant",
            "category": "tool_result",
        }
        original_replace = Path.replace
        calls = {"permission_errors": 0}

        def flaky_replace(path_obj, target):
            if path_obj.name.startswith("_index.json.") and calls["permission_errors"] < 2:
                calls["permission_errors"] += 1
                raise PermissionError("transient Windows lock")
            return original_replace(path_obj, target)

        with patch("core.file_store.Path.replace", new=flaky_replace), \
                patch("core.file_store.time.sleep"):
            self.store._save_index()

        assert calls["permission_errors"] == 2
        assert (Path(self._tmpdir) / "_index.json").exists()


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
        assert "fs://filestore/" in result
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
        ff.set_attribute("http.auth.principal", "test_user")
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

    def test_initial_theme_css_uses_saved_theme_cookie(self):
        from tasks.io.serve_chat_ui import ServeChatUITask
        task = ServeChatUITask({"agent_path": "/api/agent"})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.cookie", "pawflow_theme_ref=global%3Amatrix")
        results = task.execute(ff)
        content = results[0].get_content().decode()
        assert 'id="custom-theme"' in content
        assert 'window.PAWFLOW_INITIAL_THEME_REF="global:matrix"' in content
        assert "--pf-bg:" in content


# ── Flow JSON structure ──────────────────────────────────────────────


class TestAgentFlowStructure(unittest.TestCase):

    def test_flow_json_valid(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
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
        assert froms.count("http_in") == 11  # api, ui, sse, chat, files, fs, login, callback, logout, chat/js, chat/ext

    def test_flow_has_conversation_store(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        agent_params = data["tasks"]["agent"]["parameters"]
        assert agent_params["conversation_store"] is True


# ── i18n ─────────────────────────────────────────────────────────────

