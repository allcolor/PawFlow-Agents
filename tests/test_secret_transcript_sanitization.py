import json

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
