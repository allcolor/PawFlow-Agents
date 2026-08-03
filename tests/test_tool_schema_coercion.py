"""Argument values must match their declared type before a handler sees them.

parse_tool_arguments repairs the argument BLOB - whether the text is JSON at
all. Nothing repaired an individual FIELD, so `edits` sent as a JSON *string*
parsed fine, passed the required-argument check, and only failed deep inside
validate_object_list with "got str" - which the model reads as "encode it
harder", producing the triple encoding.

The coercion is deliberately narrow: unambiguous fixes only, an explicit error
where a fix would require guessing, and never a change to a value that already
matches its type.
"""

import pytest

from core.tool_handler import ToolHandler
from core.tool_json import coerce_to_schema
from core.tool_registry import ToolRegistry


def _schema(props, required=None):
    return {"type": "object", "properties": props,
            "required": list(required or [])}


# -- the invariant that protects everything else ----------------------

def test_conforming_arguments_are_returned_as_the_same_object():
    schema = _schema({
        "path": {"type": "string"},
        "limit": {"type": "integer"},
        "deep": {"type": "boolean"},
        "edits": {"type": "array"},
        "opts": {"type": "object"},
        "ratio": {"type": "number"},
    })
    args = {"path": "/tmp/x", "limit": 5, "deep": False,
            "edits": [{"a": 1}], "opts": {"k": "v"}, "ratio": 1.5}

    out, repairs, error = coerce_to_schema(args, schema)

    assert error == ""
    assert repairs == []
    assert out is args, "a conforming payload must not even be copied"


def test_keys_absent_from_the_schema_are_left_alone():
    # The registry rejects unknown arguments separately, with the valid list.
    args = {"path": "/tmp/x", "mystery": "[1,2]", "_secret_env": {"T": "x"}}
    out, repairs, error = coerce_to_schema(
        args, _schema({"path": {"type": "string"}}))
    assert (out, repairs, error) == (args, [], "")


def test_untyped_and_union_properties_are_not_interpreted():
    schema = _schema({"any": {}, "either": {"type": ["string", "null"]}})
    args = {"any": "[1]", "either": "[1]"}
    out, repairs, error = coerce_to_schema(args, schema)
    assert (out, repairs, error) == (args, [], "")


# -- the double-encoding case this whole lot exists for ---------------

def test_json_encoded_array_is_decoded():
    schema = _schema({"edits": {"type": "array"}})
    out, repairs, error = coerce_to_schema(
        {"edits": '[{"path": "a.py", "old_string": "x"}]'}, schema)

    assert error == ""
    assert out["edits"] == [{"path": "a.py", "old_string": "x"}]
    assert repairs == ["edits: decoded JSON string to array"]


def test_json_encoded_object_is_decoded():
    out, _, error = coerce_to_schema(
        {"opts": '{"k": "v"}'}, _schema({"opts": {"type": "object"}}))
    assert error == ""
    assert out["opts"] == {"k": "v"}


def test_json_text_that_decodes_to_the_wrong_type_is_refused():
    _, _, error = coerce_to_schema(
        {"edits": '{"not": "a list"}'}, _schema({"edits": {"type": "array"}}))
    assert "decoded to object" in error and "expects array" in error


def test_json_text_that_does_not_parse_is_refused_with_guidance():
    _, _, error = coerce_to_schema(
        {"edits": '[{"path": '}, _schema({"edits": {"type": "array"}}))
    assert "does not parse" in error
    assert "not a string containing it" in error


def test_a_bare_string_is_never_guessed_into_an_array():
    # Handlers own this: normalize_string_list splits "a,b" into two tags.
    # Wrapping it here as ["a,b"] would silently change what the user meant.
    _, repairs, error = coerce_to_schema(
        {"tags": "a,b"}, _schema({"tags": {"type": "array"}}))
    assert repairs == []
    assert "got a string" in error
    assert "not a string containing it" in error


# -- booleans: the bool("false") is True family -----------------------

@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), (" 1 ", True),
    ("false", False), ("False", False), ("0", False),
])
def test_boolean_strings_are_converted(raw, expected):
    out, _, error = coerce_to_schema(
        {"local": raw}, _schema({"local": {"type": "boolean"}}))
    assert error == ""
    assert out["local"] is expected


