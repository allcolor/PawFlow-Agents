"""ScraplingFetch Task — Fetch web pages using Scrapling.

Scrapling provides advanced web scraping with anti-bot handling,
JavaScript rendering, and CSS/XPath selectors.

Modes:
- fast: Basic HTTP via curl_cffi (no JS, fastest)
- stealth: Anti-bot browser (Cloudflare bypass, TLS fingerprint)
- browser: Full Playwright browser (JavaScript rendering)

The fetched content (text or HTML) replaces the FlowFile body.
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ScraplingFetchTask(BaseTask):
    """Fetch a web page using Scrapling."""

    TYPE = "scraplingFetch"
    VERSION = "1.0.0"
    NAME = "Scrapling Fetch"
    DESCRIPTION = "Fetch web pages with anti-bot handling, JS rendering, and CSS selectors"
    ICON = "globe"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "url": {
                "type": "string",
                "required": True,
                "description": "URL to fetch (supports ${attribute} expressions)",
            },
            "mode": {
                "type": "string",
                "required": False,
                "default": "fast",
                "description": "Fetch mode: fast (HTTP), stealth (anti-bot), browser (JS rendering)",
            },
            "selector": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "CSS selector to extract specific elements",
            },
            "output_format": {
                "type": "string",
                "required": False,
                "default": "text",
                "description": "Output format: text (extracted text) or html (raw HTML)",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": 30,
                "description": "Request timeout in seconds",
            },
            "headers": {
                "type": "object",
                "required": False,
                "default": {},
                "description": "Custom HTTP headers (JSON object)",
            },
            "impersonate": {
                "type": "string",
                "required": False,
                "default": "chrome",
                "description": "Browser to impersonate (fast mode): chrome, firefox, etc.",
            },
            "headless": {
                "type": "boolean",
                "required": False,
                "default": True,
                "description": "Run browser headless (stealth/browser modes)",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        url = self.config.get("url", "")
        # Resolve expressions in URL
        url = self._resolve_attribute_value(flowfile, url)

        mode = self.config.get("mode", "fast")
        selector = self.config.get("selector", "")
        output_format = self.config.get("output_format", "text")
        timeout = int(self.config.get("timeout", 30))
        headless = self.config.get("headless", True)
        impersonate = self.config.get("impersonate", "chrome")
        custom_headers = self.config.get("headers", {})

        if not url:
            raise ValueError("scraplingFetch: no URL provided")

        try:
            page = self._fetch(url, mode, timeout, headless, impersonate,
                               custom_headers)
        except ImportError as e:
            raise ImportError(f"scrapling not installed: {e}") from e

        # Extract content
        if selector:
            elements = page.css(selector)
            if output_format == "html":
                parts = [str(el) for el in elements]
                content = "\n".join(parts)
            else:
                parts = []
                for el in elements:
                    if hasattr(el, 'get_all_text'):
                        parts.append(el.get_all_text())
                    else:
                        parts.append(str(el.text))
                content = "\n---\n".join(p for p in parts if p.strip())
        else:
            if output_format == "html":
                content = str(page.html_content) if hasattr(page, 'html_content') else str(page)
            else:
                content = page.get_all_text(separator="\n", strip=True)

        flowfile.set_content(content.encode("utf-8"))

        # Set output attributes
        flowfile.set_attribute("scraping.url", url)
        flowfile.set_attribute("scraping.mode", mode)
        flowfile.set_attribute("scraping.status", str(getattr(page, 'status', 200)))
        if selector:
            flowfile.set_attribute("scraping.selector", selector)
            flowfile.set_attribute("scraping.elements_found",
                                   str(len(page.css(selector))))
        flowfile.set_attribute("scraping.content_length", str(len(content)))

        return [flowfile]

    def _fetch(self, url: str, mode: str, timeout: int, headless: bool,
               impersonate: str, headers: dict):
        """Fetch URL using the appropriate Scrapling fetcher."""
        if mode == "stealth":
            from scrapling import StealthyFetcher
            return StealthyFetcher.fetch(
                url, headless=headless, timeout=timeout * 1000)
        elif mode == "browser":
            from scrapling import DynamicFetcher
            return DynamicFetcher.fetch(
                url, headless=headless, timeout=timeout * 1000)
        else:
            from scrapling import Fetcher
            # Minimal GDPR consent cookies to bypass European consent walls
            gdpr_cookies = {
                "authId": "anonymous",
                "didomi_token": (
                    "eyJ1c2VyX2lkIjoiIiwiY3JlYXRlZCI6IjIwMjQtMDEtMDFUMDA6M"
                    "DA6MDAuMDAwWiIsInVwZGF0ZWQiOiIyMDI0LTAxLTAxVDAwOjAwOjAw"
                    "LjAwMFoiLCJ2ZW5kb3JzIjp7ImVuYWJsZWQiOltdfSwicHVycG9zZXM"
                    "iOnsiZW5hYmxlZCI6W119fQ=="
                ),
                "euconsent-v2": "CQ",
                "consentUUID": "anonymous",
            }
            kwargs = {"timeout": timeout, "impersonate": impersonate,
                      "verify": False, "cookies": gdpr_cookies}
            if headers:
                kwargs["headers"] = headers
            return Fetcher.get(url, **kwargs)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        """Resolve ${attribute} expressions in a string."""
        import re
        def replacer(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replacer, value)


TaskFactory.register(ScraplingFetchTask)
