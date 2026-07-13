"""Regression tests for see(screen/screenshot) relay captures."""

import base64
import io
import re

from PIL import Image
import pytest

from core.handlers.screen import SCREENSHOT_TTL_SECONDS, ScreenHandler
from core.handlers.see import SeeHandler


_ONE_BY_ONE_PNG = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lI2V2wAAAABJRU5ErkJggg=="
    )
).decode("ascii")


def _valid_png_b64(size=(4, 4)):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class _Relay:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def _request(self, action, path, local=False, **kwargs):
        self.calls.append((action, path, local, kwargs))
        return self.result


class _ScreenStore:
    def __init__(self):
        self.files = {}
        self.last_store = {}

    def store(self, filename, content, content_type, **kwargs):
        file_id = f"fid{len(self.files) + 1}"
        self.files[file_id] = (filename, content, content_type)
        self.last_store = {
            "filename": filename,
            "content": content,
            "content_type": content_type,
            **kwargs,
        }
        return file_id

    def get_required(self, file_id, user_id, conversation_id):
        assert user_id == "user-1"
        assert conversation_id == "conv-1"
        return self.files[file_id]


def _patch_screen_store(monkeypatch):
    from core.conversation_store import ConversationStore
    from core.file_store import FileStore

    store = _ScreenStore()
    monkeypatch.setattr(FileStore, "instance", classmethod(lambda cls: store))
    monkeypatch.setattr(
        ConversationStore, "instance",
        classmethod(lambda cls: type("_ConvStore", (), {
            "get_extra": lambda self, conv_id, key: {},
        })()),
    )
    return store


def test_see_screenshot_accepts_relay_image_dict(monkeypatch):
    _patch_screen_store(monkeypatch)
    relay = _Relay({"image": _ONE_BY_ONE_PNG, "width": 2560, "height": 1440})
    handler = SeeHandler()
    handler.set_fs_service(relay)
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")

    result = handler.execute({"path": "screenshot", "local": True})

    assert relay.calls[0][:3] == ("screen_screenshot", ".", True)
    assert "Screen resolution: 2560x1440" in result
    assert "physical screen pixels" in result
    assert "Screen revision: fid1:" in result
    assert "without another vision call" in result
    assert "__image_data__:" in result
    assert "unexpected screen capture result" not in result


def test_screen_schema_warns_about_physical_pixels_not_chat_preview():
    handler = ScreenHandler()
    schema = handler.parameters_schema

    assert "physical pixels" in handler.description
    assert "resized screenshot image rendered in chat" in handler.description
    assert "physical screenshot pixels" in schema["properties"]["x"]["description"]
    assert "resized chat-image pixels" in schema["properties"]["y"]["description"]
    assert "expected_screen_revision" in schema["properties"]
    assert schema["properties"]["target_bbox"]["minItems"] == 4


def test_screen_click_resolves_revision_to_private_guard(monkeypatch):
    _patch_screen_store(monkeypatch)
    relay = _Relay({"clicked": True, "x": 0, "y": 0})
    handler = ScreenHandler()
    handler.set_service(relay)
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")

    from core.handlers._screen_guard import screen_route_key
    screenshot_result = handler._handle_result(
        "screenshot",
        {"image": _valid_png_b64(), "width": 4, "height": 4},
        route_key=screen_route_key(relay, True),
    )
    revision = re.search(r"Screen revision: (\S+)", screenshot_result).group(1)

    result = handler.execute({
        "action": "click",
        "x": 0,
        "y": 0,
        "local": True,
        "expected_screen_revision": revision,
        "target_bbox": [0, 0, 1, 1],
    })

    assert result == "OK: click completed"
    action, _path, local, kwargs = relay.calls[-1]
    assert (action, local) == ("screen_click", True)
    assert "expected_screen_revision" not in kwargs
    assert "target_bbox" not in kwargs
    assert kwargs["_screen_guard"]["revision"] == revision
    assert kwargs["_screen_guard"]["region"] == {
        "x": 0, "y": 0, "width": 4, "height": 4,
    }
    assert kwargs["_screen_guard"]["expected_image"] not in result


def test_screen_click_without_revision_never_reaches_relay():
    relay = _Relay({"clicked": True})
    handler = ScreenHandler()
    handler.set_service(relay)
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")

    result = handler.execute({
        "action": "click", "x": 20, "y": 20, "local": True,
    })

    assert "expected_screen_revision is required" in result
    assert relay.calls == []


def test_screen_stale_result_is_never_reported_as_success():
    result = ScreenHandler()._handle_result(
        "click", {"stale_screen": True, "difference": 0.25})

    assert result.startswith("STALE_SCREEN: click cancelled")
    assert "difference=0.2500" in result
    assert not result.startswith("OK:")


def test_screen_revision_cannot_cross_relay_display(monkeypatch):
    _patch_screen_store(monkeypatch)
    from core.handlers._screen_guard import (
        prepare_click_guard, store_screen_capture,
    )

    _url, revision = store_screen_capture(
        base64.b64decode(_valid_png_b64()),
        user_id="user-1",
        conversation_id="conv-1",
        route_key="relay-a|local=1",
    )

    with pytest.raises(ValueError, match="another relay/display"):
        prepare_click_guard(
            revision,
            user_id="user-1",
            conversation_id="conv-1",
            route_key="relay-b|local=1",
            x=0,
            y=0,
        )


def test_screen_guard_crop_is_bounded_for_large_targets(monkeypatch):
    _patch_screen_store(monkeypatch)
    from core.handlers._screen_guard import (
        prepare_click_guard, store_screen_capture,
    )

    _url, revision = store_screen_capture(
        base64.b64decode(_valid_png_b64((1000, 800))),
        user_id="user-1",
        conversation_id="conv-1",
        route_key="relay-a|local=1",
    )
    guard = prepare_click_guard(
        revision,
        user_id="user-1",
        conversation_id="conv-1",
        route_key="relay-a|local=1",
        x=900,
        y=700,
        target_bbox=[0, 0, 1000, 800],
    )

    assert guard["region"]["width"] == 512
    assert guard["region"]["height"] == 512


def test_screen_screenshot_filestore_entry_expires_after_five_minutes(monkeypatch):
    from core.conversation_store import ConversationStore
    from core.file_store import FileStore

    stored = {}

    class _Store:
        def store(self, filename, content, content_type, **kwargs):
            stored.update({
                "filename": filename,
                "content": content,
                "content_type": content_type,
                **kwargs,
            })
            return "fid123"

    monkeypatch.setattr(FileStore, "instance", classmethod(lambda cls: _Store()))
    monkeypatch.setattr(
        ConversationStore, "instance",
        classmethod(lambda cls: type("_ConvStore", (), {
            "get_extra": lambda self, conv_id, key: {},
        })()),
    )
    handler = ScreenHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")

    result = handler._handle_result("screenshot", {
        "image": _ONE_BY_ONE_PNG,
        "width": 1280,
        "height": 800,
    })

    assert "fs://filestore/fid123/" in result
    assert stored["ttl"] == SCREENSHOT_TTL_SECONDS == 300
    assert stored["category"] == "screenshot"
    assert stored["user_id"] == "user-1"
    assert stored["conversation_id"] == "conv-1"
