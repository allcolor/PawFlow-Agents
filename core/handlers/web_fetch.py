"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class ExecuteScriptHandler(ToolHandler):
    """Execute a Python expression or short script and return the result.

    File I/O is sandboxed through a virtual filesystem backed by FileStore.
    Uses the unified sandbox from core.sandbox.
    """

    _base_url: str = "http://localhost:9090"
    _vfs: Dict[str, bytes]

    def __init__(self):
        self._vfs = {}
        self._vfs_lock = threading.Lock()
        self._fs_resolver = None

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_fs_resolver(self, resolver):
        """Set filesystem service resolver: (service_id) -> service instance."""
        self._fs_resolver = resolver

    @property
    def name(self) -> str:
        return "execute_script"

    @property
    def description(self) -> str:
        return (
            "Execute Python code ON THE SERVER (sandboxed) and return the result. "
            "This does NOT run on the user's machine. "
            "To run commands on the user's filesystem, use filesystem(action=exec) instead. "
            "File I/O uses URL schemes: "
            "open('filestore://name.zip', 'wb') to create downloadable files, "
            "open('filestore://file_id_or_name', 'rb') to read from FileStore, "
            "open('fs://service_name/path', 'rb'/'wb') for filesystem services. "
            "Plain open('file.csv', 'w') uses in-memory sandbox. "
            "Safe imports: math, json, re, csv, datetime, zipfile, pathlib, etc. "
            "For web dev: use filesystem(action=exec) to run local servers and build scripts, "
            "and browser_action tool for screenshots and visual verification."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Can be an expression ('2+2') "
                        "or statements. Use 'result' variable for output. "
                        "open('file.csv', 'w') writes to a virtual sandbox "
                        "and returns a download URL."
                    ),
                },
            },
            "required": ["code"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.sandbox import execute_sandboxed

        code = arguments.get("code", "")
        if not code:
            return "Error: no code provided"

        try:
            with self._vfs_lock:
                output, created_files, _ = execute_sandboxed(
                    code,
                    base_url=self._base_url,
                    vfs=self._vfs,
                    fs_resolver=self._fs_resolver,
                )
        except Exception as e:
            return f"Error: {e}"

        if not output:
            output = "Script executed (no 'result' variable set)"

        if created_files:
            output += "\n\nFiles created (use show_file in a SEPARATE turn, not the same batch):\n"
            for url in created_files:
                # Extract file_id from URL for easy reference
                import re as _re_fid
                _m = _re_fid.search(r'/files/([a-f0-9]+)/', url)
                _fid = _m.group(1) if _m else ""
                output += f"- {url}" + (f" (file_id: {_fid})" if _fid else "") + "\n"
        return output


class ReadFileHandler(ToolHandler):
    """Read a file from the virtual sandbox (FileStore).

    Only files stored in the FileStore are accessible — the real filesystem
    is never touched.  Files can be looked up by filename or by file_id.
    """

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file from the sandbox. You can read any file that was "
            "previously created by create_file or open() in execute_script. "
            "Provide the filename (e.g. 'report.csv') or file ID."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Filename (e.g. 'data.csv') or file ID to read from the sandbox",
                },
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        path = arguments.get("path", "")
        if not path:
            return "Error: no path provided"

        from core.file_store import FileStore
        store = FileStore.instance()
        import os

        name = os.path.basename(path) or path

        # Try direct file_id lookup first
        result = store.get(name)
        if result:
            content = result[1].decode("utf-8", errors="replace")
            if len(content) > 10000:
                content = content[:10000] + "\n... (truncated)"
            return content

        # Try filename match
        for f in store.list_files():
            if f["filename"] == name:
                result = store.get(f["file_id"])
                if result:
                    content = result[1].decode("utf-8", errors="replace")
                    if len(content) > 10000:
                        content = content[:10000] + "\n... (truncated)"
                    return content

        return f"Error: file '{name}' not found in sandbox"


