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
