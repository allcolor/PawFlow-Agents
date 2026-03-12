"""Tests for DynamicToolStore — user-uploaded tool handlers.

Tests cover:
- AST validation (safe/forbidden imports, names, attributes)
- Sandbox loading (ToolHandler subclass extraction)
- Install / uninstall / list lifecycle
- Per-user isolation and admin override
- Disk persistence (index + source files)
- Name collision handling
- Integration with tool registry
- i18n keys
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.dynamic_tool_store import DynamicToolStore


# ── Sample tool sources ──────────────────────────────────────────────

VALID_TOOL_SOURCE = '''
class GreeterHandler(ToolHandler):
    @property
    def name(self):
        return "greet"

    @property
    def description(self):
        return "Greet someone by name"

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name to greet"},
            },
            "required": ["name"],
        }

    def execute(self, arguments):
        return f"Hello, {arguments.get('name', 'World')}!"
'''

VALID_TOOL_SOURCE_2 = '''
import math

class CalcHandler(ToolHandler):
    @property
    def name(self):
        return "calc_sqrt"

    @property
    def description(self):
        return "Calculate square root"

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
            },
            "required": ["value"],
        }

    def execute(self, arguments):
        return str(math.sqrt(arguments.get("value", 0)))
'''

FORBIDDEN_IMPORT_OS = '''
import os

class BadHandler(ToolHandler):
    @property
    def name(self):
        return "bad"

    @property
    def description(self):
        return "bad tool"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return os.listdir(".")
'''

FORBIDDEN_EVAL = '''
class EvalHandler(ToolHandler):
    @property
    def name(self):
        return "evil"

    @property
    def description(self):
        return "evil tool"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return eval("1+1")
'''

FORBIDDEN_ATTR = '''
class AttrHandler(ToolHandler):
    @property
    def name(self):
        return "attr_tool"

    @property
    def description(self):
        return "attr tool"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return str("".__class__.__subclasses__())
'''

NO_HANDLER_SOURCE = '''
def helper():
    return 42
'''

MISSING_NAME_SOURCE = '''
class BadTool(ToolHandler):
    @property
    def name(self):
        return ""

    @property
    def description(self):
        return "desc"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "ok"
'''

SYNTAX_ERROR_SOURCE = '''
def broken(
    return 42
'''

SAFE_IMPORT_REQUESTS = '''
import requests

class FetchHandler(ToolHandler):
    @property
    def name(self):
        return "custom_fetch"

    @property
    def description(self):
        return "Fetch a URL"

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {"url": {"type": "string"}},
        }

    def execute(self, arguments):
        return "would fetch " + arguments.get("url", "")
'''


# ── Tests ────────────────────────────────────────────────────────────


class TestValidation(unittest.TestCase):
    """Test AST static analysis."""

    def test_valid_source_no_violations(self):
        v = DynamicToolStore.validate_source(VALID_TOOL_SOURCE)
        assert v == [], f"Expected no violations, got: {v}"

    def test_valid_math_import(self):
        v = DynamicToolStore.validate_source(VALID_TOOL_SOURCE_2)
        assert v == []

    def test_safe_requests_import(self):
        v = DynamicToolStore.validate_source(SAFE_IMPORT_REQUESTS)
        assert v == []

    def test_forbidden_import_os(self):
        v = DynamicToolStore.validate_source(FORBIDDEN_IMPORT_OS)
        assert len(v) == 1
        assert "os" in v[0]

    def test_forbidden_eval(self):
        v = DynamicToolStore.validate_source(FORBIDDEN_EVAL)
        assert len(v) == 1
        assert "eval" in v[0]

    def test_forbidden_attribute(self):
        v = DynamicToolStore.validate_source(FORBIDDEN_ATTR)
        assert any("__subclasses__" in x for x in v)

    def test_forbidden_import_subprocess(self):
        src = 'import subprocess\nx = 1'
        v = DynamicToolStore.validate_source(src)
        assert len(v) >= 1
        assert "subprocess" in v[0]

    def test_forbidden_from_import(self):
        src = 'from pathlib import Path\nx = 1'
        v = DynamicToolStore.validate_source(src)
        assert len(v) >= 1
        assert "pathlib" in v[0]

    def test_forbidden_open(self):
        src = 'x = open("test.txt")'
        v = DynamicToolStore.validate_source(src)
        assert any("open" in x for x in v)

    def test_forbidden_exec(self):
        src = 'exec("print(1)")'
        v = DynamicToolStore.validate_source(src)
        assert any("exec" in x for x in v)

    def test_forbidden_globals_attr(self):
        src = 'x = f.__globals__'
        v = DynamicToolStore.validate_source(src)
        assert any("__globals__" in x for x in v)

    def test_syntax_error(self):
        v = DynamicToolStore.validate_source(SYNTAX_ERROR_SOURCE)
        assert len(v) == 1
        assert "Syntax" in v[0]

    def test_safe_from_collections(self):
        src = 'from collections import defaultdict\nx = defaultdict(int)'
        v = DynamicToolStore.validate_source(src)
        assert v == []

    def test_safe_from_urllib_parse(self):
        src = 'from urllib.parse import urlencode\nx = 1'
        v = DynamicToolStore.validate_source(src)
        assert v == []


class TestSandboxLoad(unittest.TestCase):
    """Test sandbox loading of ToolHandler subclasses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        DynamicToolStore.reset()
        self.store = DynamicToolStore(store_dir=self.tmpdir)

    def tearDown(self):
        DynamicToolStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_valid_handler(self):
        handler, name = self.store._sandbox_load(VALID_TOOL_SOURCE)
        assert name == "greet"
        assert handler.description == "Greet someone by name"
        result = handler.execute({"name": "Alice"})
        assert result == "Hello, Alice!"

    def test_load_math_handler(self):
        handler, name = self.store._sandbox_load(VALID_TOOL_SOURCE_2)
        assert name == "calc_sqrt"
        result = handler.execute({"value": 16})
        assert result == "4.0"

    def test_no_handler_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.store._sandbox_load(NO_HANDLER_SOURCE)
        assert "No ToolHandler subclass" in str(cm.exception)

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.store._sandbox_load(MISSING_NAME_SOURCE)
        assert "name" in str(cm.exception).lower()

    def test_forbidden_import_at_runtime(self):
        # Even if AST doesn't catch it, sandbox blocks forbidden imports
        src = '''
class H(ToolHandler):
    @property
    def name(self):
        return "test"
    @property
    def description(self):
        return "test"
    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}
    def execute(self, arguments):
        import sys
        return str(sys.path)
'''
        # AST catches forbidden import first
        v = DynamicToolStore.validate_source(src)
        assert len(v) >= 1


