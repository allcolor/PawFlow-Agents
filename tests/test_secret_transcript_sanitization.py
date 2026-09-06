import json
import os

import pytest

from core.conversation_store import ConversationStore
from core.segmented_jsonl import SegmentedJsonl
from core.secret_sanitization import strip_secret_runtime_values
from scripts.scrub_conversation_secret_env import scrub


def _leaked_row():
    return {
        "role": "tool_call",
        "arguments": {
            "command": "safe command",
            "_secret_env": {"TOKEN": "CANARY_SECRET"},
            "nested": [{"_secret_env": {"OTHER": "CANARY_NESTED"}}],
        },
    }


def test_clean_values_keep_identity_on_the_hot_read_path():
    row = {"role": "user", "content": ["already", "safe"]}

    assert strip_secret_runtime_values(row) is row


def test_segmented_jsonl_strips_runtime_secrets_on_write_and_read(tmp_path):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_dicts([_leaked_row()])

    stored = list(log._iter_file(log.iter_paths()[0]))
    assert "_secret_env" not in json.dumps(stored)
    assert "CANARY_SECRET" not in json.dumps(stored)
    assert list(log.iter_rows())[0]["arguments"]["command"] == "safe command"


def test_existing_segment_is_hidden_then_physically_scrubbed(tmp_path):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_dicts([{"role": "user", "content": "seed"}])
    segment = log.iter_paths()[0]
    segment.write_text(json.dumps(_leaked_row()) + "\n", encoding="utf-8")

    visible = list(log.iter_rows())
    assert "_secret_env" not in json.dumps(visible)
    changed_rows, removed_keys = log.scrub_secret_runtime_values()

    assert (changed_rows, removed_keys) == (1, 2)
    raw = list(log._iter_file(segment))
    assert "_secret_env" not in json.dumps(raw)
    assert "CANARY_SECRET" not in json.dumps(raw)
    assert log.scrub_secret_runtime_values() == (0, 0)


def test_patch_and_truncate_cannot_reemit_or_rewrite_legacy_secret(tmp_path):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl", max_rows=10)
    leaked = _leaked_row()
    leaked.update({"msg_id": "m1", "content": "before"})
    log.append_dicts([{"msg_id": "seed", "role": "user", "content": "seed"}])
    segment = log.iter_paths()[0]
    segment.write_text(json.dumps(leaked) + "\n", encoding="utf-8")

    patched = log.patch_first_by_msg_id(
        "m1", {"content": "after", "_secret_env": {"NEW": "CANARY_NEW"}})

    assert patched["content"] == "after"
    assert "_secret_env" not in json.dumps(patched)
    truncated = log.truncate_after_msg_id("m1")
    assert "_secret_env" not in json.dumps(truncated)
    raw = list(log._iter_file(segment))
    assert "_secret_env" not in json.dumps(raw)


def test_scrub_cleans_transcript_shared_and_agent_context(tmp_path):
    root = tmp_path / "conversations"
    conv = root / "alice" / "conv1"
    for path in (
        conv / "transcript.jsonl",
        conv / "shared.jsonl",
        conv / "assistant" / "context.jsonl",
    ):
        log = SegmentedJsonl(path)
        log.append_dicts([{"role": "user", "content": "seed"}])
        segment = log.iter_paths()[0]
        segment.write_text(json.dumps(_leaked_row()) + "\n", encoding="utf-8")

    totals = scrub(root)

    assert totals.changed_streams == 3
    assert totals.changed_rows == 3
    assert totals.removed_keys == 6
    assert totals.errors == 0
    for path in (
        conv / "transcript.jsonl",
        conv / "shared.jsonl",
        conv / "assistant" / "context.jsonl",
    ):
        log = SegmentedJsonl(path)
        raw = list(log._iter_file(log.iter_paths()[0]))
        assert "_secret_env" not in json.dumps(raw)


