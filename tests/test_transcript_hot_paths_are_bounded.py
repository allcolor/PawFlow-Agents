"""Hot transcript paths decode a page, not the conversation.

On a 670k-message conversation (2026-09-29) one pass over the transcript cost
45-60 s of CPU, and several paths made one on every call: the idempotent
ingress, the context-usage repair run on every message_meta, the search index
refresh after any metadata patch, and read_history's range, oldest, filtered
recent and around-by-msg_id. Each test counts the JSON rows a call decodes
and fails if that number follows the conversation instead of the answer.
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.conversation_store import ConversationStore
from core.handlers.history import ReadHistoryHandler
from core.segmented_jsonl import SegmentedJsonl

CONV = "hot"
CONV_SIZE = 5000
SEGMENT_ROWS = 500


def _small_segments(self, cid):
    return SegmentedJsonl(self._transcript_path(cid), max_rows=SEGMENT_ROWS,
                          codec=self._codec_for(cid))


class _Decoded:
    """Count json.loads calls (one per decoded row) inside a block."""

    def __init__(self):
        self.count = 0
        self._real = json.loads

    def __enter__(self):
        def counting(*args, **kwargs):
            self.count += 1
            return self._real(*args, **kwargs)
        self._patch = patch("json.loads", counting)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class HotPaths(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        self._segments = patch.object(
            ConversationStore, "_transcript_log", _small_segments)
        self._segments.start()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message number {i}",
             "msg_id": f"m{i:05d}",
             "source": ({"type": "user", "name": "owner", "target_agent": "A"}
                        if i % 2 == 0 else {"type": "agent", "name": "A"}),
             "seq": i + 1,
             "ts": 1000.0 + i}
            for i in range(CONV_SIZE)
        ]
        store.save(CONV, messages, ttl=3600, user_id="owner")
        self.store = store
        self.assertGreater(len(store._transcript_log(CONV).iter_paths()), 5)
        self.handler = ReadHistoryHandler()
        self.handler.set_conversation_id(CONV)

    def tearDown(self):
        self._segments.stop()
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def assertBounded(self, decoded, what, cap=3 * SEGMENT_ROWS):
        self.assertLess(decoded, cap,
                        f"{what} decoded {decoded} rows of {CONV_SIZE}")

    # -- idempotent ingress --------------------------------------------------

    def _user(self, msg_id, content="hello"):
        return {"role": "user", "content": content, "msg_id": msg_id,
                "source": {"type": "user", "name": "owner",
                           "target_agent": "A"}}

    def test_idempotent_append_finds_a_duplicate_without_a_full_load(self):
        existing = self.store.load(CONV)[-2]  # a user row near the tail
        with patch.object(ConversationStore, "load",
                          side_effect=AssertionError("full load")), \
                _Decoded() as decoded:
            inserted = self.store.append_message_if_absent(
                CONV, dict(existing), user_id="owner")
        self.assertFalse(inserted)
        self.assertBounded(decoded.count, "duplicate check")

    def test_idempotent_append_still_refuses_a_different_payload(self):
        from core._conversation_store_append import MessageIdempotencyConflict
        with self.assertRaises(MessageIdempotencyConflict):
            self.store.append_message_if_absent(
                CONV, self._user("m00004", "not the same"), user_id="owner")

    def test_idempotent_append_writes_a_new_id_once(self):
        self.assertTrue(self.store.append_message_if_absent(
            CONV, self._user("fresh-1"), user_id="owner"))
        self.assertFalse(self.store.append_message_if_absent(
            CONV, self._user("fresh-1"), user_id="owner"))

    # -- read_history ----------------------------------------------------------

    def test_around_a_msg_id_parses_one_segment(self):
        with _Decoded() as decoded:
            index = self.store.find_display_index(CONV, msg_id="m04900")
        self.assertEqual(index, 4900)
        self.assertBounded(decoded.count, "find_display_index(msg_id)",
                           cap=SEGMENT_ROWS + 50)

    def test_filtered_recent_reads_backwards_and_stops(self):
        with _Decoded() as decoded:
            out = self.handler.execute(
                {"action": "recent", "role_filter": "user", "limit": 5})
        self.assertIn("[#4998]", out)
        self.assertIn("More older", out)
        self.assertBounded(decoded.count, "filtered recent",
                           cap=self.store._WINDOW_CHUNK + 2 * SEGMENT_ROWS)

    def test_filtered_recent_pages_backwards(self):
        first = self.handler.execute(
            {"action": "recent", "role_filter": "user", "limit": 3})
        second = self.handler.execute(
            {"action": "recent", "role_filter": "user", "limit": 3,
             "offset": 3})
        self.assertIn("[#4994]", first)
        self.assertIn("[#4998]", first)
        self.assertIn("[#4988]", second)
        self.assertIn("[#4992]", second)
        self.assertNotIn("[#4994]", second)

    def test_range_stops_at_its_closing_id(self):
        with _Decoded() as decoded:
            out = self.handler.execute({"action": "range",
                                        "from_msg_id": "m00010",
                                        "to_msg_id": "m00020"})
        self.assertNotIn("Error", out)
        self.assertBounded(decoded.count, "range",
                           cap=self.store._WINDOW_CHUNK + SEGMENT_ROWS)

    def test_unfiltered_oldest_reads_the_head(self):
        with _Decoded() as decoded:
            out = self.handler.execute({"action": "oldest", "limit": 5})
        self.assertIn("[#0]", out)
        self.assertIn(f"of {CONV_SIZE}", out)
        self.assertBounded(decoded.count, "oldest", cap=SEGMENT_ROWS + 50)

    # The covering segment, its two neighbours and the open tail segment.
    RANGE_CAP = 4 * SEGMENT_ROWS + 1

    def _full_pass(self, arguments):
        with patch.object(ConversationStore, "iter_display_range_windows",
                          return_value=None):
            return self.handler.execute(arguments)

    def test_range_by_date_decodes_only_the_segments_it_covers(self):
        arguments = {"action": "range_by_date",
                     "from_date": "1970-01-01T00:43:30+00:00",
                     "to_date": "1970-01-01T00:43:40+00:00",
                     "role_filter": "user"}
        with _Decoded() as decoded:
            out = self.handler.execute(arguments)
        self.assertIn("[#1610]", out)
        self.assertIn("[#1620]", out)
        self.assertNotIn("[#1622]", out)
        self.assertEqual(out, self._full_pass(arguments))
        self.assertBounded(decoded.count, "range_by_date",
                           cap=self.RANGE_CAP)

    def test_range_by_seq_decodes_only_the_segments_it_covers(self):
        arguments = {"action": "range_by_seq", "from_seq": 2601,
                     "to_seq": 2605, "agent_filter": "A"}
        with _Decoded() as decoded:
            out = self.handler.execute(arguments)
        self.assertIn("[#2600]", out)
        self.assertIn("[#2604]", out)
        self.assertEqual(out, self._full_pass(arguments))
        self.assertBounded(decoded.count, "range_by_seq",
                           cap=self.RANGE_CAP)

    def test_range_in_the_open_tail_segment(self):
        arguments = {"action": "range_by_seq", "from_seq": 4998,
                     "to_seq": 5000}
        out = self.handler.execute(arguments)
        self.assertIn("[#4999]", out)
        self.assertEqual(out, self._full_pass(arguments))

    def test_segment_bounds_are_cached_and_a_rewrite_rescans(self):
        from core._segmented_jsonl_io import _SegmentedJsonlIOMixin
        log = self.store._transcript_log(CONV)
        first = log.field_bounds_by_path()
        self.assertEqual(len(first), len(log.iter_paths()) - 1)
        with patch.object(_SegmentedJsonlIOMixin, "_scan_field_bounds",
                          side_effect=AssertionError("rescanned")):
            self.assertEqual(log.field_bounds_by_path(), first)
        # Rewriting a sealed segment changes its identity: its bounds are
        # read again, and a row moved out of the old range is still found.
        self.store.patch_message(CONV, "m00003", ts=9_000_000.0)
        out = self.handler.execute({"action": "range_by_date",
                                    "from_date": "1970-04-15T00:00:00+00:00",
                                    "to_date": "1970-04-16T00:00:00+00:00"})
        self.assertIn("[#3]", out)

    def test_bucket_char_sum_decodes_only_the_segments_it_covers(self):
        from core._bg_bucket_build import _BgBucketBuildMixin
        path = self.store._transcript_path(CONV)
        expected = sum(len(r["content"])
                       for r in SegmentedJsonl(path).iter_rows()
                       if 2601 <= int(r.get("seq") or 0) <= 2645)
        with _Decoded() as decoded:
            total = _BgBucketBuildMixin._sum_chars_in_range(path, 2601, 2645)
        self.assertGreater(total, 0)
        self.assertEqual(total, expected)
        self.assertBounded(decoded.count, "bucket char sum")

    def test_rows_in_range_need_a_positive_low_bound(self):
        log = self.store._transcript_log(CONV)
        with self.assertRaises(ValueError):
            next(log.iter_rows_in_range("seq", 0, 10))

    # -- search index generation ---------------------------------------------

    def _generation(self):
        return int(self.store.get_extra(CONV, "transcript_generation", 0) or 0)

    def test_a_metadata_patch_keeps_the_search_index_valid(self):
        before = self._generation()
        self.store.patch_message(CONV, "m04999", turn_final=True,
                                 turn_id="t1")
        self.store.patch_message(CONV, "m04999", is_error=True)
        self.assertEqual(self._generation(), before)

    def test_a_content_patch_invalidates_the_search_index(self):
        before = self._generation()
        self.store.patch_message(CONV, "m04999", content="redacted")
        self.assertEqual(self._generation(), before + 1)

    # -- context usage repair ----------------------------------------------

    def test_context_usage_repair_reads_only_new_rows(self):
        self.store.get_extra(CONV, "context_usage")  # first repair: full read
        for i in range(3):
            self.store.append_message(CONV, {
                "role": "assistant", "content": f"late {i}",
                "msg_id": f"late{i}",
                "source": {"type": "agent", "name": "A",
                           "context_used": 1000 + i, "context_max": 10000}},
                agent_name="A", user_id="owner")
        self.store._context_usage_repair_mtime.pop(CONV, None)
        with _Decoded() as decoded:
            usage = self.store.get_extra(CONV, "context_usage")
        self.assertEqual(usage["A"]["used"], 1002)
        self.assertBounded(decoded.count, "context usage repair", cap=50)


class SearchRefreshSkip(unittest.TestCase):

    def test_the_excluded_conversation_is_not_read(self):
        from core.conversation_index import ConversationIndex
        index = ConversationIndex.__new__(ConversationIndex)
        index._user_id = "owner"

        class _Store:
            def list_conversations(self, user_id=""):
                return [{"conversation_id": "current", "updated_at": 5.0}]

            def encryption_status(self, cid):
                raise AssertionError("the skipped conversation was inspected")

        with patch.object(ConversationIndex, "_known", return_value={}), \
                patch.object(ConversationIndex, "purge") as purge:
            stats = index.refresh(store=_Store(), skip={"current"})
        purge.assert_not_called()
        self.assertEqual(stats["conversations"], 1)
        self.assertEqual(stats["indexed"], 0)


class DelegateStatusStale(unittest.TestCase):

    def test_old_unanswered_delegates_are_counted_not_listed(self):
        from core.handlers import delegate_status as module
        now = time.time()
        live = [{"task_id": f"old{i}", "target": "GameDev2",
                 "started_at": now - 3 * 86400 - i} for i in range(30)]
        live += [{"task_id": "recent", "target": "GameDev2",
                  "started_at": now - 60},
                 {"task_id": "attached", "target": "GameDev3",
                  "started_at": now - 5 * 86400, "runtime_attached": True}]
        handler = module.DelegateStatusHandler.__new__(
            module.DelegateStatusHandler)
        handler._conversation_id = "conv"
        handler._user_id = "owner"
        with patch.object(module.DelegateStatusHandler,
                          "_resolve_source_context",
                          return_value=("claude", "")), \
                patch.object(module, "_merged_state",
                             return_value=(live, [])), \
                patch("core.service_registry._parent_conversation_id",
                      return_value=""):
            out = json.loads(handler.execute({}))
        self.assertEqual({item["task_id"] for item in out["live"]},
                         {"recent", "attached"})
        self.assertEqual(out["counts"]["stale"], 30)
        self.assertEqual(len(out["stale"]), module._STALE_SHOWN)
