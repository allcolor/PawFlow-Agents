"""read_history's segment prefilter applies its threshold with rg, not Python.

On 2026-10-31 a single unfiltered search on the 5 GB Ultima7 transcript spent
35 s reading, lowering and splitting the candidate segments in Python to drop
read_history's own results, and searches took 94 % of the server's GIL. The
same exclusion and term threshold run as rg -P passes; these tests pin that
the rg answer equals the Python one and that no segment is read in Python.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

import core._conversation_store_transcript as transcript
from core.conversation_store import ConversationStore
from core.segmented_jsonl import SegmentedJsonl

CONV = "rgsearch"


def _small_segments(self, cid):
    return SegmentedJsonl(self._transcript_path(cid), max_rows=3,
                          codec=self._codec_for(cid))


def _history_result(text):
    return {"role": "tool", "tool_name": "read_history",
            "content": f'<tool_output tool="read_history">\n{text}\n</tool_output>'}


MESSAGES = (
    [{"role": "assistant", "content": f"filler {i}"} for i in range(6)]
    + [_history_result("Alpha Beta copied"), _history_result("gamma delta"),
       {"role": "assistant", "content": "filler"}]
    + [{"role": "assistant", "content": "alpha only"},
       {"role": "user", "content": "filler"},
       {"role": "assistant", "content": "filler"}]
    + [{"role": "assistant", "content": "ALPHA and gamma"},
       {"role": "tool", "tool_name": "bash",
        "content": '<tool_output tool="bash">foo_bar Déployer</tool_output>'},
       {"role": "assistant", "content": "filler"}]
    + [{"role": "assistant", "content": f"filler {i}"} for i in range(6)]
)

SEARCHES = [
    (["alpha beta"], 1, [], ["read_history"]),
    (["alpha", "gamma"], 2, [], ["read_history"]),
    (["alpha", "gamma"], 2, [], []),
    (["gamma", "foo_bar"], 2, ["foo_bar"], ["read_history"]),
    (["déployer", "absent"], 2, ["déployer"], ["read_history"]),
    (["gamma", "delta"], 2, [], ["read_history"]),
]


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep")
class RgThreshold(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        self._segments = patch.object(
            ConversationStore, "_transcript_log", _small_segments)
        self._segments.start()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        store.save(CONV, MESSAGES, ttl=3600, user_id="owner")
        self.assertGreater(len(store._transcript_log(CONV).iter_paths()), 5)
        self.store = store

    def tearDown(self):
        self._segments.stop()
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _starts(self, search):
        terms, minimum, standalone, excluded = search
        windows = self.store.iter_display_search_windows(
            CONV, terms, minimum_matches=minimum, standalone_terms=standalone,
            excluded_tool_names=excluded)
        return [start for start, _ in windows]

    def test_rg_threshold_equals_the_python_threshold(self):
        real_which = shutil.which
        for search in SEARCHES:
            with self.subTest(search=search):
                with_rg = self._starts(search)
                with patch.object(
                        transcript.shutil, "which",
                        lambda name: None if name == "rg" else real_which(name)):
                    python = self._starts(search)
                self.assertEqual(with_rg, python)
        self.assertEqual(self._starts(SEARCHES[0]), [])
        self.assertNotEqual(self._starts(SEARCHES[1]), [])

    def test_rg_threshold_reads_no_segment_in_python(self):
        reads = []
        real_bytes, real_text = Path.read_bytes, Path.read_text

        def read_bytes(path):
            reads.append(path)
            return real_bytes(path)

        def read_text(path, *args, **kwargs):
            reads.append(path)
            return real_text(path, *args, **kwargs)

        with patch.object(Path, "read_bytes", read_bytes), \
                patch.object(Path, "read_text", read_text):
            for terms, minimum, standalone, excluded in SEARCHES:
                self.store.iter_display_search_windows(
                    CONV, terms, minimum_matches=minimum,
                    standalone_terms=standalone, excluded_tool_names=excluded)
        self.assertEqual([p for p in reads if p.suffix == ".jsonl"], [])
