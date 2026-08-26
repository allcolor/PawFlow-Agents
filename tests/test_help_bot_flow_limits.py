"""Execution-limit contracts for the shipped public help-bot flows."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATHS = (
    ROOT / "data/repository/flows/global/telegram/telegram_help_bot"
    / "versions/1.0.0.json",
    ROOT / "data/repository/flows/global/http_bots/web_help_bot"
    / "versions/1.0.0.json",
)


def test_help_bot_response_timeout_defaults_to_unlimited():
    for path in FLOW_PATHS:
        flow = json.loads(path.read_text(encoding="utf-8"))
        timeout = flow["parameters"]["response_timeout_seconds"]
        script = flow["tasks"]["route"]["parameters"]["script"]

        assert timeout["default"] == 0
        assert "response_timeout_seconds}'''.strip() or '120'" not in script
        assert 'response_timeout_seconds}""".strip() or "120"' not in script
        assert (
            "response_timeout_seconds}'''.strip() or '0'" in script
            or 'response_timeout_seconds}""".strip() or "0"' in script)
        assert "timeout=TIMEOUT if TIMEOUT > 0 else None" in script
