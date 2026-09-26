"""/nimsg sends a user message that does not interrupt the agent."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

CHAT_UI = Path(__file__).resolve().parents[1] / "tasks" / "io" / "chat_ui"

_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const sent = [];
const notes = [];
const ctx = {
  console, JSON,
  parseQuotedArgs: (s) => s.trim().split(/\s+/),
  stripTarget: (s) => s.replace(/^@/, ''),
  resolveAgentName: (s) => s,
  selectedAgent: 'claude',
  addMsg: (kind, text) => { notes.push([kind, text]); return {}; },
  t: (key) => key,
  pendingFiles: [], renderAttachments: () => {},
  sourceBadge: () => '', escapeHtml: (s) => s, renderUserAttachments: () => '',
  clearStream: () => {}, connectSSE: () => {}, _checkServerRestart: () => {},
  document: { getElementById: () => ({ value: '0', textContent: '' }) },
  getAuthHeaders: () => ({}), API: '/api/agent', conversationId: 'conv',
  fetch: (url, opts) => { sent.push(JSON.parse(opts.body));
    return { then: () => ({ then: () => ({ catch: () => {} }) }) }; },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
for (const line of JSON.parse(process.argv[2])) {
  const [cmd, text] = line;
  vm.runInContext(cmd === 'nimsg'
    ? `cmdMsg(${JSON.stringify(text)}, { noInterrupt: true })`
    : `cmdMsg(${JSON.stringify(text)})`, ctx);
}
process.stdout.write(JSON.stringify({ sent, notes }));
"""


def _run(lines):
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(CHAT_UI / "cmd_agent.js"),
         json.dumps(lines)],
        capture_output=True, text=True, timeout=30, check=True)
    return json.loads(proc.stdout)


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not available")


def test_nimsg_sends_the_message_without_interrupt():
    out = _run([["nimsg", "/nimsg @GameDev7 FYI build is green"]])
    assert out["sent"] == [{"message": "FYI build is green",
                            "target_agent": "GameDev7",
                            "no_interrupt": True,
                            "conversation_id": "conv"}]


def test_nimsg_without_target_uses_the_selected_agent():
    out = _run([["nimsg", "/nimsg check the logs later"]])
    assert out["sent"][0]["target_agent"] == "claude"
    assert out["sent"][0]["no_interrupt"] is True


def test_msg_keeps_interrupting():
    out = _run([["msg", "/msg @GameDev7 stop"]])
    assert "no_interrupt" not in out["sent"][0]


def test_nimsg_to_all_is_refused():
    out = _run([["nimsg", "/nimsg @ALL hello"]])
    assert out["sent"] == []
    assert "/nimsg needs one agent" in out["notes"][-1][1]


def test_nimsg_is_wired_and_documented():
    commands = (CHAT_UI / "commands.js").read_text(encoding="utf-8")
    help_text = (CHAT_UI / "commands_help.js").read_text(encoding="utf-8")
    assert "'/nimsg':       (text, parts, cmd) => cmdMsg(text, { noInterrupt: true })" in commands
    assert "usage: '/nimsg [@agent] <message>'" in help_text