def test_an_unrecognised_boolean_string_is_refused_not_guessed():
    # _truthy() would answer False for "maybe" - replacing one silent wrong
    # answer with another. An unreadable boolean is an error.
    _, _, error = coerce_to_schema(
        {"local": "maybe"}, _schema({"local": {"type": "boolean"}}))
    assert "must be a boolean" in error


# -- numbers: booleans must never satisfy them ------------------------

def test_integer_strings_are_converted():
    out, _, error = coerce_to_schema(
        {"limit": "50"}, _schema({"limit": {"type": "integer"}}))
    assert error == "" and out["limit"] == 50


def test_whole_floats_become_integers():
    out, _, error = coerce_to_schema(
        {"limit": 50.0}, _schema({"limit": {"type": "integer"}}))
    assert error == "" and out["limit"] == 50


def test_a_fractional_value_is_not_silently_truncated():
    _, _, error = coerce_to_schema(
        {"limit": 50.5}, _schema({"limit": {"type": "integer"}}))
    assert "must be an integer" in error


def test_a_boolean_is_not_accepted_as_an_integer():
    # True is an int in Python; accepting it would make `limit=true` mean 1.
    _, _, error = coerce_to_schema(
        {"limit": True}, _schema({"limit": {"type": "integer"}}))
    assert "must be an integer" in error and "boolean" in error


def test_a_boolean_is_not_accepted_as_a_number():
    _, _, error = coerce_to_schema(
        {"ratio": True}, _schema({"ratio": {"type": "number"}}))
    assert "must be number" in error or "must be a number" in error


def test_an_integer_satisfies_a_number_without_conversion():
    args = {"ratio": 3}
    out, repairs, error = coerce_to_schema(
        args, _schema({"ratio": {"type": "number"}}))
    assert (out, repairs, error) == (args, [], "")


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "NaN"])
def test_non_finite_numbers_are_refused(raw):
    _, _, error = coerce_to_schema(
        {"ratio": raw}, _schema({"ratio": {"type": "number"}}))
    assert "finite" in error or "must be a number" in error


# -- nulls -------------------------------------------------------------

def test_a_null_optional_is_dropped():
    out, repairs, error = coerce_to_schema(
        {"path": "/tmp/x", "limit": None},
        _schema({"path": {"type": "string"},
                 "limit": {"type": "integer"}}, required=["path"]))
    assert error == ""
    assert "limit" not in out
    assert repairs == ["limit: dropped null optional"]


def test_a_null_required_argument_is_an_error():
    _, _, error = coerce_to_schema(
        {"path": None},
        _schema({"path": {"type": "string"}}, required=["path"]))
    assert "required" in error and "cannot be null" in error


# -- bounds ------------------------------------------------------------

def test_an_absurdly_deep_decoded_structure_is_refused():
    deep = "[" * 40 + "]" * 40
    _, _, error = coerce_to_schema(
        {"edits": deep}, _schema({"edits": {"type": "array"}}))
    assert "too deep or too large" in error


# -- wired into the registry ------------------------------------------

class _Recorder(ToolHandler):
    def __init__(self):
        self.received = None

    @property
    def name(self):
        return "recorder"

    @property
    def description(self):
        return "records what it was handed"

    @property
    def parameters_schema(self):
        return _schema({
            "edits": {"type": "array"},
            "deep": {"type": "boolean"},
            "limit": {"type": "integer"},
        }, required=["edits"])

    def execute(self, arguments):
        self.received = arguments
        return "ok"


def test_registry_coerces_before_dispatch():
    reg = ToolRegistry()
    handler = _Recorder()
    reg.register(handler)

    caller = {"edits": '[{"path": "a.py"}]', "deep": "false", "limit": "3"}
    assert reg.execute("recorder", caller) == "ok"

    assert handler.received["edits"] == [{"path": "a.py"}]
    assert handler.received["deep"] is False
    assert handler.received["limit"] == 3
    # The caller's dict is never rewritten in place.
    assert caller["deep"] == "false"


def test_registry_refuses_an_uncoercible_value_before_executing():
    reg = ToolRegistry()
    handler = _Recorder()
    reg.register(handler)

    result = reg.execute("recorder", {"edits": [], "deep": "maybe"})

    assert result.startswith("Error")
    assert "'deep' must be a boolean" in result
    assert "recorder" in result
    assert handler.received is None, "nothing runs on a refused argument"
