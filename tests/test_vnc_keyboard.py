"""NumLock synchronization uses the selected VNC target and preserves input order."""

import json
import ctypes
import os
import select
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import capability_auth as ca
from services import vnc_keyboard, vnc_proxy


@pytest.fixture()
def sessions(tmp_path):
    ca._reset_for_tests()
    ca.init_db(tmp_path / "caps.json")
    with vnc_proxy._lock:
        vnc_proxy._sessions.clear()
    yield
    with vnc_proxy._lock:
        vnc_proxy._sessions.clear()
    ca._reset_for_tests()


class Request:
    def __init__(self, token, user="alice", method="GET"):
        self.path_params = {"session_id": "desktop", "token": token, "path": "keyboard-state"}
        self.auth_user_id = user
        self.remote_addr = "127.0.0.1"
        self.method = method
        self.completed = None
        self.done = threading.Event()

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)
        self.done.set()


@pytest.mark.parametrize("local,display", [(False, ":102"), (True, None), (True, ":99")])
def test_reads_selected_relay_and_display(local, display):
    relay = Mock()
    relay._request.return_value = {"numlock": True}
    state = vnc_keyboard.read_keyboard_state({
        "keyboard_relay_service": relay, "keyboard_local": local,
        "keyboard_display": display, "host": "172.17.0.2", "port": 6080,
    })
    assert state == {"numlock": True}
    relay._request.assert_called_once_with(
        "screen_keyboard_state", ".", _request_timeout=3, timeout=2,
        local=local, display=display)
    relay.exec_argv.assert_not_called()


def test_reads_cli_login_container_without_shell(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout='{"numlock": false}'))
    monkeypatch.setattr(vnc_keyboard.subprocess, "run", run)
    monkeypatch.setattr("core.docker_utils.docker_cmd", lambda: ["docker"])
    assert vnc_keyboard.read_keyboard_state({"container": "pf-cc-login-123"}) == {"numlock": False}
    args, kwargs = run.call_args
    assert args[0] == ["docker", "exec", "-e", "DISPLAY=:99", "pf-cc-login-123",
                       "python3", "-c", vnc_keyboard.KEYBOARD_STATE_SCRIPT]
    assert kwargs == dict(capture_output=True, text=True, timeout=3, check=True)


@pytest.mark.parametrize("result", [
    {"error": "probe failed", "numlock": True},
    {"ok": False, "numlock": True},
    {"numlock": 1},
    {"numlock": "invalid"},
])
def test_invalid_probe_output_is_not_an_led_state(result):
    relay = Mock()
    relay._request.return_value = result
    with pytest.raises((ValueError, RuntimeError)):
        vnc_keyboard.read_keyboard_state({"relay_service": relay})


