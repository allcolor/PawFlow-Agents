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

    def test_sidecars_are_plain_text_and_reused(self):
        self.log.find_segment_for_msg_id("id003")
        sidecar_dir = self.log.segment_dir / "msg_ids"
        self.assertTrue(sidecar_dir.is_dir())
        files = sorted(p.name for p in sidecar_dir.glob("*.txt"))
        self.assertTrue(files)
        # Plain text: an identity line, then one msg_id per line. No JSON to
        # parse on the search path.
        body = (sidecar_dir / files[0]).read_text(encoding="utf-8")
        self.assertTrue(body.splitlines()[0].split())
        self.assertIn("id", body)

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


def _segmented_log(path: Path, segments: int) -> SegmentedJsonl:
    """A flushed log of ``segments`` segments, with no msg_id sidecar yet."""
    log = SegmentedJsonl(path, max_rows=SEGMENT_ROWS)
    log.append_dicts([_row(n, f"{path.stem}{n:03d}")
                      for n in range(segments * SEGMENT_ROWS)])
    log.flush_append_handles(log.flat_path)
    log._flush_own_append_handles()
    shutil.rmtree(log.segment_dir / "msg_ids", ignore_errors=True)
    return log


class SidecarValidityReadsTheHeaderTest(unittest.TestCase):
    """The warmer's check reads one line, not the body (2026-10-31)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="msgid-valid-"))
        self.log = _segmented_log(self.tmp / "transcript.jsonl", 3)
        self.path = self.log._segment_paths()[0]
        self.sidecar = self.log.segment_dir / "msg_ids" / f"{self.path.name}.txt"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_sidecar_is_invalid(self):
        self.assertFalse(self.log._msg_id_sidecar_valid(self.path))

    def test_the_identity_line_alone_decides(self):
        self.log.build_msg_id_sidecar(self.path)
        header = self.sidecar.read_bytes().split(b"\n", 1)[0]
        self.sidecar.write_bytes(header + b"\n" + b"x" * 1_000_000)
        self.assertTrue(self.log._msg_id_sidecar_valid(self.path))

    def test_a_sidecar_of_a_changed_segment_is_invalid(self):
        self.log.build_msg_id_sidecar(self.path)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_row(999, "late")) + "\n")
        self.assertFalse(self.log._msg_id_sidecar_valid(self.path))


class _InlineThread:
    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


class WarmerCoversEveryPatchStreamTest(unittest.TestCase):
    """patch_message searches transcript, shared and agent contexts; the
    warmer must give all of them sidecars. shared.jsonl had none and cost
    1.3-4.3 s per patch on 2026-10-31."""

    def setUp(self):
        from tasks.ai import agent_poller

        self.agent_poller = agent_poller
        self.tmp = Path(tempfile.mkdtemp(prefix="msgid-warm-"))
        (self.tmp / "gamedev").mkdir()
        self.streams = [self.tmp / "transcript.jsonl",
                        self.tmp / "shared.jsonl",
                        self.tmp / "gamedev" / "context.jsonl"]
        self.logs = [_segmented_log(path, 4) for path in self.streams]
        self.listed = []

        def _list_conversations():
            self.listed.append(1)
            return [{"conversation_id": "c1", "message_count": 40}]

        self.store = type("FakeStore", (), {})()
        self.store.list_conversations = _list_conversations
        self.store._patch_stream_paths = lambda cid: list(self.streams)
        self.store._content_seg = (
            lambda cid, path: SegmentedJsonl(path, max_rows=SEGMENT_ROWS))
        agent_poller._MSG_ID_WARMED.clear()
        agent_poller._MSG_ID_WARM_LAST[0] = 0.0

    def tearDown(self):
        self.agent_poller._MSG_ID_WARMED.clear()
        self.agent_poller._MSG_ID_WARM_LAST[0] = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _warm(self):
        from core.conversation_store import ConversationStore

        with mock_patch.object(ConversationStore, "instance",
                               return_value=self.store), \
                mock_patch.object(self.agent_poller.threading, "Thread",
                                  _InlineThread):
            self.agent_poller.AgentPollerMixin._warm_msg_id_sidecars(object())

    def test_every_stream_gets_sidecars_except_its_open_tail(self):
        self._warm()
        for log in self.logs:
            paths = log._segment_paths()
            self.assertGreater(len(paths), 1)
            for path in paths[:-1]:
                self.assertTrue(log._msg_id_sidecar_valid(path),
                                f"{log.flat_path.name}/{path.name}")
            self.assertFalse(
                (log.segment_dir / "msg_ids" / f"{paths[-1].name}.txt").exists())

    def test_a_second_pass_right_after_does_nothing(self):
        self._warm()
        self._warm()
        self.assertEqual(len(self.listed), 1)


if __name__ == "__main__":
    unittest.main()
