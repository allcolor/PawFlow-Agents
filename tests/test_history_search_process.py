"""read_history search scans the transcript in a child process.

The scan decodes JSON rows; in the server process that holds the GIL and
stalls every unrelated thread (relays, UI, other agents' tools). These tests
pin that a real transcript is scanned out of process, that the child gives
the in-process answer character for character, and that the cases the child
cannot serve stay in process.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.conversation_store import ConversationStore
from core.handlers import _history_search_process as search_process
from core.handlers.history import ReadHistoryHandler
from core.segmented_jsonl import SegmentedJsonl

SEARCHES = [
    {"query": "needle"},
    {"query": "needle", "role_filter": "assistant"},
    {"query": "needle", "limit": 2, "offset": 1},
    {"query": "alpha gamma"},
    {"query": "needle", "role_filter": "tool"},
    {"query": "absent words"},
]


class SearchProcess(unittest.TestCase):

    def setUp(self):
        # The full-text index answers some role-filtered searches once it is
        # current, and renders no seq; these tests compare two scans.
        index = patch("core.handlers.history.indexed_search_hits",
                      return_value=None)
        index.start()
        self.addCleanup(index.stop)
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"line {i} needle" if i % 3 == 0 else f"line {i}",
             "msg_id": f"m{i}", "seq": i + 1, "ts": 1000.0 + i}
            for i in range(30)
        ]
        rows[7]["content"] = "alpha beta gamma"
        rows[10].update({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "rh1", "name": "read_history",
                            "arguments": {"action": "search"}}],
        })
        rows[11].update({
            "role": "tool", "tool_call_id": "rh1",
            "content": '<tool_output tool="read_history">'
                       "[#3] needle copied</tool_output>",
        })
        store.save("c", rows[:1], ttl=3600, user_id="owner")
        SegmentedJsonl(store._transcript_path("c"),
                       max_rows=4).replace_dicts(rows)
        self.store = store
        self.handler = ReadHistoryHandler()
        self.handler.set_conversation_id("c")
        self.handler.set_user_id("owner")

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _search(self, search):
        return self.handler.execute({"action": "search", **search})

    def test_the_child_answers_exactly_as_the_server_would(self):
        with patch("core.handlers.history._SEARCH_IN_PROCESS_MAX_BYTES",
                   float("inf")):
            expected = [self._search(s) for s in SEARCHES]
        spawned = []
        real = search_process.run_search_process

        def counted(request):
            spawned.append(request["query"])
            return real(request)

        def no_scan_here(*_args, **_kwargs):
            raise AssertionError("the server process scanned the transcript")

        with patch("core.handlers.history.run_search_process",
                   side_effect=counted), patch.object(
                       ReadHistoryHandler, "_scan_search", no_scan_here):
            got = [self._search(s) for s in SEARCHES]

        self.assertEqual(got, expected)
        self.assertEqual(len(spawned), len(SEARCHES))
        self.assertIn("[#3]", got[0])
        self.assertNotIn("needle copied", got[0])

    def test_the_snapshot_flushes_and_describes_every_segment(self):
        snapshot = self.store.history_search_snapshot("c")
        paths = self.store._transcript_log("c").iter_paths()

        self.assertEqual(snapshot["paths"], [str(p) for p in paths])
        self.assertEqual(sum(snapshot["role_rows"].values()), 30)
        self.assertEqual(snapshot["bytes"],
                         sum(p.stat().st_size for p in paths))

    def test_an_encrypted_transcript_stays_in_process(self):
        with patch.object(type(self.store), "_codec_for",
                          return_value=object()):
            self.assertIsNone(self.store.history_search_snapshot("c"))

    def test_another_users_conversation_spawns_nothing(self):
        self.handler.set_user_id("mallory")
        with patch("core.handlers.history.run_search_process",
                   side_effect=AssertionError("spawned for a stranger")):
            out = self._search({"query": "needle"})

        self.assertIn("No messages matching", out)

    def test_a_failed_child_is_reported_not_retried_in_process(self):
        failed = SimpleNamespace(returncode=1, stdout="",
                                 stderr="Traceback\nMemoryError")
        with patch("core.handlers._history_search_process.subprocess.run",
                   return_value=failed), patch.object(
                       ReadHistoryHandler, "_scan_search",
                       side_effect=AssertionError("fell back in process")):
            out = self._search({"query": "needle"})

        self.assertEqual(
            out, "Error: history search failed: history search process "
                 "exited 1: MemoryError")

    def test_the_child_has_no_deadline(self):
        done = SimpleNamespace(returncode=0, stdout='{"text": "ok"}\n',
                               stderr="")
        with patch("core.handlers._history_search_process.subprocess.run",
                   return_value=done) as run:
            self.assertEqual(self._search({"query": "needle"}), "ok")

        self.assertNotIn("timeout", run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][1:],
                         ["-m", "core.handlers._history_search_process"])


if __name__ == "__main__":
    unittest.main()
