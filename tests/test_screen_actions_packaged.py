import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_screen_actions():
    path = ROOT / "tools" / "screen_actions.py"
    spec = importlib.util.spec_from_file_location("screen_actions_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_screen_action_child_uses_packaged_entry_only_when_frozen(monkeypatch):
    screen_actions = _load_screen_actions()
    monkeypatch.setattr(screen_actions.sys, "executable", "/opt/pawflow/pawflow-relay", raising=False)
    monkeypatch.setattr(screen_actions.sys, "frozen", True, raising=False)

    assert screen_actions._screen_action_child_command("screen_screenshot") == [
        "/opt/pawflow/pawflow-relay",
        "__pawflow_screen_action_child__",
        "screen_screenshot",
    ]


def test_screen_action_child_uses_script_when_not_frozen(monkeypatch):
    screen_actions = _load_screen_actions()
    monkeypatch.setattr(screen_actions.sys, "executable", "/opt/pawflow/pawflow-relay", raising=False)
    monkeypatch.setattr(screen_actions.sys, "frozen", False, raising=False)

    assert screen_actions._screen_action_child_command("screen_screenshot") == [
        "/opt/pawflow/pawflow-relay",
        str(ROOT / "tools" / "screen_actions.py"),
        "screen_screenshot",
    ]


def test_relay_binary_entry_exposes_screen_action_child_route():
    entry = (ROOT / "pawflow-relay-desktop" / "scripts" / "relay-bin-entry.py").read_text(encoding="utf-8")

    assert "__pawflow_screen_action_child__" in entry
    assert "from screen_actions import _handle_screen_action_direct" in entry


def test_host_python_command_does_not_return_frozen_relay_binary(monkeypatch):
    from pawflow_relay import thread

    monkeypatch.delenv("PAWFLOW_RELAY_PYTHON", raising=False)
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setattr(thread.sys, "executable", "/opt/pawflow/pawflow-relay", raising=False)
    monkeypatch.setattr(thread.sys, "frozen", True, raising=False)
    monkeypatch.setattr(thread.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    assert thread._host_python_command() == "/usr/bin/python3"
