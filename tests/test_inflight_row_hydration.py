"""A tool call still running must still look running after a reload.

Observed 2026-08-01: switching conversation (or reloading the tab) while a
tool was executing rendered its row as finished — no pending bullet, no
BG/Kill — because "running" existed only in the live SSE stream. The relay's
`_inflight` table knew the truth but was write-only. These tests lock the read
side and the hydration that consumes it.
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core import FlowFile
from core.conversation_store import ConversationStore
from services.tool_relay_service import ToolRelayService


# ── The read side of the in-flight table ────────────────────────────


class TestInflightSnapshot(unittest.TestCase):

    def setUp(self):
        self._saved = ToolRelayService._inflight
        ToolRelayService._inflight = {}

    def tearDown(self):
        ToolRelayService._inflight = self._saved

    def test_running_call_is_reported(self):
        started = time.time() - 5
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "claude", "cc_tc_id": "toolu_1",
                     "bg_tc_id": "toolu_1", "tool_name": "bash",
                     "started_at": started, "background": threading.Event()},
        }

        snap = ToolRelayService.inflight_snapshot("c1")

        assert len(snap) == 1
        assert snap[0]["tc_id"] == "toolu_1"
        assert snap[0]["tool_name"] == "bash"
        assert snap[0]["agent_name"] == "claude"
        assert snap[0]["backgrounded"] is False
        assert snap[0]["duration"] >= 5

    def test_other_conversations_are_never_reported(self):
        ToolRelayService._inflight = {
            "rid1": {"conv": "other", "agent": "claude", "cc_tc_id": "toolu_1",
                     "tool_name": "bash", "started_at": time.time()},
        }

        assert ToolRelayService.inflight_snapshot("c1") == []

    def test_a_sub_conversation_hydrates_in_its_parent(self):
        # A delegate/task call runs under a marked conversation id but its row
        # is rendered in the parent conversation's view.
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1::task::t_7", "agent": "worker",
                     "cc_tc_id": "toolu_9", "tool_name": "read",
                     "started_at": time.time()},
        }

        snap = ToolRelayService.inflight_snapshot("c1")

        assert [e["tc_id"] for e in snap] == ["toolu_9"]

    def test_agent_filter_is_applied_when_given(self):
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "other", "cc_tc_id": "toolu_1",
                     "tool_name": "bash", "started_at": time.time()},
        }

        assert ToolRelayService.inflight_snapshot("c1", "claude") == []
        assert len(ToolRelayService.inflight_snapshot("c1", "other")) == 1

    def test_backgrounded_state_is_carried(self):
        evt = threading.Event()
        evt.set()
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "claude", "cc_tc_id": "toolu_1",
                     "tool_name": "bash", "started_at": time.time(),
                     "background": evt},
        }

        snap = ToolRelayService.inflight_snapshot("c1")

        assert snap[0]["backgrounded"] is True

    def test_an_unbound_request_is_reported_without_a_tc_id(self):
        # In flight, but no provider tool_call id yet: no row can be addressed
        # for it, and the caller keying on ids skips it.
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "claude", "cc_tc_id": "",
                     "tool_name": "bash", "started_at": time.time()},
        }

        snap = ToolRelayService.inflight_snapshot("c1")

        assert snap[0]["tc_id"] == ""
        assert snap[0]["request_id"] == "rid1"


# ── What load_history renders ───────────────────────────────────────


class TestLoadHistoryMarksLiveRows(unittest.TestCase):

    def setUp(self):
        ConversationStore.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)
        self._saved = ToolRelayService._inflight
        ToolRelayService._inflight = {}

    def tearDown(self):
        ToolRelayService._inflight = self._saved
        ConversationStore.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_task(self):
        from tasks.ai.agent_loop import AgentLoopTask
        return AgentLoopTask({"api_key": "test", "conversation_store": True})

    def _load_history(self, conv_id, user_id="alice@test.com", timeout=3.0):
        from core.conversation_event_bus import ConversationEventBus

        reply_conv = f"__test__:{time.time_ns()}"
        call_id = f"call-{time.time_ns()}"
        body = {"action": "load_history", "conversation_id": conv_id,
                "_reply_conversation_id": reply_conv, "_call_id": call_id}
        writer = ConversationEventBus.instance().subscribe(reply_conv)
        try:
            ff = FlowFile(content=json.dumps(body).encode())
            ff.set_attribute("http.auth.principal", user_id)
            self._make_task().execute(ff)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    item = writer._queue.get(timeout=0.1)
                except Exception:
                    continue
                if not hasattr(item, "event") or item.event != "command_result":
                    continue
                data = item.data
                if isinstance(data, str):
                    data = json.loads(data)
                if data.get("_callId") != call_id:
                    continue
                assert "error" not in data, data.get("error")
                return json.loads(data["result"])
            raise AssertionError("no command_result for load_history")
        finally:
            ConversationEventBus.instance().unsubscribe(reply_conv, writer)

    def _seed(self, messages):
        from core.conv_agent_config import add_agent_to_conv
        ConversationStore.instance().save(
            "c1", messages, ttl=60, user_id="alice@test.com")
        add_agent_to_conv("c1", "assistant", llm_service="default",
                          definition="assistant")

    def _tool_rows(self, body):
        return [m for m in body["messages"] if m.get("type") == "tool_call"]

    def test_a_call_the_relay_still_runs_is_rendered_live(self):
        self._seed([
            {"role": "user", "content": "run it"},
            {"role": "tool_call", "tool_name": "bash",
             "arguments": {"command": "sleep 300"}, "tool_call_id": "toolu_1"},
        ])
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "claude", "cc_tc_id": "toolu_1",
                     "bg_tc_id": "toolu_1", "tool_name": "bash",
                     "started_at": time.time()},
        }

        body = self._load_history("c1")

        rows = self._tool_rows(body)
        assert len(rows) == 1
        assert rows[0].get("live") is True
        assert [c["tc_id"] for c in body["active_tool_calls"]] == ["toolu_1"]

    def test_a_finished_call_is_never_rendered_live(self):
        self._seed([
            {"role": "user", "content": "run it"},
            {"role": "tool_call", "tool_name": "bash",
             "arguments": {"command": "ls"}, "tool_call_id": "toolu_1"},
        ])

        body = self._load_history("c1")

        assert self._tool_rows(body)[0].get("live") is None
        assert body["active_tool_calls"] == []

    def test_a_result_in_the_transcript_beats_a_stale_table_entry(self):
        # The result landed between the page read and the snapshot: the row is
        # finished whatever the in-flight table still says.
        self._seed([
            {"role": "user", "content": "run it"},
            {"role": "tool_call", "tool_name": "bash",
             "arguments": {"command": "ls"}, "tool_call_id": "toolu_1"},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "done"},
        ])
        ToolRelayService._inflight = {
            "rid1": {"conv": "c1", "agent": "claude", "cc_tc_id": "toolu_1",
                     "tool_name": "bash", "started_at": time.time()},
        }

        body = self._load_history("c1")

        assert self._tool_rows(body)[0].get("live") is None

    def test_another_conversations_call_never_marks_a_row(self):
        self._seed([
            {"role": "user", "content": "run it"},
            {"role": "tool_call", "tool_name": "bash",
             "arguments": {"command": "ls"}, "tool_call_id": "toolu_1"},
        ])
        ToolRelayService._inflight = {
            "rid1": {"conv": "other", "agent": "claude", "cc_tc_id": "toolu_1",
                     "tool_name": "bash", "started_at": time.time()},
        }

        body = self._load_history("c1")

        assert self._tool_rows(body)[0].get("live") is None


if __name__ == "__main__":
    unittest.main()
