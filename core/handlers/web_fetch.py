"""Web fetch/search tool handlers.

ScraplingFetchHandler and the anti-injection sanitizer live here; the
ExecuteScript and WebSearch handlers were split into web_execute.py and
web_search.py to keep files <=800 lines and are re-exported below so the
core.handlers.web_fetch import path is unchanged.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from core.tool_handler import ToolHandler

from core.handlers.web_execute import ExecuteScriptHandler  # noqa: F401
from core.handlers.web_search import WebSearchHandler  # noqa: F401

logger = logging.getLogger(__name__)


# ── Anti-injection: sanitize external content ─────────────────────

_INJECTION_PATTERNS = re.compile(
    r'(?i)'
    r'(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions'
    r'|you\s+are\s+now\s+(?:a|an|the)\s+'
    r'|(?:^|\n)\s*system\s*:\s+'
    r'|new\s+instructions?\s*:'
    r'|override\s+(?:all\s+)?(?:previous|system)\s+'
    r'|(?:^|\n)\s*\[system\]\s*'
    r'|do\s+not\s+follow\s+(?:your|the)\s+(?:original|previous|system)'
    r'|jailbreak|prompt\s*inject'
)


def _sanitize_external_content(text: str) -> str:
    """Flag prompt injection attempts in external content.

    Replaces suspicious patterns with a visible marker so the LLM
    sees them as flagged data, not as instructions to follow.
    Not foolproof — raises the bar against naive injection.
    """
    def _replace(m: re.Match) -> str:
        return f"[⚠ INJECTION ATTEMPT REDACTED: {m.group()[:60]}]"
    return _INJECTION_PATTERNS.sub(_replace, text)


class ScraplingFetchHandler(ToolHandler):
    """Fetch a web page using Scrapling and extract its text content.

    Supports three modes:
    - fast: Basic HTTP via curl_cffi (Fetcher) — fast, no JS
    - stealth: Anti-bot browser (StealthyFetcher) — Cloudflare bypass
    - browser: Full Playwright browser (DynamicFetcher) — JS rendering

    Returns extracted text by default, or CSS-selected content if selector given.
    """

    _conversation_id: str = ""

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id or ""

    @property
    def name(self) -> str:
        return "fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch a web page and extract its text content. "
            "For raw HTTP requests (APIs, downloads), use method/headers/body parameters. "
            "Use 'selector' to extract specific elements via CSS selector."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to extract specific elements (e.g. 'article', '.content', 'h1')",
                },
                "mode": {
                    "type": "string",
                    "description": "Fetch mode: 'fast' (default, recommended), 'stealth' (anti-bot bypass for protected sites), 'raw' (direct HTTP, returns raw content — for APIs/downloads). Auto-escalates if first mode fails.",
                    "enum": ["fast", "stealth", "raw"],
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method for raw mode (default: GET)",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers (for raw/API requests)",
                },
                "body": {
                    "type": "string",
                    "description": "Request body for POST/PUT (raw mode)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of characters to return",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Alias for limit; maximum number of characters to return",
                },
            },
            "required": ["url"],
        }

    @staticmethod
    def _limit_output(text: str, arguments: Dict[str, Any]) -> str:
        limit = arguments.get("limit")
        if limit is None:
            limit = arguments.get("max_chars")
        try:
            limit = int(limit or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0 and len(text) > limit:
            return text[:limit] + f"\n\n... [truncated to {limit} chars; {len(text)} chars total]"
        return text

    # Minimal GDPR consent cookies for common European consent providers.
    # These signal "all cookies rejected" to bypass consent walls without
    # actually accepting tracking — the scraper only reads page content.
    _GDPR_COOKIES = {
        "authId": "anonymous",
        "didomi_token": (  # nosec B105
            "eyJ1c2VyX2lkIjoiIiwiY3JlYXRlZCI6IjIwMjQtMDEtMDFUMDA6M"
            "DA6MDAuMDAwWiIsInVwZGF0ZWQiOiIyMDI0LTAxLTAxVDAwOjAwOjAw"
            "LjAwMFoiLCJ2ZW5kb3JzIjp7ImVuYWJsZWQiOltdfSwicHVycG9zZXM"
            "iOnsiZW5hYmxlZCI6W119fQ=="
        ),
        "euconsent-v2": "CQ",  # minimal TCF2 signal
        "consentUUID": "anonymous",
    }

    def execute(self, arguments: Dict[str, Any]) -> str:
        url = arguments.get("url", "")
        if not url:
            return "Error: no URL provided"

        selector = arguments.get("selector", "")
        mode = arguments.get("mode", "fast")

        # Auto-detect raw mode when method/headers/body are provided
        if mode == "fast" and (arguments.get("method") or arguments.get("headers") or arguments.get("body")):
            mode = "raw"

        # Raw mode: direct HTTP request (replaces old web_fetch behavior)
        if mode == "raw":
            return self._limit_output(self._raw_fetch(url, arguments), arguments)

        try:
            # Step 1: Always try fast mode first (httpx, no Playwright)
            page = None
            fast_err = None
            if mode != "stealth":
                try:
                    from scrapling import Fetcher
                    page = Fetcher.get(url, timeout=30, verify=False,
                                       cookies=self._GDPR_COOKIES)
                except Exception as e:
                    fast_err = e
                    logger.debug(f"scrapling fast mode failed for {url}: {e}")

            # Step 2: Handle PDF
            if page is not None and self._is_pdf(page, url):
                return self._extract_pdf(page, url)

            # Step 3: Check if fast result needs escalation to stealth
            needs_stealth = (page is None) or (mode == "stealth")
            if page is not None and not needs_stealth:
                text = self._extract_text(page, selector)
                if self._looks_like_js_wall(text, page):
                    logger.info(f"scrapling: fast mode got JS wall for {url}, "
                                f"escalating to stealth subprocess")
                    needs_stealth = True
                else:
                    return self._limit_output(self._finalize(text), arguments)

            # Step 4: Stealth mode via subprocess (avoids Playwright asyncio bug)
            if needs_stealth:
                stealth_result = self._stealth_subprocess(url, selector)
                if stealth_result is not None and not self._looks_like_blocked_text(stealth_result):
                    return self._limit_output(self._finalize(stealth_result), arguments)
                # Stealth failed — use fast result if available (even if suspect)
                if page is not None:
                    fast_text = self._extract_text(page, selector)
                    if not self._looks_like_blocked_text(fast_text):
                        return self._limit_output(self._finalize(fast_text), arguments)
                return self._limit_output(self._http_fallback(url, "stealth blocked or empty"), arguments)

            if fast_err:
                raise fast_err
            return "(empty page)"

        except ImportError as e:
            return f"Error: scrapling not installed — {e}"
        except Exception as e:
            error_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.warning(f"scrapling failed for {url}: {error_detail}",
                           exc_info=True)
            return self._limit_output(self._http_fallback(url, error_detail), arguments)

    def _is_pdf(self, page, url: str) -> bool:
        """Check if the response is a PDF."""
        content_type = ""
        if hasattr(page, 'headers') and page.headers:
            ct = (page.headers.get('content-type', '')
                  if isinstance(page.headers, dict) else '')
            content_type = ct.lower().split(';')[0].strip() if ct else ''
        return (content_type == 'application/pdf'
                or url.rstrip('/').lower().endswith('.pdf'))

    def _extract_text(self, page, selector: str) -> str:
        """Extract text from a scrapling page."""
        if selector:
            elements = page.css(selector)
            if not elements:
                return f"No elements found for selector '{selector}'"
            texts = [el.get_all_text() if hasattr(el, 'get_all_text')
                     else str(el.text) for el in elements]
            return "\n---\n".join(t for t in texts if t.strip())
        return page.get_all_text(separator="\n", strip=True)

    @staticmethod
    def _looks_like_js_wall(text: str, page) -> bool:
        """Detect JS wall / Cloudflare challenge / empty JS shell."""
        stripped = text.strip()
        if not stripped:
            return True
        if len(stripped) < 200:
            html = ""
            if hasattr(page, 'html'):
                html = str(page.html) if page.html else ""
            elif hasattr(page, 'body'):
                html = page.body if isinstance(page.body, str) else ""
            if html and html.lower().count('<script') > 3:
                return True
        return ScraplingFetchHandler._looks_like_blocked_text(stripped)

    @staticmethod
    def _looks_like_blocked_text(text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return True
        challenge_signs = [
            "checking your browser", "just a moment", "cloudflare",
            "enable javascript", "please turn javascript on",
            "ray id", "attention required", "captcha", "unusual traffic",
            "verify you are human", "access denied", "automated requests",
            "blocked because", "temporarily blocked",
        ]
        return any(sign in lower for sign in challenge_signs)

    @staticmethod
    def _finalize(text: str) -> str:
        """Sanitize and return scraped text.

        No truncation here — the agent framework handles tool result
        size limits (4096 chars in context, full content in FileStore).
        """
        if not text or not text.strip():
            return "(empty page)"
        return _sanitize_external_content(text)

    def _stealth_subprocess(self, url: str, selector: str) -> Optional[str]:
        """Run stealth fetch in a subprocess to avoid Playwright asyncio issues.

        Spawns a separate Python process with its own event loop, so
        Playwright's asyncio internals don't conflict with the main process.
        """
        import subprocess  # nosec B404
        import sys

        script = (
            'import sys, json\n'
            'try:\n'
            '    from scrapling import StealthyFetcher\n'
            '    url = sys.argv[1]\n'
            '    selector = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""\n'
            '    page = StealthyFetcher.fetch(url, headless=True, timeout=30000)\n'
            '    if selector:\n'
            '        elements = page.css(selector)\n'
            '        if not elements:\n'
            '            text = ""\n'
            '        else:\n'
            '            texts = [el.get_all_text() if hasattr(el, "get_all_text")\n'
            '                     else str(el.text) for el in elements]\n'
            '            text = "\\n---\\n".join(t for t in texts if t.strip())\n'
            '    else:\n'
            '        text = page.get_all_text(separator="\\n", strip=True)\n'
            '    print(json.dumps({"ok": True, "text": text}))\n'
            'except Exception as e:\n'
            '    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))\n'
        )
        try:
            args = [sys.executable, "-c", script, url]
            if selector:
                args.append(selector)
            # Spawn instead of run() so FORCE STOP can terminate the
            # 60s-bound headless fetch via the kill_hook (the daemon
            # exec_thread won't observe the cancel_event during a
            # blocking subprocess.run otherwise).
            popen = subprocess.Popen(  # nosec B603
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            try:
                from services.tool_relay_service import register_kill_hook
                register_kill_hook(popen.terminate)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            try:
                stdout, stderr = popen.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                popen.kill()
                stdout, stderr = popen.communicate()
                raise
            proc = subprocess.CompletedProcess(args, popen.returncode, stdout, stderr)
            if proc.returncode != 0:
                logger.warning(f"stealth subprocess failed for {url}: "
                               f"{proc.stderr[:500]}")
                return None
            output = proc.stdout.strip()
            if not output:
                return None
            result = json.loads(output.split('\n')[-1])
            if result.get("ok"):
                text = result.get("text", "")
                return text if text else None
            logger.warning(f"stealth subprocess error for {url}: "
                           f"{result.get('error')}")
            return None
        except Exception as e:
            logger.warning(f"stealth subprocess exception for {url}: {e}")
            return None

    def _http_fallback(self, url: str, scrapling_error: str) -> str:
        """Last-resort fallback using http.client."""
        try:
            import ssl
            import http.client
            from urllib.parse import urlparse as _up
            parsed = _up(url)
            ctx = ssl.create_default_context()
            if parsed.scheme == 'https':
                conn = http.client.HTTPSConnection(
                    parsed.hostname, parsed.port or 443, timeout=30,
                    context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    parsed.hostname, parsed.port or 80, timeout=30)
            conn.request("GET", parsed.path or "/", headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/131.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            conn.close()
            if resp.status == 200:
                import re as _re
                text = _re.sub(r"<script[^>]*>.*?</script>", "", body,
                               flags=_re.DOTALL)
                text = _re.sub(r"<style[^>]*>.*?</style>", "", text,
                               flags=_re.DOTALL)
                text = _re.sub(r"<[^>]+>", " ", text)
                text = _re.sub(r"\s+", " ", text).strip()
                if self._looks_like_blocked_text(text):
                    return (f"Error fetching {url}: all fetch methods reached "
                            f"an anti-bot or JavaScript challenge "
                            f"(last error: {scrapling_error})")
                return _sanitize_external_content(text) if text else "(empty page)"
            return (f"Error fetching {url}: scrapling={scrapling_error}, "
                    f"http={resp.status}")
        except Exception as e2:
            return (f"Error fetching {url}: scrapling={scrapling_error}, "
                    f"fallback={e2}")

    def _raw_fetch(self, url: str, arguments: Dict[str, Any]) -> str:
        """Direct HTTP fetch — returns raw content (replaces old web_fetch tool)."""
        import http.client
        import base64
        from urllib.parse import urlparse

        # Resolve expressions in all arguments (secrets in headers, urls, etc.)
        from core.expression import resolve_value
        arguments = resolve_value(
            arguments, owner=getattr(self, '_user_id', ''),
            conversation_id=getattr(self, '_conversation_id', '') or '')

        method = arguments.get("method", "GET").upper()
        extra_headers = arguments.get("headers", {}) or {}
        body = arguments.get("body", "")
        max_size = 5 * 1024 * 1024

        try:
            parsed = urlparse(url)
            use_ssl = parsed.scheme == "https"
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if use_ssl else 80)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query

            if use_ssl:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=30)

            headers = {"User-Agent": "PawFlow/1.0"}
            headers.update(extra_headers)

            payload = body.encode("utf-8") if body else None
            if payload and "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"

            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read(max_size)
            conn.close()

            content_type = resp.getheader("Content-Type", "")
            status = resp.status

            result = f"HTTP {status} {resp.reason}\n"
            result += f"Content-Type: {content_type}\n"
            result += f"Size: {len(data)} bytes\n\n"

            is_text = any(t in content_type.lower() for t in
                         ["text/", "json", "xml", "javascript", "html", "css", "yaml", "toml"])
            if is_text or not content_type:
                try:
                    text = data.decode("utf-8")
                    result += _sanitize_external_content(text)
                    return result
                except UnicodeDecodeError:
                    pass

            b64 = base64.b64encode(data).decode("ascii")
            result += f"(binary content, base64 encoded)\n{b64}"
            return result

        except Exception as e:
            return f"Error fetching {url}: {e}"

    def _extract_pdf(self, page, url: str) -> str:
        """Extract text from a PDF response.

        Tries pymupdf first, falls back to raw binary download + pymupdf,
        and finally returns a clear error if no PDF library is available.
        """
        # Get raw PDF bytes — scrapling may have the body available
        pdf_bytes = None
        if hasattr(page, 'body') and page.body:
            raw = page.body
            pdf_bytes = raw if isinstance(raw, bytes) else raw.encode('latin-1', errors='replace')

        # If scrapling didn't give us usable bytes, download directly
        if not pdf_bytes or not pdf_bytes[:5].startswith(b'%PDF'):
            try:
                import ssl as _ssl2
                import http.client as _http2
                from urllib.parse import urlparse as _up
                parsed = _up(url)
                ctx = _ssl2.create_default_context()
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                if parsed.scheme == 'https':
                    conn = _http2.HTTPSConnection(host, port, timeout=30,
                                                       context=ctx)
                else:
                    conn = _http2.HTTPConnection(host, port, timeout=30)
                conn.request("GET", parsed.path or "/", headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/pdf,*/*",
                })
                resp = conn.getresponse()
                if resp.status == 200:
                    pdf_bytes = resp.read()
                conn.close()
            except Exception as dl_err:
                logger.debug(f"PDF direct download failed for {url}: {dl_err}")

        if not pdf_bytes:
            return f"Error: could not download PDF from {url}"

        # Extract text with pymupdf (fitz)
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for i, pg in enumerate(doc):
                text = pg.get_text("text")
                if text.strip():
                    pages.append(f"--- Page {i + 1} ---\n{text.strip()}")
            doc.close()
            if not pages:
                return "(PDF has no extractable text — may be scanned/image-based)"
            result = "\n\n".join(pages)
            return _sanitize_external_content(result)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pymupdf extraction failed for {url}: {e}")

        # Fallback: try pdfminer
        try:
            from pdfminer.high_level import extract_text
            import io
            result = extract_text(io.BytesIO(pdf_bytes))
            if not result.strip():
                return "(PDF has no extractable text — may be scanned/image-based)"
            return _sanitize_external_content(result)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pdfminer extraction failed for {url}: {e}")

        return (f"Error: PDF detected at {url} but no PDF extraction library is available. "
                f"Install pymupdf (`pip install pymupdf`) or pdfminer (`pip install pdfminer.six`).")