def test_keyboard_route_is_async_and_uncached(sessions, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def probe(session):
        entered.set()
        assert release.wait(2)
        return {"numlock": False}

    monkeypatch.setattr(vnc_keyboard, "read_keyboard_state", probe)
    token = vnc_proxy.register_session("desktop", 6080, owner_user_id="alice")
    request = Request(token)
    vnc_proxy.vnc_http_proxy(request)
    try:
        assert entered.wait(1)
        assert request.completed is None
    finally:
        release.set()
    assert request.done.wait(1)
    assert request.completed[0] == 200
    assert request.completed[1]["Cache-Control"] == "no-store"
    assert json.loads(request.completed[2]) == {"numlock": False}


@pytest.mark.parametrize("kind", ["wrong_user", "wrong_token", "revoked"])
def test_keyboard_route_requires_session_capability(sessions, monkeypatch, kind):
    probe = Mock()
    monkeypatch.setattr(vnc_keyboard, "read_keyboard_state", probe)
    token = vnc_proxy.register_session("desktop", 6080, owner_user_id="alice")
    if kind == "revoked":
        vnc_proxy.unregister_session("desktop")
    request = Request("invalid" if kind == "wrong_token" else token,
                      user="bob" if kind == "wrong_user" else "alice")
    vnc_proxy.vnc_http_proxy(request)
    assert request.done.wait(1)
    assert request.completed[0] in (401, 403, 404)
    probe.assert_not_called()


def test_keyboard_route_refuses_non_get(sessions, monkeypatch):
    probe = Mock()
    monkeypatch.setattr(vnc_keyboard, "read_keyboard_state", probe)
    token = vnc_proxy.register_session("desktop", 6080, owner_user_id="alice")
    request = Request(token, method="POST")
    vnc_proxy.vnc_http_proxy(request)
    assert request.completed[0] == 405
    probe.assert_not_called()


def test_keyboard_route_rejects_result_for_replaced_session(sessions, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def probe(session):
        entered.set()
        assert release.wait(2)
        return {"numlock": True}

    monkeypatch.setattr(vnc_keyboard, "read_keyboard_state", probe)
    token = vnc_proxy.register_session("desktop", 6080, owner_user_id="alice")
    request = Request(token)
    vnc_proxy.vnc_http_proxy(request)
    try:
        assert entered.wait(1)
        vnc_proxy.register_session("desktop", 6081, owner_user_id="alice")
    finally:
        release.set()
    assert request.done.wait(1)
    assert request.completed[0] == 409


def test_keyboard_route_probe_failure_is_bounded_and_unknown(sessions, monkeypatch):
    monkeypatch.setattr(vnc_keyboard, "read_keyboard_state", Mock(side_effect=TimeoutError))
    token = vnc_proxy.register_session("desktop", 6080, owner_user_id="alice")
    request = Request(token)
    vnc_proxy.vnc_http_proxy(request)
    assert request.done.wait(1)
    assert request.completed[0] == 503
    assert json.loads(request.completed[2])["numlock"] is None


@pytest.mark.parametrize("value,expected", [(0, False), (1, True), (0x8000, False), (0x8001, True)])
def test_windows_probe_reads_toggle_bit(monkeypatch, capsys, value, expected):
    get_key_state = Mock(return_value=value)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        user32=SimpleNamespace(GetKeyState=get_key_state)), raising=False)
    exec(vnc_keyboard.KEYBOARD_STATE_SCRIPT, {})
    assert json.loads(capsys.readouterr().out) == {"numlock": expected}
    get_key_state.assert_called_once_with(0x90)
    from tools import screen_actions
    assert screen_actions._keyboard_state({}) == {"numlock": expected}


