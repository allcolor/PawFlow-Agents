"""Tests for the offline transcript/context row-format migration."""

import importlib.util
import json
import sys
from pathlib import Path

from core.segmented_jsonl import SegmentedJsonl


SCRIPT_PATH = Path("scripts/migrate_transcript_context_format.py")


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "migrate_transcript_context_format", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _rows(path: Path) -> list[dict]:
    return list(SegmentedJsonl(path).iter_rows())


def test_migrates_provider_turn_rows_in_transcript_and_context(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    legacy = [
        {
            "t": "msg",
            "msg_id": "A",
            "role": "assistant",
            "content": "texte",
            "thinking": "plan",
            "thinking_signature": "sig",
            "tool_calls": [
                {"id": "tc_123", "name": "grep", "arguments": {"pattern": "x"}}
            ],
            "ts": 10.0,
            "seq": 1,
        },
        {
            "t": "msg",
            "msg_id": "R",
            "role": "tool",
            "content": "result",
            "tool_call_id": "tc_123",
            "ts": 11.0,
            "seq": 2,
        },
    ]
    _write_jsonl(conv / "transcript.jsonl", legacy)
    _write_jsonl(conv / "assistant" / "context.jsonl", legacy)

    totals = mod.migrate(root, apply=True)

    assert totals.errors == 0
    assert totals.changed == 2
    for path in (conv / "transcript.jsonl", conv / "assistant" / "context.jsonl"):
        rows = _rows(path)
        assert [row["role"] for row in rows] == [
            "assistant", "thinking", "tool_call", "tool",
        ]
        assert all("t" not in row for row in rows)
        assert "tool_calls" not in rows[0]
        assert "thinking" not in rows[0]

        anchor_id = rows[0]["msg_id"]
        thinking_id = rows[1]["msg_id"]
        tool_call_id = rows[2]["msg_id"]
        result_id = rows[3]["msg_id"]

        assert anchor_id == "A"
        assert thinking_id and thinking_id != anchor_id
        assert tool_call_id and tool_call_id not in {anchor_id, thinking_id}
        assert result_id == "R"
        assert rows[1]["parent_message_id"] == "A"
        assert rows[1]["content"] == "plan"
        assert rows[1]["thinking_signature"] == "sig"
        assert rows[2]["parent_message_id"] == "A"
        assert rows[2]["tool_call_id"] == "tc_123"
        assert rows[2]["tool_name"] == "grep"
        assert rows[2]["arguments"] == {"pattern": "x"}
        assert rows[3]["parent_message_id"] == tool_call_id
        assert rows[3]["tool_call_id"] == "tc_123"

    backups = sorted((conv / "_transcript_context_migration_backup").rglob("*.jsonl"))
    assert {p.name for p in backups} == {"transcript.jsonl", "context.jsonl"}

    second = mod.migrate(root, apply=True)

    assert second.errors == 0
    assert second.changed == 0
    assert {item.status for item in second.files} == {"unchanged"}


def test_dry_run_does_not_modify_stream(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    transcript = conv / "transcript.jsonl"
    _write_jsonl(
        transcript,
        [{"t": "msg", "msg_id": "A", "role": "assistant", "content": "x"}],
    )

    totals = mod.migrate(root, apply=False)

    assert totals.would_change == 1
    assert json.loads(transcript.read_text(encoding="utf-8").splitlines()[0])["t"] == "msg"


def test_already_normalized_tool_chain_is_unchanged(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    rows = [
        {"msg_id": "A", "role": "assistant", "content": "", "ts": 1.0, "seq": 1,
         "conversation_id": "conv1", "user_id": "user1"},
        {"msg_id": "T", "role": "thinking", "content": "plan", "parent_message_id": "A",
         "ts": 1.0, "seq": 2, "conversation_id": "conv1", "user_id": "user1"},
        {"msg_id": "C", "role": "tool_call", "content": "", "parent_message_id": "A",
         "tool_call_id": "tc_123", "ts": 1.0, "seq": 3,
         "conversation_id": "conv1", "user_id": "user1"},
        {"msg_id": "R", "role": "tool", "content": "result", "parent_message_id": "C",
         "tool_call_id": "tc_123", "ts": 2.0, "seq": 4,
         "conversation_id": "conv1", "user_id": "user1"},
    ]
    _write_jsonl(conv / "transcript.jsonl", rows)

    totals = mod.migrate(root, apply=True)

    assert totals.changed == 0
    assert totals.unchanged == 1
    assert _rows(conv / "transcript.jsonl") == rows
