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
import unittest
from typing import Dict, Any

from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry, create_default_registry


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
