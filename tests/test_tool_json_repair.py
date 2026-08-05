"""Last-resort JSON repair for near-valid LLM tool params.

The repair must ONLY fire after strict parsing fails and must never alter
an already-valid payload (see core.tool_json.repair_invalid_json_escapes).
"""

import json

from core.tool_json import (
    repair_invalid_json_escapes,
    parse_tool_arguments,
    PARSE_ERROR_KEY,
)

BS = chr(92)    # backslash
SQ = chr(39)    # single quote
NL = chr(10)    # newline
Q = chr(34)     # double quote


def _obj(inner_value):
    # Build {"p": "<inner_value>"} as raw text without literal backslashes.
    return "{" + Q + "p" + Q + ":" + Q + inner_value + Q + "}"


def test_repair_fixes_invalid_single_quote_escape():
    bad = _obj("a" + BS + SQ + "b")          # {"p": "a\'b"}  (invalid)
    assert json.loads(repair_invalid_json_escapes(bad)) == {"p": "a" + SQ + "b"}


def test_repair_fixes_raw_control_char():
    bad = _obj("a" + NL + "b")               # raw newline inside string
    assert json.loads(repair_invalid_json_escapes(bad)) == {"p": "a" + NL + "b"}


def test_repair_leaves_valid_json_unchanged():
    good = _obj("a" + BS + "nb")             # {"p": "a\nb"}  (valid escape)
    assert repair_invalid_json_escapes(good) == good
    plain = '{"a": "b", "n": 5}'
    assert repair_invalid_json_escapes(plain) == plain


def test_repair_preserves_literal_backslash_path():
    # {"p": "c:\\x"} already valid -> unchanged
    good = _obj("c:" + BS + BS + "x")
    assert repair_invalid_json_escapes(good) == good
    assert json.loads(good)["p"] == "c:" + BS + "x"


def test_parse_tool_arguments_recovers_invalid_escape():
    bad = _obj("a" + BS + SQ + "b")
    out = parse_tool_arguments(bad, tool_name="edit", provider="cc")
    assert PARSE_ERROR_KEY not in out
    assert out == {"p": "a" + SQ + "b"}


def test_parse_tool_arguments_valid_unaffected():
    out = parse_tool_arguments('{"x": 1}', tool_name="t")
    assert out == {"x": 1}


def test_parse_tool_arguments_recovers_bare_quotes_in_command():
    # Shell command embedding its own quoting: {"command": "... grep -n
    # "pattern" file ..."} — the inner double quotes are unescaped and
    # previously killed the whole payload ("Expecting ',' delimiter").
    bad = '{"command": "cd /workspace && grep -n \"pattern\" file.py"}'
    bad = bad.replace('\\"', '"')  # strip escapes: emit them BARE like an LLM
    out = parse_tool_arguments(bad, tool_name="bash", provider="cc")
    assert PARSE_ERROR_KEY not in out
    assert out == {"command": 'cd /workspace && grep -n "pattern" file.py'}


def test_parse_tool_arguments_recovers_mixed_bare_quotes_and_escapes():
    # Real server-log case: the LLM mixed invalid \' escapes, bare " quotes
    # and double-escaped pipes in one bash command — previously the whole
    # payload failed as [unwrap] with "Expecting ',' delimiter".
    cmd = (
        "cd /workspace && echo " + BS + SQ + "===x===" + BS + SQ
        + " && grep -n " + Q + "pat" + BS + BS + "|" + BS + BS + "pat2"
        + Q + " f.py | head"
    )
    bad = "{" + Q + "command" + Q + ": " + Q + cmd + Q + "}"
    out = parse_tool_arguments(bad, tool_name="use_tool", provider="unwrap")
    assert PARSE_ERROR_KEY not in out
    assert out["command"].startswith("cd /workspace && echo '===x==='")
    assert Q + "pat" + BS + "|" + BS + "pat2" + Q in out["command"]


def test_parse_tool_arguments_recovers_bare_quotes_with_braces():
    # A command embedding a JSON-ish literal: echo '{"a": 1}' unescaped.
    bad = "{\"command\": \"echo '{\"a\": 1}'\"}"
    bad = bad.replace('\\"', '"').replace("\\'", "'")
    out = parse_tool_arguments(bad, tool_name="bash", provider="cc")
    assert PARSE_ERROR_KEY not in out
    assert out == {"command": "echo '{\"a\": 1}'"}


def test_parse_tool_arguments_escaped_quotes_unaffected():
    # Properly escaped quotes never reach the repair path.
    out = parse_tool_arguments(
        '{"command": "grep -n \\"p\\" f"}', tool_name="bash")
    assert out == {"command": 'grep -n "p" f'}


def test_parse_tool_arguments_truly_broken_still_errors():
    out = parse_tool_arguments('{"x": ', tool_name="t")
    assert PARSE_ERROR_KEY in out
