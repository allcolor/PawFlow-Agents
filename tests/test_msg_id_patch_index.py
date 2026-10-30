"""A patch must read the owning segment, never the whole conversation.

2026-10-30, live server: one conversation held 2,240,519 rows in 645 segments
(5 GB). patch_message patches the transcript AND shared.jsonl AND every agent
context. For each stream where the msg_id is absent, the old code read every
segment looking for the id: measured on that transcript, a single miss read
4,075 ms of transcript WHILE HOLDING THE CONVERSATION LOCK. Lock waits reached
13.3 s and the webchat queued behind them.

The id is stored in clear, so the fix is an index from msg_id to the segment
that owns it. Building the index means reading the log once (59 s on that
conversation), so it is built by the poller on a background thread and only
consulted once complete: a half index answering "absent" would drop rows.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from core import _segmented_jsonl_io as _io
from core.segmented_jsonl import SegmentedJsonl

SEGMENT_ROWS = 10
SEGMENTS = 40


def _row(i, msg_id=None):
    row = {"role": "assistant", "content": f"row {i}", "ts": i, "seq": i}
    if msg_id:
        row["msg_id"] = msg_id
    return row


def _reset_index_caches():
    with _io._MSG_ID_INDEX_LOCK:
        _io._MSG_ID_INDEX_CACHE.clear()
        _io._MSG_ID_INDEX_WARMING.clear()


class PatchReadsOnlyTheOwningSegmentTest(unittest.TestCase):

    def setUp(self):
        _reset_index_caches()
        self.tmp = Path(tempfile.mkdtemp(prefix="msgid-index-"))
        self.log = SegmentedJsonl(self.tmp / "transcript.jsonl",
                                  max_rows=SEGMENT_ROWS)
        rows = []
        for seg in range(SEGMENTS):
            for i in range(SEGMENT_ROWS):
                n = seg * SEGMENT_ROWS + i
                rows.append(_row(n, f"id{seg:03d}" if i == 0 else None))
        self.log.append_dicts(rows)
        self.assertTrue(self.log.exists())
        # Build the index the way the poller does, before any patch runs.
        self.log.msg_id_index()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_index_caches()

    def test_the_index_maps_every_id_to_its_segment(self):
        indexed = self.log.msg_id_index()
        expected = {f"id{seg:03d}": f"{seg:06d}.jsonl" for seg in range(SEGMENTS)}
        self.assertEqual(indexed, expected)

    def test_patching_an_old_row_reads_only_the_owning_segment(self):
        reads = []
        real = SegmentedJsonl._segment_bytes_mentioning

        def spy(path, needles):
            data = real(path, needles)
            reads.append((path.name, len(data or b""), data is not None))
            return data

        with mock_patch.object(SegmentedJsonl, "_segment_bytes_mentioning",
                               side_effect=spy):
            got = self.log.patch_first_by_msg_id("id005", {"turn_final": True})

        self.assertIsNotNone(got)
        self.assertEqual(got.get("turn_final"), True)
        self.assertEqual(len(reads), 1, f"read {len(reads)} segments: {reads}")
        self.assertTrue(reads[0][2])
        self.assertEqual(reads[0][0], "000005.jsonl")

    def test_a_missing_id_reads_nothing_at_all(self):
        """The expensive case: the id belongs to no segment here."""
        reads = []

        def spy(path, needles):
            reads.append(path.name)
            return None

        with mock_patch.object(SegmentedJsonl, "_segment_bytes_mentioning",
                               side_effect=spy):
            got = self.log.patch_first_by_msg_id("not-here-at-all",
                                                 {"turn_final": True})

        self.assertIsNone(got)
        self.assertEqual(reads, [])

    def test_a_half_built_index_is_never_trusted(self):
        """Before the build finishes, the caller must search, not trust it."""
        _reset_index_caches()
        self.assertIsNone(self.log.msg_id_index_if_ready())
        # The fallback still finds the row: correctness beats speed here.
        got = self.log.patch_first_by_msg_id("id007", {"turn_final": True})
        self.assertIsNotNone(got)

    def test_the_index_is_not_written_beside_the_segments(self):
        self.log.patch_first_by_msg_id("id001", {"turn_final": True})
        self.assertFalse((self.log.segment_dir / "msg_id_index.json").exists())

    def test_a_rewritten_segment_is_detected(self):
        owner = self.log.msg_id_index()["id003"]
        target = self.log.segment_dir / owner
        rows = [json.loads(line) for line in
                target.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["msg_id"] = "rewritten"
        target.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        # A changed segment makes the ready index refuse to answer...
        self.assertIsNone(self.log.msg_id_index_if_ready())
        # ...and a rebuild sees the new id.
        self.assertIsNotNone(
            self.log.patch_first_by_msg_id("rewritten", {"turn_final": True}))

    def test_warming_is_started_once(self):
        _reset_index_caches()
        self.assertTrue(self.log.warm_msg_id_index_async())
        self.assertFalse(self.log.warm_msg_id_index_async())


if __name__ == "__main__":
    unittest.main()
