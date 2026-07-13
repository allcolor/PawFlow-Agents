"""Token-free stale-screen validation for Docker and host desktop clicks."""

import base64
import importlib.util
import io
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _png(color, size=(80, 60)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _guard(image):
    return {
        "revision": "revision-1",
        "region": {"x": 10, "y": 20, "width": 80, "height": 60},
        "expected_image": base64.b64encode(image).decode("ascii"),
    }


class _Mouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, button="left"):
        self.clicks.append((x, y, button))

    def doubleClick(self, x, y):
        self.clicks.append((x, y, "double"))


def _load_host_screen_actions():
    path = ROOT / "tools" / "screen_actions.py"
    spec = importlib.util.spec_from_file_location(
        "screen_actions_stale_guard_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_docker_click_occurs_when_target_region_is_unchanged(monkeypatch):
    from tools import fs_screen

    expected = _png("white")
    xdo_calls = []
    monkeypatch.setattr(fs_screen, "_ensure_desktop", lambda: None)
    monkeypatch.setattr(
        fs_screen, "_capture_guard_region_png", lambda region: expected)
    monkeypatch.setattr(
        fs_screen, "_xdo", lambda *args, **kwargs: xdo_calls.append(args) or "")

    result = fs_screen.action_screen_click(
        ".", ".", {"x": 30, "y": 40, "_screen_guard": _guard(expected)})

    assert result["clicked"] is True
    assert xdo_calls == [("mousemove", "30", "40"), ("click", "1")]


def test_docker_click_is_cancelled_when_target_region_changed(monkeypatch):
    from tools import fs_screen

    expected = _png("white")
    monkeypatch.setattr(fs_screen, "_ensure_desktop", lambda: None)
    monkeypatch.setattr(
        fs_screen, "_capture_guard_region_png", lambda region: _png("black"))
    monkeypatch.setattr(
        fs_screen, "_xdo",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("mouse input must not occur")))

    result = fs_screen.action_screen_click(
        ".", ".", {"x": 30, "y": 40, "_screen_guard": _guard(expected)})

    assert result["stale_screen"] is True
    assert result["reason"] == "target_region_changed"
    assert result["difference"] > result["threshold"]


def test_host_click_occurs_when_target_region_is_unchanged(monkeypatch):
    screen_actions = _load_host_screen_actions()
    expected = _png("white")
    mouse = _Mouse()
    monkeypatch.setattr(
        screen_actions, "_capture_guard_region_png", lambda region: expected)
    monkeypatch.setattr(screen_actions, "_get_pyautogui", lambda: mouse)

    result = screen_actions._click({
        "x": 30, "y": 40, "_screen_guard": _guard(expected),
    })

    assert result["clicked"] is True
    assert mouse.clicks == [(30, 40, "left")]


def test_host_click_is_cancelled_when_target_region_changed(monkeypatch):
    screen_actions = _load_host_screen_actions()
    expected = _png("white")
    monkeypatch.setattr(
        screen_actions, "_capture_guard_region_png", lambda region: _png("black"))
    monkeypatch.setattr(
        screen_actions, "_get_pyautogui",
        lambda: (_ for _ in ()).throw(
            AssertionError("mouse input must not occur")))

    result = screen_actions._click({
        "x": 30, "y": 40, "_screen_guard": _guard(expected),
    })

    assert result["stale_screen"] is True
    assert result["reason"] == "target_region_changed"


def test_both_relays_reject_unguarded_clicks(monkeypatch):
    from tools import fs_screen

    screen_actions = _load_host_screen_actions()
    monkeypatch.setattr(fs_screen, "_ensure_desktop", lambda: None)

    docker_result = fs_screen.action_screen_click(".", ".", {"x": 1, "y": 1})
    host_result = screen_actions._click({"x": 1, "y": 1})

    assert docker_result["reason"] == "missing_screen_guard"
    assert host_result["reason"] == "missing_screen_guard"
