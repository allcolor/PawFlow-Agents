"""Tests for browser automation service and tool handler (~35 tests).

All tests mock Playwright — no real browser required.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from dataclasses import fields


# ---------------------------------------------------------------------------
# Helpers to mock playwright so imports succeed without a real install
# ---------------------------------------------------------------------------

def _make_mock_playwright_module():
    """Return a fake playwright module sufficient for import."""
    mod = MagicMock()
    mod.sync_api = MagicMock()
    return mod


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset BrowserService singleton between tests."""
    try:
        from services.browser_service import BrowserService
        BrowserService.reset()
    except Exception:
        pass
    yield
    try:
        from services.browser_service import BrowserService
        BrowserService.reset()
    except Exception:
        pass


# ===================================================================
# 1. TestBrowserService
# ===================================================================

class TestBrowserService:
    """Tests for services.browser_service.BrowserService."""

    def test_singleton_pattern(self):
        from services.browser_service import BrowserService
        a = BrowserService.instance()
        b = BrowserService.instance()
        assert a is b

    def test_singleton_reset(self):
        from services.browser_service import BrowserService
        a = BrowserService.instance()
        BrowserService.reset()
        b = BrowserService.instance()
        assert a is not b

    # -- validate_url -------------------------------------------------------

    def test_validate_url_blocks_file_scheme(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        with pytest.raises(ValueError, match="(?i)blocked|scheme|not allowed"):
            svc.validate_url("file:///etc/passwd")

    def test_validate_url_blocks_javascript_scheme(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        with pytest.raises(ValueError, match="(?i)blocked|scheme|not allowed"):
            svc.validate_url("javascript:alert(1)")

    def test_validate_url_blocks_data_scheme(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        with pytest.raises(ValueError, match="(?i)blocked|scheme|not allowed"):
            svc.validate_url("data:text/html,<h1>hi</h1>")

    def test_validate_url_allows_https(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        # Should not raise
        result = svc.validate_url("https://example.com")
        # Result is either None or the url itself — both acceptable
        assert result is None or "example.com" in result

    def test_validate_url_allowed_domains(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        with patch.dict("os.environ", {"PYFI2_BROWSER_ALLOWED_DOMAINS": "trusted.com,safe.org"}):
            # Re-instantiate to pick up env
            BrowserService.reset()
            svc = BrowserService.instance()
            # trusted domain should pass
            svc.validate_url("https://trusted.com/page")
            # untrusted domain should fail
            with pytest.raises(ValueError):
                svc.validate_url("https://evil.com/page")

    def test_validate_url_blocked_domains(self):
        from services.browser_service import BrowserService
        with patch.dict("os.environ", {"PYFI2_BROWSER_BLOCKED_DOMAINS": "evil.com,bad.org"}):
            BrowserService.reset()
            svc = BrowserService.instance()
            with pytest.raises(ValueError):
                svc.validate_url("https://evil.com/steal")

    def test_validate_url_empty_hostname_raises(self):
        from services.browser_service import BrowserService
        svc = BrowserService.instance()
        with pytest.raises((ValueError, Exception)):
            svc.validate_url("https://")


# ===================================================================
# 2. TestBrowserActionHandler
# ===================================================================

class TestBrowserActionHandler:
    """Tests for BrowserActionHandler from core.tool_registry."""

    @pytest.fixture()
    def handler(self):
        from core.tool_registry import BrowserActionHandler
        h = BrowserActionHandler()
        return h

    def test_schema_has_actions_enum(self, handler):
        schema = handler.parameters_schema
        schema_str = str(schema)
        for action in ("navigate", "click", "fill", "extract", "screenshot", "scroll", "wait", "close"):
            assert action in schema_str, f"Missing action '{action}' in schema"

    def test_set_conversation_id(self, handler):
        handler.set_conversation_id("conv-123")
        assert handler._conversation_id == "conv-123"

    # -- Missing required arguments -----------------------------------------

    def test_execute_navigate_without_url(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "navigate"})
        assert "error" in str(result).lower() or "url" in str(result).lower()

    def test_execute_click_without_selector(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "click"})
        assert "error" in str(result).lower() or "selector" in str(result).lower()

    def test_execute_fill_without_selector(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "fill"})
        assert "error" in str(result).lower() or "selector" in str(result).lower()

    def test_execute_extract_without_selector(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "extract"})
        assert "error" in str(result).lower() or "selector" in str(result).lower()

    def test_execute_wait_without_selector(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "wait"})
        assert "error" in str(result).lower() or "selector" in str(result).lower()

    def test_execute_unknown_action(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({"action": "hack_the_planet"})
        assert "error" in str(result).lower() or "unknown" in str(result).lower()

    def test_execute_without_action(self, handler):
        handler.set_conversation_id("c1")
        result = handler.execute({})
        assert "error" in str(result).lower() or "action" in str(result).lower()

    # -- Mocked service calls -----------------------------------------------

    def _patch_service(self):
        """Return a context manager that patches BrowserService.instance()."""
        mock_svc = MagicMock()
        mock_svc.validate_url = MagicMock()
        mock_svc.navigate = MagicMock(return_value={"status": "ok", "title": "Example"})
        mock_svc.click = MagicMock(return_value={"status": "ok"})
        mock_svc.fill = MagicMock(return_value={"status": "ok"})
        mock_svc.extract = MagicMock(return_value={"status": "ok", "text": "hello"})
        mock_svc.screenshot = MagicMock(return_value={"status": "ok", "path": "/tmp/shot.png"})
        mock_svc.scroll = MagicMock(return_value={"status": "ok"})
        mock_svc.wait_for = MagicMock(return_value={"status": "ok"})
        mock_svc.close_session = MagicMock(return_value={"status": "ok"})
        return patch("services.browser_service.BrowserService.instance", return_value=mock_svc), mock_svc

    def test_execute_navigate_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "navigate", "url": "https://example.com"})
            mock_svc.navigate.assert_called_once()
            args = mock_svc.navigate.call_args
            assert "c1" in str(args)
            assert "https://example.com" in str(args)

    def test_execute_click_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "click", "selector": "#btn"})
            mock_svc.click.assert_called_once()

    def test_execute_fill_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "fill", "selector": "#input", "value": "hello"})
            mock_svc.fill.assert_called_once()

    def test_execute_extract_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "extract", "selector": ".content"})
            mock_svc.extract.assert_called_once()

    def test_execute_screenshot_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "screenshot"})
            mock_svc.screenshot.assert_called_once()

    def test_execute_scroll_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "scroll", "direction": "down"})
            mock_svc.scroll.assert_called_once()

    def test_execute_wait_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "wait", "selector": "#loaded", "timeout_ms": 5000})
            mock_svc.wait_for.assert_called_once()

    def test_execute_close_calls_service(self, handler):
        ctx, mock_svc = self._patch_service()
        with ctx:
            handler.set_conversation_id("c1")
            handler.execute({"action": "close"})
            mock_svc.close_session.assert_called_once()


