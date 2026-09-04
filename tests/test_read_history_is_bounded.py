"""read_history reads a page, never the conversation.

The bug this locks down: every action but one loaded the entire transcript,
sorted it, and composed every display trace in it -- to render at most a
hundred messages. On a 257k-message conversation that is hundreds of megabytes
and seconds of CPU per call, and read_history is called in chains: the summary
of an archived phase hands the agent a range() hint per phase, and `range`
managed it twice per call (once to slice, once more inside the renderer to
rebuild an index). A handful of those calls took the server down.

The property under test is not "it is faster". It is that the amount of the
transcript held at once is bounded by the size of the answer, whatever the size
of the conversation. Each test therefore measures how many rows the store
actually composed, and fails if that number tracks the conversation instead of
the page.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.conversation_store import ConversationStore
from core.handlers.history import ReadHistoryHandler

#: Big enough that an unbounded reader is unmistakable next to a page of 50,
#: small enough to stay a unit test.
CONV_SIZE = 5000


class BoundedReads(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message number {i}",
             "msg_id": f"m{i:05d}",
             "seq": i + 1,
             "ts": 1000.0 + i}
            for i in range(CONV_SIZE)
        ]
        store.save("big", messages, ttl=3600, user_id="owner")
        self.store = store
        self.handler = ReadHistoryHandler()
        self.handler.set_conversation_id("big")

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _peak_rows(self, arguments):
        """Run one read_history call; return the largest batch it composed.

        ``_compose_display_traces`` is the single point every reader turns raw
        rows into messages through, so the largest list it is ever handed is
        exactly the high-water mark of what the call held.
        """
        cls = type(self.store)
        original = cls._compose_display_traces
        peak = {"rows": 0}

        def spy(rows):
            peak["rows"] = max(peak["rows"], len(rows))
            return original(rows)

        cls._compose_display_traces = staticmethod(spy)
        try:
            out = self.handler.execute(arguments)
        finally:
            cls._compose_display_traces = original
        return out, peak["rows"]

    #: A single call may compose one window plus the page it returns. Anything
    #: proportional to CONV_SIZE is the bug coming back.
    def _assert_bounded(self, arguments):
        out, rows = self._peak_rows(arguments)
        self.assertNotIn("Error:", out, f"{arguments} -> {out[:200]}")
        limit = self.store._WINDOW_CHUNK + 200
        self.assertLess(
            rows, limit,
            f"{arguments} composed {rows} rows at once (cap {limit}); "
            f"the conversation has {CONV_SIZE} messages -- this action is "
            f"reading the transcript instead of a page")
        return out

    def test_every_action_holds_a_page_not_the_conversation(self):
        for arguments in (
            {"action": "recent", "limit": 50},
            {"action": "oldest", "limit": 50},
            {"action": "read", "index": 4000},
            {"action": "count"},
            {"action": "count", "role_filter": "user"},
            {"action": "search", "query": "message number", "limit": 50},
            {"action": "range", "from_msg_id": "m00010",
             "to_msg_id": "m04990"},
            {"action": "range_by_seq", "from_seq": 11, "to_seq": 4991},
            {"action": "range_by_date", "from_date": "1970-01-01",
             "to_date": "2100-01-01"},
            {"action": "around", "from_msg_id": "m02500", "limit": 40},
            {"action": "around", "from_seq": 2500, "limit": -40},
        ):
            with self.subTest(action=arguments):
                self._assert_bounded(arguments)

    def test_a_chain_of_range_calls_stays_flat(self):
        """The exact shape that took the server down: one range per phase.

        Ten calls must cost ten pages, not ten transcripts.
        """
        for start in range(0, 5000, 500):
            arguments = {"action": "range",
                         "from_msg_id": f"m{start:05d}",
                         "to_msg_id": f"m{min(start + 499, 4999):05d}"}
            self._assert_bounded(arguments)

    def test_the_handler_never_asks_for_the_whole_transcript(self):
        """No action may call ``store.load()``, whatever the arguments.

        The readers are bounded; the one call that is not must not creep back
        in through a code path a size assertion happens not to cover.
        """
        cls = type(self.store)
        original = cls.load
        calls = []

        def spy(self_store, cid, user_id=""):
            calls.append(cid)
            return original(self_store, cid, user_id=user_id)

        cls.load = spy
        try:
            for arguments in (
                {"action": "recent"},
                {"action": "oldest"},
                {"action": "read", "index": 3},
                {"action": "count", "role_filter": "user"},
                {"action": "search", "query": "number 42"},
                {"action": "range", "from_msg_id": "m00001",
                 "to_msg_id": "m00009"},
                {"action": "range_by_seq", "from_seq": 2, "to_seq": 9},
                {"action": "range_by_date", "from_date": "1970-01-01",
                 "to_date": "2100-01-01"},
                {"action": "around", "from_msg_id": "m00050", "limit": 5},
            ):
                self.handler.execute(arguments)
        finally:
            cls.load = original
        self.assertEqual(calls, [],
                         "read_history called store.load() -- that reads the "
                         "whole conversation to answer one page")

    def test_read_by_tail_index_skips_earlier_segments(self):
        """Bounded memory must not hide an O(index) prefix scan."""
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("big")
        rows = list(log.iter_rows())
        SegmentedJsonl(
            self.store._transcript_path("big"), max_rows=100,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("big").iter_paths()
        skipped_paths = set(paths[:-1])
        iter_file = SegmentedJsonl._iter_file

        def reject_skipped_segment_reads(path):
            if path in skipped_paths:
                raise AssertionError(
                    "read(index) decoded a segment before the target")
            return iter_file(path)

        with patch.object(
                SegmentedJsonl, "_iter_file",
                side_effect=reject_skipped_segment_reads):
            out = self.handler.execute({"action": "read", "index": 4900})

        self.assertIn("message number 4900", out)
        self.assertIn("[#4900]", out)


class StillCorrect(unittest.TestCase):
    """Bounded is worthless if it also became wrong."""

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        store.save("c", [
            {"role": "user", "content": f"line {i}", "msg_id": f"m{i}",
             "seq": i + 1, "ts": 1000.0 + i}
            for i in range(10)
        ], ttl=3600, user_id="owner")
        self.store = store
        self.handler = ReadHistoryHandler()
        self.handler.set_conversation_id("c")

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_indices_are_absolute_not_page_relative(self):
        out = self.handler.execute({"action": "range", "from_msg_id": "m4",
                                    "to_msg_id": "m6"})
        self.assertIn("[#4]", out)
        self.assertIn("[#6]", out)
        self.assertNotIn("[#0]", out)

    def test_search_prefilter_keeps_absolute_indices_across_segments(self):
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[8]["content"] = "unique-late-needle"
        rows.insert(2, {
            "t": "trace_update", "trace_id": "orphan",
            "entry": {"role": "assistant", "content": "not a display row"},
        })
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=3,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        skipped_paths = set(paths[:-2])
        iter_file = SegmentedJsonl._iter_file

        def reject_skipped_segment_reads(path):
            if path in skipped_paths:
                raise AssertionError(
                    "search decoded a non-candidate transcript segment")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value=None), patch.object(
                       SegmentedJsonl, "_iter_file",
                       side_effect=reject_skipped_segment_reads):
            out = self.handler.execute({
                "action": "search", "query": "unique-late-needle", "limit": 10,
            })

        self.assertIn("unique-late-needle", out)
        self.assertIn("[#8]", out)

    def test_exact_search_does_not_decode_token_only_segments(self):
        """An exact hit must win before broad token candidates are decoded."""
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[0]["content"] = "alpha appears alone here"
        rows[8]["content"] = "the exact alpha beta phrase"
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=2,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        token_only_path = paths[0]
        iter_file = SegmentedJsonl._iter_file

        def reject_token_only_segment(path):
            if path == token_only_path:
                raise AssertionError(
                    "exact search decoded a token-only transcript segment")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value=None), patch.object(
                       SegmentedJsonl, "_iter_file",
                       side_effect=reject_token_only_segment):
            out = self.handler.execute({
                "action": "search", "query": "alpha beta", "limit": 10,
            })

        self.assertIn("the exact alpha beta phrase", out)
        self.assertIn("[#8]", out)

    def test_keyword_search_decodes_only_segments_meeting_its_threshold(self):
        """One ordinary token cannot make a multi-term segment a candidate."""
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[0]["content"] = "alpha appears alone here"
        rows[2]["content"] = "beta appears alone here"
        rows[8]["content"] = "alpha and later beta qualify together"
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=2,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        token_only_paths = set(paths[:2])
        iter_file = SegmentedJsonl._iter_file

        def reject_token_only_segments(path):
            if path in token_only_paths:
                raise AssertionError(
                    "keyword search decoded a one-token transcript segment")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value=None), patch.object(
                       SegmentedJsonl, "_iter_file",
                       side_effect=reject_token_only_segments):
            out = self.handler.execute({
                "action": "search", "query": "alpha beta gamma", "limit": 10,
            })

        self.assertIn("alpha and later beta qualify together", out)
        self.assertIn("[#8]", out)

    def test_external_search_candidates_are_thresholded_before_decode(self):
        """Ripgrep's broad file list is narrowed before parsing JSON rows."""
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[0]["content"] = "alpha appears alone here"
        rows[2]["content"] = "beta appears alone here"
        rows[8]["content"] = "alpha and later beta qualify together"
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=2,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        token_only_paths = set(paths[:2])
        iter_file = SegmentedJsonl._iter_file

        def search_result(*_args, **kwargs):
            if "alpha beta gamma" in kwargs["input"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="".join(f"{path}\n" for path in paths), stderr="")

        def reject_token_only_segments(path):
            if path in token_only_paths:
                raise AssertionError(
                    "external prefilter left a one-token segment selected")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value="/test/rg"), patch(
                       "core._conversation_store_transcript.subprocess.run",
                       side_effect=search_result) as run, patch.object(
                           SegmentedJsonl, "_iter_file",
                           side_effect=reject_token_only_segments):
            out = self.handler.execute({
                "action": "search", "query": "alpha beta gamma", "limit": 10,
            })

        self.assertEqual(run.call_count, 2)
        self.assertIn("alpha and later beta qualify together", out)

    def test_keyword_segment_prefilter_keeps_structured_standalone_terms(self):
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[0]["content"] = "alpha appears alone here"
        rows[8]["content"] = "PAWFLOW_USE_RTK remains disabled"
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=2,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        iter_file = SegmentedJsonl._iter_file

        def reject_generic_segment(path):
            if path == paths[0]:
                raise AssertionError(
                    "structured-token search decoded a generic-token segment")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value=None), patch.object(
                       SegmentedJsonl, "_iter_file",
                       side_effect=reject_generic_segment):
            out = self.handler.execute({
                "action": "search",
                "query": "alpha PAWFLOW_USE_RTK missing", "limit": 10,
            })

        self.assertIn("PAWFLOW_USE_RTK remains disabled", out)
        self.assertIn("[#8]", out)

    def test_search_prefilter_skips_nested_read_history_result_segments(self):
        """Copied search output must not select or decode its own segment."""
        from core.segmented_jsonl import SegmentedJsonl

        log = self.store._transcript_log("c")
        rows = list(log.iter_rows())
        rows[0].update({
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": "rh1", "name": "read_history",
                "arguments": {"action": "search"},
            }],
        })
        rows[1].update({
            "role": "tool", "tool_call_id": "rh1",
            "content": (
                '<tool_output tool="read_history">\n'
                "[#999] assistant: unique-nested-needle copied-result\n"
                "</tool_output>"
            ),
        })
        rows[8]["content"] = "genuine unique-nested-needle answer"
        SegmentedJsonl(
            self.store._transcript_path("c"), max_rows=2,
        ).replace_dicts(rows)
        paths = self.store._transcript_log("c").iter_paths()
        nested_result_path = paths[0]
        iter_file = SegmentedJsonl._iter_file

        def reject_nested_result_segment(path):
            if path == nested_result_path:
                raise AssertionError(
                    "search decoded a segment selected only by nested output")
            return iter_file(path)

        with patch("core._conversation_store_transcript.shutil.which",
                   return_value=None), patch.object(
                       SegmentedJsonl, "_iter_file",
                       side_effect=reject_nested_result_segment):
            out = self.handler.execute({
                "action": "search", "query": "unique-nested-needle",
                "limit": 10,
            })

        self.assertIn("genuine unique-nested-needle answer", out)
        self.assertIn("[#8]", out)
        self.assertNotIn("copied-result", out)

    def test_search_uses_standard_grep_when_ripgrep_is_unavailable(self):
        path = self.store._transcript_log("c").iter_paths()[0]
        result = SimpleNamespace(
            returncode=0, stdout=f"{path}\n", stderr="")

        def which(name):
            return None if name == "rg" else "/test/grep"

        with patch("core._conversation_store_transcript.shutil.which",
                   side_effect=which), patch(
                       "core._conversation_store_transcript.subprocess.run",
                       return_value=result) as run:
            out = self.handler.execute({
                "action": "search", "query": "line 8", "limit": 10,
            })

        self.assertIn("line 8", out)
        self.assertEqual(run.call_args.args[0][:6], [
            "/test/grep", "-F", "-i", "-l", "-f", "-",
        ])

    def test_dependency_free_search_keeps_unicode_case_insensitive(self):
        self.assertEqual(
            self.store.edit_message("c", "m8", "ÉTÉ BRÛLANT"), 1)
        checked = []

        def which(name):
            checked.append(name)
            return None if name == "rg" else "/test/grep"

        with patch("core._conversation_store_transcript.shutil.which",
                   side_effect=which):
            out = self.handler.execute({
                "action": "search", "query": "été brûlant", "limit": 10,
            })

        self.assertIn("ÉTÉ BRÛLANT", out)
        self.assertEqual(checked, ["rg"])

    def test_range_returns_exactly_the_closed_interval(self):
        out = self.handler.execute({"action": "range", "from_msg_id": "m4",
                                    "to_msg_id": "m6"})
        self.assertIn("line 4", out)
        self.assertIn("line 6", out)
        self.assertNotIn("line 3", out)
        self.assertNotIn("line 7", out)

    def test_an_unterminated_range_names_the_anchor_that_is_wrong(self):
        """It used to answer "No messages found", which reads as "nothing to
        see" rather than "your second anchor is bogus"."""
        out = self.handler.execute({"action": "range", "from_msg_id": "m4",
                                    "to_msg_id": "nope"})
        self.assertIn("nope", out)
        self.assertIn("Error", out)

    def test_a_reversed_range_is_an_error_not_a_silent_tail(self):
        out = self.handler.execute({"action": "range", "from_msg_id": "m6",
                                    "to_msg_id": "m4"})
        self.assertIn("Error", out)

    def test_read_by_index_returns_that_message(self):
        out = self.handler.execute({"action": "read", "index": 7})
        self.assertIn("line 7", out)
        self.assertIn("[#7]", out)

    def test_an_index_past_the_end_is_refused_with_the_real_bound(self):
        out = self.handler.execute({"action": "read", "index": 99})
        self.assertIn("0-9", out)

    def test_around_backward_stops_at_the_anchor(self):
        out = self.handler.execute({"action": "around", "from_msg_id": "m5",
                                    "limit": -3})
        self.assertIn("line 5", out)
        self.assertIn("line 3", out)
        self.assertNotIn("line 6", out)
        self.assertNotIn("line 2", out)

    def test_around_forward_starts_at_the_anchor(self):
        out = self.handler.execute({"action": "around", "from_msg_id": "m5",
                                    "limit": 3})
        self.assertIn("line 5", out)
        self.assertIn("line 7", out)
        self.assertNotIn("line 4", out)
        self.assertNotIn("line 8", out)

    def test_the_footer_counts_every_match_not_the_page(self):
        out = self.handler.execute({"action": "oldest", "limit": 3})
        self.assertIn("10 messages total", out)
        self.assertIn("of 10", out)

    def test_a_second_page_continues_where_the_first_stopped(self):
        first = self.handler.execute({"action": "oldest", "limit": 3})
        second = self.handler.execute({"action": "oldest", "limit": 3,
                                       "offset": 3})
        self.assertIn("line 2", first)
        self.assertNotIn("line 3", first)
        self.assertIn("line 3", second)
        self.assertIn("line 5", second)

    def test_another_users_conversation_stays_invisible(self):
        self.handler.set_user_id("mallory")
        self.store.save("owned", [{"role": "user", "content": "secret",
                                   "msg_id": "s1", "seq": 1, "ts": 1.0}],
                        ttl=3600, user_id="alice")
        self.handler.set_conversation_id("owned")
        for arguments in ({"action": "oldest"}, {"action": "recent"},
                          {"action": "read", "index": 0}):
            with self.subTest(action=arguments):
                self.assertNotIn("secret", self.handler.execute(arguments))
        # search echoes the query back, so the message body is what must be
        # absent -- not the word the caller supplied.
        found = self.handler.execute({"action": "search", "query": "secret"})
        self.assertIn("No messages matching", found)


if __name__ == "__main__":
    unittest.main()
