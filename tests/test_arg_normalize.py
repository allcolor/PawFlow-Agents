"""Tests for tolerant tool-arg normalization.

Covers the shapes LLMs actually send when they don't strictly follow
the JSON schema — so handlers accept the call on the first attempt
instead of erroring and forcing a retry.
"""
import pytest

from core.handlers._arg_normalize import (
    normalize_string_list,
    normalize_single_field_object_list,
    validate_object_list,
)


# ── normalize_string_list ──────────────────────────────────────────

def test_string_list_none():
    assert normalize_string_list(None) == []


def test_string_list_list_passthrough():
    assert normalize_string_list(["a", "b"]) == ["a", "b"]


def test_string_list_trims_and_drops_empty():
    assert normalize_string_list([" a ", "", "  ", "b"]) == ["a", "b"]


def test_string_list_single_string_comma():
    assert normalize_string_list("a,b,c") == ["a", "b", "c"]


def test_string_list_single_string_no_sep():
    assert normalize_string_list("just-one") == ["just-one"]


def test_string_list_newline_fallback():
    assert normalize_string_list("a\nb\nc") == ["a", "b", "c"]


def test_string_list_mixed_types():
    assert normalize_string_list([1, "two", 3]) == ["1", "two", "3"]


# ── normalize_single_field_object_list ─────────────────────────────

def test_single_field_none():
    assert normalize_single_field_object_list(None, key="description") == []


def test_single_field_list_of_dict_passthrough():
    data = [{"description": "a"}, {"description": "b"}]
    assert normalize_single_field_object_list(data, key="description") == data


def test_single_field_list_of_str():
    assert normalize_single_field_object_list(
        ["do this", "do that"], key="description"
    ) == [{"description": "do this"}, {"description": "do that"}]


def test_single_field_plain_string_splits_on_newline():
    # The real create_plan failure case: agent sent one string with line breaks
    result = normalize_single_field_object_list(
        "Analyze code\nFix routing\nShip", key="description")
    assert result == [
        {"description": "Analyze code"},
        {"description": "Fix routing"},
        {"description": "Ship"},
    ]


def test_single_field_plain_string_splits_on_comma_when_single_line():
    result = normalize_single_field_object_list(
        "a, b, c", key="description")
    assert result == [{"description": "a"}, {"description": "b"}, {"description": "c"}]


def test_single_field_line_split_false_wraps_whole_string():
    result = normalize_single_field_object_list(
        "whole thing, with commas", key="description", line_split=False)
    assert result == [{"description": "whole thing, with commas"}]


def test_single_field_mixed_list_dict_and_str():
    result = normalize_single_field_object_list(
        [{"description": "a"}, "b", 3], key="description")
    assert result == [
        {"description": "a"},
        {"description": "b"},
        {"description": "3"},
    ]


# ── validate_object_list ───────────────────────────────────────────

def test_validate_none_is_empty():
    out, err = validate_object_list(None, "x", ["a"], "x=[...]")
    assert out == [] and err is None


def test_validate_rejects_non_list():
    out, err = validate_object_list(
        "a string", "edits", ["path"], 'edits=[{...}]')
    assert out is None
    assert err is not None
    assert "must be a list" in err
    assert "str" in err


def test_validate_rejects_list_of_str():
    # Multi-field: can't auto-coerce strings into objects
    out, err = validate_object_list(
        ["foo", "bar"], "edits", ["path", "old_string"], 'edits=[{...}]')
    assert out is None
    assert "is str" in err


def test_validate_missing_required_keys():
    out, err = validate_object_list(
        [{"path": "a.py"}], "edits",
        ["path", "old_string", "new_string"], 'edits=[{...}]')
    assert out is None
    assert "missing" in err
    assert "old_string" in err


def test_validate_passthrough_when_valid():
    data = [{"path": "a", "old_string": "b", "new_string": "c"}]
    out, err = validate_object_list(
        data, "edits", ["path", "old_string", "new_string"], '...')
    assert out == data and err is None


def test_validate_multiple_errors_joined():
    out, err = validate_object_list(
        [{"path": "a"}, "bad", {"path": "c"}],
        "edits", ["path", "old_string"], '...')
    assert out is None
    assert "edits[0] missing" in err
    assert "edits[1] is str" in err
    assert "edits[2] missing" in err