# ===================================================================
# 3. TestBrowserGracefulImport
# ===================================================================

class TestBrowserGracefulImport:
    """Handler should not appear in default registry when playwright is missing."""

    def test_handler_not_in_registry_without_playwright(self):
        """If playwright is not importable, BrowserActionHandler should not
        be auto-registered in the default tool registry."""
        with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
            from core.tool_registry import ToolRegistry
            registry = ToolRegistry()
            handler_names = [h.name for h in registry.handlers] if hasattr(registry, "handlers") else []
            # If load_agent_tools exists, call it to populate
            if hasattr(registry, "load_agent_tools"):
                try:
                    registry.load_agent_tools()
                except Exception:
                    pass
                handler_names = [h.name for h in registry.handlers] if hasattr(registry, "handlers") else []
            assert "browser" not in handler_names


# ===================================================================
# 4. TestBrowserSecurity
# ===================================================================

class TestBrowserSecurity:
    """Security-related checks."""

    def test_blocked_schemes_comprehensive(self):
        from services.browser_service import _BLOCKED_SCHEMES
        expected = {"file", "javascript", "data", "vbscript"}
        assert expected.issubset(_BLOCKED_SCHEMES)

    def test_domain_env_vars_read(self):
        with patch.dict("os.environ", {
            "PYFI2_BROWSER_ALLOWED_DOMAINS": "a.com,b.com",
            "PYFI2_BROWSER_BLOCKED_DOMAINS": "x.com",
        }):
            from services.browser_service import BrowserService
            BrowserService.reset()
            svc = BrowserService.instance()
            # The service should have parsed the env vars into some internal structure
            # We verify by trying validate_url against allowed/blocked
            svc.validate_url("https://a.com/ok")
            with pytest.raises(ValueError):
                svc.validate_url("https://x.com/bad")


# ===================================================================
# 5. TestBrowserSession
# ===================================================================

class TestBrowserSession:
    """Tests for the BrowserSession dataclass."""

    def test_dataclass_fields(self):
        from services.browser_service import BrowserSession
        field_names = {f.name for f in fields(BrowserSession)}
        assert "context" in field_names
        assert "page" in field_names
        assert "last_activity" in field_names

    def test_dataclass_instantiation(self):
        from services.browser_service import BrowserSession
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        ts = time.time()
        session = BrowserSession(context=mock_ctx, page=mock_page, last_activity=ts)
        assert session.context is mock_ctx
        assert session.page is mock_page
        assert session.last_activity == ts
