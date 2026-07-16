"""Tests for the CUA screen backend (PAWFLOW_SCREEN_MODE=cua)."""

import base64
import json
import stat
import sys

import pytest

from tools import screen_actions, screen_actions_cua

# 1x1 transparent PNG
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

_FAKE_BIN = '''#!{python}
import json, sys
log_path = {log_path!r}
tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {{}}
with open(log_path, "a") as fh:
    fh.write(json.dumps({{"tool": tool, "args": args}}) + "\\n")
if tool == "get_desktop_state":
    out = args.get("screenshot_out_file")
    if out:
        with open(out, "wb") as fh:
            fh.write({png!r})
    print("\\u2705 desktop state captured")
elif tool == "get_cursor_position":
    print("\\u2705 cursor")
    print(json.dumps({{"x": 12, "y": 34}}))
elif tool == "click" and args.get("x") == 999:
    print("background_unavailable: occluded surface on this compositor")
    sys.exit(1)
elif tool == "list_windows":
    print("\\u2705 windows")
    print(json.dumps([{{"pid": 42, "window_id": "w1", "title": "Editor"}}]))
elif tool == "get_window_state":
    out = args.get("screenshot_out_file")
    if out:
        with open(out, "wb") as fh:
            fh.write({png!r})
    print("\\u2705 window state")
    print(json.dumps({{"elements": [{{"index": 0, "role": "button",
                                     "name": "OK"}}],
                      "markdown": "- [0] button OK"}}))
else:
    print("\\u2705 ok")
'''


