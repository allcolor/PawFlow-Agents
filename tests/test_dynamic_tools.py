"""Tests for dynamic tools — validation + loader + ResourceStore type 'tool'.

Replaces the previous DynamicToolStore-only test file: dynamic tools are now
first-class ResourceStore resources (tri-scoped), validated by
core.tool_validation, and loaded into a registry by core.tool_loader.
"""

import tempfile
import unittest
from pathlib import Path

from core import paths as _paths
from core.tool_validation import (
    validate_source, sandbox_load, validate_and_load,
)


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
                "name": {"type": "string"},
            },
            "required": ["name"],
        }

    def execute(self, arguments):
        return f"Hello, {arguments.get('name', 'World')}!"
'''

VALID_MATH_SOURCE = '''
import math

class CalcHandler(ToolHandler):
    @property
    def name(self):
        return "calc_sqrt"

    @property
    def description(self):
        return "sqrt"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {"value": {"type": "number"}}}

    def execute(self, arguments):
        return str(math.sqrt(arguments.get("value", 0)))
'''

FORBIDDEN_OS = '''
import os
class H(ToolHandler):
    @property
    def name(self): return "h"
    @property
    def description(self): return "h"
    @property
    def parameters_schema(self): return {"type": "object"}
    def execute(self, a): return os.listdir(".")
'''

FORBIDDEN_EVAL = '''
class H(ToolHandler):
    @property
    def name(self): return "h"
    @property
    def description(self): return "h"
    @property
    def parameters_schema(self): return {"type": "object"}
    def execute(self, a): return eval("1+1")
'''

NO_HANDLER = 'def helper():\n    return 42\n'

SYNTAX_ERROR = 'def broken(\n    return 42\n'


class TestValidateSource(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(validate_source(VALID_TOOL_SOURCE), [])

    def test_safe_math_import(self):
        self.assertEqual(validate_source(VALID_MATH_SOURCE), [])

    def test_safe_requests(self):
        self.assertEqual(validate_source('import requests\nx = 1'), [])

    def test_safe_from_urllib_parse(self):
        self.assertEqual(
            validate_source('from urllib.parse import urlencode\nx = 1'), [])

    def test_forbidden_os(self):
        v = validate_source(FORBIDDEN_OS)
        self.assertTrue(any("os" in s for s in v))

    def test_forbidden_subprocess(self):
        v = validate_source('import subprocess\nx = 1')
        self.assertTrue(any("subprocess" in s for s in v))

    def test_forbidden_from_pathlib(self):
        v = validate_source('from pathlib import Path\nx = 1')
        self.assertTrue(any("pathlib" in s for s in v))

    def test_forbidden_eval(self):
        v = validate_source(FORBIDDEN_EVAL)
        self.assertTrue(any("eval" in s for s in v))

    def test_forbidden_open(self):
        v = validate_source('x = open("a.txt")')
        self.assertTrue(any("open" in s for s in v))

    def test_forbidden_globals_attr(self):
        v = validate_source('x = f.__globals__')
        self.assertTrue(any("__globals__" in s for s in v))

    def test_syntax_error(self):
        v = validate_source(SYNTAX_ERROR)
        self.assertEqual(len(v), 1)
        self.assertIn("Syntax", v[0])


class TestSandboxLoad(unittest.TestCase):

    def test_load_returns_handler(self):
        handler, name = sandbox_load(VALID_TOOL_SOURCE)
        self.assertEqual(name, "greet")
        self.assertEqual(handler.execute({"name": "Alice"}), "Hello, Alice!")

    def test_load_with_safe_import(self):
        handler, name = sandbox_load(VALID_MATH_SOURCE)
        self.assertEqual(name, "calc_sqrt")
        self.assertEqual(handler.execute({"value": 16}), "4.0")

    def test_no_handler_class_raises(self):
        with self.assertRaises(ValueError) as cm:
            sandbox_load(NO_HANDLER)
        self.assertIn("No ToolHandler", str(cm.exception))

    def test_validate_and_load_one_shot(self):
        handler, name = validate_and_load(VALID_TOOL_SOURCE)
        self.assertEqual(name, "greet")

    def test_validate_and_load_rejects_forbidden(self):
        with self.assertRaises(ValueError) as cm:
            validate_and_load(FORBIDDEN_OS)
        self.assertIn("Security validation", str(cm.exception))


class _RegistryStub:
    def __init__(self):
        self.handlers = {}
    def get(self, name):
        return self.handlers.get(name)
    def register(self, h):
        self.handlers[h.name] = h
    def list_tools(self):
        return list(self.handlers.values())


class TestToolLoaderResourceStore(unittest.TestCase):
    """End-to-end: write a tool resource via ResourceStore + load via tool_loader."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_repo = _paths.REPOSITORY_DIR
        _paths.REPOSITORY_DIR = Path(self._tmp.name)
        # Reset singletons that hold the old paths
        from core.repository import ScopedRepository
        from core.resource_store import ResourceStore
        ScopedRepository.reset()
        ResourceStore.reset()

    def tearDown(self):
        _paths.REPOSITORY_DIR = self._old_repo
        from core.repository import ScopedRepository
        from core.resource_store import ResourceStore
        ScopedRepository.reset()
        ResourceStore.reset()
        self._tmp.cleanup()

    def _create_tool(self, scope_kwargs):
        from core.resource_store import ResourceStore
        ResourceStore.instance().create(
            "tool", "greet", "alice",
            {"source": VALID_TOOL_SOURCE,
             "description": "Greet someone by name",
             "parameters": {"name": {"type": "string"}}},
            **scope_kwargs)

    def test_loader_picks_up_user_tool(self):
        from core.tool_loader import load_tools_into_registry
        self._create_tool({})  # user scope (no conv_id)
        reg = _RegistryStub()
        n = load_tools_into_registry(reg, "alice")
        self.assertEqual(n, 1)
        self.assertIn("greet", reg.handlers)
        self.assertEqual(reg.handlers["greet"]._origin, "dynamic")
        self.assertEqual(reg.handlers["greet"]._origin_scope, "user")

    def test_loader_picks_up_conv_tool(self):
        from core.tool_loader import load_tools_into_registry
        self._create_tool({"conversation_id": "c1"})
        reg = _RegistryStub()
        n = load_tools_into_registry(reg, "alice", conversation_id="c1")
        self.assertEqual(n, 1)
        self.assertEqual(
            reg.handlers["greet"]._origin_scope, "conversation")

    def test_loader_skips_other_user(self):
        from core.tool_loader import load_tools_into_registry
        self._create_tool({})  # owned by alice
        reg = _RegistryStub()
        n = load_tools_into_registry(reg, "bob")
        self.assertEqual(n, 0)

    def test_cleanup_conversation_deletes_conv_tools_only(self):
        from core.tool_loader import (load_tools_into_registry,
                                       cleanup_conversation_tools)
        self._create_tool({})  # user-scoped
        self._create_tool({"conversation_id": "c1"})  # conv-scoped
        deleted = cleanup_conversation_tools("alice", "c1")
        self.assertEqual(deleted, 1)
        # User-scoped still loadable
        reg = _RegistryStub()
        n = load_tools_into_registry(reg, "alice")
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
