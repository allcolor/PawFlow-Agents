"""Tests for Scrapling integration — ScraplingFetchHandler (fetch tool) + ScraplingFetchTask.

Tests cover:
- ScraplingFetchHandler (builtin agent tool, name="fetch")
- ScraplingFetchTask (standalone task)
- Registration and parameter schema
- Error handling (missing URL, import errors)
- CSS selector extraction
- Mode selection (fast, stealth, raw)
- Default registry includes fetch
- i18n keys
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
from core.tool_registry import (
    ScraplingFetchHandler, create_default_registry,
)


def _make_mock_scrapling():
    """Create a mock scrapling module with Fetcher, StealthyFetcher, DynamicFetcher."""
    mock_mod = MagicMock()

    # Default page mock
    mock_page = MagicMock()
    mock_page.get_all_text.return_value = "Hello World"
    mock_page.status = 200
    mock_page.css.return_value = []
    mock_page.html_content = "<html><body>Hello</body></html>"

    mock_mod.Fetcher.get.return_value = mock_page
    mock_mod.StealthyFetcher.fetch.return_value = mock_page
    mock_mod.DynamicFetcher.fetch.return_value = mock_page

    return mock_mod, mock_page


# ── ScraplingFetchHandler ────────────────────────────────────────────


class TestScraplingFetchHandler(unittest.TestCase):

    def test_handler_properties(self):
        h = ScraplingFetchHandler()
        assert h.name == "fetch"
        assert "web page" in h.description.lower()
        assert "url" in h.parameters_schema["properties"]
        assert "selector" in h.parameters_schema["properties"]
        assert "mode" in h.parameters_schema["properties"]

    def test_handler_in_default_registry(self):
        registry = create_default_registry()
        handler = registry.get("fetch")
        assert handler is not None
        assert isinstance(handler, ScraplingFetchHandler)

    def test_no_url_returns_error(self):
        h = ScraplingFetchHandler()
        result = h.execute({})
        assert "Error" in result

    def test_fast_mode(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "Page text content"

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            result = h.execute({"url": "https://example.com"})
        assert "Page text content" in result
        mock_mod.Fetcher.get.assert_called_once()

    def test_fast_mode_with_selector(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_el = MagicMock()
        mock_el.get_all_text.return_value = "Selected content"
        mock_page.css.return_value = [mock_el]

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            result = h.execute({"url": "https://example.com", "selector": "article"})
        assert "Selected content" in result
        mock_page.css.assert_called_with("article")

    def test_empty_selector_result(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.css.return_value = []

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            result = h.execute({"url": "https://example.com", "selector": ".nonexistent"})
        assert "No elements found" in result

    def test_truncation(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "x" * 20000

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            result = h.execute({"url": "https://example.com"})
        assert len(result) <= 20000

    def test_empty_page_escalates_to_stealth(self):
        """Empty fast result triggers stealth subprocess escalation."""
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = ""
        mock_page.html = "<html><body></body></html>"

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}), \
             patch.object(h, '_stealth_subprocess', return_value=None):
            result = h.execute({"url": "https://example.com"})
        assert "empty page" in result.lower()

    def test_stealth_mode_uses_subprocess(self):
        """Stealth mode runs via subprocess to avoid Playwright asyncio bug."""
        h = ScraplingFetchHandler()
        with patch.object(h, '_stealth_subprocess',
                          return_value="Stealth content") as mock_sub:
            result = h.execute({"url": "https://example.com", "mode": "stealth"})
        assert "Stealth content" in result
        mock_sub.assert_called_once_with("https://example.com", "")

    def test_js_wall_detection_escalates(self):
        """Pages with JS wall signatures trigger stealth escalation."""
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "Checking your browser..."
        mock_page.html = "<html><body>Checking your browser...</body></html>"

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}), \
             patch.object(h, '_stealth_subprocess',
                          return_value="Real content") as mock_sub:
            result = h.execute({"url": "https://example.com"})
        assert "Real content" in result
        mock_sub.assert_called_once()

    def test_fetch_error_returns_error_string(self):
        mock_mod = MagicMock()
        mock_mod.Fetcher.get.side_effect = ConnectionError("Connection refused")

        h = ScraplingFetchHandler()
        with patch.dict(sys.modules, {"scrapling": mock_mod}), \
             patch.object(h, '_stealth_subprocess', return_value=None), \
             patch("http.client.HTTPSConnection") as mock_conn:
            mock_conn.return_value.getresponse.return_value.status = 403
            mock_conn.return_value.getresponse.return_value.read.return_value = b""
            result = h.execute({"url": "https://example.com"})
        assert "Error" in result or "scrapling" in result


# ── ScraplingFetchTask ───────────────────────────────────────────────


class TestScraplingFetchTask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        task_class = TaskFactory.get("scraplingFetch")
        assert task_class is not None
        assert task_class.TYPE == "scraplingFetch"

    def test_task_metadata(self):
        from tasks.io.scrapling_fetch import ScraplingFetchTask
        assert ScraplingFetchTask.NAME == "Scrapling Fetch"
        assert ScraplingFetchTask.ICON == "globe"

    def test_parameter_schema(self):
        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({"url": "https://example.com"})
        schema = task.get_parameter_schema()
        assert "url" in schema
        assert schema["url"]["required"] is True
        assert "mode" in schema
        assert "selector" in schema
        assert "output_format" in schema
        assert "timeout" in schema
        assert "headless" in schema
        assert "impersonate" in schema

    def test_execute_fast(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "Page text"

        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({"url": "https://example.com"})
        ff = FlowFile(content=b"")

        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"Page text"
        assert results[0].get_attribute("scraping.url") == "https://example.com"
        assert results[0].get_attribute("scraping.mode") == "fast"
        assert results[0].get_attribute("scraping.status") == "200"

    def test_execute_with_selector(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_el = MagicMock()
        mock_el.get_all_text.return_value = "Article body"
        mock_page.css.return_value = [mock_el]

        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({
            "url": "https://example.com",
            "selector": "article",
        })
        ff = FlowFile(content=b"")

        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            results = task.execute(ff)

        assert b"Article body" in results[0].get_content()
        assert results[0].get_attribute("scraping.selector") == "article"

    def test_execute_html_output(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.html_content = "<h1>Hello</h1>"

        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({
            "url": "https://example.com",
            "output_format": "html",
        })
        ff = FlowFile(content=b"")

        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            results = task.execute(ff)

        assert b"<h1>Hello</h1>" in results[0].get_content()

    def test_execute_no_url_raises(self):
        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({"url": ""})
        ff = FlowFile(content=b"")
        with self.assertRaises(ValueError):
            task.execute(ff)

    def test_url_expression_resolution(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "OK"

        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({"url": "https://${target.host}/page"})
        ff = FlowFile(content=b"")
        ff.set_attribute("target.host", "example.com")

        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            results = task.execute(ff)

        called_url = mock_mod.Fetcher.get.call_args[0][0]
        assert called_url == "https://example.com/page"

    def test_execute_stealth_mode(self):
        mock_mod, mock_page = _make_mock_scrapling()
        mock_page.get_all_text.return_value = "Stealth text"

        from tasks.io.scrapling_fetch import ScraplingFetchTask
        task = ScraplingFetchTask({
            "url": "https://example.com",
            "mode": "stealth",
        })
        ff = FlowFile(content=b"")

        with patch.dict(sys.modules, {"scrapling": mock_mod}):
            results = task.execute(ff)

        assert results[0].get_attribute("scraping.mode") == "stealth"
        mock_mod.StealthyFetcher.fetch.assert_called_once()


# ── i18n ─────────────────────────────────────────────────────────────

