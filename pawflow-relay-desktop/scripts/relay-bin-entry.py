"""PyInstaller entry point for the packaged PawFlow Relay client."""

import json
import os
import sys
from pathlib import Path

from pawflow_relay.__main__ import main


def _runtime_root() -> Path:
    override = os.environ.get("PAWFLOW_RELAY_RUNTIME_ROOT", "")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[1]


def _screen_action_child(action: str) -> int:
    tools_dir = str(_runtime_root() / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from screen_actions import _handle_screen_action_direct

    request = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps(_handle_screen_action_direct(action, request)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "__pawflow_screen_action_child__":
        raise SystemExit(_screen_action_child(sys.argv[2]))
    raise SystemExit(main() or 0)

