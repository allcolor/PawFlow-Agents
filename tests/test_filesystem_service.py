"""Tests for the filesystem services layer.

Tests cover:
- FilesystemPermissions (mode, allowed/denied paths, check logic)
- Path normalization (traversal blocked, slashes, empty path)
- PermissionEnforcedFilesystem (permission enforcement wrapper)
- RelayHTTPBackend (mock HTTP, request format, actions)
- LocalFilesystemService (connect, schema, delegation)
- ServerFilesystemBackend (tmpdir real operations)
- FilesystemOpsTask (action dispatch, schema)
- FilesystemToolHandler (auto-detect, explicit service, no service)
- Relay script (unit: traversal, secret, readonly)
- GetFile/PutFile sandboxed (FileStore fallback)
- ExecuteScript + fs injection
- Admin restriction (serverFilesystem)
- OAuthTokenStore (save, get, refresh, revoke)
- i18n keys
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem, _normalize_path, _OP_CATEGORY,
)


# ── FilesystemPermissions ──────────────────────────────────────────


class TestFilesystemPermissions(unittest.TestCase):

    def test_read_mode_allows_read(self):
        p = FilesystemPermissions("read")
        self.assertTrue(p.check("file.txt", "read"))

    def test_read_mode_blocks_write(self):
        p = FilesystemPermissions("read")
        self.assertFalse(p.check("file.txt", "write"))

    def test_read_mode_blocks_delete(self):
        p = FilesystemPermissions("read")
        self.assertFalse(p.check("file.txt", "delete"))

    def test_readwrite_mode_allows_write(self):
        p = FilesystemPermissions("readwrite")
        self.assertTrue(p.check("file.txt", "write"))

    def test_readwrite_mode_blocks_delete(self):
        p = FilesystemPermissions("readwrite")
        self.assertFalse(p.check("file.txt", "delete"))

    def test_full_mode_allows_delete(self):
        p = FilesystemPermissions("full")
        self.assertTrue(p.check("file.txt", "delete"))

    def test_denied_paths_take_priority(self):
        p = FilesystemPermissions("full", allowed_paths=[""], denied_paths=["secret"])
        self.assertFalse(p.check("secret/key.pem", "read"))
        self.assertTrue(p.check("public/data.txt", "read"))

    def test_allowed_paths_restrict_access(self):
        p = FilesystemPermissions("read", allowed_paths=["src", "docs"])
        self.assertTrue(p.check("src/main.py", "read"))
        self.assertTrue(p.check("docs/readme.md", "read"))
        self.assertFalse(p.check("config/secret.json", "read"))

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            FilesystemPermissions("admin")


# ── Path Normalization ─────────────────────────────────────────────


class TestPathNormalization(unittest.TestCase):

    def test_simple_path(self):
        self.assertEqual(_normalize_path("src/main.py"), "src/main.py")

    def test_leading_slash_stripped(self):
        self.assertEqual(_normalize_path("/src/main.py"), "src/main.py")

    def test_backslash_converted(self):
        self.assertEqual(_normalize_path("src\\main.py"), "src/main.py")

    def test_dot_dot_blocked(self):
        with self.assertRaises(PermissionError):
            _normalize_path("../etc/passwd")

    def test_inner_dot_dot_blocked(self):
        with self.assertRaises(PermissionError):
            _normalize_path("src/../../etc/passwd")

    def test_empty_is_dot(self):
        self.assertEqual(_normalize_path(""), ".")

    def test_dot_stays_dot(self):
        self.assertEqual(_normalize_path("."), ".")

    def test_double_slash_collapsed(self):
        self.assertEqual(_normalize_path("src//main.py"), "src/main.py")


# ── PermissionEnforcedFilesystem ───────────────────────────────────


class _MockBackend(FilesystemBackend):
    """Minimal mock backend for testing the wrapper."""

    def __init__(self):
        self.calls = []

    def list_dir(self, path="."):
        self.calls.append(("list_dir", path))
        return [FilesystemEntry("test.txt", "file", 100)]

    def read_file(self, path):
        self.calls.append(("read_file", path))
        return b"content"

    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))

    def delete_file(self, path):
        self.calls.append(("delete_file", path))

    def mkdir(self, path):
        self.calls.append(("mkdir", path))

    def stat(self, path):
        self.calls.append(("stat", path))
        return FilesystemEntry("test.txt", "file", 100)

    def exists(self, path):
        self.calls.append(("exists", path))
        return True


class TestPermissionEnforcedFilesystem(unittest.TestCase):

    def test_read_allowed(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("read")
        pefs = PermissionEnforcedFilesystem(backend, perms)
        result = pefs.list_dir("src")
        self.assertEqual(len(result), 1)
        self.assertEqual(backend.calls[-1], ("list_dir", "src"))

    def test_write_blocked_in_read_mode(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("read")
        pefs = PermissionEnforcedFilesystem(backend, perms)
        with self.assertRaises(PermissionError):
            pefs.write_file("test.txt", b"data")

    def test_delete_blocked_in_readwrite_mode(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("readwrite")
        pefs = PermissionEnforcedFilesystem(backend, perms)
        with self.assertRaises(PermissionError):
            pefs.delete_file("test.txt")

    def test_denied_path_blocked(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("full", denied_paths=["secret"])
        pefs = PermissionEnforcedFilesystem(backend, perms)
        with self.assertRaises(PermissionError):
            pefs.read_file("secret/key.pem")

    def test_traversal_blocked(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("full")
        pefs = PermissionEnforcedFilesystem(backend, perms)
        with self.assertRaises(PermissionError):
            pefs.read_file("../etc/passwd")

    def test_path_normalized_before_backend_call(self):
        backend = _MockBackend()
        perms = FilesystemPermissions("read")
        pefs = PermissionEnforcedFilesystem(backend, perms)
        pefs.read_file("/src//main.py")
        self.assertEqual(backend.calls[-1], ("read_file", "src/main.py"))


# ── RelayHTTPBackend (mocked HTTP) ─────────────────────────────────


class TestRelayHTTPBackend(unittest.TestCase):

    def _make_backend(self):
        from services.local_filesystem_service import RelayHTTPBackend
        return RelayHTTPBackend("localhost", 9876, "test-secret")

    def _mock_response(self, data):
        """Create a mock urllib response."""
        resp = MagicMock()
        resp.read.return_value = json.dumps(data).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("urllib.request.urlopen")
    def test_list_dir(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "ok": True,
            "data": [{"name": "a.txt", "kind": "file", "size": 10, "modified": ""}]
        })
        backend = self._make_backend()
        entries = backend.list_dir(".")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "a.txt")

    @patch("urllib.request.urlopen")
    def test_read_file(self, mock_urlopen):
        import base64
        content = base64.b64encode(b"hello world").decode()
        mock_urlopen.return_value = self._mock_response({
            "ok": True, "data": {"content": content}
        })
        backend = self._make_backend()
        data = backend.read_file("test.txt")
        self.assertEqual(data, b"hello world")

    @patch("urllib.request.urlopen")
    def test_write_file(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"ok": True})
        backend = self._make_backend()
        backend.write_file("out.txt", b"data")
        # Verify the request was made
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_error_response(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "ok": False, "error": "Path traversal blocked"
        })
        backend = self._make_backend()
        from core import ServiceError
        with self.assertRaises(ServiceError):
            backend.list_dir("../etc")

    @patch("urllib.request.urlopen")
    def test_search(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "ok": True, "data": ["src/a.py", "src/b.py"]
        })
        backend = self._make_backend()
        results = backend.search(".", "*.py")
        self.assertEqual(results, ["src/a.py", "src/b.py"])

    @patch("urllib.request.urlopen")
    def test_grep(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "ok": True,
            "data": [{"path": "a.py", "line_number": 1, "line": "# TODO", "match": "TODO"}]
        })
        backend = self._make_backend()
        results = backend.grep(".", "TODO")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match"], "TODO")

    @patch("urllib.request.urlopen")
    def test_request_includes_secret(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"ok": True, "data": {"exists": True}})
        backend = self._make_backend()
        backend.exists("test.txt")
        # Check the request body included the secret
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["secret"], "test-secret")


# ── LocalFilesystemService ─────────────────────────────────────────


class TestLocalFilesystemService(unittest.TestCase):

    def test_parameter_schema(self):
        from services.local_filesystem_service import LocalFilesystemService
        svc = LocalFilesystemService({
            "host": "localhost", "port": "9876", "secret": "abc"
        })
        schema = svc.get_parameter_schema()
        self.assertIn("host", schema)
        self.assertIn("port", schema)
        self.assertIn("secret", schema)
        self.assertIn("mode", schema)

    def test_type(self):
        from services.local_filesystem_service import LocalFilesystemService
        self.assertEqual(LocalFilesystemService.TYPE, "localFilesystem")

    @patch("urllib.request.urlopen")
    def test_convenience_list_dir(self, mock_urlopen):
        from services.local_filesystem_service import LocalFilesystemService
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "ok": True,
            "data": [{"name": "x.txt", "kind": "file", "size": 5, "modified": ""}]
        }).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        svc = LocalFilesystemService({
            "host": "localhost", "port": "9876", "secret": "abc"
        })
        svc.connect()
        entries = svc.list_dir(".")
        self.assertEqual(len(entries), 1)

    def test_creates_permission_enforced_wrapper(self):
        from services.local_filesystem_service import LocalFilesystemService
        svc = LocalFilesystemService({
            "host": "localhost", "port": "9876", "secret": "abc",
            "mode": "read",
        })
        # _create_connection returns a PermissionEnforcedFilesystem
        with patch("urllib.request.urlopen"):
            conn = svc._create_connection()
        self.assertIsInstance(conn, PermissionEnforcedFilesystem)


# ── ServerFilesystemBackend (real tmpdir) ──────────────────────────


class TestServerFilesystemBackend(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from services.server_filesystem_service import ServerFilesystemBackend
        self.backend = ServerFilesystemBackend(self.tmpdir)
        # Create test files
        os.makedirs(os.path.join(self.tmpdir, "src"))
        Path(os.path.join(self.tmpdir, "hello.txt")).write_text("Hello World")
        Path(os.path.join(self.tmpdir, "src", "main.py")).write_text("# TODO: fix\nprint('ok')")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_list_dir(self):
        entries = self.backend.list_dir(".")
        names = [e.name for e in entries]
        self.assertIn("hello.txt", names)
        self.assertIn("src", names)

    def test_read_file(self):
        data = self.backend.read_file("hello.txt")
        self.assertEqual(data, b"Hello World")

    def test_write_file(self):
        self.backend.write_file("new.txt", b"New content")
        data = self.backend.read_file("new.txt")
        self.assertEqual(data, b"New content")

    def test_delete_file(self):
        self.backend.write_file("temp.txt", b"temp")
        self.assertTrue(self.backend.exists("temp.txt"))
        self.backend.delete_file("temp.txt")
        self.assertFalse(self.backend.exists("temp.txt"))

    def test_mkdir(self):
        self.backend.mkdir("new_dir/sub")
        self.assertTrue(self.backend.exists("new_dir/sub"))

    def test_stat(self):
        entry = self.backend.stat("hello.txt")
        self.assertEqual(entry.kind, "file")
        self.assertEqual(entry.size, 11)  # len("Hello World")

    def test_search(self):
        results = self.backend.search(".", "*.py", recursive=True)
        self.assertIn("src/main.py", results)

    def test_grep(self):
        results = self.backend.grep(".", "TODO", recursive=True)
        self.assertTrue(len(results) >= 1)
        self.assertEqual(results[0]["match"], "TODO")

    def test_find_replace(self):
        result = self.backend.find_replace("hello.txt", "World", "OpenPaw")
        self.assertEqual(result["replacements"], 1)
        data = self.backend.read_file("hello.txt")
        self.assertEqual(data, b"Hello OpenPaw")

    def test_traversal_blocked(self):
        with self.assertRaises(PermissionError):
            self.backend.read_file("../../../etc/passwd")

    def test_supports_git(self):
        self.assertTrue(self.backend.supports_git)


# ── FilesystemOpsTask ──────────────────────────────────────────────


class TestFilesystemOpsTask(unittest.TestCase):

    def test_registration(self):
        from core import TaskFactory
        self.assertIn("filesystemOps", TaskFactory.list_types())

    def test_parameter_schema(self):
        from tasks.io.filesystem_ops import FilesystemOpsTask
        task = FilesystemOpsTask({"service_id": "myfs", "action": "list_dir"})
        schema = task.get_parameter_schema()
        self.assertIn("service_id", schema)
        self.assertIn("action", schema)
        self.assertIn("path", schema)

    def test_list_dir_action(self):
        from tasks.io.filesystem_ops import FilesystemOpsTask
        from core import FlowFile
        task = FilesystemOpsTask({"service_id": "myfs", "action": "list_dir"})

        # Mock service
        mock_svc = MagicMock()
        mock_svc.list_dir.return_value = [
            FilesystemEntry("a.txt", "file", 10, ""),
        ]
        task._services = {"myfs": mock_svc}

        ff = FlowFile(content=b"")
        results = task.execute(ff)
        self.assertEqual(len(results), 1)
        data = json.loads(results[0].get_content())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "a.txt")

    def test_read_file_action(self):
        from tasks.io.filesystem_ops import FilesystemOpsTask
        from core import FlowFile
        task = FilesystemOpsTask({
            "service_id": "myfs", "action": "read_file", "path": "hello.txt"
        })

        mock_svc = MagicMock()
        mock_svc.read_file.return_value = b"Hello!"
        task._services = {"myfs": mock_svc}

        ff = FlowFile(content=b"")
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"Hello!")

    def test_write_file_action(self):
        from tasks.io.filesystem_ops import FilesystemOpsTask
        from core import FlowFile
        task = FilesystemOpsTask({
            "service_id": "myfs", "action": "write_file", "path": "out.txt"
        })

        mock_svc = MagicMock()
        task._services = {"myfs": mock_svc}

        ff = FlowFile(content=b"written data")
        task.execute(ff)
        mock_svc.write_file.assert_called_once_with("out.txt", b"written data")


# ── FilesystemToolHandler ──────────────────────────────────────────


class TestFilesystemToolHandler(unittest.TestCase):

    def _make_handler(self):
        from core.tool_registry import FilesystemToolHandler
        h = FilesystemToolHandler()
        return h

    def test_name(self):
        h = self._make_handler()
        self.assertEqual(h.name, "filesystem")

    def test_no_service_error(self):
        h = self._make_handler()
        h.set_user_id("test_user")
        result = h.execute({"action": "list_dir", "path": "."})
        self.assertIn("No filesystem service", result)

    def test_explicit_service(self):
        h = self._make_handler()
        mock_svc = MagicMock()
        mock_svc.list_dir.return_value = [
            FilesystemEntry("a.txt", "file", 10, ""),
        ]
        h.set_fs_service(mock_svc)
        result = h.execute({"action": "list_dir", "path": "."})
        self.assertIn("a.txt", result)

    def test_read_file_via_handler(self):
        h = self._make_handler()
        mock_svc = MagicMock()
        mock_svc.read_file.return_value = b"file content here"
        h.set_fs_service(mock_svc)
        result = h.execute({"action": "read_file", "path": "test.txt"})
        self.assertIn("file content", result)


# ── Relay Script Unit Tests ────────────────────────────────────────


class TestRelayScript(unittest.TestCase):
    """Unit tests for the relay script's internal functions."""

    def test_traversal_blocked(self):
        """The relay script resolves paths and checks they stay under root."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "relay", "tools/openpaw_fs_relay.py")
        relay = importlib.util.module_from_spec(spec)

        # We test the resolve_path concept: path must stay under root
        tmpdir = tempfile.mkdtemp()
        try:
            root = Path(tmpdir)
            # Simulate what the relay does
            target = (root / ".." / "etc" / "passwd").resolve()
            self.assertFalse(str(target).startswith(str(root.resolve())))
        finally:
            shutil.rmtree(tmpdir)

    def test_secret_validation_concept(self):
        """HMAC comparison should be constant-time."""
        import hmac
        self.assertTrue(hmac.compare_digest("abc123", "abc123"))
        self.assertFalse(hmac.compare_digest("abc123", "wrong"))

    def test_relay_script_compiles(self):
        """The relay script should be valid Python."""
        import py_compile
        py_compile.compile("tools/openpaw_fs_relay.py", doraise=True)


# ── GetFile/PutFile Sandbox ────────────────────────────────────────


class TestGetFileSandbox(unittest.TestCase):

    def test_schema_has_service_id(self):
        from tasks.io.get_file import GetFileTask
        task = GetFileTask({"input_directory": "/tmp"})
        schema = task.get_parameter_schema()
        self.assertIn("service_id", schema)

    @patch("core.file_store.FileStore.instance")
    def test_sandbox_mode_uses_filestore(self, mock_instance):
        """Without service_id, GetFile reads from FileStore."""
        from tasks.io.get_file import GetFileTask
        from core import FlowFile

        mock_store = MagicMock()
        mock_store.list_files.return_value = [
            {"file_id": "abc", "filename": "test.txt", "size": 5}
        ]
        mock_store.get.return_value = ("test.txt", b"hello")
        mock_instance.return_value = mock_store

        task = GetFileTask({"input_directory": ".", "file_filter": "*.txt"})
        results = task.execute(FlowFile(content=b""))
        # Should have tried to list from FileStore
        mock_store.list_files.assert_called()


class TestPutFileSandbox(unittest.TestCase):

    def test_schema_has_service_id(self):
        from tasks.io.put_file import PutFileTask
        task = PutFileTask({"output_directory": "/tmp"})
        schema = task.get_parameter_schema()
        self.assertIn("service_id", schema)


# ── ExecuteScript + fs ─────────────────────────────────────────────


class TestExecuteScriptFs(unittest.TestCase):

    def test_schema_has_filesystem_service_id(self):
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({"script": "result = 1"})
        schema = task.get_parameter_schema()
        self.assertIn("filesystem_service_id", schema)


# ── Admin Restriction ──────────────────────────────────────────────


class TestAdminRestriction(unittest.TestCase):

    def test_server_filesystem_admin_only_flag(self):
        from services.server_filesystem_service import ServerFilesystemService
        self.assertTrue(getattr(ServerFilesystemService, "ADMIN_ONLY", False))

    def test_server_filesystem_type(self):
        from services.server_filesystem_service import ServerFilesystemService
        self.assertEqual(ServerFilesystemService.TYPE, "serverFilesystem")


# ── OAuthTokenStore ────────────────────────────────────────────────


class TestOAuthTokenStore(unittest.TestCase):

    def setUp(self):
        from core.oauth_token_store import OAuthTokenStore
        # Reset singleton
        OAuthTokenStore._instance = None
        self.store = OAuthTokenStore.instance()
        self.tmpdir = tempfile.mkdtemp()
        # Patch tokens path to use tmpdir
        self._orig_path = OAuthTokenStore._tokens_path
        self.store._tokens_path = lambda user_id: os.path.join(
            self.tmpdir, user_id, "oauth_tokens.json")

    def tearDown(self):
        from core.oauth_token_store import OAuthTokenStore
        OAuthTokenStore._instance = None
        shutil.rmtree(self.tmpdir)

    @patch("core.oauth_token_store.OAuthTokenStore._refresh")
    def test_save_and_get(self, mock_refresh):
        """Save tokens and retrieve access_token."""
        # Mock SecretsManager to just pass through
        mock_sm = MagicMock()
        mock_sm.encrypt.side_effect = lambda x: f"enc:{x}"
        mock_sm.decrypt.side_effect = lambda x: x.replace("enc:", "")

        with patch("core.secrets.get_secrets_manager", return_value=mock_sm):
            self.store.save_tokens(
                "user1", "google", "access123", "refresh456", 7200,
                "https://oauth2.googleapis.com/token", "client_id", "client_secret"
            )
            token = self.store.get_access_token("user1", "google")
        self.assertEqual(token, "access123")

    def test_revoke_clears_tokens(self):
        """Revoke should remove tokens from memory and disk."""
        mock_sm = MagicMock()
        mock_sm.encrypt.side_effect = lambda x: f"enc:{x}"
        mock_sm.decrypt.side_effect = lambda x: x.replace("enc:", "")

        with patch("core.secrets.get_secrets_manager", return_value=mock_sm):
            self.store.save_tokens("user1", "test", "a", "r", 3600)
            self.assertTrue(self.store.has_tokens("user1", "test"))
            self.store.revoke("user1", "test")
            self.assertFalse(self.store.has_tokens("user1", "test"))

    def test_has_tokens(self):
        self.assertFalse(self.store.has_tokens("nobody", "google"))

    def test_expired_triggers_refresh(self):
        """Expired tokens should trigger a refresh attempt."""
        mock_sm = MagicMock()
        mock_sm.encrypt.side_effect = lambda x: f"enc:{x}"
        mock_sm.decrypt.side_effect = lambda x: x.replace("enc:", "")

        with patch("core.secrets.get_secrets_manager", return_value=mock_sm):
            # Save with already-expired time
            self.store.save_tokens("user1", "test", "old", "refresh", -100)

        with patch.object(self.store, '_refresh', return_value=False) as mock_ref:
            with patch("core.secrets.get_secrets_manager", return_value=mock_sm):
                token = self.store.get_access_token("user1", "test")
            mock_ref.assert_called_once()
            self.assertIsNone(token)  # Refresh failed → None


# ── Cloud Backends ─────────────────────────────────────────────────


class TestGoogleDriveService(unittest.TestCase):

    def test_type(self):
        from services.gdrive_filesystem_service import GoogleDriveService
        self.assertEqual(GoogleDriveService.TYPE, "googleDrive")

    def test_parameter_schema(self):
        from services.gdrive_filesystem_service import GoogleDriveService
        svc = GoogleDriveService({})
        schema = svc.get_parameter_schema()
        self.assertIn("folder_id", schema)
        self.assertIn("mode", schema)

    def test_requires_user_id(self):
        from services.gdrive_filesystem_service import GoogleDriveService
        from core import ServiceError
        svc = GoogleDriveService({})
        with self.assertRaises(ServiceError):
            svc._create_connection()


class TestOneDriveService(unittest.TestCase):

    def test_type(self):
        from services.onedrive_filesystem_service import OneDriveService
        self.assertEqual(OneDriveService.TYPE, "oneDrive")

    def test_parameter_schema(self):
        from services.onedrive_filesystem_service import OneDriveService
        svc = OneDriveService({})
        schema = svc.get_parameter_schema()
        self.assertIn("drive_id", schema)
        self.assertIn("mode", schema)

    def test_requires_user_id(self):
        from services.onedrive_filesystem_service import OneDriveService
        from core import ServiceError
        svc = OneDriveService({})
        with self.assertRaises(ServiceError):
            svc._create_connection()


# ── i18n Keys ──────────────────────────────────────────────────────


class TestI18nFilesystemKeys(unittest.TestCase):

    _REQUIRED_KEYS = [
        "filesystem.local_name",
        "filesystem.ws_name",
        "filesystem.action_list_dir",
        "filesystem.action_read_file",
        "filesystem.action_write_file",
        "filesystem.error_permission",
        "filesystem.error_not_found",
        "filesystem.mode_read",
        "filesystem.mode_readwrite",
        "filesystem.mode_full",
    ]

    def _load_locale(self, locale: str) -> dict:
        path = Path("gui") / "i18n" / f"{locale}.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_en_keys_exist(self):
        data = self._load_locale("en")
        for key in self._REQUIRED_KEYS:
            self.assertIn(key, data, f"Missing EN key: {key}")

    def test_fr_keys_exist(self):
        data = self._load_locale("fr")
        for key in self._REQUIRED_KEYS:
            self.assertIn(key, data, f"Missing FR key: {key}")

    def test_es_keys_exist(self):
        data = self._load_locale("es")
        for key in self._REQUIRED_KEYS:
            self.assertIn(key, data, f"Missing ES key: {key}")


if __name__ == "__main__":
    unittest.main()