def test_unsupported_platform_does_not_invent_state(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    exec(vnc_keyboard.KEYBOARD_STATE_SCRIPT, {})
    assert json.loads(capsys.readouterr().out) == {"numlock": None}
    from tools import screen_actions
    assert screen_actions._keyboard_state({}) == {"numlock": None}


@pytest.mark.skipif(sys.platform != "linux", reason="isolated X11 integration")
def test_probe_observes_real_xkb_numlock():
    xvfb, xdotool = shutil.which("Xvfb"), shutil.which("xdotool")
    if not xvfb or not xdotool:
        pytest.skip("Xvfb and xdotool are required")
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [xvfb, "-displayfd", str(write_fd), "-screen", "0", "640x480x24", "-nolisten", "tcp", "-noreset"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 5)[0], "Xvfb did not become ready"
        number = os.read(read_fd, 64).decode().strip()
        assert number.isdecimal()
        env = {**os.environ, "DISPLAY": ":" + number}

        def read_state():
            result = subprocess.run([sys.executable, "-c", vnc_keyboard.KEYBOARD_STATE_SCRIPT],
                                    env=env, capture_output=True, text=True, timeout=3, check=True)
            return json.loads(result.stdout)["numlock"]

        before = read_state()
        from tools import screen_actions
        assert screen_actions._keyboard_state({"display": ":" + number}) == {"numlock": before}
        assert type(before) is bool
        subprocess.run([xdotool, "key", "Num_Lock"], env=env, timeout=3, check=True)
        assert read_state() is not before
        assert screen_actions._keyboard_state({"display": ":" + number}) == {"numlock": not before}
        subprocess.run([xdotool, "key", "Num_Lock"], env=env, timeout=3, check=True)
        assert read_state() is before
    finally:
        os.close(read_fd)
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("mode", ["pawflow", "cua"])
def test_keyboard_probe_uses_frozen_relay_child_without_external_python(monkeypatch, mode):
    from tools import screen_actions

    monkeypatch.delenv(screen_actions._CHILD_ENV, raising=False)
    monkeypatch.setenv("PAWFLOW_SCREEN_MODE", mode)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/pawflow/pawflow-relay")
    run = Mock(return_value=SimpleNamespace(
        returncode=0, stdout='{"numlock": true}', stderr=""))
    monkeypatch.setattr(screen_actions.subprocess, "run", run)
    assert screen_actions.handle_screen_action(
        "screen_keyboard_state", {"display": ":104", "timeout": 2}) == {"numlock": True}
    args, kwargs = run.call_args
    assert args[0] == ["/opt/pawflow/pawflow-relay",
                       "__pawflow_screen_action_child__", "screen_keyboard_state"]
    assert json.loads(kwargs["input"]) == {"display": ":104", "timeout": 2}
    assert kwargs["timeout"] == 2


def test_keyboard_action_dispatch_uses_selected_display(monkeypatch):
    from tools import fs_actions
    import screen_actions

    probe = Mock(return_value={"numlock": False})
    monkeypatch.setenv(screen_actions._CHILD_ENV, "1")
    monkeypatch.setattr(screen_actions, "_keyboard_state", probe)
    request = {"display": ":105", "timeout": 2}
    assert fs_actions.ACTIONS["screen_keyboard_state"]("/workspace", "/workspace", request) == {
        "numlock": False}
    probe.assert_called_once_with(request)


def test_host_keyboard_probe_uses_explicit_x11_display_without_environment_change(monkeypatch):
    from tools import screen_actions

    x11 = SimpleNamespace(
        XOpenDisplay=Mock(return_value=123), XCloseDisplay=Mock(),
        XInternAtom=Mock(return_value=9), XkbGetNamedIndicator=Mock(return_value=1))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", Mock(return_value=x11))
    monkeypatch.setenv("DISPLAY", ":99")
    assert screen_actions._keyboard_state({"display": ":103"}) == {"numlock": False}
    x11.XOpenDisplay.assert_called_once_with(b":103")
    x11.XCloseDisplay.assert_called_once_with(123)
    assert os.environ["DISPLAY"] == ":99"

def test_novnc_numlock_input_order_and_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    script = vnc_proxy._PAWFLOW_NOVNC_CLIENT_SCRIPT.decode().split("<script>", 1)[1].split("</script>", 1)[0]
    harness = """
const assert = require('node:assert/strict');
const windowEvents = {}, documentEvents = {};
global.window = {
  location: {href: 'https://pawflow.test/vnc/desktop/cap/vnc.html?path=/vnc/desktop/cap/websockify'},
  isSecureContext: false,
  addEventListener(name, fn) { windowEvents[name] = fn; }
};
global.document = {addEventListener(name, fn) { documentEvents[name] = fn; }};
Object.defineProperty(global, 'navigator', {value: {platform: 'Win32'}, configurable: true});
let remote = false, reads = 0, resolveRead = null, failRead = false, deferRead = false;
global.fetch = async (url, options) => {
  assert.equal(String(url), 'https://pawflow.test/vnc/desktop/cap/keyboard-state');
  assert.equal(options.cache, 'no-store');
  reads++;
  if (failRead) throw Error('unavailable');
  if (deferRead) return new Promise(resolve => { resolveRead = resolve; });
  return {ok: true, json: async () => ({numlock: remote})};
};
function makeRfb() {
  const callbacks = {};
  const target = {
    sent: [], received: [], viewOnly: false, _rfbConnectionState: 'connected',
    addEventListener(name, fn) { callbacks[name] = fn; },
    removeEventListener(name, fn) { if (callbacks[name] === fn) delete callbacks[name]; },
    sendKey(keysym, code, down) {
      if (down === undefined) {
        this.sendKey(keysym, code, true);
        this.sendKey(keysym, code, false);
        return;
      }
      this.sent.push([code, down]);
      if (code === 'NumLock' && down) remote = !remote;
    },
    fire(name) { if (callbacks[name]) callbacks[name](); }
  };
  target._keyboard = {onkeyevent: (keysym, code, down, numlock, capslock) => {
    target.received.push([code, down, numlock, capslock]);
    target.sendKey(keysym, code, down);
  }};
  return target;
}
function key(rfb, code, down, numlock = null) {
  rfb._keyboard.onkeyevent(1, code, down, numlock, false);
}
const flush = () => new Promise(resolve => setImmediate(resolve));
""" + script + """
(async () => {
  const first = makeRfb();
  window.PawFlowNoVNC.attach(first);
  deferRead = true;
  key(first, 'Numpad1', true, true);
  key(first, 'Numpad1', false);
  key(first, 'KeyA', true, true);
  key(first, 'KeyA', false);
  await flush();
  assert.deepEqual(first.sent, []);
  assert.equal(reads, 1);
  resolveRead({ok: true, json: async () => ({numlock: false})});
  deferRead = false;
  await flush();
  assert.deepEqual(first.sent, [
    ['NumLock', true], ['NumLock', false], ['Numpad1', true], ['Numpad1', false],
    ['KeyA', true], ['KeyA', false]]);
  assert.equal(first.received[0][2], null, 'native noVNC must not apply a second correction');
  assert.equal(remote, true);

  // The browser regained focus after NumLock changed elsewhere.
  remote = false;
  windowEvents.focus();
  key(first, 'Numpad2', true, true);
  key(first, 'Numpad2', false);
  await flush();
  assert.equal(reads, 2);
  assert.deepEqual(first.sent.slice(-4), [
    ['NumLock', true], ['NumLock', false], ['Numpad2', true], ['Numpad2', false]]);

  // A physical press describes the desired state, and is never toggled twice.
  key(first, 'NumLock', true, false);
  key(first, 'NumLock', false);
  await flush();
  assert.equal(remote, false);
  assert.deepEqual(first.sent.slice(-2), [['NumLock', true], ['NumLock', false]]);

  // A local-host viewer may share the very keyboard that was just toggled.
  remote = true;
  const sentBefore = first.sent.length;
  key(first, 'NumLock', true, true);
  key(first, 'NumLock', false);
  await flush();
  assert.equal(first.sent.length, sentBefore);
  assert.equal(remote, true);

  // Failed/unsupported LED reads never invent a remote state or block typing.
  failRead = true;
  windowEvents.focus();
  key(first, 'Numpad3', true, false);
  key(first, 'Numpad3', false);
  await flush();
  assert.deepEqual(first.sent.slice(-2), [['Numpad3', true], ['Numpad3', false]]);
  const oldReads = reads;
  key(first, 'KeyB', true, false);
  key(first, 'KeyB', false);
  await flush();
  assert.equal(reads, oldReads);
  failRead = false;
  const mac = makeRfb();
  window.PawFlowNoVNC.attach(mac);
  key(mac, 'Numpad4', true, null);
  key(mac, 'Numpad4', false);
  await flush();
  assert.equal(reads, oldReads);
  assert.deepEqual(mac.sent, [['Numpad4', true], ['Numpad4', false]]);

  // No queued keys can leak into a disconnected or replacement RFB.
  const old = makeRfb();
  window.PawFlowNoVNC.attach(old);
  deferRead = true;
  key(old, 'KeyC', true, true);
  key(old, 'KeyC', false);
  await flush();
  old.fire('disconnect');
  const replacement = makeRfb();
  window.PawFlowNoVNC.attach(replacement);
  resolveRead({ok: true, json: async () => ({numlock: false})});
  await flush();
  assert.deepEqual(old.sent, []);
  assert.deepEqual(replacement.sent, []);
  deferRead = false;

  // A reconnect on the same object cannot revive pre-disconnect keystrokes.
  deferRead = true;
  key(replacement, 'KeyD', true, true);
  key(replacement, 'KeyD', false);
  await flush();
  replacement.fire('disconnect');
  replacement.fire('connect');
  resolveRead({ok: true, json: async () => ({numlock: false})});
  await flush();
  assert.deepEqual(replacement.sent, []);
  deferRead = false;

  // Read-only mode must not query or send keyboard events.
  replacement.viewOnly = true;
  const readsBeforeView = reads;
  key(replacement, 'Numpad1', true, true);
  await flush();
  assert.equal(reads, readsBeforeView);
  assert.deepEqual(replacement.sent, []);

  // Repeat and paste shortcuts cannot overtake an in-flight keyboard read.
  const shortcuts = makeRfb();
  remote = false;
  deferRead = true;
  window.PawFlowNoVNC.attach(shortcuts);
  key(shortcuts, 'Numpad5', true, true);
  key(shortcuts, 'Numpad5', false);
  const event = {
    getModifierState: () => true, preventDefault() {}, stopImmediatePropagation() {}
  };
  documentEvents.keydown({...event, key: 'Backspace', code: 'Backspace', repeat: true});
  documentEvents.keydown({...event, key: 'v', code: 'KeyV', ctrlKey: true});
  key(shortcuts, 'KeyE', true, true);
  key(shortcuts, 'KeyE', false);
  await flush();
  assert.deepEqual(shortcuts.sent, []);
  resolveRead({ok: true, json: async () => ({numlock: false})});
  await flush();
  assert.deepEqual(shortcuts.sent, [
    ['NumLock', true], ['NumLock', false], ['Numpad5', true], ['Numpad5', false],
    ['Backspace', true], ['Backspace', false], ['ControlLeft', true],
    ['KeyV', true], ['KeyV', false], ['ControlLeft', false], ['KeyE', true], ['KeyE', false]
  ]);

  // Clipboard permission is requested synchronously inside the user gesture.
  const paste = makeRfb();
  let clipboardReads = 0;
  window.isSecureContext = true;
  navigator.clipboard = {readText() { clipboardReads++; return Promise.resolve('host text'); }};
  paste.clipboardPasteFrom = (text) => paste.sent.push(['clipboard', text]);
  window.PawFlowNoVNC.attach(paste);
  documentEvents.keydown({...event, key: 'v', code: 'KeyV', ctrlKey: true});
  assert.equal(clipboardReads, 1);
  await flush();
  assert.deepEqual(paste.sent, []);
  resolveRead({ok: true, json: async () => ({numlock: true})});
  await flush();
  assert.deepEqual(paste.sent, [
    ['clipboard', 'host text'], ['ControlLeft', true], ['KeyV', true],
    ['KeyV', false], ['ControlLeft', false]
  ]);

  // Switching to read-only while the request is pending also suppresses input.
  const readonly = makeRfb();
  window.PawFlowNoVNC.attach(readonly);
  key(readonly, 'KeyF', true, true);
  await flush();
  readonly.viewOnly = true;
  resolveRead({ok: true, json: async () => ({numlock: false})});
  await flush();
  assert.deepEqual(readonly.sent, []);
// Missing private noVNC APIs must leave ordinary input and shortcuts alone.
  deferRead = false;
  for (const missing of ['keyboard', 'callback', 'connection']) {
    const fallback = makeRfb();
    if (missing === 'keyboard') delete fallback._keyboard;
    if (missing === 'callback') fallback._keyboard.onkeyevent = null;
    if (missing === 'connection') delete fallback._rfbConnectionState;
    const original = fallback._keyboard && fallback._keyboard.onkeyevent;
    window.PawFlowNoVNC.attach(fallback);
    const before = reads;
    const untouched = {
      preventDefault() { throw Error('native key was swallowed'); },
      stopImmediatePropagation() { throw Error('native key was swallowed'); }
    };
    documentEvents.keydown({...untouched, key: 'v', code: 'KeyV', ctrlKey: true});
    documentEvents.keydown({...untouched, key: 'Backspace', code: 'Backspace', repeat: true});
    if (original) {
      assert.equal(fallback._keyboard.onkeyevent, original);
      key(fallback, 'KeyG', true);
      key(fallback, 'KeyG', false);
      assert.deepEqual(fallback.sent, [['KeyG', true], ['KeyG', false]]);
    }
    await flush();
    assert.equal(reads, before);
  }

  // Preserve the receiver of a regular function callback, and detach safely
  // even when noVNC has already disposed its private keyboard object.
  const receiver = makeRfb();
  receiver._keyboard.onkeyevent = function () { this.called = true; };
  const keyboard = receiver._keyboard;
  const callback = keyboard.onkeyevent;
  window.PawFlowNoVNC.attach(receiver);
  key(receiver, 'KeyH', true);
  await flush();
  assert.equal(keyboard.called, true);
  delete receiver._keyboard;
  window.PawFlowNoVNC.attach(null);
  assert.equal(keyboard.onkeyevent, callback);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