class WebSearchHandler(ToolHandler):
    """Search the web using DuckDuckGo and return results."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web. Returns titles, URLs and snippets. Parameters: query (required), max_results (int, default 5)."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max number of results to return (default 5)",
                },
            },
            "required": ["query"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        max_results = int(arguments.get("max_results", 5))
        if not query:
            return "Error: no query provided"

        try:
            from urllib.parse import urlencode, quote_plus
            import html as html_mod
            import re as _re

            # Use DuckDuckGo HTML lite (no JS needed, no API key)
            path = f"/html/?{urlencode({'q': query})}"
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection("html.duckduckgo.com", timeout=15, context=ctx)
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            })
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            conn.close()

            # Parse results from HTML
            results = []
            # DuckDuckGo HTML lite uses <a class="result__a" href="...">title</a>
            # and <a class="result__snippet" ...>snippet</a>
            blocks = _re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                body, _re.DOTALL,
            )
            for raw_url, title, snippet in blocks[:max_results]:
                title_clean = _re.sub(r"<[^>]+>", "", html_mod.unescape(title)).strip()
                snippet_clean = _re.sub(r"<[^>]+>", "", html_mod.unescape(snippet)).strip()
                # Extract actual URL from DuckDuckGo redirect
                from urllib.parse import parse_qs, urlparse as _urlparse
                url = raw_url
                try:
                    qs = parse_qs(_urlparse(raw_url).query)
                    if "uddg" in qs:
                        url = qs["uddg"][0]
                except Exception:
                    pass
                results.append(f"- {title_clean}\n  {url}\n  {snippet_clean}")

            if not results:
                return f"No results found for: {query}"

            return f"Search results for '{query}':\n\n" + "\n\n".join(results)
        except Exception as e:
            return f"Error searching: {e}"


class WebFetchHandler(ToolHandler):
    """Fetch raw content from a URL (text or binary)."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch the raw content of a URL. Returns text for HTML/JSON/text content, "
            "or base64 for binary content. Use for downloading files, reading APIs, "
            "or fetching web page source."
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
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers",
                },
                "body": {
                    "type": "string",
                    "description": "Request body for POST/PUT",
                },
                "max_size": {
                    "type": "integer",
                    "description": "Max response size in bytes (default: 5MB)",
                },
            },
            "required": ["url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        import http.client
        import base64
        from urllib.parse import urlparse

        url = arguments.get("url", "")
        if not url:
            return "Error: missing 'url' parameter"

        method = arguments.get("method", "GET").upper()
        extra_headers = arguments.get("headers", {})
        body = arguments.get("body", "")
        max_size = arguments.get("max_size", 5 * 1024 * 1024)

        try:
            parsed = urlparse(url)
            use_ssl = parsed.scheme == "https"
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if use_ssl else 80)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query

            if use_ssl:
                import ssl
                ctx = ssl.create_default_context()
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

            # Build result
            result = f"HTTP {status} {resp.reason}\n"
            result += f"Content-Type: {content_type}\n"
            result += f"Size: {len(data)} bytes\n\n"

            # Try text decoding
            is_text = any(t in content_type.lower() for t in
                         ["text/", "json", "xml", "javascript", "html", "css", "yaml", "toml"])
            if is_text or not content_type:
                try:
                    text = data.decode("utf-8")
                    # Truncate very large text responses
                    if len(text) > 50000:
                        result += text[:50000] + f"\n... (truncated, {len(text)} chars total)"
                    else:
                        result += text
                    return result
                except UnicodeDecodeError:
                    pass

            # Binary content - return base64
            b64 = base64.b64encode(data).decode("ascii")
            result += f"(binary content, base64 encoded)\n{b64}"
            return result

        except Exception as e:
            return f"Error fetching {url}: {e}"


class ScraplingFetchHandler(ToolHandler):
    """Fetch a web page using Scrapling and extract its text content.

    Supports three modes:
    - fast: Basic HTTP via curl_cffi (Fetcher) — fast, no JS
    - stealth: Anti-bot browser (StealthyFetcher) — Cloudflare bypass
    - browser: Full Playwright browser (DynamicFetcher) — JS rendering

    Returns extracted text by default, or CSS-selected content if selector given.
    """

    @property
    def name(self) -> str:
        return "scrape_url"

    @property
    def description(self) -> str:
        return (
            "Fetch a web page and extract its text content. "
            "Handles JavaScript-heavy sites and anti-bot protections. "
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
                    "description": "Fetch mode: 'fast' (default, recommended), 'stealth' (anti-bot bypass for protected sites). Auto-escalates if first mode fails.",
                    "enum": ["fast", "stealth"],
                },
            },
            "required": ["url"],
        }

    # Minimal GDPR consent cookies for common European consent providers.
    # These signal "all cookies rejected" to bypass consent walls without
    # actually accepting tracking — the scraper only reads page content.
    _GDPR_COOKIES = {
        "authId": "anonymous",
        "didomi_token": (
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
                    return self._finalize(text)

            # Step 4: Stealth mode via subprocess (avoids Playwright asyncio bug)
            if needs_stealth:
                stealth_result = self._stealth_subprocess(url, selector)
                if stealth_result is not None:
                    return self._finalize(stealth_result)
                # Stealth failed — use fast result if available (even if suspect)
                if page is not None:
                    return self._finalize(self._extract_text(page, selector))

            if fast_err:
                raise fast_err
            return "(empty page)"

        except ImportError as e:
            return f"Error: scrapling not installed — {e}"
        except Exception as e:
            error_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.warning(f"scrapling failed for {url}: {error_detail}",
                           exc_info=True)
            return self._http_fallback(url, error_detail)

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
        lower = stripped.lower()
        challenge_signs = [
            "checking your browser", "just a moment", "cloudflare",
            "enable javascript", "please turn javascript on",
            "ray id", "attention required",
        ]
        return any(sign in lower for sign in challenge_signs)

    @staticmethod
    def _finalize(text: str) -> str:
        """Truncate and return text."""
        if not text or not text.strip():
            return "(empty page)"
        if len(text) > 15000:
            text = text[:15000] + "\n... (truncated)"
        return text

    def _stealth_subprocess(self, url: str, selector: str) -> Optional[str]:
        """Run stealth fetch in a subprocess to avoid Playwright asyncio issues.

        Spawns a separate Python process with its own event loop, so
        Playwright's asyncio internals don't conflict with the main process.
        """
        import subprocess
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
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=60,
            )
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
                if len(text) > 15000:
                    text = text[:15000] + "\n... (truncated)"
                return text if text else "(empty page)"
            return (f"Error fetching {url}: scrapling={scrapling_error}, "
                    f"http={resp.status}")
        except Exception as e2:
            return (f"Error fetching {url}: scrapling={scrapling_error}, "
                    f"fallback={e2}")

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
                from urllib.parse import urlparse as _up
                parsed = _up(url)
                ctx = ssl.create_default_context()
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                if parsed.scheme == 'https':
                    conn = http.client.HTTPSConnection(host, port, timeout=30,
                                                       context=ctx)
                else:
                    conn = http.client.HTTPConnection(host, port, timeout=30)
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
            if len(result) > 15000:
                result = result[:15000] + "\n... (truncated)"
            return result
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
            if len(result) > 15000:
                result = result[:15000] + "\n... (truncated)"
            return result
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pdfminer extraction failed for {url}: {e}")

        return (f"Error: PDF detected at {url} but no PDF extraction library is available. "
                f"Install pymupdf (`pip install pymupdf`) or pdfminer (`pip install pdfminer.six`).")
