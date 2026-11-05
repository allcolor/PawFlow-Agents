"""Desktop sound plays from exactly one source, chosen by the viewer shown.

voxl streams the desktop audio with its video; PawFlow's own audio stream
(audio.js) is only for noVNC, which has no audio. Opening both doubled the
sound with a slight delay ("like in a cave").
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TABS = Path("tasks/io/chat_ui/tabs.js")
CHAT_UI = Path("tasks/io/chat_ui")

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const calls = [];
function el() {
  return { dataset: {}, style: {}, children: [], textContent: '', title: '',
           appendChild(c) { this.children.push(c); return c; },
           querySelector(sel) {
             if (sel === 'iframe') return this.children.find(c => c.tag === 'iframe') || null;
             if (sel === '.desktop-viewer-btn') return this.children.find(c => c.className === 'desktop-viewer-btn') || null;
             return null; },
           replaceWith() {}, remove() {}, addEventListener() {} };
}
const panels = {};
const spacer = el(); spacer.parentNode = { insertBefore() {} };
const context = {
  console, calls,
  window: { isSecureContext: true, VideoDecoder: function () {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  t: (k) => k,
  audioConnect: (s, tok) => calls.push(['connect', s, tok]),
  audioDisconnect: () => calls.push(['disconnect']),
  switchTab() {},
  document: {
    readyState: 'complete', addEventListener() {},
    getElementById: (id) => panels[id] || null,
    querySelector: (sel) => sel === '.tab-spacer' ? spacer : null,
    querySelectorAll: () => [],
    createElement: (tag) => { const e = el(); e.tag = tag; return e; },
  },
  workspaceRegisterSurface: (panel) => { panels[panel.id] = panel; },
};
context.window.VideoDecoder = function () {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const resp = { voxl_url: '/v', novnc_url: '/n', audio_session: 'as', audio_token: 'at' };
const steps = {};
vm.runInContext("addDesktopTab('r', '/v', " + JSON.stringify(resp) + ")", context);
steps.open_voxl = calls.splice(0);
vm.runInContext("toggleDesktopViewer('desktop-r')", context);
steps.to_novnc = calls.splice(0);
vm.runInContext("toggleDesktopViewer('desktop-r')", context);
steps.back_to_voxl = calls.splice(0);
vm.runInContext("addDesktopTab('r', '/n', " + JSON.stringify(resp) + ")", context);
steps.reopen_novnc = calls.splice(0);
process.stdout.write(JSON.stringify(steps));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_one_audio_source_follows_the_viewer():
    result = subprocess.run(["node", "-e", _HARNESS, str(TABS)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    steps = json.loads(result.stdout)
    # voxl plays its own audio: PawFlow's stream stays closed.
    assert steps["open_voxl"] == [["disconnect"]]
    assert steps["to_novnc"] == [["connect", "as", "at"]]
    assert steps["back_to_voxl"] == [["disconnect"]]
    assert steps["reopen_novnc"] == [["connect", "as", "at"]]


def test_desktop_openers_leave_audio_to_the_viewer_rule():
    for name in ("desktop_dock.js", "terminal_commands.js"):
        src = (CHAT_UI / name).read_text(encoding="utf-8")
        # Only the audio-only tab (/audio) still opens the stream directly.
        assert src.count("audioConnect(") == (1 if name == "terminal_commands.js" else 0), name