@pytest.fixture()
def fake_driver(tmp_path, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    bin_path = tmp_path / "fake-cua-driver"
    bin_path.write_text(_FAKE_BIN.format(
        python=sys.executable, log_path=str(log_path), png=_PNG_1X1))
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(screen_actions_cua.MODE_ENV, "cua")
    monkeypatch.setenv(screen_actions_cua.BIN_ENV, str(bin_path))
    monkeypatch.setenv(screen_actions_cua.SESSION_ENV, "test-session")

    def calls():
        if not log_path.exists():
            return []
        return [json.loads(line) for line in
                log_path.read_text().splitlines() if line.strip()]
    return calls


def _guard(req):
    """Attach a screen guard that always validates (monkeypatched below)."""
    req["_screen_guard"] = {"region": {"x": 0, "y": 0, "width": 1,
                                       "height": 1},
                            "expected_image": "aa=="}
    return req


@pytest.fixture()
def guard_ok(monkeypatch):
    monkeypatch.setattr(screen_actions, "_validate_screen_guard",
                        lambda req: None)


class TestDispatch:
    def test_mode_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(screen_actions_cua.MODE_ENV, raising=False)
        assert screen_actions_cua.cua_mode_enabled() is False

    def test_handle_screen_action_routes_to_cua(self, fake_driver):
        result = screen_actions.handle_screen_action(
            "screen_mouse_position", {})
        assert result == {"x": 12, "y": 34}
        assert fake_driver()[0]["tool"] == "get_cursor_position"

    def test_unknown_action(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua("screen_nope", {})
        assert "error" in out


class TestActions:
    def test_screenshot_roundtrip(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_screenshot", {})
        assert out["width"] == 1 and out["height"] == 1
        assert base64.b64decode(out["image"]) == _PNG_1X1
        call = fake_driver()[0]
        assert call["tool"] == "get_desktop_state"
        assert call["args"]["session"] == "test-session"

    def test_click_maps_desktop_scope(self, fake_driver, guard_ok):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_click", _guard({"x": 10, "y": 20, "button": "right"}))
        assert out["clicked"] is True and out["cua"] is True
        call = fake_driver()[0]
        assert call["tool"] == "click"
        assert call["args"] == {"x": 10, "y": 20, "scope": "desktop",
                                "button": "right",
                                "session": "test-session"}

    def test_double_click_sets_click_count(self, fake_driver, guard_ok):
        screen_actions_cua.handle_screen_action_cua(
            "screen_double_click", _guard({"x": 1, "y": 2}))
        assert fake_driver()[0]["args"]["click_count"] == 2

    def test_click_requires_guard(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_click", {"x": 1, "y": 2})
        assert out.get("stale_screen") is True
        assert fake_driver() == []  # refused before reaching the driver

    def test_refusal_surfaces_verbatim(self, fake_driver, guard_ok):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_click", _guard({"x": 999, "y": 1}))
        assert "background_unavailable" in out["error"]

    def test_type_and_key_and_scroll(self, fake_driver):
        assert screen_actions_cua.handle_screen_action_cua(
            "screen_type", {"text": "hello"})["typed"] == 5
        assert screen_actions_cua.handle_screen_action_cua(
            "screen_key", {"key": "ctrl+s"})["pressed"] == "ctrl+s"
        assert screen_actions_cua.handle_screen_action_cua(
            "screen_scroll", {"x": 5, "y": 6, "amount": 2})["scrolled"] == 2
        tools = [c["tool"] for c in fake_driver()]
        assert tools == ["type_text", "press_key", "scroll"]
        for call in fake_driver():
            assert call["args"]["scope"] == "desktop"

    def test_move_is_honest_noop(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_move", {"x": 3, "y": 4})
        assert out["moved"] is False
        assert out["reason"] == "cua_background_mode"
        assert fake_driver() == []

    def test_status_reports_binary(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_status", {})
        assert out["mode"] == "cua"
        assert fake_driver()[0]["tool"] == "health_report"


class TestAXActions:
    def test_windows_lists(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_windows", {})
        assert out["windows"][0] == {"pid": 42, "window_id": "w1",
                                     "title": "Editor"}
        assert fake_driver()[0]["tool"] == "list_windows"

    def test_window_state_requires_target(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_window_state", {})
        assert "pid and/or window_id" in out["error"]
        assert fake_driver() == []  # never reaches the driver

    def test_window_state_roundtrip(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_window_state", {"pid": 42})
        assert out["state"]["elements"][0]["role"] == "button"
        assert out["state"]["markdown"] == "- [0] button OK"
        assert base64.b64decode(out["image"]) == _PNG_1X1
        assert (out["width"], out["height"]) == (1, 1)
        call = fake_driver()[0]
        assert call["tool"] == "get_window_state"
        assert call["args"]["pid"] == 42
        assert call["args"]["session"] == "test-session"

    def test_element_click_skips_pixel_guard(self, fake_driver):
        # No _screen_guard attached: element addressing must not require
        # the desktop-pixel guard (the window may be backgrounded).
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_click", {"element_index": 3, "pid": 42})
        assert out["clicked"] is True and out["element_index"] == 3
        call = fake_driver()[0]
        assert call["tool"] == "click"
        assert call["args"]["element_index"] == 3
        assert call["args"]["pid"] == 42
        assert "scope" not in call["args"] and "x" not in call["args"]

    def test_element_click_requires_window_target(self, fake_driver):
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_click", {"element_index": 3})
        assert "pid and/or window_id" in out["error"]
        assert fake_driver() == []

    def test_element_double_click_and_type(self, fake_driver):
        screen_actions_cua.handle_screen_action_cua(
            "screen_double_click", {"element_index": 1, "window_id": "w1"})
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_type", {"element_index": 1, "window_id": "w1",
                            "text": "abc"})
        assert out["typed"] == 3
        first, second = fake_driver()
        assert first["tool"] == "click"
        assert first["args"]["click_count"] == 2
        assert first["args"]["window_id"] == "w1"
        assert second["tool"] == "type_text"
        assert second["args"]["window_id"] == "w1"
        assert "scope" not in second["args"]


class TestContainerDispatch:
    """fs_screen.py (relay container / Xvfb) routes through cua too."""

    @pytest.fixture()
    def no_desktop(self, monkeypatch):
        from tools import fs_screen
        monkeypatch.setattr(fs_screen, "_ensure_desktop", lambda: None)
        return fs_screen

    def test_routes_to_cua(self, fake_driver, no_desktop):
        out = no_desktop.action_screen_mouse_position(None, None, {})
        assert out == {"x": 12, "y": 34}
        assert fake_driver()[0]["tool"] == "get_cursor_position"

    def test_ax_actions_available(self, fake_driver, no_desktop):
        out = no_desktop.action_screen_windows(None, None, {})
        assert out["windows"][0]["pid"] == 42
        out = no_desktop.action_screen_window_state(None, None, {"pid": 42})
        assert out["state"]["markdown"] == "- [0] button OK"

    def test_pawflow_mode_falls_through_to_xdotool(self, monkeypatch,
                                                   no_desktop):
        monkeypatch.setenv(screen_actions_cua.MODE_ENV, "pawflow")
        xdo_calls = []
        monkeypatch.setattr(no_desktop, "_xdo",
                            lambda *a, **k: xdo_calls.append(a) or "")
        out = no_desktop.action_screen_move(None, None, {"x": 1, "y": 2})
        assert out == {"moved": True, "x": 1, "y": 2}
        assert xdo_calls == [("mousemove", "1", "2")]

    def test_ax_actions_error_without_cua(self, monkeypatch, no_desktop):
        monkeypatch.setenv(screen_actions_cua.MODE_ENV, "pawflow")
        out = no_desktop.action_screen_windows(None, None, {})
        assert "PAWFLOW_SCREEN_MODE=cua" in out["error"]
        status = no_desktop.action_screen_status(None, None, {})
        assert status["mode"] == "pawflow"


class TestFailureModes:
    def test_missing_binary(self, monkeypatch):
        monkeypatch.setenv(screen_actions_cua.MODE_ENV, "cua")
        monkeypatch.setenv(screen_actions_cua.BIN_ENV,
                           "/nonexistent/cua-driver")
        out = screen_actions_cua.handle_screen_action_cua(
            "screen_mouse_position", {})
        assert "not found" in out["error"]
        assert screen_actions_cua.MODE_ENV in out["error"]

    def test_pawflow_mode_untouched(self, monkeypatch):
        monkeypatch.setenv(screen_actions_cua.MODE_ENV, "pawflow")
        called = {}
        def fake_subprocess(action, req):
            called["action"] = action
            return {"ok": True}
        monkeypatch.setattr(screen_actions, "_screen_action_subprocess",
                            fake_subprocess)
        out = screen_actions.handle_screen_action("screen_move",
                                                  {"x": 1, "y": 1})
        assert out == {"ok": True}
        assert called["action"] == "screen_move"


def test_png_size_parses_ihdr():
    assert screen_actions_cua._png_size(_PNG_1X1) == (1, 1)
    with pytest.raises(ValueError):
        screen_actions_cua._png_size(b"notapng")
