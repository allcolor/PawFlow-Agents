"""Browser setup failures must not contaminate later asyncio tests."""

import asyncio
from types import SimpleNamespace

import pytest

import test_webchat_motion_browser as browser_fixtures


@pytest.mark.parametrize("phase", ["missing", "launch", "close", "success"])
def test_browser_fixture_stops_playwright_on_every_exit(monkeypatch, phase):
    playwright_api = pytest.importorskip("playwright.sync_api")

    def close():
        if phase == "close":
            raise RuntimeError("close failed")

    browser = SimpleNamespace(close=close)

    def launch(*args, **kwargs):
        if phase == "launch":
            raise RuntimeError("launch failed")
        return browser

    monkeypatch.setattr(browser_fixtures, "_chromium_executable",
                        lambda _: None if phase == "missing" else "chromium")
    monkeypatch.setattr(playwright_api.BrowserType, "launch", launch)
    fixture = browser_fixtures.chromium_browser.__wrapped__()
    if phase == "missing":
        with pytest.raises(pytest.skip.Exception):
            next(fixture)
    elif phase == "launch":
        with pytest.raises(RuntimeError, match="launch failed"):
            next(fixture)
    else:
        assert next(fixture) is browser
        if phase == "close":
            with pytest.raises(RuntimeError, match="close failed"):
                next(fixture)
        else:
            with pytest.raises(StopIteration):
                next(fixture)

    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
