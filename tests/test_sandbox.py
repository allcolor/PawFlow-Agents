"""Tests for sandbox — secure script execution environment.

Tests cover:
- execute_sandboxed: expressions, print capture, result variable
- Safe imports allowed (json, math, re, datetime, collections, hashlib)
- Dangerous imports blocked (os, sys, subprocess, shutil)
- open() with filestore:// and fs:// schemes
- build_sandbox_globals: safe builtins present, dangerous builtins absent
- make_sandbox_open: filestore:// write-then-read roundtrip
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.sandbox import (
    execute_sandboxed,
    build_sandbox_globals,
    make_sandbox_open,
    make_safe_import,
    SAFE_MODULES,
)


# ── execute_sandboxed ───────────────────────────────────────────────

class TestExecuteSandboxed(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_simple_expression(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        output, files, ns = execute_sandboxed("2+2")
        assert output == "4"

    @patch("core.file_store.FileStore")
    def test_string_expression(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        output, files, ns = execute_sandboxed("'hello ' + 'world'")
        assert output == "hello world"

    @patch("core.file_store.FileStore")
    def test_print_captured(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "print('line1')\nprint('line2')"
        output, files, ns = execute_sandboxed(code)
        assert "line1" in output
        assert "line2" in output

    @patch("core.file_store.FileStore")
    def test_result_variable_returned(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "x = 10\nresult = x * 3"
        output, files, ns = execute_sandboxed(code)
        assert output == "30"

    @patch("core.file_store.FileStore")
    def test_local_vars_injected(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        output, files, ns = execute_sandboxed("x + 1", local_vars={"x": 41})
        assert output == "42"

    def test_execute_script_handler_preserves_destination_case(self):
        from core.handlers.web_execute import ExecuteScriptHandler

        seen = {}

        class Relay:
            def write_file(self, path, data):
                seen["write_path"] = path

            def exec(self, path, command, env=None):
                return {"stdout": "ok", "stderr": "", "returncode": 0}

            def delete_file(self, path):
                seen["delete_path"] = path

        def resolver(service_id):
            seen["service_id"] = service_id
            return Relay() if service_id == "FallKartWS" else None

        handler = ExecuteScriptHandler()
        handler.set_fs_resolver(resolver)

        result = handler.execute({"code": "print('ok')", "destination": "FallKartWS"})

        assert "ok" in result
        assert seen["service_id"] == "FallKartWS"


# ── Safe imports ────────────────────────────────────────────────────

class TestSafeImports(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_import_json(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import json\nresult = json.dumps({'a': 1})"
        output, _, _ = execute_sandboxed(code)
        assert '"a"' in output

    @patch("core.file_store.FileStore")
    def test_import_math(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import math\nresult = math.sqrt(16)"
        output, _, _ = execute_sandboxed(code)
        assert output == "4.0"

    @patch("core.file_store.FileStore")
    def test_import_re(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import re\nresult = re.match(r'\\d+', '123abc').group()"
        output, _, _ = execute_sandboxed(code)
        assert output == "123"

    @patch("core.file_store.FileStore")
    def test_import_datetime(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import datetime\nresult = type(datetime.date.today()).__name__"
        output, _, _ = execute_sandboxed(code)
        assert output == "date"

    @patch("core.file_store.FileStore")
    def test_import_collections(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import collections\nresult = type(collections.Counter()).__name__"
        output, _, _ = execute_sandboxed(code)
        assert output == "Counter"

    @patch("core.file_store.FileStore")
    def test_import_hashlib(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        code = "import hashlib\nresult = hashlib.md5(b'test').hexdigest()"
        output, _, _ = execute_sandboxed(code)
        assert len(output) == 32  # MD5 hex digest is 32 chars


# ── Dangerous imports blocked ───────────────────────────────────────

class TestDangerousImports(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_import_os_blocked(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        with self.assertRaises(ImportError):
            execute_sandboxed("import os")

    @patch("core.file_store.FileStore")
    def test_import_sys_blocked(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        with self.assertRaises(ImportError):
            execute_sandboxed("import sys")

    @patch("core.file_store.FileStore")
    def test_import_subprocess_blocked(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        with self.assertRaises(ImportError):
            execute_sandboxed("import subprocess")

    @patch("core.file_store.FileStore")
    def test_import_shutil_blocked(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        with self.assertRaises(ImportError):
            execute_sandboxed("import shutil")


# ── make_safe_import ────────────────────────────────────────────────

class TestMakeSafeImport(unittest.TestCase):

    def test_safe_module_allowed(self):
        safe_import = make_safe_import()
        mod = safe_import("json")
        assert hasattr(mod, "dumps")

    def test_dangerous_module_blocked(self):
        safe_import = make_safe_import()
        with self.assertRaises(ImportError) as cm:
            safe_import("os")
        assert "not allowed" in str(cm.exception)

    def test_safe_prefix_allowed(self):
        safe_import = make_safe_import()
        # collections.abc is under collections. prefix
        mod = safe_import("collections.abc")
        assert mod is not None

    def test_safe_modules_set_has_expected_entries(self):
        for name in ("json", "math", "re", "datetime", "hashlib", "csv", "io"):
            assert name in SAFE_MODULES, f"{name} should be in SAFE_MODULES"

    def test_dangerous_modules_not_in_safe(self):
        for name in ("os", "sys", "subprocess", "shutil", "socket", "ctypes"):
            assert name not in SAFE_MODULES, f"{name} should NOT be in SAFE_MODULES"


# ── build_sandbox_globals ───────────────────────────────────────────

class TestBuildSandboxGlobals(unittest.TestCase):

    def test_safe_builtins_present(self):
        globals_dict, _ = build_sandbox_globals()
        builtins = globals_dict["__builtins__"]
        for name in ("str", "int", "float", "list", "dict", "set", "tuple",
                      "range", "len", "bool", "enumerate", "zip", "sorted",
                      "sum", "min", "max", "abs", "round", "isinstance",
                      "map", "filter", "reversed", "type", "dir",
                      "hasattr", "getattr"):
            assert name in builtins, f"Missing safe builtin: {name}"

    def test_dangerous_builtins_absent(self):
        globals_dict, _ = build_sandbox_globals()
        builtins = globals_dict["__builtins__"]
        # eval and exec should NOT be in builtins (as the real builtins)
        for name in ("eval", "exec", "compile"):
            assert name not in builtins, f"Dangerous builtin should be absent: {name}"

    def test_import_is_restricted(self):
        globals_dict, _ = build_sandbox_globals()
        builtins = globals_dict["__builtins__"]
        safe_import = builtins["__import__"]
        # Should allow safe modules
        safe_import("json")
        # Should block dangerous modules
        with self.assertRaises(ImportError):
            safe_import("os")

    def test_print_captured(self):
        globals_dict, print_buf = build_sandbox_globals()
        builtins = globals_dict["__builtins__"]
        builtins["print"]("hello", "world")
        assert "hello world\n" in print_buf

    def test_pre_injected_modules(self):
        globals_dict, _ = build_sandbox_globals()
        builtins = globals_dict["__builtins__"]
        # io, datetime, math, json, re should be pre-injected
        assert builtins["math"] is not None
        assert builtins["json"] is not None
        assert builtins["re"] is not None
        assert builtins["io"] is not None
        assert builtins["datetime"] is not None

    def test_extra_vars_injected(self):
        globals_dict, _ = build_sandbox_globals(extra_vars={"my_var": 42})
        assert globals_dict["my_var"] == 42

    @patch("core.paths.user_params_path")
    @patch("core.file_store.FileStore")
    def test_get_variable_uses_injected_user_id(self, mock_fs_cls, mock_params_path):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        with tempfile.TemporaryDirectory() as tmp:
            params_path = Path(tmp) / "params.json"
            params_path.write_text('{"answer": {"value": "42"}}', encoding="utf-8")
            mock_params_path.return_value = params_path

            output, _, ns = execute_sandboxed(
                "result = get_variable('answer') + ':' + _user_id",
                user_id="alice",
                conversation_id="conv1",
            )

        assert output == "42:alice"
        assert ns["result"] == "42:alice"

    def test_open_without_sandbox_open_raises(self):
        globals_dict, _ = build_sandbox_globals(sandbox_open=None)
        builtins = globals_dict["__builtins__"]
        with self.assertRaises(RuntimeError):
            builtins["open"]("test.txt")

    def test_open_with_sandbox_open(self):
        called = {}
        def fake_open(name, mode="r", **kw):
            called["name"] = name
            called["mode"] = mode

        globals_dict, _ = build_sandbox_globals(sandbox_open=fake_open)
        builtins = globals_dict["__builtins__"]
        builtins["open"]("test.txt", "w")
        assert called["name"] == "test.txt"
        assert called["mode"] == "w"


# ── open() with filestore:// and fs:// ──────────────────────────────

class TestSandboxOpenFilestore(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_filestore_write_then_read(self, mock_fs_cls):
        """Write via filestore:// then read back."""
        storage = {}

        def mock_store(filename, content, ct, **kw):
            fid = f"id_{filename}"
            storage[fid] = (filename, content)
            return fid

        def mock_get(fid, user_id=""):
            if fid in storage:
                return storage[fid]
            return None

        def mock_list():
            return [{"file_id": fid, "filename": meta[0]}
                    for fid, meta in storage.items()]

        mock_instance = MagicMock()
        mock_instance.store = mock_store
        mock_instance.get = mock_get
        mock_instance.list_files = mock_list
        mock_fs_cls.instance.return_value = mock_instance

        created_files = []
        sandbox_open = make_sandbox_open(
            base_url="http://test:9090",
            created_files=created_files,
        )

        # Write
        with sandbox_open("filestore://test.txt", "w") as f:
            f.write("hello filestore")

        assert len(created_files) == 1
        assert "test.txt" in created_files[0]

        # Read back
        with sandbox_open("filestore://id_test.txt", "r") as f:
            content = f.read()
        assert content == "hello filestore"

    @patch("core.file_store.FileStore")
    def test_filestore_read_not_found(self, mock_fs_cls):
        mock_instance = MagicMock()
        mock_instance.get.return_value = None
        mock_instance.list_files.return_value = []
        mock_fs_cls.instance.return_value = mock_instance

        sandbox_open = make_sandbox_open()
        with self.assertRaises(FileNotFoundError):
            sandbox_open("filestore://missing.txt", "r")


