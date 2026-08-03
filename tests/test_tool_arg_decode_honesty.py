"""A tool call that could not be decoded must say so, once, and count as failed.

Three defects of the same family:

- `bash` answered an empty command with "(no command provided - ignored)", a
  result without the "Error:" prefix that ToolRegistry.execute records as a
  SUCCESS, so malformed bash calls were invisible in the tool metrics.
- BaseFsHandler._unwrap_json kept its own decode loop (the u1/u2 unification
  missed it) and returned {} on failure, discarding a diagnostic the canonical
  parser produces - including the character-position window.
- The CC alias rewrite renamed keys in the CALLER's dict, so the arguments kept
  for re-authorization, the post_tool_call hook and the transcript stopped
  matching the ones the user approved.
"""

import pytest

from core.handlers._fs_base import BaseFsHandler
from core.tool_handler import ToolHandler
from core.tool_json import ToolArgumentError
from core.tool_registry import ToolRegistry


# -- bash: an empty command is an error, not a silent success ----------

def test_empty_bash_command_is_reported_as_an_error():
    from core.handlers.bash import BashHandler

    result = BashHandler().execute({"cmd": "", "description": "noop"})

    assert result.startswith("Error:"), (
        "without the prefix ToolRegistry.execute records ok=True")
    # The keys we did receive are echoed: the usual cause is a wrong alias.
    assert "description" in result


def test_empty_bash_command_does_not_leak_internal_keys():
    from core.handlers.bash import BashHandler

    result = BashHandler().execute({"_secret_env": {"TOKEN": "x"}})

    assert result.startswith("Error:")
    assert "_secret_env" not in result
    assert "TOKEN" not in result


# -- _unwrap_json: undecodable arguments raise instead of emptying -----

def test_unwrap_json_passes_a_dict_through_untouched():
    d = {"path": "/tmp/x"}
    assert BaseFsHandler._unwrap_json(d) is d


def test_unwrap_json_decodes_a_json_string():
    assert BaseFsHandler._unwrap_json('{"path": "/tmp/x"}') == {"path": "/tmp/x"}


def test_unwrap_json_still_repairs_what_the_canonical_parser_repairs():
    # Truncated at EOF - the canonical parser autocloses it; the old inline
    # loop returned {}.
    assert BaseFsHandler._unwrap_json('{"command": "echo hi') == {
        "command": "echo hi"}


def test_unwrap_json_raises_on_genuinely_undecodable_arguments():
    with pytest.raises(ToolArgumentError) as exc:
        BaseFsHandler._unwrap_json("{bad json")
    assert "failed to decode tool arguments" in str(exc.value)


# -- the registry turns that into one clean diagnostic ----------------

class _Undecodable(ToolHandler):
    @property
    def name(self):
        return "undecodable"

    @property
    def description(self):
        return "raises ToolArgumentError like a filesystem handler would"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments):
        return BaseFsHandler._unwrap_json("{bad json")


def test_registry_returns_the_diagnostic_without_double_prefixing():
    reg = ToolRegistry()
    reg.register(_Undecodable())

    result = reg.execute("undecodable", {})

    assert result.startswith("Error: failed to decode tool arguments")
    assert "Error executing tool" not in result, "no second prefix"
    # The position window the model needs to fix its own output survives.
    assert "Window around char" in result or "Raw arguments" in result


# -- CC aliases must not mutate the caller's dict ---------------------

class _Recorder(ToolHandler):
    def __init__(self):
        self.received = None

    @property
    def name(self):
        return "recorder"

    @property
    def description(self):
        return "records the arguments it was handed"

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        }

    def execute(self, arguments):
        self.received = arguments
        return "ok"


def test_cc_alias_rewrite_leaves_the_callers_dict_intact():
    reg = ToolRegistry()
    handler = _Recorder()
    reg.register(handler)

    caller_args = {"file_path": "/tmp/foo.txt"}
    reg.execute("recorder", caller_args)

    # The handler sees the PawFlow spelling...
    assert handler.received["path"] == "/tmp/foo.txt"
    assert "file_path" not in handler.received
    # ...and the caller's dict is untouched, so the approved call, the
    # post_tool_call hook and the transcript still agree.
    assert caller_args == {"file_path": "/tmp/foo.txt"}


def test_no_alias_no_copy_and_no_mutation():
    reg = ToolRegistry()
    handler = _Recorder()
    reg.register(handler)

    caller_args = {"path": "/tmp/foo.txt"}
    reg.execute("recorder", caller_args)

    assert caller_args == {"path": "/tmp/foo.txt"}
    assert handler.received["path"] == "/tmp/foo.txt"
