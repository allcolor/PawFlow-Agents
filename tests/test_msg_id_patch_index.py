"""A patch must read at most the tail segments, never the whole conversation.

2026-10-30, live server: one conversation held 2,240,519 rows in 645 segments
(5 GB). patch_message patches the transcript AND shared.jsonl AND every agent
context, and for each stream where the msg_id was absent the old code read
every segment looking for it -- 4,075 ms of transcript per patch, held under
the conversation lock. Lock waits reached 13.3 s and the webchat queued.

Server logs settled where the ids are: of 300 patches that found their row,
ALL 300 scanned exactly one segment. A patch of a live row lives in the tail.
So the tail is read directly (2 segments, 14 ms measured) and an older row is
located from per-segment sidecars without opening any segment body.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from core.segmented_jsonl import SegmentedJsonl

SEGMENT_ROWS = 10
SEGMENTS = 40


def _row(i, msg_id=None):
    row = {"role": "assistant", "content": f"row {i}", "ts": i, "seq": i}
    if msg_id:
        row["msg_id"] = msg_id
    return row


class PatchReadsOnlyTheTailTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="msgid-tail-"))
        self.log = SegmentedJsonl(self.tmp / "transcript.jsonl",
                                  max_rows=SEGMENT_ROWS)
        rows = []
        for seg in range(SEGMENTS):
            for i in range(SEGMENT_ROWS):
                n = seg * SEGMENT_ROWS + i
                rows.append(_row(n, f"id{seg:03d}" if i == 0 else None))
        self.log.append_dicts(rows)
        self.assertTrue(self.log.exists())
        # Production state: sealing writes a sidecar per segment, and the poller
        # catches up on older logs. Without them the search still answers, just
        # by reading segments -- covered by the fallback test below. Flush first:
        # sidecars are built from bytes on disk, and buffers empty the files.
        self.log.flush_append_handles(self.log.flat_path)
        self.log._flush_own_append_handles()
        for path in self.log._segment_paths():
            self.log.build_msg_id_sidecar(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spy(self, reads):
        real = SegmentedJsonl._segment_bytes_mentioning

        def spy(path, needles):
            data = real(path, needles)
            reads.append(path.name)
            return data
        return spy

    def test_a_missing_id_reads_only_the_tail(self):
        """The expensive case: the id belongs to no segment in this stream."""
        reads = []
        with mock_patch.object(SegmentedJsonl, "_segment_bytes_mentioning",
                               side_effect=self._spy(reads)):
            got = self.log.patch_first_by_msg_id("not-here-at-all",
                                                 {"turn_final": True})

        self.assertIsNone(got)
        self.assertEqual(len(reads), 2, f"read {reads}")

    def test_a_live_row_reads_only_the_tail(self):
        reads = []
        with mock_patch.object(SegmentedJsonl, "_segment_bytes_mentioning",
                               side_effect=self._spy(reads)):
            got = self.log.patch_first_by_msg_id("id039", {"turn_final": True})

        self.assertIsNotNone(got)
        self.assertEqual(got.get("turn_final"), True)
        # One read locates the row, one patches it: both on the tail segment.
        self.assertLessEqual(len(reads), 2, f"read {reads}")
        self.assertEqual(set(reads), {"000039.jsonl"})

    def test_an_old_row_never_scans_the_whole_log(self):
        """B: the sidecar locates it; no distant segment body is read."""
        reads = []
        with mock_patch.object(SegmentedJsonl, "_segment_bytes_mentioning",
                               side_effect=self._spy(reads)):
            got = self.log.patch_first_by_msg_id("id005", {"turn_final": True})

        self.assertIsNotNone(got)
        self.assertEqual(got.get("turn_final"), True)
        # Only the two tail segments may be opened by the search, plus the owner
        # once it is known: never the 40-segment walk.
        self.assertLessEqual(len(reads), 3, f"read {reads}")
        self.assertIn("000005.jsonl", reads)

    def test_without_sidecars_the_answer_is_still_correct(self):
        """Slower, never wrong: a missing sidecar must not hide a row."""
        shutil.rmtree(self.log.segment_dir / "msg_ids", ignore_errors=True)
        got = self.log.patch_first_by_msg_id("id005", {"turn_final": True})
        self.assertIsNotNone(got)
        self.assertEqual(got.get("turn_final"), True)

    def test_sidecars_are_written_once_and_reused(self):
        self.log.find_segment_for_msg_id("id003")
        sidecar_dir = self.log.segment_dir / "msg_ids"
        self.assertTrue(sidecar_dir.is_dir())
        files = sorted(p.name for p in sidecar_dir.glob("*.json"))
        self.assertTrue(files)
        entry = json.loads((sidecar_dir / files[0]).read_text(encoding="utf-8"))
        self.assertEqual(entry.get("signature") is not None, True)
        self.assertIsInstance(entry.get("ids"), list)

        # A second lookup must not rescan a segment whose sidecar is valid.
        scanned = []
        real = SegmentedJsonl._scan_msg_ids
        with mock_patch.object(SegmentedJsonl, "_scan_msg_ids",
                               side_effect=lambda p: scanned.append(p.name) or real(p)):
            self.log.find_segment_for_msg_id("id004")
        self.assertEqual(scanned, [], f"rescanned {scanned}")

    def test_a_sidecar_for_a_changed_segment_is_rebuilt(self):
        owner = self.log.find_segment_for_msg_id("id003")
        self.assertIsNotNone(owner)
        rows = [json.loads(line) for line in
                owner.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["msg_id"] = "rewritten"
        owner.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                         encoding="utf-8")

        # The stale sidecar must not answer for the old id...
        self.assertIsNone(self.log.find_segment_for_msg_id("id003"))
        # ...and the new id is found.
        self.assertIsNotNone(self.log.find_segment_for_msg_id("rewritten"))


if __name__ == "__main__":
    unittest.main()
