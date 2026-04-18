"""Unit tests for LLMClaudeCodeMixin._scrub_legacy_image_placeholders.

Regression guard for the runtime warning:
    'LLMClient' object has no attribute '_scrub_legacy_image_placeholders'
The call site exists in core/llm_providers/claude_code.py::_stream_claude_code
(resume path) but the method was missing. This test locks down its shape.
"""

import json

import pytest

from core.llm_providers.claude_code import LLMClaudeCodeMixin


class _Instance(LLMClaudeCodeMixin):
    """Bare mixin instance, no real __init__ needed for the scrub method."""
    pass


@pytest.fixture
def session_file(tmp_path):
    return tmp_path / "session.jsonl"


def _write_jsonl(path, entries):
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_scrubs_string_content_user(session_file):
    _write_jsonl(session_file, [
        {"type": "user", "message": {"role": "user",
         "content": "hello [image: image_1745678912_2.png] world"}},
    ])
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    assert entry["message"]["content"] == "hello world"


def test_scrubs_array_text_parts_user(session_file):
    _write_jsonl(session_file, [
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "before [image: image_1_3.jpg] after"},
            {"type": "image", "source": {"data": "..."}},
        ]}},
    ])
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    parts = entry["message"]["content"]
    assert parts[0]["text"] == "before after"
    # Non-text parts pass through untouched
    assert parts[1] == {"type": "image", "source": {"data": "..."}}


def test_multiple_placeholders_in_same_field(session_file):
    _write_jsonl(session_file, [
        {"type": "user", "message": {"role": "user",
         "content": "[image: image_1_1.png] a [image: image_2_2.webp] b"}},
    ])
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    # Each placeholder is substituted with a single space, so two adjacent
    # markers collapse to "a" + sp + sp + "b" and strip trims outer space.
    # Only real content + one space between words survives.
    assert entry["message"]["content"] == "a b"


def test_leaves_assistant_messages_alone(session_file):
    # An assistant message that happens to quote the legacy pattern must not
    # be touched — scrub only applies to user-authored text.
    original = {"type": "assistant", "message": {"role": "assistant",
                "content": "I saw [image: image_1_1.png] in your turn."}}
    _write_jsonl(session_file, [original])
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    assert entry == original


def test_preserves_unrelated_text(session_file):
    _write_jsonl(session_file, [
        {"type": "user", "message": {"role": "user",
         "content": "no placeholder here, just regular text"}},
    ])
    _mtime_before = session_file.stat().st_mtime
    # No changes → no rewrite, so mtime should stay put.
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    assert entry["message"]["content"] == "no placeholder here, just regular text"
    assert session_file.stat().st_mtime == _mtime_before


def test_non_user_non_json_lines_pass_through(session_file):
    # Claude Code session files may contain queue-operation control entries
    # and occasional malformed lines. Both should be preserved verbatim.
    session_file.write_text(
        '{"type":"queue-operation","operation":"enqueue"}\n'
        'not-json-garbage\n'
        '{"type":"user","message":{"role":"user","content":"x [image: image_1_1.png] y"}}\n',
        encoding="utf-8",
    )
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    lines = session_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"type":"queue-operation","operation":"enqueue"}'
    assert lines[1] == 'not-json-garbage'
    assert '"content": "x y"' in lines[2] or '"content":"x y"' in lines[2]


def test_missing_file_is_silent(tmp_path):
    # Must not raise on a nonexistent path.
    _Instance()._scrub_legacy_image_placeholders(str(tmp_path / "missing.jsonl"))


def test_ignores_modern_image_refs(session_file):
    # Anything that doesn't match image_<digits>_<digits>.<ext> stays.
    _write_jsonl(session_file, [
        {"type": "user", "message": {"role": "user",
         "content": "see [image: chart.png] and [image: image_abc_1.png]"}},
    ])
    _Instance()._scrub_legacy_image_placeholders(str(session_file))
    [entry] = _read_jsonl(session_file)
    assert entry["message"]["content"] == "see [image: chart.png] and [image: image_abc_1.png]"
