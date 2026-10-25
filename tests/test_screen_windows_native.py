"""screen_windows / screen_window_state must work on a Windows relay.

2026-10-25: an agent on a Windows relay called screen_windows and got
"screen_windows needs the CUA backend: set PAWFLOW_SCREEN_MODE=cua (cua-driver
is bundled in desktop-capable relay images)".

That advice is a dead end on Windows. cua-driver is installed by a Linux-only
script (``curl -fsSL https://cua.ai/driver/install.sh | bash``) in
docker/relay-dev/Dockerfile and in the desktop.runtime entry of
relay_image_catalog.json; no Windows build is shipped. Meanwhile
screen_actions.py already has a native Windows path for every other screen
action (pyautogui + ImageGrab + DPI awareness), and Windows exposes window
enumeration and an accessibility tree natively through Win32/UIA -- no external
binary needed.

These tests pin the routing: on win32 the native implementation answers, on
Linux the CUA backend (or the explicit error) still does.
"""

import sys

import pytest

from tools import screen_actions


class _FakeWindow:
    """Mirror of one native window row (the real implementation returns dicts)."""
    def __init__(self, pid, window_id, title, x=0, y=0, w=800, h=600):
        self.row = {"pid": pid, "window_id": window_id, "title": title,
                    "rect": [x, y, w, h]}

    def __getitem__(self, key):
        return self.row[key]

    def get(self, key, default=None):
        return self.row.get(key, default)


def test_windows_action_is_native_on_win32(monkeypatch):
    """On Windows the action must not demand the CUA backend."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(screen_actions, "_native_windows_available",
                        lambda: True, raising=False)
    monkeypatch.setattr(
        screen_actions, "_native_list_windows",
        lambda req: [_FakeWindow(42, "0x1a", "Untitled - Notepad")],
        raising=False)

    result = screen_actions._handle_screen_action_direct("screen_windows", {})

    assert "error" not in result
    assert result["windows"][0]["pid"] == 42
    assert result["windows"][0]["title"] == "Untitled - Notepad"
    assert result["backend"] == "native"


def test_windows_action_still_reports_cua_on_linux(monkeypatch):
    """Linux keeps the existing CUA-only behaviour and its message."""
    monkeypatch.setattr(sys, "platform", "linux")

    result = screen_actions._handle_screen_action_direct("screen_windows", {})

    assert "error" in result
    assert "CUA" in result["error"]


def test_the_windows_error_no_longer_points_at_cua_on_win32(monkeypatch):
    """If the native path is unavailable, say so honestly.

    Advertising PAWFLOW_SCREEN_MODE=cua on Windows sends the user after a binary
    that is never installed there.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(screen_actions, "_native_windows_available",
                        lambda: False, raising=False)

    result = screen_actions._handle_screen_action_direct("screen_windows", {})

    assert "error" in result
    assert "PAWFLOW_SCREEN_MODE" not in result["error"]
    assert "uiautomation" in result["error"]


def test_window_state_requires_a_target_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(screen_actions, "_native_windows_available",
                        lambda: True, raising=False)

    result = screen_actions._handle_screen_action_direct(
        "screen_window_state", {})

    assert "error" in result
    assert "pid" in result["error"] and "window_id" in result["error"]
