"""A live error bubble names the agent that failed.

Observed 2026-11-16: with several agents running, the webchat showed
"LLM call failed: Codex interactive turn failed ... overloaded" with no
agent badge, so nothing told which agent the failure belonged to. The
error_event carried agent_name but the handler never passed a source to
addMsg, so the error row rendered an empty badge.
"""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tasks.ai.agent_emitter import StreamEmitter

CHAT_UI = Path(__file__).resolve().parents[1] / "tasks" / "io" / "chat_ui"

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const rendered = [];
const el = { textContent: '', disabled: false };
const ctx = {
  console, JSON, setTimeout: () => 0, Date,
  eventSource: { addEventListener: (name, fn) => { listeners[name] = fn; } },
  lastSSEActivity: 0,
  captureConversationSessionCallback: (fn) => fn,
  isAgentTerminalCurrent: () => true,
  addMsg: (role, text, extra) => { rendered.push({ role, text, extra }); return {}; },
  t: (key) => key,
  _finalizeLiveToolCalls: () => {}, clearStream: () => {},
  trackAgentDone: () => {}, updateActivePanel: () => {},
  activeInteractions: {}, sending: true,
  document: { getElementById: () => el },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
vm.runInContext('_sseWireB()', ctx);
listeners['error_event']({ data: process.argv[2] });
process.stdout.write(JSON.stringify(rendered));
"""


def _render_error_event(payload):
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(CHAT_UI / "sse_handlers_b.js"),
         json.dumps(payload)],
        capture_output=True, text=True, timeout=30, check=True)
    rows = json.loads(proc.stdout)
    assert len(rows) == 1 and rows[0]["role"] == "error"
    return rows[0]


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not available")


@needs_node
def test_error_bubble_uses_the_emitted_agent_source():
    source = {"type": "agent", "name": "GameDev4",
              "llm_service": "codex_interactive_llm_service"}
    row = _render_error_event({
        "message": "LLM call failed: overloaded", "agent_name": "GameDev4",
        "source": source, "ts": 1.0})
    assert row["extra"]["source"] == source
    assert row["extra"]["transcript"] is True


@needs_node
def test_error_bubble_falls_back_to_agent_name():
    # Crash / context-preparation / workflow emitters send agent_name only.
    row = _render_error_event({
        "message": "Agent loop crashed: boom", "agent_name": "GameDev7"})
    assert row["extra"]["source"] == {"type": "agent", "name": "GameDev7"}


@needs_node
def test_error_bubble_without_agent_has_no_badge_source():
    row = _render_error_event({"message": "boom"})
    assert row["extra"]["source"] is None


class _Bus:
    def __init__(self):
        self.events = []

    def publish_event(self, cid, event, data):
        self.events.append((event, data))


def _emitter():
    bus = _Bus()
    ctx = {"active_agent_name": "GameDev4",
           "active_llm_service": "codex_interactive_llm_service",
           "request_msg_id": "turn-1", "channel": "web",
           "client": SimpleNamespace(provider="codex-interactive",
                                     base_url="", default_model="gpt")}
    return StreamEmitter("conv1", bus, ctx, agent=None, gen_key="k",
                         generation=1), bus


@pytest.mark.parametrize("emit", [
    lambda e: e.on_fatal_error("LLM call failed: overloaded"),
    lambda e: e.on_error(RuntimeError("boom")),
])
def test_emitter_error_events_carry_the_agent_source(emit):
    emitter, bus = _emitter()
    emit(emitter)
    [(event, data)] = bus.events
    assert event == "error_event"
    assert data["agent_name"] == "GameDev4"
    assert data["source"]["type"] == "agent"
    assert data["source"]["name"] == "GameDev4"
    assert data["source"]["llm_service"] == "codex_interactive_llm_service"
