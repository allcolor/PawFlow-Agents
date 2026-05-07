"""Tests for ToolRegistry — tool dispatch system.

Tests cover:
- register / unregister / get / list_tools
- execute (happy path + unknown tool)
- CC argument aliases (file_path→path, include→glob, filesystem→source)
- JSON string unwrapping in execute
- Argument validation (unknown args rejected)
- create_default_registry returns 50+ handlers
- Pre/post hooks
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Dict, Any

from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry, create_default_registry
from core.handlers.edit_handler import EditHandler


# ── Mock handler ────────────────────────────────────────────────────

class MockHandler(ToolHandler):
    """Minimal handler for testing."""

    def __init__(self, name="mock_tool", description="A mock tool",
                 schema=None, result="mock_result"):
        self._name = name
        self._description = description
        self._schema = schema or {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "input value"},
            },
        }
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._schema

    def execute(self, arguments: Dict[str, Any]) -> str:
        return self._result


class CapturingHandler(MockHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_args = None

    def execute(self, arguments: Dict[str, Any]) -> str:
        self.received_args = arguments
        return self._result


class ErrorHandler(MockHandler):
    """Handler that raises on execute."""

    def execute(self, arguments: Dict[str, Any]) -> str:
        raise RuntimeError("boom")


# ── Tests ───────────────────────────────────────────────────────────

class TestRegisterAndGet(unittest.TestCase):

    def test_register_and_get(self):
        reg = ToolRegistry()
        h = MockHandler()
        reg.register(h)
        assert reg.get("mock_tool") is h

    def test_get_unknown_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_register_duplicate_overwrites(self):
        reg = ToolRegistry()
        h1 = MockHandler(result="first")
        h2 = MockHandler(result="second")
        reg.register(h1)
        reg.register(h2)
        assert reg.get("mock_tool") is h2
        assert reg.get("mock_tool").execute({}) == "second"

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(MockHandler())
        reg.unregister("mock_tool")
        assert reg.get("mock_tool") is None

    def test_unregister_unknown_is_noop(self):
        reg = ToolRegistry()
        reg.unregister("nonexistent")  # should not raise


class TestListTools(unittest.TestCase):

    def test_list_tools_empty(self):
        reg = ToolRegistry()
        assert reg.list_tools() == []

    def test_list_tools_returns_all(self):
        reg = ToolRegistry()
        h1 = MockHandler(name="tool_a")
        h2 = MockHandler(name="tool_b")
        reg.register(h1)
        reg.register(h2)
        tools = reg.list_tools()
        names = {t.name for t in tools}
        assert names == {"tool_a", "tool_b"}


class TestExecute(unittest.TestCase):

    def test_execute_returns_result(self):
        reg = ToolRegistry()
        reg.register(MockHandler(result="hello"))
        result = reg.execute("mock_tool", {"input": "test"})
        assert result == "hello"

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {})
        assert "Error" in result
        assert "unknown tool" in result
        assert "nonexistent" in result

    def test_execute_handler_exception(self):
        reg = ToolRegistry()
        reg.register(ErrorHandler(name="bad"))
        result = reg.execute("bad", {})
        assert "Error executing tool" in result
        assert "boom" in result

    def test_execute_records_tool_metrics(self):
        ToolRegistry.reset_metrics()
        reg = ToolRegistry()
        reg.register(MockHandler(result="hello"))
        assert reg.execute("mock_tool", {"input": "test"}) == "hello"
        reg.execute("missing", {})
        metrics = ToolRegistry.get_metrics()
        assert metrics["mock_tool"]["calls"] == 1
        assert metrics["mock_tool"]["successes"] == 1
        assert metrics["mock_tool"]["avg_duration_ms"] >= 0
        assert metrics["mock_tool"]["last_duration_ms"] >= 0
        assert metrics["mock_tool"]["last_ok"] is True
        assert metrics["missing"]["errors"] == 1
        assert metrics["missing"]["last_ok"] is False
        assert "unknown tool" in metrics["missing"]["last_error"]

    def test_execute_counts_error_result_as_metric_error(self):
        ToolRegistry.reset_metrics()
        reg = ToolRegistry()
        reg.register(MockHandler(name="soft_error", result="Error: invalid args"))
        assert reg.execute("soft_error", {}) == "Error: invalid args"
        metrics = ToolRegistry.get_metrics()
        assert metrics["soft_error"]["calls"] == 1
        assert metrics["soft_error"]["successes"] == 0
        assert metrics["soft_error"]["errors"] == 1
        assert metrics["soft_error"]["last_ok"] is False
        assert metrics["soft_error"]["last_error"] == "Error: invalid args"


class TestCCAliases(unittest.TestCase):
    """CC argument aliases rewrite file_path→path, include→glob, filesystem→source."""

    def setUp(self):
        self.received_args = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                self.received_args = dict(arguments)
                return "ok"

        self.reg = ToolRegistry()
        self.reg.register(CapturingHandler(
            name="test_tool",
            schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "source": {"type": "string"},
                    "input": {"type": "string"},
                },
            },
        ))

    def test_file_path_to_path(self):
        self.reg.execute("test_tool", {"file_path": "/tmp/foo.txt"})
        assert self.received_args.get("path") == "/tmp/foo.txt"
        assert "file_path" not in self.received_args

    def test_include_to_glob(self):
        self.reg.execute("test_tool", {"include": "*.py"})
        assert self.received_args.get("glob") == "*.py"
        assert "include" not in self.received_args

    def test_filesystem_to_source(self):
        self.reg.execute("test_tool", {"filesystem": "local"})
        assert self.received_args.get("source") == "local"
        assert "filesystem" not in self.received_args

    def test_alias_skipped_when_target_present(self):
        """If PawFlow name already in args, alias key is left alone."""
        reg = ToolRegistry()
        received = {}

        class CaptureAll(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "ok"

        reg.register(CaptureAll(
            name="permissive",
            schema={"type": "object", "properties": {}},  # no validation
        ))
        reg.execute("permissive", {"file_path": "cc", "path": "pf"})
        # path already present -> alias not applied -> file_path stays
        assert received.get("path") == "pf"
        assert received.get("file_path") == "cc"


class TestMetaToolAliases(unittest.TestCase):

    def test_read_file_alias_resolves_schema_and_execution(self):
        from core.handlers.meta_tools import GetToolSchemaHandler, UseToolHandler

        reg = ToolRegistry()
        reg.register(MockHandler(
            name="read",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            result="read-ok",
        ))

        schema = json.loads(GetToolSchemaHandler(reg).execute({"tool_name": "read_file"}))
        assert schema["name"] == "read"
        assert schema["parameters"]["required"] == ["path"]

        result = UseToolHandler(reg).execute({
            "tool_name": "read_file",
            "arguments": {"path": "/tmp/a.py"},
        })
        assert result == "read-ok"

    def test_nested_use_tool_read_is_unwrapped(self):
        from core.handlers.meta_tools import UseToolHandler

        received = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "read-ok"

        reg = ToolRegistry()
        reg.register(CapturingHandler(
            name="read",
            schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        ))

        result = UseToolHandler(reg).execute({
            "tool_name": "use_tool",
            "arguments": {
                "tool_name": "read",
                "arguments": {"path": "/workspace/cli.py", "start_line": 1},
            },
        })

        assert result == "read-ok"
        assert received == {"path": "/workspace/cli.py", "start_line": 1}

    def test_use_tool_accepts_arguments_json_string(self):
        from core.handlers.meta_tools import UseToolHandler

        received = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "glob-ok"

        reg = ToolRegistry()
        reg.register(CapturingHandler(
            name="glob",
            schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        ))

        use_tool = UseToolHandler(reg)
        schema = use_tool.parameters_schema
        assert "arguments_json" in schema["properties"]
        assert "arguments" not in schema["properties"]
        assert schema["required"] == ["tool_name", "arguments_json"]

        result = use_tool.execute({
            "tool_name": "glob",
            "arguments_json": '{"pattern": "*.py", "path": "/workspace"}',
        })

        assert result == "glob-ok"
        assert received == {"pattern": "*.py", "path": "/workspace"}

    def test_nested_use_tool_accepts_arguments_json_string(self):
        from core.handlers.meta_tools import UseToolHandler

        received = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "glob-ok"

        reg = ToolRegistry()
        reg.register(CapturingHandler(
            name="glob",
            schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        ))

        result = UseToolHandler(reg).execute({
            "tool_name": "use_tool",
            "arguments": {
                "tool_name": "glob",
                "arguments_json": '{"pattern": "*.md"}',
            },
        })

        assert result == "glob-ok"
        assert received == {"pattern": "*.md"}

    def test_use_tool_rejects_missing_required_argument_before_execution(self):
        from core.handlers.meta_tools import UseToolHandler

        called = False

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                nonlocal called
                called = True
                return "read-ok"

        reg = ToolRegistry()
        reg.register(CapturingHandler(
            name="read",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ))

        result = UseToolHandler(reg).execute({
            "tool_name": "read",
            "arguments": {},
        })

        assert "missing required argument" in result
        assert "path" in result
        assert called is False

    def test_nested_mcp_pawflow_use_tool_is_unwrapped(self):
        from core.handlers.meta_tools import UseToolHandler

        received = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "bash-ok"

        reg = ToolRegistry()
        reg.register(CapturingHandler(
            name="bash",
            schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["command"],
            },
        ))

        result = UseToolHandler(reg).execute({
            "tool_name": "mcp_pawflow_use_tool",
            "arguments": {
                "tool_name": "bash",
                "arguments": {"command": "git status", "cwd": "/workspace"},
            },
        })

        assert result == "bash-ok"
        assert received == {"command": "git status", "path": "/workspace"}

    def test_read_handler_schema_accepts_claude_line_aliases(self):
        from core.handlers.read import ReadHandler

        props = ReadHandler().parameters_schema["properties"]
        assert "start_line" in props
        assert "end_line" in props
        assert "ranges" in props

    def test_fs_meta_schema_exposes_local_flag_for_bash(self):
        from core.handlers.bash import BashHandler
        from core.handlers.meta_tools import GetToolSchemaHandler, UseToolHandler

        received = {}

        class CapturingBash(BashHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "bash-ok"

        reg = ToolRegistry()
        reg.register(CapturingBash())

        schema = json.loads(GetToolSchemaHandler(reg).execute({"tool_name": "bash"}))
        assert "local" in schema["parameters"]["properties"]

        result = UseToolHandler(reg).execute({
            "tool_name": "bash",
            "arguments": {"command": "pwd", "local": True},
        })
        assert result == "bash-ok"
        assert received == {"command": "pwd", "local": True}

    def test_fs_meta_schema_exposes_relay_and_normalizes_to_source(self):
        from core.handlers.meta_tools import GetToolSchemaHandler, UseToolHandler
        from core.handlers.read import ReadHandler

        received = {}

        class CapturingRead(ReadHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "read-ok"

        reg = ToolRegistry()
        reg.register(CapturingRead())

        schema = json.loads(GetToolSchemaHandler(reg).execute({"tool_name": "read"}))
        props = schema["parameters"]["properties"]
        assert "relay" in props
        assert "local" in props

        result = UseToolHandler(reg).execute({
            "tool_name": "read",
            "arguments": {
                "path": "/workspace/README.md",
                "relay": "fs_main",
                "local": True,
            },
        })
        assert result == "read-ok"
        assert received["relay"] == "fs_main"
        assert received["source"] == "fs_main"
        assert received["local"] is True

    def test_fetch_schema_accepts_max_chars_alias(self):
        from core.handlers.meta_tools import GetToolSchemaHandler, UseToolHandler

        received = {}

        class CapturingFetch(MockHandler):
            def execute(inner_self, arguments):
                received.update(arguments)
                return "fetch-ok"

        reg = ToolRegistry()
        reg.register(CapturingFetch(
            name="fetch",
            schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        ))

        schema = json.loads(GetToolSchemaHandler(reg).execute({"tool_name": "fetch"}))
        assert "max_chars" in schema["parameters"]["properties"]
        result = UseToolHandler(reg).execute({
            "tool_name": "fetch",
            "arguments": {"url": "https://example.com", "max_chars": 1000},
        })
        assert result == "fetch-ok"
        assert received == {"url": "https://example.com", "limit": 1000}

    def test_tool_relay_schema_exposes_local_flag_for_bash(self):
        from services.tool_relay_service import ToolRelayService

        svc = ToolRelayService({})
        result = svc._handle_get_schema("rid", "bash", "user", "conv")

        assert result["type"] == "result"
        assert "local" in result["data"]["parameters"]["properties"]

    def test_tool_relay_schema_exposes_relay_for_read_and_max_chars_for_fetch(self):
        from services.tool_relay_service import ToolRelayService

        svc = ToolRelayService({})
        read_schema = svc._handle_get_schema("rid1", "read", "user", "conv")
        fetch_schema = svc._handle_get_schema("rid2", "fetch", "user", "conv")

        assert "relay" in read_schema["data"]["parameters"]["properties"]
        assert "local" in read_schema["data"]["parameters"]["properties"]
        assert "max_chars" in fetch_schema["data"]["parameters"]["properties"]

    def test_all_filesystem_handlers_expose_relay_and_local(self):
        from core.handlers._fs_base import BaseFsHandler
        from core.handlers.meta_tools import _schema_with_local
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        fs_handlers = [h for h in registry.list_tools()
                       if isinstance(h, BaseFsHandler)]

        assert fs_handlers
        for handler in fs_handlers:
            props = (_schema_with_local(handler).get("properties") or {})
            assert "relay" in props, handler.name
            assert "local" in props, handler.name

    def test_all_media_handlers_expose_service_override(self):
        from core.handlers.meta_tools import _schema_with_local
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        media_handlers = [h for h in registry.list_tools()
                          if hasattr(h, "set_service_resolver")]

        assert media_handlers
        for handler in media_handlers:
            props = (_schema_with_local(handler).get("properties") or {})
            assert "service" in props, handler.name
        assert "image_service" in _schema_with_local(
            registry.get("generate_image"))["properties"]
        assert "video_service" in _schema_with_local(
            registry.get("generate_video"))["properties"]
        assert "audio_service" in _schema_with_local(
            registry.get("generate_audio"))["properties"]

    def test_relay_alias_maps_to_native_selector_names(self):
        from core.handlers.meta_tools import _normalize_tool_args

        assert _normalize_tool_args(
            "read", {"path": "a", "relay": "fs1"})["source"] == "fs1"
        assert _normalize_tool_args(
            "write", {"path": "a", "relay": "fs1"})["destination"] == "fs1"
        assert _normalize_tool_args(
            "edit", {"path": "a", "relay": "fs1"})["filesystem"] == "fs1"
        copy_args = _normalize_tool_args(
            "copy", {"source_path": "a", "dest_path": "b", "relay": "fs1"})
        assert copy_args["source_service"] == "fs1"
        assert copy_args["dest_service"] == "fs1"
        assert _normalize_tool_args(
            "bash", {"command": "pwd", "relay": "fs1"})["relay"] == "fs1"

    def test_relay_service_methods_forward_local_to_request(self):
        from services.filesystem_service import RelayService

        svc = RelayService({"_service_id": "fs1"})
        calls = []

        def fake_request(action, path=".", **kwargs):
            calls.append((action, path, kwargs))
            if action == "read_file":
                return {"content": ""}
            if action == "list_dir":
                return []
            if action == "stat":
                return {"name": "x", "path": "x", "kind": "file", "size": 0}
            if action == "exists":
                return {"exists": True}
            if action == "grep":
                return []
            if action in {"find_replace", "edit"}:
                return {"replacements": 0}
            if action == "exec":
                return {"stdout": "", "stderr": "", "returncode": 0}
            if action == "edit_notebook":
                return {"operation": "edit", "cell_index": 0, "total_cells": 1}
            return {}

        svc._request = fake_request
        svc.read_file("a", local=True)
        svc.write_file("a", b"x", local=True)
        svc.delete_file("a", local=True)
        svc.mkdir("a", local=True)
        svc.list_dir(".", local=True, recursive=True, max_entries=5)
        svc.stat("a", local=True)
        svc.exists("a", local=True)
        svc.search(".", "*.py", local=True)
        svc.grep(".", "x", local=True)
        svc.find_replace("a", "x", "y", local=True)
        svc.edit("a", "x", "y", local=True)
        svc.batch_edit([], local=True)
        svc.apply_patch("diff", local=True)
        svc.edit_notebook("a.ipynb", 0, local=True)
        svc.exec(".", "pwd", local=True)

        assert calls
        for action, _path, kwargs in calls:
            assert kwargs.get("local") is True, action
        list_call = next(call for call in calls if call[0] == "list_dir")
        assert list_call[2]["recursive"] is True
        assert list_call[2]["max_entries"] == 5

    def test_batch_edit_relay_action_supports_replace_all_defaults(self):
        from tools.fs_actions import action_batch_edit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("foo foo\n", encoding="utf-8")
            (root / "b.txt").write_text("foo foo\n", encoding="utf-8")

            result = action_batch_edit(str(root), str(root), {
                "replace_all": True,
                "edits": [
                    {"path": "a.txt", "old_string": "foo", "new_string": "bar"},
                    {"path": "b.txt", "old_string": "foo", "new_string": "baz"},
                ],
            })

            assert result["edits_applied"] == 2
            assert (root / "a.txt").read_text(encoding="utf-8") == "bar bar\n"
            assert (root / "b.txt").read_text(encoding="utf-8") == "baz baz\n"

    def test_batch_edit_relay_action_supports_per_edit_replace_all(self):
        from tools.fs_actions import action_batch_edit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("foo foo\n", encoding="utf-8")
            (root / "b.txt").write_text("foo foo\n", encoding="utf-8")

            result = action_batch_edit(str(root), str(root), {
                "edits": [
                    {"path": "a.txt", "old_string": "foo", "new_string": "bar", "replace_all": True},
                    {"path": "b.txt", "old_string": "foo", "new_string": "baz", "replace_all": True},
                ],
            })

            assert result["edits_applied"] == 2
            assert (root / "a.txt").read_text(encoding="utf-8") == "bar bar\n"
            assert (root / "b.txt").read_text(encoding="utf-8") == "baz baz\n"


    def test_relay_list_dir_action_supports_recursive_limit(self):
        from tools.fs_actions import action_list_dir

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a" / "b").mkdir(parents=True)
            (root / "a" / "one.txt").write_text("1", encoding="utf-8")
            (root / "a" / "b" / "two.txt").write_text("2", encoding="utf-8")

            entries = action_list_dir(str(root), str(root), {
                "recursive": True,
                "max_entries": 3,
            })

        assert len(entries) == 3
        names = [entry["name"] for entry in entries]
        assert "a" in names
        assert any(name.startswith("a/") for name in names)

    def test_monitor_exposes_and_forwards_relay_local_to_bash(self):
        from core.handlers import bash as bash_mod
        from core.handlers.monitor import MonitorHandler

        received = {}

        class FakeBash:
            def set_conversation_id(self, conversation_id):
                pass

            def set_user_id(self, user_id):
                pass

            def execute(self, arguments):
                received.update(arguments)
                return "ok"

        handler = MonitorHandler()
        props = handler.parameters_schema["properties"]
        assert "relay" in props
        assert "local" in props

        with patch.object(bash_mod, "BashHandler", FakeBash):
            result = handler.execute({
                "command": "pytest -q",
                "relay": "fs1",
                "local": True,
                "timeout_ms": 1000,
            })

        assert "ok" in result
        assert received["relay"] == "fs1"
        assert received["local"] is True


class TestJsonStringUnwrapping(unittest.TestCase):

    def setUp(self):
        self.received_args = {}

        class CapturingHandler(MockHandler):
            def execute(inner_self, arguments):
                self.received_args = dict(arguments) if isinstance(arguments, dict) else arguments
                return "ok"

        self.reg = ToolRegistry()
        self.reg.register(CapturingHandler(
            name="test_tool",
            schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                },
            },
        ))

    def test_json_string_gets_parsed(self):
        args_str = json.dumps({"input": "hello"})
        self.reg.execute("test_tool", args_str)
        assert self.received_args == {"input": "hello"}

    def test_non_json_string_stays_string(self):
        """If string is not valid JSON, it passes through as-is."""
        self.reg.execute("test_tool", "not json")
        # execute still runs (args may be a string)


class TestArgumentValidation(unittest.TestCase):

    def test_unknown_args_rejected(self):
        reg = ToolRegistry()
        reg.register(MockHandler(
            name="strict",
            schema={
                "type": "object",
                "properties": {
                    "allowed": {"type": "string"},
                },
            },
        ))
        result = reg.execute("strict", {"allowed": "ok", "bogus": "bad"})
        assert "Error" in result
        assert "unknown argument" in result
        assert "bogus" in result

    def test_known_args_accepted(self):
        reg = ToolRegistry()
        reg.register(MockHandler(
            name="strict",
            schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                },
            },
        ))
        result = reg.execute("strict", {"input": "ok"})
        assert result == "mock_result"

    def test_result_limit_aliases_normalized_before_validation(self):
        reg = ToolRegistry()
        handler = CapturingHandler(
            name="limited",
            schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        )
        reg.register(handler)
        result = reg.execute("limited", {"path": "/workspace", "max_results": 50})
        assert result == "mock_result"
        assert handler.received_args == {"path": "/workspace", "limit": 50}

    def test_output_limit_aliases_normalized_before_validation(self):
        reg = ToolRegistry()
        handler = CapturingHandler(
            name="output_limited",
            schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "max_output": {"type": "integer"},
                },
            },
        )
        reg.register(handler)
        result = reg.execute("output_limited", {"command": "git status", "max_chars": 1200})
        assert result == "mock_result"
        assert handler.received_args == {"command": "git status", "max_output": 1200}

    def test_edit_schema_accepts_old_new_aliases(self):
        schema = EditHandler().parameters_schema
        assert "old" in schema["properties"]
        assert "new" in schema["properties"]
        assert "old_str" in schema["properties"]
        assert "new_str" in schema["properties"]

    def test_underscore_prefixed_args_allowed(self):
        """Arguments starting with _ are not validated (internal use)."""
        reg = ToolRegistry()
        reg.register(MockHandler(
            name="strict",
            schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                },
            },
        ))
        result = reg.execute("strict", {"input": "ok", "_internal": True})
        assert result == "mock_result"

    def test_empty_properties_skips_validation(self):
        """If schema has no properties, validation is skipped."""
        reg = ToolRegistry()
        reg.register(MockHandler(
            name="loose",
            schema={"type": "object", "properties": {}},
        ))
        result = reg.execute("loose", {"anything": "goes"})
        assert result == "mock_result"


class TestCreateDefaultRegistry(unittest.TestCase):

    def test_has_many_handlers(self):
        reg = create_default_registry()
        tools = reg.list_tools()
        assert len(tools) >= 50, f"Expected 50+ handlers, got {len(tools)}"

    def test_known_builtins_present(self):
        reg = create_default_registry()
        for name in ("execute_script", "web_search", "share_file",
                      "remember", "recall", "create_plan"):
            assert reg.get(name) is not None, f"Missing builtin handler: {name}"


class TestHooks(unittest.TestCase):

    def test_pre_hook_called(self):
        reg = ToolRegistry()
        reg.register(MockHandler(result="ok"))
        calls = []

        def pre_hook(name, args):
            calls.append(("pre", name, args))
            return args  # must return args to continue

        reg.register_hook("pre:mock_tool", pre_hook)
        reg.execute("mock_tool", {"input": "x"})
        assert len(calls) == 1
        assert calls[0][0] == "pre"
        assert calls[0][1] == "mock_tool"

    def test_post_hook_called(self):
        reg = ToolRegistry()
        reg.register(MockHandler(result="original"))
        calls = []

        def post_hook(name, args, result):
            calls.append(("post", name, result))
            return result + "_modified"

        reg.register_hook("post:mock_tool", post_hook)
        result = reg.execute("mock_tool", {"input": "x"})
        assert len(calls) == 1
        assert result == "original_modified"

    def test_wildcard_pre_hook(self):
        reg = ToolRegistry()
        reg.register(MockHandler(name="tool_a", result="a"))
        reg.register(MockHandler(name="tool_b", result="b"))
        calls = []

        def hook(name, args):
            calls.append(name)
            return args

        reg.register_hook("pre:*", hook)
        reg.execute("tool_a", {})
        reg.execute("tool_b", {})
        assert calls == ["tool_a", "tool_b"]

    def test_pre_hook_blocks_execution(self):
        """A pre-hook returning None blocks the tool execution."""
        reg = ToolRegistry()
        reg.register(MockHandler(result="should_not_see"))

        def blocking_hook(name, args):
            return None  # block

        reg.register_hook("pre:mock_tool", blocking_hook)
        result = reg.execute("mock_tool", {"input": "x"})
        assert "blocked by pre-hook" in result

    def test_unregister_hook(self):
        reg = ToolRegistry()
        reg.register(MockHandler(result="ok"))
        calls = []

        def hook(name, args):
            calls.append(name)
            return args

        reg.register_hook("pre:mock_tool", hook)
        reg.execute("mock_tool", {})
        assert len(calls) == 1

        reg.unregister_hook("pre:mock_tool", hook)
        reg.execute("mock_tool", {})
        assert len(calls) == 1  # not called again


class TestGetToolDefinitions(unittest.TestCase):

    def test_returns_definition_list(self):
        reg = ToolRegistry()
        reg.register(MockHandler(name="t1", description="desc1"))
        reg.register(MockHandler(name="t2", description="desc2"))
        defs = reg.get_tool_definitions()
        assert len(defs) == 2
        names = {d["name"] for d in defs}
        assert names == {"t1", "t2"}
        for d in defs:
            assert "description" in d
            assert "parameters" in d

    def test_batch_edit_schema_exposes_replace_all(self):
        from core.handlers.batch_edit import BatchEditHandler

        schema = BatchEditHandler().parameters_schema
        assert "replace_all" in schema["properties"]
        edit_props = schema["properties"]["edits"]["items"]["properties"]
        assert "replace_all" in edit_props



class TestImageMarkerCapBypass(unittest.TestCase):
    """Cap bypass for __image_data__: must be gated by _returns_images.

    Regression: a grep result matching the literal marker string used to
    bypass the 50K cap and trigger split-into-blocks downstream, leaking
    huge payloads back to MCP callers.
    """

    def test_marker_in_text_does_not_bypass_cap_for_regular_handler(self):
        # 200K of text containing the marker as plain content (e.g. a grep hit)
        big = ("line with __image_data__:fake content\n" * 6000)
        self.assertIn("__image_data__:", big)
        self.assertGreater(len(big), 50_000)
        reg = ToolRegistry()
        reg.register(MockHandler(name="grep_like", result=big))
        out = reg.execute("grep_like", {})
        # Cap fires — either FileStore link or simple truncation suffix
        self.assertLessEqual(
            len(out),
            50_000 + 500,
            f"cap should clip; got {len(out):,} chars",
        )

    def test_marker_bypasses_cap_only_when_handler_returns_images(self):
        big = ("__image_data__:image/png:" + ("A" * 200_000))
        h = MockHandler(name="image_tool", result=big)
        h._returns_images = True
        reg = ToolRegistry()
        reg.register(h)
        out = reg.execute("image_tool", {})
        # Marker present + flag set → cap skipped, full payload preserved
        self.assertEqual(len(out), len(big))

    def test_returns_images_without_marker_still_caps(self):
        # Defensive: flag alone doesn't disable the cap; marker must also be present
        big = "X" * 200_000
        h = MockHandler(name="see_no_marker", result=big)
        h._returns_images = True
        reg = ToolRegistry()
        reg.register(h)
        out = reg.execute("see_no_marker", {})
        self.assertLessEqual(len(out), 50_000 + 500)


if __name__ == "__main__":
    unittest.main()
