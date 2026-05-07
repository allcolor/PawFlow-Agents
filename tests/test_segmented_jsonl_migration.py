"""Tests for the offline segmented JSONL migration script."""

import importlib.util
import json
import sys
from pathlib import Path

from core.segmented_jsonl import SegmentedJsonl


SCRIPT_PATH = Path("scripts/migrate_segmented_jsonl.py")


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "migrate_segmented_jsonl", SCRIPT_PATH)
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


def test_migration_dry_run_does_not_modify_flat_files(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    transcript = conv / "transcript.jsonl"
    _write_jsonl(transcript, [{"t": "msg", "seq": 1, "content": "hello"}])

    totals = mod.migrate(root, apply=False, max_rows=2)

    assert totals.scanned == 1
    assert totals.migrated == 0
    assert totals.files[0].status == "would_migrate"
    assert transcript.exists()
    assert not (conv / "transcript" / "index.json").exists()


def test_migration_apply_segments_logs_and_backs_up_flat_files(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    _write_jsonl(
        conv / "transcript.jsonl",
        [{"t": "msg", "seq": 1, "content": "hello"}],
    )
    _write_jsonl(
        conv / "assistant" / "context.jsonl",
        [{"role": "user", "seq": 1, "content": "hello"}],
    )

    totals = mod.migrate(root, apply=True, max_rows=1)

    assert totals.errors == 0
    assert totals.migrated == 2
    assert not (conv / "transcript.jsonl").exists()
    assert not (conv / "assistant" / "context.jsonl").exists()
    assert (conv / "transcript" / "index.json").exists()
    assert (conv / "assistant" / "context" / "index.json").exists()
    assert [r["content"] for r in SegmentedJsonl(conv / "transcript.jsonl").iter_rows()] == ["hello"]
    backups = sorted((conv / "_jsonl_migration_backup").rglob("*.jsonl"))
    assert {p.name for p in backups} == {"transcript.jsonl", "context.jsonl"}

    second = mod.migrate(root, apply=True, max_rows=1)

    assert second.errors == 0
    assert second.migrated == 0
    assert {item.status for item in second.files} == {"already_segmented"}


def test_migration_reports_invalid_json_without_removing_flat_file(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    transcript = conv / "transcript.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")

    totals = mod.migrate(root, apply=True, max_rows=2)

    assert totals.errors == 1
    assert totals.files[0].status == "error"
    assert "invalid JSON" in totals.files[0].error
    assert transcript.exists()
    assert not (conv / "transcript" / "index.json").exists()
    assert not (conv / "transcript" / "index.json").exists()


def test_migration_reports_mixed_flat_and_segment_layout(tmp_path):
    mod = _load_script()
    root = tmp_path / "conversations"
    conv = root / "user1" / "conv1"
    _write_jsonl(conv / "transcript.jsonl", [{"seq": 1, "content": "flat"}])
    SegmentedJsonl(conv / "transcript.jsonl").replace_dicts(
        [{"seq": 1, "content": "segmented"}])
    _write_jsonl(conv / "transcript.jsonl", [{"seq": 2, "content": "flat again"}])

    totals = mod.migrate(root, apply=True, max_rows=2)

    assert totals.errors == 1
    assert totals.files[0].status == "skipped_mixed_layout"
    assert "both flat file and segment directory exist" in totals.files[0].error
