"""Shared filters for Claude Code interactive transcript observations."""

from __future__ import annotations

import json
import re


_USE_TOOL_WRAPPERS = {
    "mcp__pawflow__use_tool", "mcp__pawflow__.use_tool",
    "mcp_pawflow_use_tool", "mcp_pawflow.use_tool",
    "pawflow.use_tool", "pawflow/use_tool", "use_tool",
}
_SCHEMA_WRAPPERS = {
    "mcp__pawflow__get_tool_schema",
    "mcp__pawflow__.get_tool_schema",
    "mcp_pawflow_get_tool_schema",
    "mcp_pawflow.get_tool_schema",
    "pawflow.get_tool_schema",
    "get_tool_schema",
}


def _loads_tolerant_str(raw: str) -> dict:
    """Best-effort parse of an EOF-truncated tool-input JSON string.

    A use_tool wrapper carries the real tool input doubly-encoded in the
    `arguments_json` STRING. When the observed CCI stream is cut at EOF, the
    provider recovers the OUTER wrapper, but this inner string can still be
    truncated — strict json.loads then drops the args to {} and the call
    renders with empty parens (worst for large inputs like a multi-line bash
    `command`). Mirror the provider's outer recovery here so the inner input
    is recovered too. Lazy import keeps the dependency-light runtime/proxy
    copies degrading gracefully to {} when core is not importable.
    """
    try:
        from core.tool_json import parse_tool_arguments, tool_argument_parse_error
    except Exception:
        return {}
    parsed = parse_tool_arguments(raw, tool_name="cci-display", provider="cci")
    if isinstance(parsed, dict) and not tool_argument_parse_error(parsed):
        return parsed
    return {}


def _json_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return _loads_tolerant_str(value)
    return value if isinstance(value, dict) else {}


def normalize_observed_tool(name: str, args) -> tuple[str, dict]:
    """Return the PawFlow tool name/args users should see for CCI events."""
    raw_name = name or ""
    tool_args = _json_dict(args)
    if raw_name in _USE_TOOL_WRAPPERS:
        if "tool_name" not in tool_args and isinstance(tool_args.get("parameters"), dict):
            tool_args = tool_args["parameters"]
        inner_name = str(tool_args.get("tool_name") or raw_name)
        # Source order mirrors the MCP bridge reader: the advertised string
        # `arguments_json` first (CCI sends it; _json_dict decodes the string),
        # then a legacy `arguments`/`parameters` object. Without this, CCI args
        # (now carried in arguments_json) render as empty parens.
        inner_raw = tool_args.get("arguments_json")
        if inner_raw is None or inner_raw == "":
            inner_raw = tool_args.get("arguments", tool_args.get("parameters", {}))
        inner_args = _json_dict(inner_raw)
        return inner_name, inner_args
    if raw_name in _SCHEMA_WRAPPERS:
        return "get_tool_schema", tool_args
    return raw_name, tool_args


# A code-mode harness (GPT-5.x "sol") does not call tools directly: it runs
# JavaScript in a freeform `exec` tool and calls everything from inside it,
# `tools.exec_command({...})` for its own shell as much as
# `tools.mcp__pawflow__use_tool({...})` for PawFlow.
_CODE_MODE_TOOL_RE = re.compile(r"\btools\.([A-Za-z_$][\w$]*)\s*\(")


def code_mode_body(args) -> bool:
    """True when a call is a code body that drives tools from inside it.

    Such a body is not a tool call to render: one opaque ``exec`` row wearing
    a native badge stands for every tool it ran. Which tools those are cannot
    be read out of the source — property shorthand, variables, loops and
    ``.filter()`` are ordinary code-mode, not corner cases — and PawFlow does
    not have to read it: it executes each of those calls itself through the
    MCP bridge, and reports them from there with their real name, arguments
    and result.
    """
    source = _json_dict(args).get("input")
    return isinstance(source, str) and bool(_CODE_MODE_TOOL_RE.search(source))


# Item fields that carry a call's arguments, in the order the Responses
# taxonomy uses them: `arguments` for a function call, `action` for the
# local shell and the computer tool, `input` for a freeform custom tool.
_CALL_ARG_KEYS = ("arguments", "action", "input")
_CALL_META_KEYS = {"id", "call_id", "type", "status", "name", "server_label"}