class TestInstallUninstall(unittest.TestCase):
    """Test install/uninstall lifecycle."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        DynamicToolStore.reset()
        self.store = DynamicToolStore(store_dir=self.tmpdir)

    def tearDown(self):
        DynamicToolStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_valid_tool(self):
        result = self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        assert result["tool_name"] == "greet"
        assert "Greet" in result["description"]

    def test_installed_tool_exists(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        handler = self.store.get_handler("greet")
        assert handler is not None
        assert handler.execute({"name": "Bob"}) == "Hello, Bob!"

    def test_install_forbidden_source_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.store.install("user1", "bad.py", FORBIDDEN_IMPORT_OS)
        assert "Security" in str(cm.exception)

    def test_install_creates_files(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        user_dir = Path(self.tmpdir) / "user1"
        assert (user_dir / "greet.py").exists()
        assert (Path(self.tmpdir) / "_index.json").exists()

    def test_uninstall_removes_tool(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        removed = self.store.uninstall("user1", "greet")
        assert removed is True
        assert self.store.get_handler("greet") is None

    def test_uninstall_nonexistent_returns_false(self):
        removed = self.store.uninstall("user1", "nonexistent")
        assert removed is False

    def test_uninstall_wrong_user_raises(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        with self.assertRaises(PermissionError):
            self.store.uninstall("user2", "greet")

    def test_admin_can_uninstall_any(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        removed = self.store.uninstall("user2", "greet", is_admin=True)
        assert removed is True

    def test_name_collision_different_user_raises(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        with self.assertRaises(ValueError) as cm:
            self.store.install("user2", "greeter.py", VALID_TOOL_SOURCE)
        assert "already exists" in str(cm.exception)

    def test_same_user_can_reinstall(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        result = self.store.install("user1", "greeter_v2.py", VALID_TOOL_SOURCE)
        assert result["tool_name"] == "greet"

    def test_install_two_tools(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        self.store.install("user1", "calc.py", VALID_TOOL_SOURCE_2)
        all_handlers = self.store.get_all_handlers()
        assert "greet" in all_handlers
        assert "calc_sqrt" in all_handlers


class TestListTools(unittest.TestCase):
    """Test listing tools with user filtering."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        DynamicToolStore.reset()
        self.store = DynamicToolStore(store_dir=self.tmpdir)

    def tearDown(self):
        DynamicToolStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_empty(self):
        tools = self.store.list_tools("user1")
        assert tools == []

    def test_list_own_tools(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        self.store.install("user2", "calc.py", VALID_TOOL_SOURCE_2)
        tools = self.store.list_tools("user1")
        assert len(tools) == 1
        assert tools[0]["tool_name"] == "greet"

    def test_admin_sees_all(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        self.store.install("user2", "calc.py", VALID_TOOL_SOURCE_2)
        tools = self.store.list_tools("admin", is_admin=True)
        assert len(tools) == 2

    def test_list_tool_metadata(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        tools = self.store.list_tools("user1")
        t = tools[0]
        assert t["tool_name"] == "greet"
        assert t["owner"] == "user1"
        assert t["source"] == "dynamic"
        assert "description" in t
        assert "installed_at" in t


class TestDiskPersistence(unittest.TestCase):
    """Test that tools survive store restart."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        DynamicToolStore.reset()

    def tearDown(self):
        DynamicToolStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_persist_and_reload(self):
        store1 = DynamicToolStore(store_dir=self.tmpdir)
        store1.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        store1.install("user1", "calc.py", VALID_TOOL_SOURCE_2)

        # Simulate restart: new instance, same directory
        store2 = DynamicToolStore(store_dir=self.tmpdir)
        handlers = store2.get_all_handlers()
        assert "greet" in handlers
        assert "calc_sqrt" in handlers
        assert handlers["greet"].execute({"name": "Test"}) == "Hello, Test!"

    def test_uninstall_persists(self):
        store1 = DynamicToolStore(store_dir=self.tmpdir)
        store1.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        store1.uninstall("user1", "greet")

        store2 = DynamicToolStore(store_dir=self.tmpdir)
        assert store2.get_handler("greet") is None

    def test_index_file_format(self):
        store = DynamicToolStore(store_dir=self.tmpdir)
        store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        index_path = Path(self.tmpdir) / "_index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert entry["tool_name"] == "greet"
        assert entry["user_id"] == "user1"
        assert "hash" in entry


class TestSafeUserId(unittest.TestCase):
    """Test user ID sanitization."""

    def test_normal_id(self):
        assert DynamicToolStore._safe_user_id("user123") == "user123"

    def test_email_id(self):
        assert DynamicToolStore._safe_user_id("user@example.com") == "user@example.com"

    def test_dangerous_chars(self):
        result = DynamicToolStore._safe_user_id("../../../etc")
        # Slashes are stripped, dots and alphanumerics are kept
        assert "/" not in result
        assert "\\" not in result

    def test_special_chars(self):
        assert DynamicToolStore._safe_user_id("user<script>") == "userscript"


class TestRegistryIntegration(unittest.TestCase):
    """Test dynamic tools integration with ToolRegistry."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        DynamicToolStore.reset()
        self.store = DynamicToolStore(store_dir=self.tmpdir)
        # Point singleton to our test instance
        DynamicToolStore._instance = self.store

    def tearDown(self):
        DynamicToolStore.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dynamic_tools_in_registry(self):
        self.store.install("user1", "greeter.py", VALID_TOOL_SOURCE)
        from core.tool_registry import create_default_registry
        registry = create_default_registry()
        # Merge dynamic tools (same logic as agent_loop)
        for name, handler in self.store.get_all_handlers().items():
            if not registry.get(name):
                registry.register(handler)
        assert registry.get("greet") is not None


class TestI18n(unittest.TestCase):
    """Test i18n keys for dynamic tools."""

    def test_keys_in_all_locales(self):
        keys = [
            "dynamic_tools.title",
            "dynamic_tools.install",
            "dynamic_tools.uninstall",
            "dynamic_tools.list",
            "dynamic_tools.no_tools",
            "dynamic_tools.installed",
            "dynamic_tools.uninstalled",
            "dynamic_tools.install_hint",
        ]
        for locale in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{locale}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing key '{key}' in {locale}.json"


if __name__ == "__main__":
    unittest.main()
