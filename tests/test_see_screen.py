"""Regression tests for see(screen/screenshot) relay captures."""

import base64

from core.handlers.screen import ScreenHandler
from core.handlers.see import SeeHandler


_ONE_BY_ONE_PNG = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lI2V2wAAAABJRU5ErkJggg=="
    )
).decode("ascii")


class _Relay:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def _request(self, action, path, local=False, **kwargs):
        self.calls.append((action, path, local, kwargs))
        return self.result


def test_see_screenshot_accepts_relay_image_dict():
    relay = _Relay({"image": _ONE_BY_ONE_PNG, "width": 2560, "height": 1440})
    handler = SeeHandler()
    handler.set_fs_service(relay)

    result = handler.execute({"path": "screenshot", "local": True})

    assert relay.calls[0][:3] == ("screen_screenshot", ".", True)
    assert "Screen resolution: 2560x1440" in result
    assert "physical screen pixels" in result
    assert "__image_data__:" in result
    assert "unexpected screen capture result" not in result


def test_screen_schema_warns_about_physical_pixels_not_chat_preview():
    handler = ScreenHandler()
    schema = handler.parameters_schema

    assert "physical pixels" in handler.description
    assert "resized screenshot image rendered in chat" in handler.description
    assert "physical screenshot pixels" in schema["properties"]["x"]["description"]
    assert "resized chat-image pixels" in schema["properties"]["y"]["description"]