def observed_call_ids(item) -> tuple:
    """Return every id a call item can be paired on, pairing id first.

    A Responses call item carries two: ``call_id``, the one its matching
    ``*_call_output`` quotes, and ``id``, the item's own. An observer that
    keys on one while another keys on the other describes a single call
    twice — the user sees the row duplicated and the result lands on only
    one of the two. Carrying both lets either id find the same block.
    """
    if not isinstance(item, dict):
        return ()
    ids = []
    for key in ("call_id", "id"):
        value = str(item.get(key) or "")
        if value and value not in ids:
            ids.append(value)
    return tuple(ids)


def observed_call_item(item) -> tuple:
    """Return ``(call_id, name, args)`` for any Responses tool-call item.

    Codex does not only emit ``function_call``. Its own shell arrives as a
    ``local_shell_call`` carrying an ``action`` object, a freeform tool such as
    ``apply_patch`` as a ``custom_tool_call`` carrying an ``input`` string, and
    hosted tools as ``web_search_call`` and friends. Matching the ``_call``
    suffix rather than one literal keeps every call the user watched the model
    run visible: an unknown member of the taxonomy renders under its own type
    instead of vanishing.

    Returns ``()`` when the item is not a call or has no id to key it on.
    """
    if not isinstance(item, dict):
        return ()
    item_type = str(item.get("type") or "")
    if not item_type.endswith("_call"):
        return ()
    ids = observed_call_ids(item)
    if not ids:
        return ()
    call_id = ids[0]
    name = str(item.get("name") or item_type[: -len("_call")])
    for key in _CALL_ARG_KEYS:
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, dict):
            return (call_id, name, value)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("{"):
                return (call_id, name, _json_dict(value))
            # A freeform tool's input is a patch or a command line, not an
            # argument object: keep it under its own field rather than
            # dropping it for failing to parse as JSON.
            return (call_id, name, {key: value} if text else {})
    return (call_id, name,
            {k: v for k, v in item.items() if k not in _CALL_META_KEYS})


def call_output_text(output) -> str:
    """Flatten a call output into the text a human should read.

    An output is not always a string: Codex returns a list of content parts
    (``[{"type": "input_text", "text": ...}, ...]``) for its own tools.
    Serialising that verbatim shows the user the JSON envelope instead of
    the command's output.
    """
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "output", "content"):
            if key in output:
                return call_output_text(output[key])
        return json.dumps(output, ensure_ascii=False)
    if isinstance(output, list):
        return "".join(call_output_text(part) for part in output)
    if output is None:
        return ""
    return json.dumps(output, ensure_ascii=False)


def observed_call_output(item) -> tuple:
    """Return ``(call_id, output text)`` for any Responses call-output item.

    Mirror of :func:`observed_call_item` for the results the client sends back
    on its next request. Returns ``()`` when the item is not one.
    """
    if not isinstance(item, dict):
        return ()
    if not str(item.get("type") or "").endswith("_call_output"):
        return ()
    # Only `call_id` pairs an output with its call: the item's own `id`
    # names the output, so it is not an alias for the call.
    call_id = str(item.get("call_id") or item.get("id") or "")
    if not call_id:
        return ()
    return (call_id, call_output_text(item.get("output", "")))


def observed_tool_origin(name: str) -> str:
    """Classify an observed CCI tool by origin for the UI badge.

    PawFlow tools reach Claude Code through the MCP bridge (the use_tool /
    get_tool_schema wrappers) -> "mcp". Everything else is one of Claude Code's
    own built-in tools -> "native". Mirrors Codex's native/mcp tagging so both
    providers render the same badges. Pass the RAW observed name, before
    normalize_observed_tool unwraps it. A code-mode body never reaches here:
    it renders no row of its own, its calls are reported by the relay that
    executes them, each already carrying its own origin.
    """
    raw_name = name or ""
    if raw_name in _USE_TOOL_WRAPPERS or raw_name in _SCHEMA_WRAPPERS:
        return "mcp"
    return "native"