class TestSandboxOpenFilesystemService(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_fs_scheme_delegates_to_service(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])

        mock_service = MagicMock()
        mock_service.read.return_value = b"service data"

        def resolver(svc_id):
            if svc_id == "myfs":
                return mock_service
            return None

        sandbox_open = make_sandbox_open(fs_resolver=resolver)
        f = sandbox_open("fs://myfs/docs/readme.txt", "r")
        content = f.read()
        assert content == "service data"
        mock_service.read.assert_called_once_with("docs/readme.txt")

    @patch("core.file_store.FileStore")
    def test_fs_scheme_write_delegates_to_service(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])

        mock_service = MagicMock()

        def resolver(svc_id):
            if svc_id == "myfs":
                return mock_service
            return None

        sandbox_open = make_sandbox_open(fs_resolver=resolver)
        with sandbox_open("fs://myfs/output.txt", "w") as f:
            f.write("written data")
        mock_service.write.assert_called_once()

    @patch("core.file_store.FileStore")
    def test_fs_invalid_url_raises(self, mock_fs_cls):
        mock_fs_cls.instance.return_value = MagicMock(list_files=lambda: [])
        sandbox_open = make_sandbox_open()
        with self.assertRaises(ValueError):
            sandbox_open("fs://", "r")


# ── VFS sandbox (default open) ──────────────────────────────────────

class TestSandboxOpenVFS(unittest.TestCase):

    @patch("core.file_store.FileStore")
    def test_vfs_write_then_read(self, mock_fs_cls):
        mock_instance = MagicMock()
        mock_instance.list_files.return_value = []
        mock_instance.store.return_value = "vfs_id"
        mock_fs_cls.instance.return_value = mock_instance

        vfs = {}
        sandbox_open = make_sandbox_open(vfs=vfs)

        with sandbox_open("test.txt", "w") as f:
            f.write("vfs content")

        assert "test.txt" in vfs

        with sandbox_open("test.txt", "r") as f:
            content = f.read()
        assert content == "vfs content"

    @patch("core.file_store.FileStore")
    def test_vfs_read_nonexistent_raises(self, mock_fs_cls):
        mock_instance = MagicMock()
        mock_instance.list_files.return_value = []
        mock_fs_cls.instance.return_value = mock_instance

        sandbox_open = make_sandbox_open()
        with self.assertRaises(FileNotFoundError):
            sandbox_open("nonexistent.txt", "r")


if __name__ == "__main__":
    unittest.main()