def test_scrub_scopes_owner_and_excludes_active_conversation(tmp_path):
    root = tmp_path / "conversations"
    paths = {
        "target": root / "alice" / "old" / "transcript.jsonl",
        "active": root / "alice" / "active" / "transcript.jsonl",
        "other": root / "bob" / "old" / "transcript.jsonl",
    }
    for path in paths.values():
        log = SegmentedJsonl(path)
        log.append_dicts([{"role": "user", "content": "seed"}])
        log.iter_paths()[0].write_text(
            json.dumps(_leaked_row()) + "\n", encoding="utf-8")

    totals = scrub(
        root, user_id="alice", exclude_conversation_id="active")

    assert totals.conversations == 1
    assert totals.changed_rows == 1
    target_raw = list(SegmentedJsonl(paths["target"])._iter_file(
        SegmentedJsonl(paths["target"]).iter_paths()[0]))
    assert "_secret_env" not in json.dumps(target_raw)
    for key in ("active", "other"):
        log = SegmentedJsonl(paths[key])
        raw = list(log._iter_file(log.iter_paths()[0]))
        assert "_secret_env" in json.dumps(raw)


def test_conversation_first_load_physically_scrubs_all_content_streams(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = "conv1"
    store.save(cid, [], user_id="alice")
    paths = (
        store._transcript_path(cid),
        store._shared_ctx_path(cid),
        store._agent_ctx_path(cid, "assistant"),
    )
    for path in paths:
        log = SegmentedJsonl(path)
        log.append_dicts([{"role": "user", "content": "seed"}])
        log.iter_paths()[0].write_text(
            json.dumps(_leaked_row()) + "\n", encoding="utf-8")

    restarted = ConversationStore(store_dir=str(tmp_path / "conversations"))
    assert restarted.load(cid, user_id="alice")

    for path in paths:
        log = SegmentedJsonl(path)
        raw = list(log._iter_file(log.iter_paths()[0]))
        assert "_secret_env" not in json.dumps(raw)


def test_scrub_completion_survives_restart_without_reading_unchanged_rows(
        tmp_path, monkeypatch):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl", max_rows=1)
    log.append_dicts([{"role": "user", "content": str(i)} for i in range(3)])
    assert log.scrub_secret_runtime_values() == (0, 0)

    restarted = SegmentedJsonl(log.flat_path)
    monkeypatch.setattr(restarted, "_iter_file", lambda path: pytest.fail(
        "unchanged history was scanned again"))
    assert restarted.scrub_secret_runtime_values() == (0, 0)


def test_scrub_only_scans_segments_changed_since_completion(tmp_path, monkeypatch):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl", max_rows=1)
    log.append_dicts([{"role": "user", "content": str(i)} for i in range(3)])
    log.scrub_secret_runtime_values()
    log.append_dicts([{"role": "user", "content": "new"}])
    paths = log.iter_paths()
    read_paths = []
    read_file = log._iter_file

    def record(path):
        read_paths.append(path)
        return read_file(path)

    monkeypatch.setattr(log, "_iter_file", record)
    assert log.scrub_secret_runtime_values() == (0, 0)
    assert read_paths == [paths[-1]]


def test_scrub_rechecks_replaced_segment_even_with_same_size_and_mtime(tmp_path):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    leaked_text = json.dumps(_leaked_row()) + "\n"
    safe_text = leaked_text.replace("_secret_env", "_public_env")
    log.append_lines([safe_text])
    log.scrub_secret_runtime_values()
    segment = log.iter_paths()[0]
    previous = segment.stat()
    replacement = segment.with_suffix(".restored")
    replacement.write_text(leaked_text, encoding="utf-8")
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    replacement.replace(segment)

    assert segment.stat().st_size == previous.st_size
    assert segment.stat().st_mtime_ns == previous.st_mtime_ns
    assert SegmentedJsonl(log.flat_path).scrub_secret_runtime_values() == (1, 2)
    assert "_secret_env" not in segment.read_text(encoding="utf-8")


@pytest.mark.parametrize("marker", ["broken json", "[]", '{"version": 0}'])
def test_scrub_rechecks_invalid_or_obsolete_completion(tmp_path, monkeypatch, marker):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_dicts([{"role": "user", "content": "safe"}])
    log.scrub_secret_runtime_values()
    (log.segment_dir / "secret_scrub.json").write_text(marker, encoding="utf-8")
    read_paths = []
    read_file = log._iter_file

    def record(path):
        read_paths.append(path)
        return read_file(path)

    monkeypatch.setattr(log, "_iter_file", record)
    assert log.scrub_secret_runtime_values() == (0, 0)
    assert read_paths == log.iter_paths()


def test_scrub_ciphertext_without_codec_does_not_certify_decrypted_content(tmp_path):
    from core.conversation_cipher import RowCodec

    codec = RowCodec(b"k" * 32)
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_lines([json.dumps(codec.encode(_leaked_row()))])
    assert log.scrub_secret_runtime_values() == (0, 0)

    unlocked = SegmentedJsonl(log.flat_path, codec=codec)
    assert unlocked.scrub_secret_runtime_values() == (1, 2)
    raw = list(unlocked._iter_file(unlocked.iter_paths()[0]))
    assert "_secret_env" not in json.dumps(codec.decode(raw[0]))


def test_locked_conversation_defers_scrub_until_unlocked(tmp_path, monkeypatch):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    store.save("conv1", [], user_id="alice")
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(store, "_is_encryption_enabled", lambda cid: True)
    monkeypatch.setattr(store, "_codec_for", lambda cid: None)
    assert store._scrub_persisted_secret_runtime_values("conv1") is False
    assert "conv1" not in store._secret_runtime_scrubbed


def test_history_restore_invalidates_process_scrub_completion(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    store.save("conv1", [], user_id="alice")
    store._scrub_persisted_secret_runtime_values("conv1")
    assert "conv1" in store._secret_runtime_scrubbed
    store._reset_jsonl_runtime_after_history_change("conv1")
    assert "conv1" not in store._secret_runtime_scrubbed


@pytest.mark.parametrize("cleanup_denied", [False, True])
def test_failed_marker_save_leaves_cleanup_retryable(tmp_path, monkeypatch, cleanup_denied):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_lines([json.dumps(_leaked_row())])
    replace = log._replace_path
    if cleanup_denied:
        from pathlib import Path
        unlink = Path.unlink

        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith("secret_scrub.json.") and path.suffix == ".tmp":
                raise PermissionError("test marker cleanup failure")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_cleanup)

    def fail_marker(src, dst):
        if dst.name == "secret_scrub.json":
            raise OSError("test marker write failure")
        return replace(src, dst)

    monkeypatch.setattr(log, "_replace_path", fail_marker)
    assert log.scrub_secret_runtime_values() == (1, 2)
    assert not (log.segment_dir / "secret_scrub.json").exists()
    assert bool(list(log.segment_dir.glob("*.tmp"))) is cleanup_denied
    assert "_secret_env" not in log.iter_paths()[0].read_text(encoding="utf-8")
    restarted = SegmentedJsonl(log.flat_path)
    read_file = restarted._iter_file
    scanned = []

    def record(path):
        scanned.append(path)
        return read_file(path)

    monkeypatch.setattr(restarted, "_iter_file", record)
    assert restarted.scrub_secret_runtime_values() == (0, 0)
    assert scanned == restarted.iter_paths()


def test_scrub_refuses_to_overwrite_a_concurrently_changed_segment(tmp_path, monkeypatch):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl")
    log.append_lines([json.dumps(_leaked_row())])
    segment = log.iter_paths()[0]
    read_file = log._iter_file

    def read_then_append(path):
        yield from read_file(path)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"role": "user", "content": "concurrent"}) + "\n")

    monkeypatch.setattr(log, "_iter_file", read_then_append)
    with pytest.raises(RuntimeError, match="Segment changed"):
        log.scrub_secret_runtime_values()
    assert "concurrent" in segment.read_text(encoding="utf-8")
    assert not (log.segment_dir / "secret_scrub.json").exists()
