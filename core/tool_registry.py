"""Tool Registry — dispatch system for agent tool execution.

Provides a registry of executable tool handlers that agents can invoke.
Each handler declares its name, description, JSON schema, and execute method.

Builtin handlers:
- execute_script: Run a Python snippet and return the result
- read_file: Read a local file's content
- scrape_url: Fetch a web page using Scrapling

Agent tool types (flow-level agent_tools section):
- builtin: Reference to a builtin handler
- http: Call an external HTTP endpoint
- task: Execute a PawFlow task inline
- mcp: Call a tool on an MCP server (HTTP transport)
"""

import json
import logging
import http.client
import re
import ssl
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _append_task_log(conversation_id: str, task_id: str, entry: dict):
    """Append an entry to the persistent task timeline log (standalone helper)."""
    import time
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    key = f"task_log:{task_id}"
    log = store.get_extra(conversation_id, key) or []
    entry["ts"] = time.time()
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    store.set_extra(conversation_id, key, log)


class ToolHandler(ABC):
    """Interface for an executable tool that an agent can call."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM."""

    @property
    @abstractmethod
    def parameters_schema(self) -> Dict[str, Any]:
        """JSON Schema describing the tool's input parameters."""

    @abstractmethod
    def execute(self, arguments: Dict[str, Any]) -> str:
        """Execute the tool and return a text result."""


class ToolRegistry:
    """Registry of available tool handlers."""

    def __init__(self):
        self._handlers: Dict[str, ToolHandler] = {}
        self._hooks: Dict[str, List] = {}  # "pre:tool_name" or "post:tool_name" or "pre:*" / "post:*"

    def register_hook(self, event: str, callback):
        """Register a pre/post hook. Event format: 'pre:tool_name', 'post:tool_name', 'pre:*', 'post:*'."""
        self._hooks.setdefault(event, []).append(callback)

    def unregister_hook(self, event: str, callback):
        """Remove a hook callback."""
        if event in self._hooks:
            try:
                self._hooks[event].remove(callback)
            except ValueError:
                pass

    def register(self, handler: ToolHandler):
        """Register a tool handler."""
        self._handlers[handler.name] = handler

    def unregister(self, name: str):
        """Remove a tool handler."""
        self._handlers.pop(name, None)

    def get(self, name: str) -> Optional[ToolHandler]:
        """Get a handler by name."""
        return self._handlers.get(name)

    def list_tools(self) -> List[ToolHandler]:
        """List all registered handlers."""
        return list(self._handlers.values())

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions in a format suitable for LLMToolDefinition."""
        return [
            {
                "name": h.name,
                "description": h.description,
                "parameters": h.parameters_schema,
            }
            for h in self._handlers.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool by name. Returns result text or error."""
        handler = self._handlers.get(name)
        if not handler:
            return f"Error: unknown tool '{name}'"
        try:
            # Run pre-hooks (specific then wildcard)
            args = arguments
            for hook in self._hooks.get(f"pre:{name}", []) + self._hooks.get("pre:*", []):
                result = hook(name, args)
                if result is None:
                    return f"Error: tool '{name}' blocked by pre-hook"
                args = result
            # Execute
            result = handler.execute(args)
            # Run post-hooks (specific then wildcard)
            for hook in self._hooks.get(f"post:{name}", []) + self._hooks.get("post:*", []):
                result = hook(name, args, result)
            return result
        except Exception as e:
            logger.error(f"Tool '{name}' execution failed: {e}")
            return f"Error executing tool '{name}': {e}"


# ── Lazy tool mode handlers ──────────────────────────────────────────

class GetToolSchemaHandler(ToolHandler):
    """Return the full JSON schema of a tool so the LLM can call it via use_tool."""

    def __init__(self, registry: "ToolRegistry"):
        self._registry = registry

    @property
    def name(self) -> str:
        return "get_tool_schema"

    @property
    def description(self) -> str:
        return "Get the full parameter schema for a tool. Call this BEFORE use_tool to know the required arguments."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to inspect"},
            },
            "required": ["tool_name"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        name = arguments.get("tool_name", "")
        handler = self._registry.get(name)
        if not handler:
            available = [h.name for h in self._registry.list_tools()
                         if h.name not in ("get_tool_schema", "use_tool")]
            return json.dumps({"error": f"Unknown tool '{name}'",
                               "available": available})
        return json.dumps({
            "name": handler.name,
            "description": handler.description,
            "parameters": handler.parameters_schema,
        }, indent=2)


class UseToolHandler(ToolHandler):
    """Execute any tool by name. The LLM should call get_tool_schema first."""

    def __init__(self, registry: "ToolRegistry"):
        self._registry = registry

    @property
    def name(self) -> str:
        return "use_tool"

    @property
    def description(self) -> str:
        return "Execute a tool by name with the given arguments. Call get_tool_schema first to know the parameters."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to execute"},
                "arguments": {"type": "object", "description": "Arguments to pass to the tool"},
            },
            "required": ["tool_name", "arguments"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "")
        tool_args = arguments.get("arguments", {})
        if tool_name in ("get_tool_schema", "use_tool"):
            return "Error: cannot call meta-tools via use_tool"
        return self._registry.execute(tool_name, tool_args)


# ── Builtin handlers ──────────────────────────────────────────────────


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


class CreateFileHandler(ToolHandler):
    """Create a downloadable file and return a URL.

    Stores the file in the FileStore singleton with a configurable TTL.
    The base_url must be set via set_base_url() before use so the tool
    can generate valid download links.
    """

    _base_url: str = "http://localhost:9090"
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "create_file"

    @property
    def description(self) -> str:
        return (
            "Create a downloadable file on the SERVER and return its URL. "
            "Use this for files the user should preview/download in the chat. "
            "WARNING: if you want to write a file to the user's FILESYSTEM, "
            "use filesystem(action=write_file) instead — it's direct and avoids an extra copy."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename with extension (e.g. 'report.csv', 'code.py')",
                },
                "content": {
                    "type": "string",
                    "description": "File content as text",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type (default: auto-detected from extension)",
                },
            },
            "required": ["filename", "content"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.file_store import FileStore

        filename = arguments.get("filename", "file.txt")
        content = arguments.get("content", "")
        content_type = arguments.get("content_type", "")

        if not content_type:
            content_type = self._guess_content_type(filename)

        store = FileStore.instance()
        file_id = store.store(filename, content.encode("utf-8"),
                              content_type=content_type,
                              user_id=self._user_id)

        url = f"{self._base_url}/files/{file_id}/{filename}"
        return (
            f"File created: {url}\n"
            f"file_id: {file_id}"
        )

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mapping = {
            "txt": "text/plain",
            "html": "text/html",
            "htm": "text/html",
            "css": "text/css",
            "js": "application/javascript",
            "json": "application/json",
            "csv": "text/csv",
            "xml": "application/xml",
            "py": "text/x-python",
            "md": "text/markdown",
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "svg": "image/svg+xml",
            "zip": "application/zip",
        }
        return mapping.get(ext, "application/octet-stream")


class ScheduleContinuationHandler(ToolHandler):
    """Signal that the agent wants to continue working after a pause.

    When the agent calls this tool, the agent loop will:
    1. Let the LLM finish its current response (status update to the user)
    2. Wait the specified delay
    3. Inject the plan as a system message and start a new round
    """

    @property
    def name(self) -> str:
        return "schedule_continuation"

    @property
    def description(self) -> str:
        return (
            "Schedule a continuation of your work. Call this when you have more "
            "research or tasks to do but want to deliver intermediate findings first. "
            "After your current response, the system will automatically resume your work. "
            "Include a clear plan of what you'll do next."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "What you plan to do in the next round (be specific)",
                },
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait before resuming (default 3)",
                },
            },
            "required": ["plan"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan = arguments.get("plan", "")
        delay = int(arguments.get("delay_seconds", 3))
        return (
            f"Continuation scheduled. Plan: {plan}. "
            f"Resuming in {delay}s. Now give the user a status update "
            f"about what you've found so far and what you'll do next."
        )


class ScheduleRecheckHandler(ToolHandler):
    """Schedule a persistent recheck for the current conversation.

    The agent calls this to say "wake me up at time X" or "wake me up in N seconds".
    The recheck survives server restarts — it's persisted to disk.
    """

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "schedule_recheck"

    @property
    def description(self) -> str:
        return (
            "Schedule a future autonomous check-in for this conversation. "
            "Use this when the user asks you to do something at a specific time or date, "
            "or when you need to periodically monitor something. "
            "The recheck survives server restarts. "
            "You can specify either a delay in seconds or an exact ISO datetime."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds from now to schedule the recheck (e.g. 3600 for 1 hour)",
                },
                "at": {
                    "type": "string",
                    "description": "ISO 8601 datetime for the recheck (e.g. '2026-03-12T14:00:00'). "
                                   "If no timezone, assumes UTC.",
                },
                "reason": {
                    "type": "string",
                    "description": "What to do when the recheck fires (e.g. 'check stock price of AAPL')",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent to wake up (e.g. 'grok', 'qwen'). Default: whichever agent is active.",
                },
            },
            "required": ["reason"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.poll_scheduler import PollScheduler

        reason = arguments.get("reason", "scheduled recheck")
        at_str = arguments.get("at", "")
        delay = arguments.get("delay_seconds", 0)
        agent = arguments.get("agent", "")

        if not self._conversation_id:
            return "Error: no conversation context — cannot schedule recheck"

        scheduler = PollScheduler.instance()

        if at_str:
            from datetime import datetime, timezone as tz
            try:
                dt = datetime.fromisoformat(at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz.utc)
                recheck_at = dt.timestamp()
            except ValueError:
                return f"Error: invalid datetime format '{at_str}'. Use ISO 8601 (e.g. '2026-03-12T14:00:00')"
        elif delay and int(delay) > 0:
            import time
            recheck_at = time.time() + int(delay)
        else:
            return "Error: provide either 'delay_seconds' or 'at'"

        # If agent specified, encode it in reason so poller wakes the right agent
        sched_reason = reason
        if agent:
            sched_reason = f"[scheduled:{agent}] {reason}"

        scheduler.schedule(
            conversation_id=self._conversation_id,
            recheck_at=recheck_at,
            user_id=self._user_id,
            reason=sched_reason,
        )

        from datetime import datetime, timezone as tz
        dt_str = datetime.fromtimestamp(recheck_at, tz=tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        agent_info = f" Agent: {agent}" if agent else ""
        return f"Recheck scheduled for {dt_str}.{agent_info} Reason: {reason}"


class LocalFilesHandler(ToolHandler):
    """Access the user's local filesystem through the browser.

    Uses the File System Access API (Chromium only).  When the agent calls
    this tool, a ``file_request`` SSE event is sent to the browser which
    executes the operation locally and POSTs the result back.  The handler
    blocks until the browser responds (or times out).
    """

    _conversation_id: str = ""

    # Class-level shared state (across threads / instances)
    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "local_files"

    @property
    def description(self) -> str:
        return (
            "Access files on the user's local machine through the browser. "
            "The user must first open a local folder by clicking the folder button in the chat UI. "
            "Actions: list_dir (list files/subdirs), read_file (read text content), "
            "write_file (create or overwrite a file). Paths are relative to the opened folder."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_dir", "read_file", "write_file"],
                    "description": "The operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within the opened folder (e.g. 'src/main.py' or '.')",
                },
                "content": {
                    "type": "string",
                    "description": "File content for write_file action",
                },
            },
            "required": ["action", "path"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid
        from core.conversation_event_bus import ConversationEventBus

        if not self._conversation_id:
            return "Error: no conversation context"

        action = arguments.get("action", "")
        path = arguments.get("path", ".")
        content = arguments.get("content", "")

        request_id = uuid.uuid4().hex[:12]
        event = threading.Event()

        with self._lock:
            self._pending[request_id] = event

        # Ask the browser to execute the file operation
        ConversationEventBus.instance().publish_event(
            self._conversation_id, "file_request", {
                "request_id": request_id,
                "action": action,
                "path": path,
                "content": content,
            },
        )

        # Block until browser responds or timeout
        if not event.wait(timeout=60):
            with self._lock:
                self._pending.pop(request_id, None)
                self._results.pop(request_id, None)
            return (
                "Error: browser did not respond within 60s. "
                "Make sure the user has opened a local folder by clicking the folder button (📁)."
            )

        with self._lock:
            result = self._results.pop(request_id, None)
            self._pending.pop(request_id, None)

        if result is None:
            return "Error: no result received"

        if isinstance(result, dict) and "error" in result:
            return f"Error: {result['error']}"

        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)

    @classmethod
    def resolve_request(cls, request_id: str, result: Any) -> bool:
        """Called when the browser POSTs a file operation result back."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                logger.warning(f"[local_files] resolve_request for unknown/expired id: {request_id}")
                return False
            cls._results[request_id] = result
            event.set()
        return True


class RemoteExecutorHandler(ToolHandler):
    """Execute commands on the user's machine through a relay.

    Uses a RemoteExecutorService to communicate with pawflow_executor_relay.py.
    Commands are classified by risk level and may require user approval via
    an SSE dialog in the chat UI (same pattern as LocalFilesHandler).
    """

    _conversation_id: str = ""
    _user_id: str = ""
    _service = None  # RemoteExecutorService instance
    _relay_info: Dict[str, Any] = {}
    _available_services: List[Dict[str, Any]] = []  # Plan D: list of compatible services

    # Class-level shared state (across threads / instances)
    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    # ── Risk classification ──────────────────────────────────────

    _GIT_RISK = {
        "git_status": "low", "git_diff": "low", "git_log": "low",
        "git_branch": "low",
        "git_add": "medium", "git_commit": "medium", "git_checkout": "medium",
        "git_push": "high", "git_pull": "high", "git_reset": "high",
    }

    _SHELL_LOW = {
        "ls", "dir", "cat", "type", "head", "tail", "wc", "echo", "pwd", "cd",
        "whoami", "date", "file", "which", "where", "env", "printenv", "set",
        "get-childitem", "get-content", "get-location", "hostname", "uname",
        "tree", "less", "more", "sort", "uniq", "diff", "wc",
    }
    _SHELL_HIGH = {
        "rm", "del", "rmdir", "sudo", "chmod", "chown", "chgrp",
        "format", "diskpart", "invoke-expression", "start-process", "iex",
        "remove-item", "kill", "taskkill", "shutdown", "reboot",
        "net", "netsh", "iptables", "mkfs", "dd",
    }

    @classmethod
    def _classify_shell(cls, command: str) -> str:
        """Classify a shell command's risk level."""
        cmd_lower = command.lower().strip()
        # Check first word
        first = cmd_lower.split()[0] if cmd_lower else ""
        # Strip path prefix (e.g. /usr/bin/rm -> rm)
        first_base = first.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        if first_base in cls._SHELL_HIGH:
            return "high"

        # Pattern-based high risk
        high_patterns = [
            r"\brm\s+.*-r", r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b",
            r">\s*/dev/", r"curl.*\|\s*(ba)?sh", r"wget.*\|\s*(ba)?sh",
            r"\bsudo\b", r"\b(rm|del)\b.*\s+/\s*$",
            r"remove-item.*-recurse", r"invoke-expression",
        ]
        for pattern in high_patterns:
            if re.search(pattern, cmd_lower):
                return "high"

        if first_base in cls._SHELL_LOW:
            return "low"

        # Default to medium for unknown commands
        return "medium"

    @classmethod
    def classify_risk(cls, action: str, **kwargs) -> str:
        """Classify the risk level of an action."""
        if action in cls._GIT_RISK:
            return cls._GIT_RISK[action]
        if action == "python_exec":
            return "medium"
        if action == "shell":
            return cls._classify_shell(kwargs.get("command", ""))
        return "medium"

    @classmethod
    def needs_approval(cls, risk: str, approval_mode: str) -> bool:
        """Determine if approval is needed based on risk and mode."""
        if approval_mode == "strict":
            return True
        if approval_mode == "auto":
            return risk == "high"
        # "ask" (default): medium and high
        return risk in ("medium", "high")

    # ── ToolHandler interface ────────────────────────────────────

    @property
    def name(self) -> str:
        return "remote_exec"

    @property
    def description(self) -> str:
        info = self._relay_info
        plat = info.get("platform", "unknown")
        shell = info.get("shell", "unknown")
        root = info.get("root", "unknown")
        actions = info.get("actions", ["shell", "python_exec", "git"])
        desc = (
            f"Execute commands on the user's machine via a relay. "
            f"Platform: {plat}, Shell: {shell}, Root: {root}. "
            f"Available actions: {', '.join(actions)}. "
            f"For shell commands, use the correct syntax for {shell} on {plat}. "
            f"Git sub-actions: git_status, git_diff, git_log, git_add, git_commit, "
            f"git_push, git_pull, git_checkout, git_reset, git_branch."
        )
        # Plan D: multi-service selection
        if len(self._available_services) > 1:
            svc_desc = ", ".join(
                f"'{s['id']}' (root={s.get('root', '?')})"
                for s in self._available_services
            )
            desc += f" Available services: {svc_desc}. Use 'service' parameter to choose."
        return desc

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "shell", "python_exec",
                        "git_status", "git_diff", "git_log", "git_add",
                        "git_commit", "git_push", "git_pull", "git_checkout",
                        "git_reset", "git_branch",
                    ],
                    "description": "The action to execute",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (for 'shell' action)",
                },
                "code": {
                    "type": "string",
                    "description": "Python code to execute (for 'python_exec' action)",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref for diff/checkout/reset (optional)",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (for 'git_commit' action)",
                },
                "files": {
                    "type": "string",
                    "description": "Space-separated file paths (for 'git_add' action)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to relay root (default: '.')",
                },
                "service": {
                    "type": "string",
                    "description": "Service ID to use (optional, default: first available)",
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def set_service(self, service) -> None:
        self._service = service
        if service:
            self._relay_info = service.get_relay_info()
            if hasattr(service, 'set_user_id') and self._user_id:
                service.set_user_id(self._user_id)

    def set_available_services(self, services: List[Dict[str, Any]]) -> None:
        """Plan D: set list of available executor services for multi-service selection."""
        self._available_services = services

    def _resolve_service(self, service_id: str = ""):
        """Resolve which service to use (Plan D: multi-service)."""
        if service_id and self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc = registry.get_live_instance(self._user_id, service_id)
                if svc:
                    if hasattr(svc, 'set_user_id'):
                        svc.set_user_id(self._user_id)
                    return svc
            except Exception:
                pass
        return self._service

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid
        from core.conversation_event_bus import ConversationEventBus

        # Plan D: multi-service selection
        service_id = arguments.get("service", "")
        service = self._resolve_service(service_id) if service_id else self._service

        if not service:
            return (
                "Error: no remote executor relay connected.\n"
                "Run: python pawflow_executor_relay.py --connect ws://<server>/ws/relay "
                "--token <api_key> --secret <secret> --dir <path>"
            )

        action = arguments.get("action", "")
        if not action:
            return "Error: missing 'action' parameter"

        # Build display command for approval dialog
        display_cmd = self._build_display_command(action, arguments)
        risk = self.classify_risk(action, **arguments)
        approval_mode = getattr(service, 'approval_mode', 'ask')

        # Check if approval is needed
        if self.needs_approval(risk, approval_mode) and self._conversation_id:
            request_id = uuid.uuid4().hex[:12]
            event = threading.Event()

            with self._lock:
                self._pending[request_id] = event

            # Send approval request via SSE
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "exec_approval_request", {
                    "request_id": request_id,
                    "action": action,
                    "command": display_cmd,
                    "risk_level": risk,
                    "cwd": arguments.get("cwd", "."),
                    "editable": action == "shell",
                },
            )

            # Block until user responds
            if not event.wait(timeout=120):
                with self._lock:
                    self._pending.pop(request_id, None)
                    self._results.pop(request_id, None)
                return "User did not respond within 120 seconds. Command not executed."

            with self._lock:
                result = self._results.pop(request_id, None)
                self._pending.pop(request_id, None)

            if result is None:
                return "Error: no approval result received"

            if not result.get("approved"):
                return f"User denied execution of: {display_cmd}"

            # User may have edited the command
            edited = result.get("edited_command", "")
            if edited and action == "shell":
                arguments = dict(arguments)
                arguments["command"] = edited
                display_cmd = edited

        # Execute the command via the service
        try:
            kwargs = {}
            if action == "shell":
                kwargs["command"] = arguments.get("command", "")
            elif action == "python_exec":
                kwargs["code"] = arguments.get("code", "")
            elif action == "git_commit":
                kwargs["message"] = arguments.get("message", "")
            elif action in ("git_diff", "git_checkout", "git_reset"):
                ref = arguments.get("ref", "")
                if ref:
                    kwargs["ref"] = ref
                if action == "git_reset":
                    kwargs["mode"] = arguments.get("mode", "--mixed")
            elif action == "git_add":
                files = arguments.get("files", "")
                if files:
                    kwargs["files"] = files

            cwd = arguments.get("cwd", ".")
            if cwd != ".":
                kwargs["cwd"] = cwd

            data = service.send_command(action, **kwargs)

            # Publish output event for chat UI terminal display
            if self._conversation_id:
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "exec_output", {
                        "action": action,
                        "command": display_cmd,
                        "exit_code": data.get("exit_code", -1),
                        "stdout": data.get("stdout", ""),
                        "stderr": data.get("stderr", ""),
                        "duration_ms": data.get("duration_ms", 0),
                    },
                )

            return self._format_result(action, data)

        except Exception as e:
            return f"Error executing {action}: {e}"

    def _build_display_command(self, action: str, arguments: Dict[str, Any]) -> str:
        """Build a human-readable command string for display."""
        if action == "shell":
            return arguments.get("command", "")
        if action == "python_exec":
            code = arguments.get("code", "")
            if len(code) > 100:
                return f"python -c '{code[:100]}...'"
            return f"python -c '{code}'"
        if action.startswith("git_"):
            sub = action[4:]  # git_status -> status
            extras = []
            if action == "git_commit":
                extras.append(f"-m \"{arguments.get('message', '')}\"")
            elif action in ("git_diff", "git_checkout", "git_reset"):
                ref = arguments.get("ref", "")
                if ref:
                    extras.append(ref)
            elif action == "git_add":
                files = arguments.get("files", "")
                extras.append(files if files else "-A")
            return f"git {sub} {' '.join(extras)}".strip()
        return action

    def _format_result(self, action: str, data: Dict[str, Any]) -> str:
        """Format relay result for the LLM."""
        exit_code = data.get("exit_code", -1)
        stdout = data.get("stdout", "")
        stderr = data.get("stderr", "")
        duration = data.get("duration_ms", 0)

        parts = []
        if exit_code == 0:
            parts.append(f"Command succeeded (exit code 0, {duration}ms)")
        else:
            parts.append(f"Command failed (exit code {exit_code}, {duration}ms)")

        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")

        return "\n".join(parts)

    @classmethod
    def resolve_request(cls, request_id: str, result: Any) -> bool:
        """Called when the user approves/denies a command in the chat UI."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                logger.warning(f"[remote_exec] resolve_request for unknown/expired id: {request_id}")
                return False
            cls._results[request_id] = result
            event.set()
        return True


class ImageGenerationHandler(ToolHandler):
    """Generate images via a dynamically resolved image generation service.

    At execution time, calls a resolver function that discovers available
    image services and selects one based on per-agent conversation preferences.
    Handles FileStore storage and URL creation.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text prompt. "
            "Returns a download URL for the generated image. "
            "Be descriptive in your prompt for best results. "
            "You can also provide a negative_prompt to exclude unwanted elements."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate",
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "What to avoid in the image (optional)",
                },
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (optional)",
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (optional)",
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        # Resolve service dynamically
        if not self._service_resolver:
            return "Error: no image service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        try:
            result = service.generate(**arguments)
            # result = {"image_bytes": bytes, "content_type": str}

            from core.file_store import FileStore
            ct = result["content_type"]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ct.split(";")[0].strip(), "png"
            )
            filename = f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"
            file_id = FileStore.instance().store(
                filename, result["image_bytes"], content_type=ct,
                user_id=self._user_id
            )
            download_url = f"{self._base_url}/files/{file_id}/{filename}"
            return (
                f"Image generated: {download_url}\n"
                f"file_id: {file_id}\n"
                f"To save to filesystem: use filesystem(action=write_file, path=<target>, file_id={file_id}, service=<fs>)"
            )

        except Exception as e:
            return f"Error generating image: {e}"


class VideoGenerationHandler(ToolHandler):
    """Generate videos via a dynamically resolved video generation service.

    At execution time, calls a resolver function that discovers available
    video services and selects one based on per-agent conversation preferences.
    Handles FileStore storage and URL creation.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "generate_video"

    @property
    def description(self) -> str:
        return (
            "Generate a video from a text prompt. "
            "Returns a download URL for the generated video. "
            "Be descriptive in your prompt for best results."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the video to generate",
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "What to avoid in the video (optional)",
                },
                "duration": {
                    "type": "number",
                    "description": "Video duration in seconds (optional, provider-dependent)",
                },
                "width": {
                    "type": "integer",
                    "description": "Video width in pixels (optional)",
                },
                "height": {
                    "type": "integer",
                    "description": "Video height in pixels (optional)",
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        # Resolve service dynamically
        if not self._service_resolver:
            return "Error: no video service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no video generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        try:
            result = service.generate(**arguments)
            # result = {"video_bytes": bytes, "content_type": str}

            from core.file_store import FileStore
            ct = result["content_type"]
            ext = {
                "video/mp4": "mp4", "video/webm": "webm",
                "video/quicktime": "mov", "video/x-msvideo": "avi",
            }.get(ct.split(";")[0].strip(), "mp4")
            filename = f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"
            file_id = FileStore.instance().store(
                filename, result["video_bytes"], content_type=ct,
                user_id=self._user_id
            )
            download_url = f"{self._base_url}/files/{file_id}/{filename}"
            return (
                f"Video generated: {download_url}\n"
                f"file_id: {file_id}"
            )

        except Exception as e:
            return f"Error generating video: {e}"


class NotifyUserHandler(ToolHandler):
    """Send a notification to the user via available channels.

    Used by the agent to push messages when the user isn't actively watching
    the chat (e.g. after a scheduled wake-up).
    """

    def __init__(self):
        self._conversation_id = ""
        self._user_id = ""

    @property
    def name(self) -> str:
        return "notify_user"

    @property
    def description(self) -> str:
        return (
            "Send a push notification to the user. Use this when you need to "
            "proactively inform the user about something (e.g. after a scheduled "
            "task completes, a reminder fires, or an event occurs). "
            "The notification is sent via all available channels (Telegram, SSE, etc.)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message to send",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "Urgency level (default: normal)",
                },
            },
            "required": ["message"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        message = arguments.get("message", "")
        if not message:
            return "Error: message is required"
        urgency = arguments.get("urgency", "normal")

        sent_channels = []

        # Channel 1: SSE (conversation event bus — buffered if no subscriber)
        if self._conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                bus = ConversationEventBus.instance()
                bus.publish_event(self._conversation_id, "notification", {
                    "message": message,
                    "urgency": urgency,
                })
                sent_channels.append("sse")
            except Exception as e:
                logger.debug(f"SSE notify failed: {e}")

        # Channel 2: Telegram (if conversation has telegram metadata)
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                tg_chat_id = store.get_extra(
                    self._conversation_id, "telegram_chat_id",
                )
                if tg_chat_id:
                    # Try to find a running TelegramBotService
                    from services.telegram_bot_service import TelegramBotService
                    # Use the service registry pattern — for now log intent
                    logger.info(
                        f"Telegram notification to {tg_chat_id}: {message[:100]}"
                    )
                    sent_channels.append("telegram_queued")
            except Exception:
                pass

        if sent_channels:
            return f"Notification sent via: {', '.join(sent_channels)}"
        return "Notification queued (no active channels detected)"


class AskUserHandler(ToolHandler):
    """Ask the user a question and wait for their response."""

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a question and pause execution until they respond. "
            "Use when you need clarification, confirmation, or a decision from the user. "
            "The question will be displayed in the chat UI and the user can reply. "
            "Returns the user's response text."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of choices (e.g. ['yes', 'no', 'skip'])",
                },
            },
            "required": ["question"],
        }

    def set_conversation_id(self, conv_id: str):
        self._conversation_id = conv_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        question = arguments.get("question", "")
        options = arguments.get("options", [])
        if not question:
            return "Error: missing 'question' parameter"

        # Publish the question via SSE event bus
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            event_data = {
                "question": question,
                "agent_name": "assistant",
            }
            if options:
                event_data["options"] = options
            bus.publish_event(self._conversation_id, "ask_user", event_data)
        except Exception:
            pass

        # Return a message that tells the agent loop to pause and wait for user input
        options_text = ""
        if options:
            options_text = " Options: " + ", ".join(f"[{o}]" for o in options)
        return f"__ASK_USER__:{question}{options_text}"


class CreateToolHandler(ToolHandler):
    """Create a new reusable tool from Python code.

    The agent can code its own tools and install them for future use.
    The code must define a ToolHandler subclass with name, description,
    parameters_schema, and execute method.
    Tools are tagged with conversation_id for lifecycle management.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "create_tool"

    @property
    def description(self) -> str:
        return (
            "Create a new reusable tool by writing Python code. The code must "
            "define a class that inherits from ToolHandler with: name (property), "
            "description (property), parameters_schema (property returning a JSON "
            "Schema dict), and execute(self, arguments) method returning a string. "
            "The tool will be validated, sandboxed, and made available immediately. "
            "Allowed imports: math, datetime, json, re, collections, requests, etc. "
            "Forbidden: os, subprocess, eval, exec, open, file I/O."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Short name for the tool file (e.g. 'csv_converter')",
                },
                "source_code": {
                    "type": "string",
                    "description": "Python source code defining a ToolHandler subclass",
                },
            },
            "required": ["tool_name", "source_code"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "")
        source = arguments.get("source_code", "")
        if not tool_name or not source:
            return "Error: tool_name and source_code are required"

        user_id = self._user_id or "agent"
        try:
            from core.dynamic_tool_store import DynamicToolStore
            result = DynamicToolStore.instance().install(
                user_id, f"{tool_name}.py", source,
                conversation_id=self._conversation_id,
            )
            return (
                f"Tool '{result['tool_name']}' created successfully!\n"
                f"Description: {result['description']}\n"
                f"You can now call it like any other tool."
            )
        except ValueError as e:
            return f"Tool creation failed:\n{e}"


class FlowManagerHandler(ToolHandler):
    """Manage PawFlow flows — create, start, stop, delete.

    The agent can only manage flows it created (tagged with user_id).
    Flows are scoped to the current conversation.
    Flow definitions are standard PawFlow JSON flow format.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "manage_flow"

    @property
    def description(self) -> str:
        return (
            "Manage PawFlow data flows. Actions:\n"
            "- catalog: List available flow templates from the repository\n"
            "- deploy: Deploy an existing template as a new instance\n"
            "- list: List flow instances in this conversation\n"
            "- list_all: List all your flow instances across conversations\n"
            "- create: Create a new flow from a JSON definition\n"
            "- start: Start a stopped flow instance\n"
            "- stop: Stop a running flow instance\n"
            "- status: Get flow instance status\n"
            "- update: Update flow instance parameters\n"
            "- delete: Delete a flow instance\n\n"
            "IMPORTANT — Flow JSON structure for 'create' action:\n"
            "The 'definition' object MUST have this EXACT top-level structure:\n"
            "{\n"
            '  "id": "my-flow-id",\n'
            '  "name": "My Flow Name",\n'
            '  "version": "1.0.0",\n'
            '  "parameters": {},\n'
            '  "tasks": {\n'
            '    "taskA": {"type": "cronTrigger", "parameters": {"schedule": "0 7 * * *"}},\n'
            '    "taskB": {"type": "fetchHTTP", "parameters": {"url": "..."}},\n'
            '    "taskC": {"type": "executeScript", "parameters": {"script": "..."}},\n'
            '    "taskD": {"type": "sendEmail", "parameters": {"to": "...", ...}}\n'
            "  },\n"
            '  "relations": [\n'
            '    {"from": "taskA", "to": "taskB", "type": "success"},\n'
            '    {"from": "taskB", "to": "taskC", "type": "success"},\n'
            '    {"from": "taskC", "to": "taskD", "type": "success"}\n'
            "  ],\n"
            '  "services": {}\n'
            "}\n\n"
            "RULES:\n"
            "- Each task is a SEPARATE key in the top-level 'tasks' dict\n"
            "- Do NOT nest tasks inside other tasks\n"
            "- 'relations' is a top-level array (NOT inside tasks)\n"
            "- Each relation has 'from', 'to', and 'type' (success/failure/all)\n"
            "- Services use 'parameters' (NOT 'config')\n"
            "- For scheduled flows, use cronTrigger as root task (NOT generateFlowFile)\n"
            "- generateFlowFile fires ONCE then the flow auto-stops\n"
            "- ROUTING: each output FlowFile is CLONED to ALL matching outgoing relations\n"
            "- To fan out to 2+ branches: add multiple relations from the SAME task\n"
            "- Do NOT use duplicateContent to fan out — it multiplies copies × relations\n"
            "- mergeContent: params are 'separator' (NOT 'delimiter'), 'min_entries'\n"
            "- sendEmail params: 'to', 'from', 'subject', 'smtp_host', 'smtp_port', 'use_tls', "
            "'auth_type' (password|oauth2), 'username', 'password', "
            "'oauth2_client_id', 'oauth2_client_secret', 'oauth2_refresh_token', "
            "'content_type' (text/plain|text/html), 'cc', 'bcc'\n"
            "- inferLLM: can use 'service' param to reference an llmConnection service "
            "(no need for api_key/provider/base_url when service is set)\n"
            "- Available task types: cronTrigger, generateFlowFile, fetchHTTP, "
            "executeScript, sendEmail, inferLLM, log, parseJSON, transformJSON, "
            "updateAttribute, routeOnAttribute, routeOnContent, mergeContent, "
            "splitContent, filterAttribute, replaceText, hashContent, validateJSON, "
            "scraplingFetch, agentLoop, httpReceiver, handleHTTPResponse, duplicateContent\n"
            "- You can only manage flows you created/deployed."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["catalog", "deploy", "list", "list_all", "create",
                             "start", "stop", "status", "update", "delete"],
                    "description": "Action to perform",
                },
                "flow_id": {
                    "type": "string",
                    "description": "Flow instance ID (for start/stop/status/update/delete)",
                },
                "template_id": {
                    "type": "string",
                    "description": "Template flow ID from catalog (for deploy action)",
                },
                "definition": {
                    "type": "object",
                    "description": (
                        "Flow JSON definition (for create action). "
                        "MUST have top-level keys: id (string), name (string), "
                        "tasks (object with each task as a separate key), "
                        "relations (array of {from, to, type} objects). "
                        "Do NOT nest tasks inside other tasks. "
                        "Do NOT put relations inside tasks. "
                        "Services use 'parameters' not 'config'."
                    ),
                },
                "parameters": {
                    "type": "object",
                    "description": "Flow parameters to set on start",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        flow_id = arguments.get("flow_id", "")

        if action == "catalog":
            return self._catalog()
        elif action == "deploy":
            template_id = arguments.get("template_id", "")
            params = arguments.get("parameters", {})
            return self._deploy_template(template_id, params)
        elif action == "list":
            return self._list_flows(conversation_only=True)
        elif action == "list_all":
            return self._list_flows(conversation_only=False)
        elif action == "create":
            definition = arguments.get("definition", {})
            return self._create_flow(definition)
        elif action == "start":
            params = arguments.get("parameters", {})
            return self._start_flow(flow_id, params)
        elif action == "stop":
            return self._stop_flow(flow_id)
        elif action == "status":
            return self._flow_status(flow_id)
        elif action == "update":
            params = arguments.get("parameters", {})
            return self._update_flow(flow_id, params)
        elif action == "delete":
            return self._delete_flow(flow_id)
        return f"Error: unknown action '{action}'"

    def _get_deployment_registry(self):
        from gui.services.deployment_registry import DeploymentRegistry
        return DeploymentRegistry.get_instance()

    def _owner_tag(self) -> str:
        return self._user_id or None

    @staticmethod
    def _get_template_dirs():
        """Return directories where flow templates can be found."""
        from pathlib import Path
        dirs = [Path("flows")]
        # Also check configured flow directories
        env_dir = __import__("os").environ.get("PAWFLOW_FLOWS_DIR", "")
        if env_dir:
            dirs.append(Path(env_dir))
        return [d for d in dirs if d.exists()]

    def _catalog(self) -> str:
        """List available flow templates from the repository."""
        templates = []
        for tdir in self._get_template_dirs():
            for f in sorted(tdir.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    templates.append({
                        "id": data.get("id", f.stem),
                        "name": data.get("name", f.stem),
                        "version": data.get("version", ""),
                        "description": data.get("description", ""),
                        "path": str(f),
                    })
                except Exception:
                    continue
        if not templates:
            return "No flow templates found in the repository."
        lines = []
        for t in templates:
            ver = f" v{t['version']}" if t["version"] else ""
            desc = f" — {t['description']}" if t["description"] else ""
            lines.append(f"- {t['id']}{ver}: {t['name']}{desc}")
        return f"Available templates ({len(templates)}):\n" + "\n".join(lines)

    def _deploy_template(self, template_id: str, params: dict = None) -> str:
        """Deploy a flow template as a new instance in this conversation."""
        if not template_id:
            return "Error: template_id is required"

        # Find the template file
        template_path = None
        template_name = template_id
        for tdir in self._get_template_dirs():
            for f in tdir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("id") == template_id or f.stem == template_id:
                        template_path = str(f)
                        template_name = data.get("name", template_id)
                        break
                except Exception:
                    continue
            if template_path:
                break

        if not template_path:
            return (
                f"Error: template '{template_id}' not found. "
                "Use action 'catalog' to see available templates."
            )

        try:
            dep_reg = self._get_deployment_registry()
            instance_id = dep_reg.deploy(
                template_path=template_path,
                owner=self._owner_tag(),
                parameters=params or {},
                source="agent",
                conversation_id=self._conversation_id,
            )
            return (
                f"Template '{template_name}' deployed as instance "
                f"'{instance_id}'. Use start to run it."
            )
        except Exception as e:
            return f"Error deploying template: {e}"

    def _list_flows(self, conversation_only: bool = True) -> str:
        dep_reg = self._get_deployment_registry()
        dep_reg.sync_with_executors()
        owner = self._owner_tag()

        if conversation_only and self._conversation_id:
            instances = dep_reg.get_by_conversation(self._conversation_id, owner=owner)
        else:
            instances = dep_reg.get_by_owner(owner)

        if not instances:
            return "No flows found. Use catalog/deploy or create."

        lines = []
        for inst in instances:
            extras = []
            if inst.flow_id != inst.instance_id:
                extras.append(f"from: {inst.flow_id}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"- {inst.instance_id}: {inst.flow_name} [{inst.status}]{suffix}")
        return f"Your flow instances ({len(instances)}):\n" + "\n".join(lines)

    def _create_flow(self, definition: Dict) -> str:
        if not definition or "id" not in definition:
            return "Error: definition must include at least 'id' and 'tasks'"

        # Validate structure
        tasks = definition.get("tasks", {})
        if not isinstance(tasks, dict) or not tasks:
            return (
                "Error: 'tasks' must be a dict with each task as a separate key. "
                "Example: {\"taskA\": {\"type\": \"fetchHTTP\", ...}, "
                "\"taskB\": {\"type\": \"log\", ...}}"
            )
        # Check for common LLM mistake: nesting tasks inside other tasks
        for task_key, task_val in tasks.items():
            if not isinstance(task_val, dict):
                return f"Error: task '{task_key}' must be a dict with 'type' and 'parameters'"
            if "type" not in task_val:
                return (
                    f"Error: task '{task_key}' is missing 'type'. "
                    f"Each task must have a 'type' field. "
                    f"Found keys: {list(task_val.keys())}"
                )
            # Detect tasks nested inside parameters of another task
            params = task_val.get("parameters", {})
            if isinstance(params, dict):
                for pk, pv in params.items():
                    if isinstance(pv, dict) and "type" in pv and pk not in (
                        "headers", "attributes", "set", "conditions",
                    ):
                        return (
                            f"Error: it looks like task '{pk}' is nested inside "
                            f"task '{task_key}'.parameters. Tasks must be "
                            f"SEPARATE top-level keys in the 'tasks' dict, "
                            f"not nested inside other tasks."
                        )
        # Validate relations (accept legacy "connections" key too)
        conns = definition.get("relations", definition.get("connections", []))
        if not isinstance(conns, list):
            return (
                "Error: 'relations' must be a top-level array, not inside tasks. "
                "Example: [{\"from\": \"taskA\", \"to\": \"taskB\", \"type\": \"success\"}]"
            )
        # Normalize: ensure the key is "relations"
        if "connections" in definition and "relations" not in definition:
            definition["relations"] = definition.pop("connections")

        flow_id = definition["id"]
        flow_name = definition.get("name", flow_id)

        # Save the flow definition as a template in a temp location
        from pathlib import Path
        tmp_dir = Path("data/agent_templates")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # Strip internal fields
        clean_def = {k: v for k, v in definition.items() if not k.startswith("_")}
        tmp_path = tmp_dir / f"{flow_id}.json"
        tmp_path.write_text(
            json.dumps(clean_def, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Deploy via DeploymentRegistry
        try:
            dep_reg = self._get_deployment_registry()
            instance_id = dep_reg.deploy(
                template_path=str(tmp_path),
                owner=self._owner_tag(),
                parameters=definition.get("parameters", {}),
                source="agent",
                conversation_id=self._conversation_id,
                instance_id=flow_id,  # Use flow_id as instance_id for created flows
            )
            return f"Flow '{instance_id}' created. Use start to run it."
        except Exception as e:
            return f"Error creating flow: {e}"

    def _start_flow(self, flow_id: str, params: Dict = None) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        # Merge parameters
        if params:
            inst.parameters.update(params)
            dep_reg._save_instance(inst)

        # Try to start via executor registry
        try:
            from gui.services.executor_registry import ExecutorRegistry
            from engine.parser import FlowParser
            from engine.continuous_executor import ContinuousFlowExecutor

            # Load the template
            flow_path = inst.flow_path
            if not flow_path or not Path(flow_path).exists():
                flow_path = dep_reg._find_flow_path(inst.flow_id)
            if not flow_path:
                dep_reg.update_status(flow_id, "error", "Template file not found")
                return f"Error: template file not found for '{flow_id}'"

            with open(flow_path, "r", encoding="utf-8") as ff:
                raw = json.load(ff)
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            # Apply instance parameters
            if inst.parameters:
                clean.setdefault("parameters", {}).update(inst.parameters)
            flow = FlowParser.parse(clean)

            reg = ExecutorRegistry.get_instance()
            # Stop existing executor if any
            existing = reg.get(flow_id)
            if existing:
                try:
                    existing.stop()
                except Exception:
                    pass
                reg.unregister(flow_id)

            executor = ContinuousFlowExecutor(
                flow, max_workers=inst.max_workers, max_retries=inst.max_retries
            )
            executor.start()
            reg.register(flow_id, executor)
            msg = f"Flow '{flow_id}' started."
        except Exception as e:
            dep_reg.update_status(flow_id, "error", str(e))
            msg = f"Flow '{flow_id}' failed to start: {e}"

        return msg

    def _stop_flow(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        try:
            from gui.services.executor_registry import ExecutorRegistry
            reg = ExecutorRegistry.get_instance()
            executor = reg.get(flow_id)
            if executor:
                executor.stop()
                reg.unregister(flow_id)
            return f"Flow '{flow_id}' stopped."
        except Exception as e:
            return f"Flow '{flow_id}' marked stopped but error: {e}"

    def _flow_status(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        # Check real executor status
        real_status = inst.status
        try:
            from gui.services.executor_registry import ExecutorRegistry
            reg = ExecutorRegistry.get_instance()
            executor = reg.get(flow_id)
            if executor:
                status_info = executor.get_status()
                real_status = "running" if status_info.get("is_running", False) else "stopped"
            elif real_status == "running":
                real_status = "not_running (no executor)"
        except Exception:
            pass

        template_info = f"\nTemplate: {inst.flow_id}" if inst.flow_id != inst.instance_id else ""
        sched_info = ""
        return (
            f"Flow: {inst.flow_name}\n"
            f"Instance: {flow_id}\n"
            f"Status: {real_status}\n"
            f"Parameters: {json.dumps(inst.parameters)}"
            f"{template_info}{sched_info}"
        )

    def _delete_flow(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        dep_reg.undeploy(flow_id)
        return f"Flow '{flow_id}' deleted."

    def _update_flow(self, flow_id: str, params: Dict) -> str:
        if not flow_id:
            return "Error: flow_id is required"
        if not params:
            return "Error: parameters are required for update"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        inst.parameters.update(params)
        dep_reg._save_instance(inst)
        return f"Flow '{flow_id}' parameters updated: {json.dumps(params)}"

    @staticmethod
    def cleanup_conversation(conversation_id: str):
        """Delete all flows belonging to a conversation. Called on conv delete."""
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            instances = dep_reg.get_by_conversation(conversation_id)
            deleted = 0
            for inst in instances:
                dep_reg.undeploy(inst.instance_id)
                deleted += 1
            if deleted:
                logger.info("[cleanup] deleted %d flows for conversation %s", deleted, conversation_id)
        except Exception as e:
            logger.warning("Failed to cleanup conversation flows: %s", e)


class AskAgentHandler(ToolHandler):
    """Ask another agent defined in the current conversation.

    Sends a question to a named agent and returns its response.
    The target agent has its own system prompt/persona but shares
    the same conversation context.
    """

    def __init__(self):
        self._conversation_id = ""
        self._user_id = ""
        self._llm_client = None
        self._client_resolver = None
        self._model = ""

    @property
    def name(self) -> str:
        return "ask_agent"

    @property
    def description(self) -> str:
        return (
            "Ask another agent defined in this conversation. "
            "Each agent has a specialized persona/prompt. Use this to "
            "delegate questions to a more specialized agent."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to ask",
                },
                "question": {
                    "type": "string",
                    "description": "Question or task for the agent",
                },
            },
            "required": ["agent_name", "question"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_llm_client(self, client, model: str):
        self._llm_client = client
        self._model = model

    def set_client_resolver(self, resolver):
        self._client_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        agent_name = arguments.get("agent_name", "")
        question = arguments.get("question", "")
        if not agent_name or not question:
            return "Error: agent_name and question are required"

        if not self._conversation_id:
            return "Error: no conversation context"

        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = self._user_id or "anonymous"
            agent_def = rs.get_any("agent", agent_name, uid,
                                   conversation_id=self._conversation_id)
            if not agent_def:
                # Case-insensitive fallback
                for a in rs.list_all("agent", uid,
                                     conversation_id=self._conversation_id):
                    if a["name"].lower() == agent_name.lower():
                        agent_def = a
                        agent_name = a["name"]
                        break
            if not agent_def:
                all_agents = rs.list_all("agent", uid,
                                         conversation_id=self._conversation_id)
                available = ", ".join(a["name"] for a in all_agents) or "none"
                return f"Error: agent '{agent_name}' not found. Available: {available}"

            # Resolve LLM client for this agent
            client = self._llm_client
            model = self._model
            llm_svc = agent_def.get("llm_service", "")
            if llm_svc and "${" in llm_svc:
                from core.expression import resolve_expression
                llm_svc = resolve_expression(llm_svc, owner=uid)
                if "${" in llm_svc:
                    llm_svc = ""
            if llm_svc and self._client_resolver:
                try:
                    resolved_client, _ = self._client_resolver(llm_svc, uid)
                    if resolved_client:
                        client = resolved_client
                except Exception:
                    pass
            agent_model = agent_def.get("model", "")
            if agent_model:
                model = agent_model

            if not client:
                return "Error: LLM client not configured"

            # Single-turn call to the target agent
            from core.llm_client import LLMMessage
            messages = [
                LLMMessage(role="system", content=agent_def["prompt"]),
                LLMMessage(role="user", content=question),
            ]
            response = client.complete(
                messages=messages,
                model=model or None,
                max_tokens=0,
            )
            return f"[{agent_name}]: {response.content}"
        except Exception as e:
            return f"Error calling agent '{agent_name}': {e}"


class CreatePlanHandler(ToolHandler):
    """Create or replace a structured plan for a multi-step task.

    The agent uses this to break down complex user requests into steps,
    show progress, and track completion.
    """

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def description(self) -> str:
        return (
            "Create a structured plan for a multi-step task. Each step has a "
            "description and status (pending/in_progress/done/skipped). "
            "Use this for complex requests that involve multiple operations. "
            "The plan is shown to the user and persisted with the conversation."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Plan title (short summary of the goal)",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "skipped"],
                                "default": "pending",
                            },
                        },
                        "required": ["description"],
                    },
                    "description": "List of plan steps",
                },
            },
            "required": ["title", "steps"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        title = arguments.get("title", "")
        steps = arguments.get("steps", [])
        if not title or not steps:
            return "Error: title and steps are required"

        plan = {
            "title": title,
            "steps": [
                {
                    "index": i + 1,
                    "description": s.get("description", ""),
                    "status": s.get("status", "pending"),
                }
                for i, s in enumerate(steps)
            ],
        }

        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                store.set_extra(self._conversation_id, "plan", plan)
            except Exception as e:
                logger.warning(f"Failed to persist plan: {e}")

        lines = [f"**Plan: {title}**"]
        for s in plan["steps"]:
            icon = {"pending": "\u25cb", "in_progress": "\u25d4",
                    "done": "\u25cf", "skipped": "\u25cb"}.get(s["status"], "\u25cb")
            lines.append(f"  {icon} {s['index']}. {s['description']}")
        return "\n".join(lines)


class UpdatePlanHandler(ToolHandler):
    """Update the status of steps in the current plan."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def description(self) -> str:
        return (
            "Update the status of one or more steps in the current plan. "
            "Call this as you complete steps to show progress to the user."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer", "description": "Step number (1-based)"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "skipped"],
                            },
                            "note": {"type": "string", "description": "Optional note about the result"},
                        },
                        "required": ["step", "status"],
                    },
                },
            },
            "required": ["updates"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        updates = arguments.get("updates", [])
        if not updates:
            return "Error: updates list is required"

        plan = None
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                plan = store.get_extra(self._conversation_id, "plan")
            except Exception:
                pass

        if not plan:
            return "Error: no active plan found. Use create_plan first."

        for u in updates:
            step_num = int(u.get("step", 0))
            status = u.get("status", "")
            note = u.get("note", "")
            for s in plan["steps"]:
                if s["index"] == step_num:
                    s["status"] = status
                    if note:
                        s["note"] = note
                    break

        # Persist
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(
                    self._conversation_id, "plan", plan,
                )
            except Exception:
                pass

        # Format
        lines = [f"**Plan: {plan['title']}**"]
        done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
        total = len(plan["steps"])
        lines.append(f"Progress: {done_count}/{total}")
        for s in plan["steps"]:
            icon = {"pending": "\u25cb", "in_progress": "\u25d4",
                    "done": "\u2713", "skipped": "\u2013"}.get(s["status"], "\u25cb")
            note = f' — {s["note"]}' if s.get("note") else ""
            lines.append(f"  {icon} {s['index']}. {s['description']}{note}")
        return "\n".join(lines)


class RememberHandler(ToolHandler):
    """Store a fact in persistent long-term memory.

    The agent uses this to remember user preferences, important facts,
    or anything that should survive across conversations.
    """

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""
        self._embed_fn = None

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact or piece of information in persistent memory. "
            "Use this to remember user preferences, important context, "
            "or anything that should be recalled in future conversations. "
            "By default the memory is scoped to your agent. Set global=true "
            "to make it accessible to all agents."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The fact or information to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization and retrieval (e.g. 'preference', 'name', 'project')",
                },
                "scope": {
                    "type": "string",
                    "enum": ["conversation", "agent", "global", "private"],
                    "description": "Where to store: conversation (this conv, all agents), agent (all convs, this agent), global (everywhere), private (this agent + this conv only). Default: agent.",
                },
            },
            "required": ["text"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_embed_fn(self, fn):
        """Set embedding function for auto-embedding memories."""
        self._embed_fn = fn

    def execute(self, arguments: Dict[str, Any]) -> str:
        text = arguments.get("text", "")
        if not text:
            return "Error: text is required"
        tags = arguments.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)]
        scope = arguments.get("scope", "agent")

        user_id = self._user_id or "anonymous"
        # Resolve scope to agent + conversation_id
        if scope == "global":
            agent, conv_id = "", ""
        elif scope == "conversation":
            agent, conv_id = "", self._conversation_id
        elif scope == "private":
            agent, conv_id = self._agent_name or "", self._conversation_id
        else:  # "agent" (default)
            agent, conv_id = self._agent_name or "", ""
        try:
            # Auto-embed if embed function is available
            embedding = None
            if self._embed_fn:
                try:
                    embedding = self._embed_fn(text)
                except Exception as emb_err:
                    logger.debug(f"Auto-embed failed: {emb_err}")

            from core.memory_store import MemoryStore
            entry = MemoryStore.instance().remember(
                user_id, text, tags, source="agent",
                embedding=embedding, agent=agent,
                conversation_id=conv_id,
            )
            scope_label = scope
            if scope == "private":
                scope_label = f"private:{agent}@{conv_id[:8]}"
            elif scope == "agent" and agent:
                scope_label = f"agent:{agent}"
            elif scope == "conversation":
                scope_label = f"conv:{conv_id[:8]}"
            return f"Remembered (id: {entry.id}, tags: {entry.tags}, scope: {scope_label})"
        except Exception as e:
            return f"Error storing memory: {e}"


class SemanticRecallHandler(ToolHandler):
    """Search memories by meaning/similarity using vector embeddings."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""
        self._embed_fn = None

    @property
    def name(self) -> str:
        return "semantic_recall"

    @property
    def description(self) -> str:
        return (
            "Search memories by meaning and similarity (semantic search). "
            "Use this when keyword search (recall) doesn't find what you need, "
            "or when the user asks about a topic using different words than stored."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query to search by meaning",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 5)",
                },
            },
            "required": ["query"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_embed_fn(self, fn):
        """Set embedding function for query embedding."""
        self._embed_fn = fn

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: query is required"
        limit = int(arguments.get("limit", 5))

        if not self._embed_fn:
            return "Error: semantic search not available (no embedding provider configured)"

        user_id = self._user_id or "anonymous"
        try:
            query_embedding = self._embed_fn(query)
            from core.memory_store import MemoryStore
            results = MemoryStore.instance().semantic_recall(
                user_id, query_embedding, limit=limit,
                agent_name=self._agent_name,
                conversation_id=self._conversation_id,
            )
            if not results:
                return "No semantically similar memories found."

            lines = []
            for entry, score in results:
                tag_str = ", ".join(entry.tags) if entry.tags else "none"
                lines.append(f"- [{entry.id}] (score: {score:.3f}, tags: {tag_str}) {entry.text}")
            return f"Found {len(results)} similar memories:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error in semantic recall: {e}"


class RecallHandler(ToolHandler):
    """Retrieve facts from persistent long-term memory."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search persistent memory for previously stored facts, preferences, "
            "or context. Use this at the start of conversations or when the user "
            "references something you should know."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in memories",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (e.g. 'preference', 'name')",
                },
            },
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        tags = arguments.get("tags")
        if isinstance(tags, str):
            tags = [tags]

        user_id = self._user_id or "anonymous"
        try:
            from core.memory_store import MemoryStore
            entries = MemoryStore.instance().recall(
                user_id, query=query, tags=tags, limit=20,
                agent_name=self._agent_name,
                conversation_id=self._conversation_id,
            )
            if not entries:
                return "No memories found matching your query."

            lines = []
            for e in entries:
                tag_str = ", ".join(e.tags) if e.tags else "none"
                scope = "🌐" if not e.agent and not e.conversation_id else (
                    "🔒" if e.agent and e.conversation_id else (
                        "💬" if e.conversation_id else "🤖"))
                lines.append(f"- [{e.id}] {scope} ({tag_str}) {e.text}")
            return f"Found {len(entries)} memories:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error recalling memories: {e}"


class ForgetHandler(ToolHandler):
    """Delete a specific memory entry."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return "Delete a specific memory by its ID. Use recall first to find the ID."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to delete (from recall results)",
                },
            },
            "required": ["memory_id"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        memory_id = arguments.get("memory_id", "")
        if not memory_id:
            return "Error: memory_id is required"

        user_id = self._user_id or "anonymous"
        try:
            from core.memory_store import MemoryStore
            deleted = MemoryStore.instance().forget(user_id, memory_id)
            return f"Memory {memory_id} deleted." if deleted else f"Memory {memory_id} not found."
        except Exception as e:
            return f"Error deleting memory: {e}"


class AssignTaskHandler(ToolHandler):
    """Assign a task to an agent (self or another agent)."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""
        self._user_id = ""

    @property
    def name(self) -> str:
        return "assign_task"

    @property
    def description(self) -> str:
        return (
            "Assign a task to yourself or another agent. The assigned agent "
            "will work on it autonomously, rescheduling at regular intervals "
            "until the task is complete."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent to assign the task to (name, or 'self' for yourself)",
                },
                "task_def_name": {
                    "type": "string",
                    "description": "Name of a task definition from the library (use instead of task+criteria)",
                },
                "task": {
                    "type": "string",
                    "description": "Inline task description (alternative to task_def_name)",
                },
                "completion_criteria": {
                    "type": "string",
                    "description": "How to know the task is done (verifiable criteria)",
                },
                "interval": {
                    "type": "string",
                    "description": "Schedule frequency. Examples: '60' (every 60s), '3/5m' (3 times per 5min), '2-4/h' (2-4 per hour). Default: 6/1m (same as autoconv)",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Max work sessions before auto-fail (default: 50)",
                },
                "verifier": {
                    "type": "string",
                    "description": "Agent that verifies completion (optional)",
                },
                "variables": {
                    "type": "object",
                    "description": "Variables to substitute in prompt/criteria. E.g. {\"nbr_images\": \"20\"} replaces ${nbr_images} in the task definition. Use \\${...} in definitions to keep literal ${...} unresolved.",
                },
                "context": {
                    "type": "string",
                    "description": "Context mode: 'isolated' (default), 'last:N' (last N messages), 'summary:N' (summary of N tokens), 'full' (entire parent context)",
                },
            },
            "required": ["agent"],
        }

    @staticmethod
    def _parse_interval(spec: str, fallback: int = 10) -> dict:
        """Parse interval spec → {min: seconds, max: seconds, spec: original}.

        Formats:
          '60'       → fixed 60s
          '3/5m'     → 3 times per 5 minutes
          '2-4/h'    → 2-4 times per hour
        """
        import re
        spec = spec.strip()
        # Plain seconds
        try:
            secs = int(spec)
            return {"min": secs, "max": secs, "spec": spec}
        except ValueError:
            pass
        # Frequency spec: count[-count]/[num]unit
        m = re.match(r'^(\d+)(?:-(\d+))?/(\d*)([smhd])$', spec)
        if not m:
            return {"min": fallback, "max": fallback, "spec": spec}
        count_min = int(m.group(1))
        count_max = int(m.group(2) or count_min)
        if count_min <= 0 or count_max < count_min:
            return {"min": fallback, "max": fallback, "spec": spec}
        duration_num = int(m.group(3) or 1)
        unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(4)]
        period = duration_num * unit
        max_interval = period // count_min
        min_interval = period // count_max
        return {"min": max(1, min_interval), "max": max(1, max_interval), "spec": spec}

    @staticmethod
    def _get_task_delay(task_data: dict) -> int:
        """Get the next delay in seconds from a task's interval config."""
        import random
        iv = task_data.get("interval", {})
        if isinstance(iv, int):
            return iv
        if isinstance(iv, dict):
            return random.randint(iv.get("min", 60), iv.get("max", 60))
        return 60

    @staticmethod
    def _resolve_task_vars(text: str, variables: dict, user_id: str = "") -> str:
        """Resolve variables in task prompt/criteria.

        Resolution order:
        1. Escaped \\${...} → preserved as literal ${...}
        2. ${secrets.*} → NEVER resolved (kept as-is to prevent leaks)
        3. Custom variables from 'variables' dict: ${key} → value
        4. Standard expressions: ${global.*}, ${env.*}
        """
        import re
        # Step 1: protect escaped \${...} with placeholder
        _esc = "\x00ESC\x00"
        text = text.replace("\\${", _esc)
        # Step 2: protect ${secrets.*} — NEVER substitute secrets into task text
        _sec = "\x00SEC\x00"
        _secrets_found = re.findall(r'\$\{secrets\.[^}]+\}', text)
        for s in _secrets_found:
            text = text.replace(s, _sec + s + _sec)
        # Step 3: replace custom variables ${key}
        if variables:
            for key, val in variables.items():
                text = text.replace(f"${{{key}}}", str(val))
        # Step 4: resolve remaining ${global.*}, ${env.*} (NOT secrets)
        if "${" in text:
            from core.expression import resolve_expression
            text = resolve_expression(text, owner=user_id)
        # Step 5: restore protected secrets and escaped expressions
        for s in _secrets_found:
            text = text.replace(_sec + s + _sec, s)
        text = text.replace(_esc, "${")
        return text

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        target = arguments.get("agent", "")
        if target == "self":
            target = self._agent_name or "assistant"
        task_desc = arguments.get("task", "")
        task_def_name = arguments.get("task_def_name", "")

        # Library lookup: resolve task_def_name → prompt + criteria + interval
        if task_def_name and not task_desc:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            definition = rs.get_any("task_def", task_def_name, self._user_id)
            if not definition:
                return f"Error: task definition '{task_def_name}' not found"
            task_desc = definition.get("prompt", "")
            if not arguments.get("completion_criteria"):
                arguments["completion_criteria"] = definition.get("criteria", "")
            if not arguments.get("interval"):
                arguments["interval"] = definition.get("default_interval", "6/1m")

        if not task_desc:
            return "Error: task description or task_def_name required"
        if not self._conversation_id:
            return "Error: no conversation context"

        # Variable substitution in prompt and criteria
        _vars = arguments.get("variables") or {}
        if _vars or "${" in task_desc:
            task_desc = self._resolve_task_vars(task_desc, _vars, self._user_id)
        criteria = arguments.get("completion_criteria", "")
        if criteria and (_vars or "${" in criteria):
            criteria = self._resolve_task_vars(criteria, _vars, self._user_id)
        _raw_iv = arguments.get("interval")
        interval_spec = str(_raw_iv) if _raw_iv else "6/1m"
        max_iter = int(arguments.get("max_iterations", 50))
        verifier = arguments.get("verifier", "")

        # Parse interval: plain seconds or frequency spec (3/5m, 2-4/h)
        interval_data = self._parse_interval(interval_spec)

        import uuid as _uuid
        task_id = "t_" + _uuid.uuid4().hex[:8]

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        # Store in agent_tasks dict (multiple tasks per agent)
        all_tasks = store.get_extra(self._conversation_id, "agent_tasks") or {}
        context_mode = arguments.get("context", "isolated")
        task_data = {
            "task_id": task_id,
            "agent": target,
            "task": task_desc,
            "completion_criteria": criteria,
            "status": "active",
            "interval": interval_data,
            "max_iterations": max_iter,
            "iterations_done": 0,
            "verifier": verifier,
            "assigned_by": self._agent_name or self._user_id or "unknown",
            "created_by": self._agent_name or self._user_id or "unknown",
            "task_def_name": task_def_name,
            "created_at": _t.time(),
            "last_result": "",
            "context_mode": context_mode,
        }
        all_tasks[task_id] = task_data
        store.set_extra(self._conversation_id, "agent_tasks", all_tasks)

        # Schedule first wake-up
        first_delay = self._get_task_delay(task_data)
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().schedule_delay(
            self._conversation_id, first_delay,
            key=f"{self._conversation_id}::task::{task_id}",
            reason=f"[agent_task:{task_id}] assigned task ({target})",
            user_id=self._user_id,
        )

        # Publish SSE
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "task_progress", {
                    "task_id": task_id, "agent": target, "stage": "assigned",
                    "task": task_desc[:200], "verifier": verifier,
                    "assigned_by": self._agent_name or "user",
                },
            )
        except Exception:
            pass

        try:
            _append_task_log(self._conversation_id, task_id, {
                "type": "assigned",
                "agent": target,
                "task": task_desc[:200],
                "detail": f"Assigned by {self._agent_name or 'user'}, verifier={verifier or 'none'}",
            })
        except Exception:
            pass

        v_info = f" (verifier: {verifier})" if verifier else ""
        iv_label = interval_data.get("spec", str(first_delay))
        return f"Task {task_id} assigned to '{target}'{v_info}. Interval: {iv_label}. First in {first_delay}s."


class CompleteTaskHandler(ToolHandler):
    """Report progress or completion of an assigned task.

    Called by the agent at each wake-up to update task status.
    If done=true and a verifier agent is assigned, triggers verification.
    """

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Report progress or completion of your assigned task. "
            "Call this at each iteration to update your progress. "
            "Set done=true when the task is finished."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to update (optional if you have only one active task)",
                },
                "done": {
                    "type": "boolean",
                    "description": "True if the task is complete, false if still in progress",
                },
                "progress": {
                    "type": "string",
                    "description": "Status update (e.g. '30/100 posts scraped')",
                },
                "result": {
                    "type": "string",
                    "description": "Final result summary (only when done=true)",
                },
            },
            "required": ["done", "progress"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        task_id = arguments.get("task_id", "")
        done = arguments.get("done", False)
        progress = arguments.get("progress", "")
        result = arguments.get("result", "")

        if not self._conversation_id:
            return "Error: no conversation context"

        agent = self._agent_name or "assistant"
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        all_tasks = store.get_extra(self._conversation_id, "agent_tasks") or {}

        # Find the task — by ID or by agent (if only one active)
        task = None
        if task_id:
            task = all_tasks.get(task_id)
        else:
            # Find active tasks for this agent
            my_tasks = [t for t in all_tasks.values()
                        if t.get("agent") == agent and t.get("status") in ("active",)]
            if len(my_tasks) == 1:
                task = my_tasks[0]
                task_id = task["task_id"]
            elif len(my_tasks) > 1:
                ids = [t["task_id"] for t in my_tasks]
                return f"Multiple active tasks. Specify task_id: {', '.join(ids)}"

        if not task or task.get("status") not in ("active", "verifying"):
            return "No active task found."

        task["iterations_done"] = task.get("iterations_done", 0) + 1
        task["last_result"] = result if done else progress
        task["last_update"] = _t.time()

        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "task_progress", {
                    "task_id": task_id, "agent": agent, "done": done,
                    "progress": progress, "result": result,
                    "iterations": task["iterations_done"],
                },
            )
        except Exception:
            pass

        try:
            _log_type = "completed" if done else "progress"
            _log_detail = result[:200] if done else progress[:200]
            _append_task_log(self._conversation_id, task_id, {
                "type": _log_type,
                "agent": agent,
                "detail": _log_detail,
            })
        except Exception:
            pass

        if done:
            verifier = task.get("verifier", "")
            if verifier:
                task["status"] = "verifying"
                all_tasks[task_id] = task
                store.set_extra(self._conversation_id, "agent_tasks", all_tasks)
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().schedule_delay(
                    self._conversation_id, 0,
                    key=f"{self._conversation_id}::task_verify::{task_id}",
                    reason=f"[task_verify:{task_id}] verify by {verifier} ({agent})",
                    user_id=task.get("assigned_by", ""),
                )
                return f"Task {task_id} marked done. Verifier '{verifier}' will check."
            else:
                # Remove completed task — trace is in chat history
                all_tasks.pop(task_id, None)
                store.set_extra(self._conversation_id, "agent_tasks", all_tasks)
                # Cancel any pending schedule
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().cancel(
                    f"{self._conversation_id}::task::{task_id}")
                return f"Task {task_id} completed."
        else:
            task["status"] = "active"
            all_tasks[task_id] = task
            store.set_extra(self._conversation_id, "agent_tasks", all_tasks)
            delay = AssignTaskHandler._get_task_delay(task)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().schedule_delay(
                self._conversation_id, delay,
                key=f"{self._conversation_id}::task::{task_id}",
                reason=f"[agent_task:{task_id}] continue ({task.get('agent', agent)})",
                user_id=task.get("assigned_by", ""),
            )
            return f"Task {task_id} progress noted. Next in {delay}s."


class VerifyTaskHandler(ToolHandler):
    """Approve or reject a completed task (used by verifier agents)."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "verify_task"

    @property
    def description(self) -> str:
        return (
            "Approve or reject a task that another agent claims to have completed. "
            "You are the verifier — check the result against the criteria."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to verify",
                },
                "approved": {
                    "type": "boolean",
                    "description": "True if the task is satisfactorily completed",
                },
                "reason": {
                    "type": "string",
                    "description": "Explanation (required if rejecting)",
                },
            },
            "required": ["task_id", "approved"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        task_id = arguments.get("task_id", "")
        approved = arguments.get("approved", False)
        reason = arguments.get("reason", "")

        if not self._conversation_id or not task_id:
            return "Error: missing conversation or task_id"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        all_tasks = store.get_extra(self._conversation_id, "agent_tasks") or {}
        task = all_tasks.get(task_id)
        if not task:
            return f"Task '{task_id}' not found"
        target_agent = task.get("agent", "?")

        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "task_progress", {
                    "task_id": task_id, "agent": target_agent,
                    "verifier": self._agent_name,
                    "approved": approved, "reason": reason,
                    "stage": "verified",
                },
            )
        except Exception:
            pass

        try:
            _append_task_log(self._conversation_id, task_id, {
                "type": "verified",
                "agent": target_agent,
                "verifier": self._agent_name,
                "approved": approved,
                "detail": reason[:200] if reason else ("approved" if approved else "rejected"),
            })
        except Exception:
            pass

        if approved:
            # Remove completed task
            all_tasks.pop(task_id, None)
            store.set_extra(self._conversation_id, "agent_tasks", all_tasks)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().cancel(
                f"{self._conversation_id}::task::{task_id}")
            PollScheduler.instance().cancel(
                f"{self._conversation_id}::task_verify::{task_id}")
            return f"Task {task_id} approved and completed."
        else:
            task["status"] = "active"
            task["last_rejection"] = {
                "by": self._agent_name, "reason": reason, "at": _t.time(),
            }
            all_tasks[task_id] = task
            store.set_extra(self._conversation_id, "agent_tasks", all_tasks)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().schedule_delay(
                self._conversation_id, 0,
                key=f"{self._conversation_id}::task::{task_id}",
                reason=f"[agent_task:{task_id}] rejected: {reason[:80]} ({target_agent})",
                user_id=task.get("assigned_by", ""),
            )
            return f"Task {task_id} rejected. Agent '{target_agent}' rescheduled."


class PawFlowHelpHandler(ToolHandler):
    """Query the PawFlow platform catalog and flow-authoring guide.

    Provides dynamic information about available tasks, services, and their
    configuration schemas, plus a static guide on how to build flows.
    """

    @property
    def name(self) -> str:
        return "pawflow_help"

    @property
    def description(self) -> str:
        return (
            "Get information about the PawFlow platform. Topics:\n"
            "- tasks: List all available task types\n"
            "- task:<type>: Get detailed info about a specific task\n"
            "- services: List all available service types\n"
            "- service:<type>: Get detailed info about a specific service\n"
            "- flow_guide: How to create a flow JSON definition\n"
            "- expressions: Expression syntax reference\n"
            "- triggers: Available trigger/scheduling options\n"
            "- resources: Agent/skill/MCP resource management guide"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic to query. Use 'tasks', 'task:<type>', 'services', "
                        "'service:<type>', 'flow_guide', 'expressions', or 'triggers'."
                    ),
                },
            },
            "required": ["topic"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        topic = arguments.get("topic", "").strip()
        if not topic:
            return "Error: topic is required"

        if topic == "tasks":
            return self._list_tasks()
        elif topic.startswith("task:"):
            return self._task_detail(topic[5:].strip())
        elif topic == "services":
            return self._list_services()
        elif topic.startswith("service:"):
            return self._service_detail(topic[8:].strip())
        elif topic == "flow_guide":
            return self._flow_guide()
        elif topic == "expressions":
            return self._expressions_guide()
        elif topic == "triggers":
            return self._triggers_guide()
        elif topic == "resources":
            return self._resources_guide()
        else:
            return (
                f"Unknown topic '{topic}'. Available: tasks, task:<type>, "
                "services, service:<type>, flow_guide, expressions, triggers, resources"
            )

    def _list_tasks(self) -> str:
        from core import TaskFactory
        types = sorted(TaskFactory.list_types())
        lines = []
        for t in types:
            try:
                cls = TaskFactory.get(t)
                desc = getattr(cls, "DESCRIPTION", "") or ""
                tags = getattr(cls, "TAGS", []) or []
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"- {t}: {desc}{tag_str}")
            except Exception:
                lines.append(f"- {t}")
        return f"Available tasks ({len(types)}):\n" + "\n".join(lines)

    def _task_detail(self, task_type: str) -> str:
        from core import TaskFactory
        try:
            cls = TaskFactory.get(task_type)
        except Exception:
            return f"Task '{task_type}' not found. Use topic 'tasks' to list available types."

        info = [f"# {task_type}"]
        for attr in ("NAME", "VERSION", "DESCRIPTION", "ICON", "TAGS"):
            val = getattr(cls, attr, None)
            if val:
                info.append(f"{attr}: {val}")

        # Get parameter schema
        schema = {}
        if hasattr(cls, "PARAMETERS") and cls.PARAMETERS:
            schema = cls.PARAMETERS
        else:
            try:
                inst = cls.__new__(cls)
                inst.config = {}
                if hasattr(inst, "get_parameter_schema"):
                    schema = inst.get_parameter_schema()
            except Exception:
                pass

        if schema:
            info.append("\nParameters:")
            for pname, pdef in schema.items():
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                req = " (required)" if pdef.get("required") else ""
                default = pdef.get("default")
                default_str = f" [default: {default}]" if default is not None else ""
                info.append(f"  - {pname}: {ptype} — {pdesc}{req}{default_str}")

        return "\n".join(info)

    def _list_services(self) -> str:
        from core import ServiceFactory
        types = sorted(ServiceFactory.list_types())
        lines = []
        for t in types:
            try:
                cls = ServiceFactory.get(t)
                desc = getattr(cls, "DESCRIPTION", "") or ""
                lines.append(f"- {t}: {desc}")
            except Exception:
                lines.append(f"- {t}")
        return f"Available services ({len(types)}):\n" + "\n".join(lines)

    def _service_detail(self, svc_type: str) -> str:
        from core import ServiceFactory
        try:
            cls = ServiceFactory.get(svc_type)
        except Exception:
            return f"Service '{svc_type}' not found. Use topic 'services' to list available types."

        info = [f"# {svc_type}"]
        for attr in ("NAME", "VERSION", "DESCRIPTION"):
            val = getattr(cls, attr, None)
            if val:
                info.append(f"{attr}: {val}")

        schema = {}
        if hasattr(cls, "PARAMETERS") and cls.PARAMETERS:
            schema = cls.PARAMETERS
        else:
            try:
                inst = cls.__new__(cls)
                inst.config = {}
                if hasattr(inst, "get_parameter_schema"):
                    schema = inst.get_parameter_schema()
            except Exception:
                pass

        if schema:
            info.append("\nParameters:")
            for pname, pdef in schema.items():
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                req = " (required)" if pdef.get("required") else ""
                info.append(f"  - {pname}: {ptype} — {pdesc}{req}")

        return "\n".join(info)

    def _flow_guide(self) -> str:
        return """# PawFlow Flow Authoring Guide

## Flow JSON Structure
```json
{
  "id": "my-flow",
  "name": "My Flow",
  "version": "1.0.0",
  "description": "What this flow does",
  "parameters": {},
  "tasks": {
    "task_id": {
      "type": "<task_type>",
      "parameters": {
        "key": "value"
      }
    }
  },
  "relations": [
    {
      "from": "task_id_1",
      "to": "task_id_2",
      "type": "success"
    }
  ],
  "services": {
    "service_id": {
      "type": "<service_type>",
      "parameters": {
        "key": "value"
      }
    }
  }
}
```

IMPORTANT:
- Relations use "from"/"to" (NOT "source"/"destination")
- The array is called "relations" (NOT "connections")
- Services use "parameters" (NOT "config")
- Relations are a TOP-LEVEL array (NOT inside tasks)

## Key Concepts
- **Tasks** are processing nodes (transform, route, fetch, send data)
- **Services** are shared resources (HTTP listeners, Telegram bots, DB connections)
- **Relations** link tasks: from → to with type (success/failure/all)
- **FlowFile** is the data unit flowing between tasks (content bytes + attributes dict)

## CRITICAL: Routing & Fan-Out Rules

When a task has MULTIPLE outgoing relations of the same type (e.g. two "success"
relations), EVERY output FlowFile is CLONED to ALL matching connections.

Example: if task A produces 1 FlowFile and has 2 success relations (A→B, A→C),
then B receives 1 FlowFile AND C receives 1 FlowFile (a clone).

### duplicateContent: WRONG way to fan out
DO NOT use `duplicateContent` to split a FlowFile to 2 branches. It produces
N copies as output FlowFiles, and EACH copy is cloned to ALL outgoing relations.

BAD (2 copies × 2 relations = 4 FlowFiles total, 2 per branch):
```
fetchData → duplicateContent(copies=2) → [branchA, branchB]
```

GOOD (1 FlowFile cloned to 2 relations = 1 per branch):
```
fetchData → branchA (success)
fetchData → branchB (success)
```

`duplicateContent` is only useful when you need multiple copies going to the
SAME downstream task (e.g. load testing, batch generation).

### mergeContent: Timing Matters!
`mergeContent` buffers FlowFiles and flushes when `min_entries` is reached.
It merges the FIRST N FlowFiles that arrive, regardless of which branch
they came from. If 2 FlowFiles from the same branch arrive before the other
branch, the merge will contain 2 copies of the same data.

Parameters: `separator` (string, default "\\n"), `min_entries` (int, default 2).
NOTE: the parameter is called "separator", NOT "delimiter".

CORRECT fan-out + merge pattern:
```json
{
  "tasks": {
    "source": { "type": "..." },
    "branchA": { "type": "..." },
    "branchB": { "type": "..." },
    "merge": { "type": "mergeContent", "parameters": { "separator": "\\n---\\n", "min_entries": 2 } },
    "final": { "type": "..." }
  },
  "relations": [
    {"from": "source", "to": "branchA", "type": "success"},
    {"from": "source", "to": "branchB", "type": "success"},
    {"from": "branchA", "to": "merge", "type": "success"},
    {"from": "branchB", "to": "merge", "type": "success"},
    {"from": "merge", "to": "final", "type": "success"}
  ]
}
```
Here `source` output is cloned to both branches (1 FlowFile each).
Each branch processes independently, then merge collects 1 from each.

## Common Patterns

### HTTP API endpoint
tasks: httpReceiver → processData → handleHTTPResponse
services: httpListener (shared port)

### Telegram bot
tasks: telegramReceiver → agentLoop → telegramSend
services: telegramBot

### Deploying existing templates
Use manage_flow with action 'catalog' to see available templates, then 'deploy'
with template_id to create an instance. Override parameters as needed.

### Scheduled pipeline
Use cronTrigger as root task (see pawflow_help topic 'triggers' for details).

### Data transformation
tasks: fetchData → updateAttribute → transformJSON → routeOnAttribute → output

## Agent Tools as Flow Tasks

Every agent tool is also available as a flow task with the prefix `tool.`.
Use these when you need tool functionality in a flow (not in agent context).

Available tool tasks (use `pawflow_help topic='tasks'` for full list):
- `tool.generate_image` — Generate an image via the configured image service
- `tool.generate_video` — Generate a video
- `tool.notify_user` — Send a notification to a user/conversation
- `tool.create_file` — Create a file in the FileStore
- `tool.remember` / `tool.recall` — Memory store/retrieve
- `tool.scrape_url` — Scrape a web page
- `tool.web_search` — Web search
- `tool.execute_script` — Run a sandboxed Python script
- `tool.spawn_agents` — Spawn sub-agents
- `tool.assign_task` — Assign a task to an agent
- `tool.manage_flow` — Create/deploy/manage flows

Tool task parameters match the tool's parameter schema.
Arguments are read from: task config → FlowFile attributes → FlowFile content (JSON).
Output: tool result as FlowFile content, with `tool.name` and `tool.status` attributes.

Example: generate an image from an upstream prompt
```json
{
  "tasks": {
    "prompt": { "type": "inferLLM", "parameters": { "system_prompt": "Generate a Ponyverse image prompt" } },
    "gen": { "type": "tool.generate_image", "parameters": { "negative_prompt": "blurry, deformed" } }
  },
  "relations": [
    { "from": "prompt", "to": "gen", "type": "success" }
  ]
}
```
The prompt task output flows as FlowFile content → tool.generate_image reads `prompt` from it.

## Task Configuration
- Parameters go in the `parameters` key inside the task definition
- Tasks read config via `self.config.get("key")`
- Use expressions like `${attribute_name}` in parameter values
- Use `${secrets.global.key}` for global secrets or `${secrets.key}` for per-user secrets
- Use `${global.key}` for global parameters
- Use `${env.VAR_NAME}` for environment variables
- Use `${flow.parameters.key}` for flow-level parameters (overridable at start)

## IMPORTANT: Before using any task, ALWAYS call pawflow_help with topic 'task:<type>'
to get the EXACT parameter names. DO NOT guess parameter names.

Common mistakes to avoid:
- sendEmail: params are 'to'/'from' (NOT 'to_email'/'from_email'), 'oauth2_client_id' (NOT 'oauth_client_id')
- mergeContent: param is 'separator' (NOT 'delimiter'), no 'strategy' param
- inferLLM: can use 'service' param to reference an llmConnection service instead of inline api_key

## Service References
Tasks can reference a service defined in the flow's `services` section:
```json
{
  "services": {
    "my_llm": {
      "type": "llmConnection",
      "parameters": { "provider": "openai", "api_key": "${secrets.global.openai_key}", "model": "gpt-4o" }
    }
  },
  "tasks": {
    "infer": {
      "type": "inferLLM",
      "parameters": { "service": "my_llm", "system_prompt": "You are helpful." }
    }
  }
}
```
The service parameters are merged into the task config (service = defaults, task = overrides).

## Connection Types
- `success`: Only on successful execution
- `failure`: Only on error
- `all`: Always (default if omitted)

## executeScript
Variables available in scripts:
- `content` (str): FlowFile content decoded as UTF-8
- `attributes` (dict): FlowFile attributes
- `flowfile` / `flow_file`: the FlowFile object
- Set `result` variable to replace FlowFile content (auto-encoded to bytes)
- Or modify `flow_file.content` directly (must be bytes)
- `get_secret('key_name')` — Retrieve a decrypted secret by name (user-scoped)
- `get_variable('key_name')` — Retrieve a plaintext variable by name (user-scoped)
- Standard safe modules: `import json`, `import re`, `import datetime`, `import math`, `import requests`, etc.

## Tips
- Use `updateAttribute` to set/transform FlowFile attributes
- Use `routeOnAttribute` to branch flows conditionally
- Use expressions `${...}` for dynamic values
- Service IDs in task config must match the services section keys
- Each task must have a unique ID within the flow
- To fan out to 2+ branches: add multiple relations from the SAME task (auto-clone)
- Do NOT use duplicateContent to fan out — it multiplies FlowFiles × relations"""

    def _expressions_guide(self) -> str:
        return """# Expression Syntax

PawFlow expressions use `${...}` syntax and are resolved at parse/runtime.

## Global Secrets (shared across all flows)
- `${secrets.global.key_name}` — Encrypted global secret (config/global_secrets.json)
- Managed via Runtime UI (🔑 button next to Global in treeview)

## User Secrets (per-user, encrypted at rest)
- `${secrets.user.key_name}` — Encrypted user secret (config/users/{username}/secrets.json)
- Store via: `/add-secret name value` in chat or `store_secret` tool
- Managed via Runtime UI (🔑 button next to user group in treeview)
- Use `list_secrets` tool or `/list-secrets` in chat to see available keys

## Global Parameters (shared across all flows)
- `${global.key_name}` — Global parameter (config/global_parameters.json)
- Managed via Runtime UI (⚙️ button next to Global in treeview)

## User Parameters (per-user)
- `${user.key_name}` — User parameter (config/users/{username}/parameters.json)
- Store via: `/add-variable name value` in chat
- Managed via Runtime UI (⚙️ button next to user group in treeview)
- Use `/list-variables` in chat to see available keys

## Attribute References
- `${attribute_name}` — FlowFile attribute value
- `${telegram.chat_id}` — Dotted attribute names work

## Flow Parameters
- `${flow.parameters.key}` — From the flow's parameter context

## Environment Variables
- `${env.VAR_NAME}` — System environment variable

## Special Variables
- `${now}` — Current ISO timestamp
- `${uuid}` — Random UUID

## Usage
Expressions can be used in most task parameter values:
```json
{
  "url": "${api_base_url}/endpoint",
  "chat_id": "${telegram.chat_id}"
}
```"""

    def _triggers_guide(self) -> str:
        return """# Triggers & Scheduling

## cronTrigger Task (PREFERRED for scheduled flows)
A persistent source task that emits a FlowFile on a CRON schedule.
Use this as the ROOT TASK of any flow that needs to run on a schedule.

```json
{
  "cron": {
    "type": "cronTrigger",
    "parameters": {
      "schedule": "0 7 * * *"
    }
  }
}
```

Then connect it to the first processing task:
```json
{"from": "cron", "to": "first_task", "type": "success"}
```

CRON format: `minute hour day_of_month month day_of_week`
Examples:
- `0 7 * * *` — Every day at 7:00 AM
- `*/5 * * * *` — Every 5 minutes
- `0 0 * * 1` — Every Monday at midnight

The cronTrigger is a persistent source (like httpReceiver), so the
ContinuousFlowExecutor stays alive and fires the flow at each CRON tick.

Output attributes: cron.schedule, cron.fired_at (ISO timestamp).

## IMPORTANT: cronTrigger vs generateFlowFile
- `cronTrigger`: persistent source, keeps flow alive, fires on schedule
- `generateFlowFile`: fires ONCE then flow auto-stops (use for one-shot batch flows only)

For scheduled flows, ALWAYS use cronTrigger as the root task.
Do NOT use generateFlowFile + external CRON — use cronTrigger instead.

## Self-Triggering Tasks (persistent sources)
Tasks with `is_persistent_source = True` and `has_pending_input()`:
- `cronTrigger`: Fires on CRON schedule
- `httpReceiver`: Triggered by incoming HTTP requests
- `telegramReceiver`: Triggered by incoming Telegram messages

## PollScheduler (persistent)
For agent-initiated scheduled checks:
- Use `schedule_recheck` tool to schedule a future wake-up
- Persists across restarts (JSON file)
- Supports absolute time or relative delay"""

    def _resources_guide(self) -> str:
        return """# Resource Management

PawFlow supports user-scoped resources: agents, skills, and MCP servers.
Both users (via chat commands) and agents (via tools) can manage them.

## Resource Types

### Agents
Sub-agents with their own system prompts and tool access.
- Create: `manage_resource(action="create", resource_type="agent", name="analyst", data={"prompt": "You are...", "model": "gpt-4", "tools": ["execute_script"]})`
- Fields: prompt (required), model, tools (list), max_depth, timeout, description

### Skills
Single-shot LLM transformations (no tools, no loop).
- Create: `manage_resource(action="create", resource_type="skill", name="summarizer", data={"prompt": "Summarize concisely"})`
- Fields: prompt (required), description

### MCP Servers
Model Context Protocol server connections.
- Create: `manage_resource(action="create", resource_type="mcp", name="db", data={"url": "http://localhost:3000"})`
- Fields: url (required), auth

## Using Resources

### manage_resource tool
CRUD operations: create, update, delete, list, get, activate, deactivate
Activation scopes a resource to the current conversation.

### spawn_agents tool
Delegate work to sub-agents in parallel:
```
spawn_agents(tasks=[
  {"agent": "analyst", "message": "Analyze this data"},
  {"agent": "writer", "message": "Write a report on..."}
], wait=true)
```
Sub-agents run their own tool-use loops with their configured tools.
Depth limit prevents infinite recursion (default: 1 level).

### use_skill tool
Apply a skill transformation:
```
use_skill(skill="summarizer", input="Long text here...")
```
Single LLM call, no tools — fast and efficient.

### show_file tool
Display a file in the chat viewer:
```
show_file(filename="report.pdf")
```

## Chat Slash Commands
- `/agent create` / `/agent list` / `/agent select <name>` / `/agent delete <name>`
- `/add-skill <name> <prompt>` / `/skill list` / `/skill del <name>`
- `/resources` — List all resources with active status
- `/activate <type> <name>` / `/deactivate <type> <name>`
- `/share <type> <name> <conversation_id>` — Share resource to another conversation
- `/view <filename>` — Open file viewer

## Scope Model
- Resource definitions are global (per user) — stored in config/*.json
- Activation is per conversation — stored in conversation metadata
- Share = activate a resource in another conversation of the same user"""


class StoreSecretHandler(ToolHandler):
    """Securely store a credential or secret value.

    Uses the SecretsManager to encrypt the value at rest.
    Stores in user-level secrets file: config/users/{username}/secrets.json
    Referenced via ${secrets.user.key_name} in flows.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "store_secret"

    @property
    def description(self) -> str:
        return (
            "Securely store a secret (API key, token, password). "
            "The value is encrypted at rest and can be referenced in "
            "flow configs as ${secrets.user.key_name}."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Secret key name (e.g. 'google_calendar_api_key')",
                },
                "value": {
                    "type": "string",
                    "description": "Secret value to store (will be encrypted)",
                },
            },
            "required": ["key", "value"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        key = arguments.get("key", "").strip()
        value = arguments.get("value", "")
        if not key or not value:
            return "Error: key and value are required"

        user_id = self._user_id or "anonymous"

        try:
            from pathlib import Path
            from core.config_store import ConfigStore
            from core.config_value import ConfigValue

            secrets_path = Path("config/users") / user_id / "secrets.json"
            secrets = ConfigStore.load_secrets(secrets_path)
            secrets[key] = ConfigValue(value=value)
            ConfigStore.save_secrets(secrets_path, secrets)
            return f"Secret '{key}' stored securely. Reference it in flows as ${{secrets.user.{key}}}"
        except Exception as e:
            return f"Error storing secret: {e}"

    @staticmethod
    def cleanup_conversation(conversation_id: str):
        """No-op: user secrets are permanent and not conversation-scoped."""
        pass


class ListSecretsHandler(ToolHandler):
    """List available secret key names (never values) for the current user."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "list_secrets"

    @property
    def description(self) -> str:
        return (
            "List available secret names for the current user. "
            "Returns only key names (never values). Use these names "
            "in flow configs as ${secrets.user.key_name}."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        from pathlib import Path
        from core.config_store import ConfigStore

        user_id = self._user_id or "anonymous"
        secrets_path = Path("config/users") / user_id / "secrets.json"
        secrets = ConfigStore.load_secrets(secrets_path)

        if not secrets:
            return "No secrets stored yet. Use store_secret tool or /add-secret in chat."

        lines = [f"Available secrets ({len(secrets)}):"]
        for k in sorted(secrets.keys()):
            cv = secrets[k]
            suffix = f" (large: {cv.size / 1024:.0f}KB)" if cv.is_large else ""
            lines.append(f"- {k}{suffix}  →  ${{secrets.user.{k}}}")
        return "\n".join(lines)


class ManageResourceHandler(ToolHandler):
    """CRUD for user resources: agents, skills, MCP servers, prompts.

    Both users (via slash commands) and agents (via tool calls) can manage
    resources. Resources are user-scoped and persist in config/ JSON files.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""      # which agent is calling (empty = assistant/user)
        self._llm_service = ""     # active agent's llm_service (for inheritance)

    @property
    def name(self) -> str:
        return "manage_resource"

    @property
    def description(self) -> str:
        return (
            "Manage user resources (agents, skills, MCP servers, prompts). Actions:\n"
            "- create: Create a new resource\n"
            "- update: Modify an existing resource\n"
            "- delete: Delete a resource\n"
            "- list: List all resources of a type\n"
            "- get: Get details of a specific resource\n"
            "- activate: Activate a resource in the current conversation\n"
            "- deactivate: Deactivate a resource from the current conversation\n\n"
            "Resource types: agent, skill, mcp, prompt\n\n"
            "Agent fields: prompt (required), model, tools (list), "
            "max_depth, timeout, description, llm_service\n"
            "Skill fields: prompt (required), description\n"
            "MCP fields: url (required), auth (dict)\n"
            "Prompt fields: content (required), title, category, description"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete", "list",
                             "get", "activate", "deactivate"],
                    "description": "Action to perform",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["agent", "skill", "mcp", "prompt", "task_def"],
                    "description": "Type of resource",
                },
                "name": {
                    "type": "string",
                    "description": "Resource name (required for create/update/delete/get/activate/deactivate)",
                },
                "data": {
                    "type": "object",
                    "description": "Resource data (for create/update). Must include required fields.",
                },
            },
            "required": ["action", "resource_type"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name or ""

    def set_llm_service(self, svc: str):
        self._llm_service = svc or ""

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.resource_store import ResourceStore, GLOBAL_USER_ID
        from core.conversation_store import ConversationStore

        action = arguments.get("action", "")
        rtype = arguments.get("resource_type", "")
        name = arguments.get("name", "")
        data = arguments.get("data", {})
        user_id = self._user_id or "anonymous"
        store = ResourceStore.instance()

        try:
            if action == "create":
                if not name:
                    return "Error: 'name' is required for create"
                scope = data.pop("scope", "user") if isinstance(data, dict) else "user"
                if rtype in ("agent", "skill") and self._agent_name:
                    data["_created_by"] = self._agent_name
                if rtype == "agent" and not data.get("llm_service") and self._llm_service:
                    data["llm_service"] = self._llm_service
                if scope == "conversation" and self._conversation_id:
                    # Store in conversation extras
                    from core.conversation_store import ConversationStore
                    cs = ConversationStore.instance()
                    conv_agents = cs.get_extra(self._conversation_id, "conversation_agents") or {}
                    conv_agents[name] = data
                    cs.set_extra(self._conversation_id, "conversation_agents", conv_agents)
                else:
                    store.create(rtype, name, user_id, data)
                self._activate_resource(rtype, name)
                creator = f" (by {self._agent_name})" if self._agent_name else ""
                return f"Created {rtype} '{name}' (scope: {scope}).{creator}"

            elif action == "update":
                if not name:
                    return "Error: 'name' is required for update"
                store.update(rtype, name, user_id, data)
                return f"Updated {rtype} '{name}'."

            elif action == "delete":
                if not name:
                    return "Error: 'name' is required for delete"
                # Ownership check for agent/skill deletion
                if rtype in ("agent", "skill"):
                    existing = store.get_any(rtype, name, user_id)
                    if existing:
                        created_by = existing.get("created_by")  # None if legacy
                        if created_by is not None and created_by != (self._agent_name or ""):
                            return (f"Error: {rtype} '{name}' was created by "
                                    f"'{created_by}' — you can only delete "
                                    f"resources you created.")
                if store.delete(rtype, name, user_id):
                    return f"Deleted {rtype} '{name}'."
                return f"{rtype} '{name}' not found."

            elif action == "list":
                items = store.list_all(rtype, user_id,
                                       conversation_id=self._conversation_id)
                if not items:
                    return f"No {rtype}s found."
                scope_icons = {"global": "🌐", "user": "👤", "conversation": "💬"}
                lines = [f"Your {rtype}s ({len(items)}):"]
                for item in items:
                    desc = item.get("description", "") or item.get("prompt", "")[:60]
                    scope = scope_icons.get(item.get("_scope", ""), "")
                    creator = item.get("_created_by", "")
                    suffix = f" [by {creator}]" if creator else ""
                    lines.append(f"- {scope} {item['name']}: {desc}{suffix}")
                return "\n".join(lines)

            elif action == "get":
                if not name:
                    return "Error: 'name' is required for get"
                item = store.get_any(rtype, name, user_id,
                                     conversation_id=self._conversation_id)
                if not item:
                    return f"{rtype} '{name}' not found."
                return json.dumps(item, ensure_ascii=False, indent=2)

            elif action == "activate":
                if not name:
                    return "Error: 'name' is required for activate"
                if store.get_any(rtype, name, user_id) is None:
                    return f"{rtype} '{name}' not found."
                self._activate_resource(rtype, name)
                return f"Activated {rtype} '{name}' in this conversation."

            elif action == "deactivate":
                if not name:
                    return "Error: 'name' is required for deactivate"
                self._deactivate_resource(rtype, name)
                return f"Deactivated {rtype} '{name}' from this conversation."

            elif action == "disable":
                if not name or not self._conversation_id:
                    return "Error: 'name' and conversation required"
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                disabled = cs.get_extra(self._conversation_id, "disabled_agents") or []
                if name not in disabled:
                    disabled.append(name)
                    cs.set_extra(self._conversation_id, "disabled_agents", disabled)
                return f"Agent '{name}' disabled in this conversation."

            elif action == "enable":
                if not name or not self._conversation_id:
                    return "Error: 'name' and conversation required"
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                disabled = cs.get_extra(self._conversation_id, "disabled_agents") or []
                if name in disabled:
                    disabled.remove(name)
                    cs.set_extra(self._conversation_id, "disabled_agents", disabled)
                return f"Agent '{name}' enabled in this conversation."

            elif action == "promote":
                if not name:
                    return "Error: 'name' is required"
                target_scope = data.get("target_scope", "user")
                # Get the agent from any scope
                item = store.get_any(rtype, name, user_id,
                                     conversation_id=self._conversation_id)
                if not item:
                    return f"{rtype} '{name}' not found."
                current_scope = item.get("_scope", "user")
                # Remove scope metadata before copying
                promote_data = {k: v for k, v in item.items()
                                if not k.startswith("_") and k != "name"}
                if target_scope == "user":
                    store.create(rtype, name, user_id, promote_data)
                elif target_scope == "global":
                    return "Error: Cannot promote to global scope from chat. Use the admin GUI."
                elif target_scope == "conversation" and self._conversation_id:
                    from core.conversation_store import ConversationStore
                    cs = ConversationStore.instance()
                    conv_agents = cs.get_extra(self._conversation_id, "conversation_agents") or {}
                    conv_agents[name] = promote_data
                    cs.set_extra(self._conversation_id, "conversation_agents", conv_agents)
                else:
                    return f"Invalid target scope: {target_scope}"
                return f"{rtype} '{name}' promoted from {current_scope} to {target_scope}."

            else:
                return f"Unknown action: {action}"

        except (ValueError, KeyError) as e:
            return f"Error: {e}"

    def _activate_resource(self, rtype: str, name: str):
        """Add resource to conversation's active_resources."""
        if not self._conversation_id:
            return
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        active = cs.get_extra(self._conversation_id, "active_resources") or {}
        if rtype == "agent":
            active["agent"] = name
        else:
            key = rtype + "s"  # skills, mcps
            lst = active.get(key, [])
            if name not in lst:
                lst.append(name)
            active[key] = lst
        cs.set_extra(self._conversation_id, "active_resources", active)

    def _deactivate_resource(self, rtype: str, name: str):
        """Remove resource from conversation's active_resources."""
        if not self._conversation_id:
            return
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        active = cs.get_extra(self._conversation_id, "active_resources") or {}
        if rtype == "agent":
            if active.get("agent") == name:
                active.pop("agent", None)
        else:
            key = rtype + "s"
            lst = active.get(key, [])
            if name in lst:
                lst.remove(name)
            active[key] = lst
        cs.set_extra(self._conversation_id, "active_resources", active)


class SpawnAgentsHandler(ToolHandler):
    """Spawn one or more sub-agents to work in parallel.

    The main agent can delegate complex sub-tasks to specialized agents
    defined in the resource store. Results are aggregated and returned.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._available_agents: List[str] = []
        self._local = threading.local()  # thread-safe source agent
        self._client_resolver = None  # callable(svc_id, uid) -> (client, svc)
        self._on_event = None  # callable(event_type, data)
        self._default_client = None  # fallback LLM client

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_spawn_deps(self, client, client_resolver, on_event, registry=None):
        """Set dependencies for spawning sub-agents."""
        self._default_client = client
        self._client_resolver = client_resolver
        self._on_event = on_event
        self._registry = registry

    def set_source_agent(self, agent_name: str, llm_service: str = "") -> None:
        self._local.source_agent = agent_name
        self._local.source_llm_service = llm_service

    def set_available_agents(self, names: List[str]):
        """Set the list of available agent names (for description injection)."""
        self._available_agents = list(names)

    @property
    def name(self) -> str:
        return "spawn_agents"

    @property
    def description(self) -> str:
        base = (
            "Send a message to one or more existing agents. "
            "Each agent runs independently with its own LLM service and tools. "
            "Use 'wait: true' (default) to get results immediately, "
            "or 'wait: false' to run in background and check later "
            "with get_agent_results."
        )
        if self._available_agents:
            base += (
                f"\n\nAvailable agents: {', '.join(self._available_agents)}. "
                f"Use these exact names in the 'agent' field."
            )
        return base

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "Exact name of an existing agent (from available agents list)",
                            },
                            "message": {
                                "type": "string",
                                "description": "The task/message to send to the agent",
                            },
                            "id": {
                                "type": "string",
                                "description": "Optional task ID for tracking",
                            },
                            "context": {
                                "type": "string",
                                "description": "Context mode: 'isolated' (default), 'last:N' (last N messages), 'summary:N' (summary of N tokens), 'full' (entire parent context)",
                            },
                        },
                        "required": ["agent", "message"],
                    },
                    "description": "List of tasks to spawn",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for all results (default true)",
                },
            },
            "required": ["tasks"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver or not self._default_client:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import resolve_agent_task, SubAgentExecutor
        import uuid

        tasks_spec = arguments.get("tasks", [])
        wait = arguments.get("wait", True)
        user_id = self._user_id or "anonymous"

        # Thread-safe source agent (each agent loop runs in its own thread)
        _src_agent = getattr(self._local, 'source_agent', '') or ''
        _src_svc = getattr(self._local, 'source_llm_service', '') or ''

        # Resolve self-name and nicknames to detect self-calls
        _self_names = {_src_agent.lower()} if _src_agent else set()
        _src_nickname = ""
        if self._conversation_id and _src_agent:
            try:
                from core.conversation_store import ConversationStore
                _nicks = ConversationStore.instance().get_extra(
                    self._conversation_id, "agent_nicknames") or {}
                _src_nickname = _nicks.get(_src_agent, "")
                if _src_nickname:
                    _self_names.add(_src_nickname.lower())
            except Exception:
                pass

        agent_tasks = []
        for spec in tasks_spec:
            agent_name = spec.get("agent", "")
            message = spec.get("message", "")
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            try:
                task = resolve_agent_task(agent_name, message, user_id,
                                         conversation_id=self._conversation_id)
                task.id = task_id
                task.source_agent = _src_agent
                task.source_agent_nickname = _src_nickname
                task.source_llm_service = _src_svc

                # Resolve context mode
                context_mode = spec.get("context", "isolated")
                task.context_mode = context_mode
                task.parent_conversation_id = self._conversation_id

                if context_mode != "isolated" and self._conversation_id:
                    task.context_messages = self._resolve_context(
                        context_mode, self._conversation_id, user_id)

                # Prevent agent from calling itself
                if agent_name.lower() in _self_names:
                    return (f"Error: You ('{_src_agent}' via {_src_svc}) "
                            f"cannot call yourself as '{agent_name}' (via {task.llm_service}). "
                            f"Use a different agent or respond directly.")

                agent_tasks.append(task)
            except KeyError as e:
                return f"Error: {e}"

        if not agent_tasks:
            return "Error: no valid tasks to spawn."

        # Create executor on-the-fly
        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )
        results = executor.spawn(agent_tasks, wait=wait)

        if not wait:
            ids = [r.task_id for r in results]
            return json.dumps({
                "status": "spawned",
                "task_ids": ids,
                "message": f"Spawned {len(ids)} agents. Use get_agent_results to check.",
            })

        # Format results
        output = []
        for r in results:
            entry = {
                "task_id": r.task_id,
                "agent": r.agent_name,
                "status": r.status,
            }
            if r.response:
                entry["response"] = r.response
            if r.error:
                entry["error"] = r.error
            entry["tokens"] = {"in": r.tokens_in, "out": r.tokens_out}
            entry["tools_called"] = r.tools_called
            output.append(entry)

        return json.dumps(output, ensure_ascii=False, indent=2)

    def _resolve_context(self, mode: str, conversation_id: str,
                         user_id: str) -> list:
        """Resolve context messages based on mode."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if mode == "full":
            raw = store.load(conversation_id, user_id=user_id) or []
            # Filter out system messages, keep user/assistant/tool
            return [m for m in raw if m.get("role") != "system"]

        if mode.startswith("last:"):
            try:
                n = int(mode.split(":")[1])
            except (ValueError, IndexError):
                n = 10
            raw = store.load(conversation_id, user_id=user_id) or []
            non_system = [m for m in raw if m.get("role") != "system"]
            return non_system[-n:]

        if mode.startswith("summary:"):
            try:
                max_tokens = int(mode.split(":")[1])
            except (ValueError, IndexError):
                max_tokens = 2000
            raw = store.load(conversation_id, user_id=user_id) or []
            # Build a simple text summary from recent messages
            text_parts = []
            for m in raw[-50:]:  # last 50 messages for summary input
                role = m.get("role", "")
                content = m.get("content", "")
                if role in ("user", "assistant") and content:
                    text_parts.append(f"{role}: {content[:200]}")
            summary = "\n".join(text_parts)
            # Truncate to approximate token limit
            if len(summary) > max_tokens * 4:
                summary = summary[-(max_tokens * 4):]
            return [{"role": "user",
                     "content": f"[Context summary from parent conversation]"
                                f"\n{summary}"}]

        return []  # isolated


class GetAgentResultsHandler(ToolHandler):
    """Retrieve results from previously spawned background agents."""

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "get_agent_results"

    @property
    def description(self) -> str:
        return (
            "Get results from agents spawned with wait=false. "
            "Pass the task_ids returned by spawn_agents."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to check",
                },
            },
            "required": ["task_ids"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        return "Error: get_agent_results is not supported. Use spawn_agents with wait=true (default)."


class UseSkillHandler(ToolHandler):
    """Apply a skill (single-shot LLM transformation) to input text."""

    def __init__(self):
        self._user_id = ""
        self._client_resolver = None  # callable(svc_id, uid) -> (client, svc)
        self._default_client = None

    @property
    def name(self) -> str:
        return "use_skill"

    @property
    def description(self) -> str:
        return (
            "Apply a skill to transform text. A skill is a specialized prompt "
            "that processes input text in a single LLM call (no tools). "
            "Useful for summarization, translation, code review, etc. "
            "Skills must be created first via manage_resource or /add-skill."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Name of the skill to use",
                },
                "input": {
                    "type": "string",
                    "description": "Text to process with the skill",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override for this skill call",
                },
            },
            "required": ["skill", "input"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_spawn_deps(self, client, client_resolver):
        """Set dependencies for LLM calls."""
        self._default_client = client
        self._client_resolver = client_resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._default_client:
            return "Error: LLM client not configured."

        from core.resource_store import ResourceStore

        skill_name = arguments.get("skill", "")
        input_text = arguments.get("input", "")
        model = arguments.get("model", "")
        user_id = self._user_id or "anonymous"

        store = ResourceStore.instance()
        skill_def = store.get_any("skill", skill_name, user_id)
        if skill_def is None:
            return f"Error: Skill '{skill_name}' not found."

        from core.llm_client import LLMMessage
        try:
            messages = [
                LLMMessage(role="system", content=skill_def.get("prompt", "")),
                LLMMessage(role="user", content=input_text),
            ]
            response = self._default_client.complete(
                messages=messages,
                model=model or None,
                temperature=0.7,
                max_tokens=4096,
            )
            return response.content or ""
        except Exception as e:
            return f"Skill error: {e}"


class ShowFileHandler(ToolHandler):
    """Display a file in the chat UI viewer (images, PDFs, text, code)."""

    def __init__(self):
        self._base_url = "http://localhost:9090"
        self._user_id = ""

    @property
    def name(self) -> str:
        return "show_file"

    @property
    def description(self) -> str:
        return (
            "Display a file in the chat viewer panel. Supports images, "
            "PDFs, text, and code files. Works with FileStore files "
            "(from create_file, generate_image, execute_script) AND "
            "filesystem service files (pass path + service). "
            "Pass file_id, filename, or path+service."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "FileStore file ID",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename to search for in FileStore",
                },
                "path": {
                    "type": "string",
                    "description": "File path on a filesystem service (e.g. 'assets/player.png')",
                },
                "service": {
                    "type": "string",
                    "description": "Filesystem service name (e.g. 'localFS') — required when using path",
                },
            },
        }

    def set_base_url(self, url: str):
        self._base_url = url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _find_fs_service(self, service_name: str):
        """Find a filesystem service by name."""
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            return GlobalServiceRegistry.get_instance().get_live_instance(service_name)
        except Exception:
            pass
        return None

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.file_store import FileStore
        import mimetypes

        store = FileStore.instance()
        file_id = arguments.get("file_id", "")
        filename = arguments.get("filename", "")
        fs_path = arguments.get("path", "")
        fs_service = arguments.get("service", "")

        if file_id:
            # Extract file_id from URL if needed
            import re as _re_sf
            url_match = _re_sf.search(r'/files/([^/]+)/', file_id)
            if url_match:
                file_id = url_match.group(1)
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                # Try by name
                found_id = store.find_by_name(file_id, user_id=self._user_id)
                if found_id:
                    result = store.get(found_id, user_id=self._user_id)
                    file_id = found_id
            if not result:
                return f"Error: File ID '{file_id}' not found."
            fname, data, content_type = result
        elif fs_path:
            # Read from filesystem service, cache in FileStore
            svc = self._find_fs_service(fs_service) if fs_service else None
            if not svc:
                return f"Error: Filesystem service '{fs_service}' not found or not connected."
            try:
                data = svc.read_file(fs_path)
            except Exception as e:
                return f"Error reading '{fs_path}' from {fs_service}: {e}"
            fname = fs_path.rsplit("/", 1)[-1] if "/" in fs_path else fs_path
            content_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            # Store in FileStore for the viewer URL
            file_id = store.store(fname, data, content_type=content_type,
                                  user_id=self._user_id)
        elif filename:
            # Search by filename in FileStore
            found = None
            for f in store.list_files(user_id=self._user_id):
                if f["filename"] == filename:
                    found = f
                    break
            if not found:
                # Fuzzy search
                found_id = store.find_by_name(filename, user_id=self._user_id)
                if found_id:
                    found = {"file_id": found_id, "filename": filename}
            if not found:
                return (f"Error: File '{filename}' not found in FileStore. "
                        f"Use path+service to show files from a filesystem service.")
            file_id = found["file_id"]
            fname = found["filename"]
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                return f"Error: Could not load file '{filename}'."
            fname, data, content_type = result
        else:
            return "Error: Provide file_id, filename, or path+service."

        url = f"{self._base_url}/files/{file_id}/{fname}"
        size_kb = len(data) / 1024

        # Return a special marker that the chat UI will intercept
        return json.dumps({
            "__show_file__": True,
            "url": url,
            "filename": fname,
            "content_type": content_type,
            "size_kb": round(size_kb, 1),
            "file_id": file_id,
        })


class FilesystemToolHandler(ToolHandler):
    """Agent tool for filesystem operations via a filesystem service.

    Auto-detects the user's filesystem service, or uses the explicitly
    specified service name. Supports all FilesystemBackend operations
    including git.
    """

    _user_id: str = ""
    _available_services: List[Dict[str, Any]] = []  # Plan D: list of compatible services

    # Filesystem service types (checked in order for auto-detection)
    _FS_TYPES = ("filesystem", "browserFilesystem", "serverFilesystem",
                 "googleDrive", "oneDrive")

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        desc = (
            "Access files and run commands on the user's filesystem through a configured service. "
            "Actions: list_dir, read_file, read_pdf, read_notebook (.ipynb), edit_notebook (edit/insert/delete cells), "
            "write_file (use content for text OR file_id to copy a server file like generated images), "
            "edit (exact string replace), batch_edit (atomic multi-file edit), apply_patch (unified diff), "
            "delete_file, mkdir, stat, exists, search (glob), grep (regex), find_replace. "
            "Shell: exec — run any shell command (e.g. exec with command='cat file.txt' or command='ls -la'). "
            "Git: git_status, git_log, git_diff, git_commit (files, amend), git_pull, git_push, git_checkout, "
            "git_add, git_reset, git_stash, git_branch, git_merge, git_rebase, git_cherry_pick, git_tag, git_blame, "
            "git_worktree_list, git_worktree_add, git_worktree_remove. "
            "Transfer: copy_to_store (filesystem→FileStore), "
            "copy_between (any combination: filesystem↔filesystem, FileStore↔filesystem — "
            "use 'FileStore' as source_service or dest_service to read/write from the server file store), "
            "list_store (list FileStore files), delete_from_store (delete from FileStore). "
            "Project: project_init (generate .pawflow.md). "
            "Paths support fs:// URLs: fs://service_id/path. "
            "Paths are relative to the service root. "
            "Aliases: 'workspace', 'ws', 'local' always resolve to the first available filesystem. "
            "Git workflow: use git_tag to create checkpoints before major changes (e.g. 'v33-stable'). "
            "Use git_branch to try alternatives. Use git_stash to save work-in-progress. "
            "Use git_diff to review changes before committing. "
        )
        if len(self._available_services) > 1:
            svc_desc = ", ".join(
                f"'{s['id']}' ({s.get('type', '?')}, root={s.get('root', '?')})"
                for s in self._available_services
            )
            desc += f" Available services: {svc_desc}. Use 'service' parameter to choose."
        return desc

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_dir", "read_file", "read_pdf", "read_notebook",
                        "edit_notebook",
                        "write_file", "edit", "batch_edit", "apply_patch",
                        "delete_file", "mkdir", "stat", "exists",
                        "search", "grep", "find_replace", "exec",
                        "git_status", "git_log", "git_diff", "git_commit",
                        "git_pull", "git_push", "git_checkout",
                        "git_add", "git_reset", "git_stash", "git_branch",
                        "git_merge", "git_rebase", "git_cherry_pick",
                        "git_tag", "git_blame",
                        "project_init",
                        "git_worktree_list", "git_worktree_add", "git_worktree_remove",
                        "copy_to_store", "copy_between", "list_store", "delete_from_store",
                    ],
                    "description": "The filesystem operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within the service root",
                },
                "content": {
                    "type": "string",
                    "description": "File content for write_file (text). For binary files, use file_id instead.",
                },
                "file_id": {
                    "type": "string",
                    "description": "Copy a server file (from generate_image, create_file, etc.) to the filesystem path. Use this instead of content for images/binary files.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern for search, or regex for find_replace",
                },
                "regex": {
                    "type": "string",
                    "description": "Regex pattern for grep",
                },
                "replacement": {
                    "type": "string",
                    "description": "Replacement text for find_replace",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recursive search/grep (default: true)",
                },
                "service": {
                    "type": "string",
                    "description": "Service name (optional — auto-detects if omitted)",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact string to find (for edit action)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement string (for edit action)",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute ON THE USER'S MACHINE (for exec action). cwd is the filesystem root. fs:// URLs are auto-resolved to real paths. $PAWFLOW_FS_ROOT env var points to the root.",
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Max pages to extract from PDF (default: 50, for read_pdf action)",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref for diff/checkout",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message for git_commit",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of entries for git_log (default: 10)",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "Cell index for edit_notebook",
                },
                "new_source": {
                    "type": "string",
                    "description": "New cell source for edit_notebook",
                },
                "cell_type": {
                    "type": "string",
                    "description": "Cell type (code/markdown) for edit_notebook",
                },
                "operation": {
                    "type": "string",
                    "description": "Operation for edit_notebook: edit, insert, or delete",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name for git_worktree_add",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Worktree path for git_worktree_add/remove",
                },
                "create_new_branch": {
                    "type": "boolean",
                    "description": "Create a new branch for git_worktree_add (default: false)",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths for git_add, git_reset, or selective git_commit",
                },
                "commits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Commit hashes for git_cherry_pick",
                },
                "tag": {
                    "type": "string",
                    "description": "Tag name for git_tag",
                },
                "onto": {
                    "type": "string",
                    "description": "Target branch for git_rebase",
                },
                "no_ff": {
                    "type": "boolean",
                    "description": "No fast-forward for git_merge",
                },
                "amend": {
                    "type": "boolean",
                    "description": "Amend last commit for git_commit",
                },
                "mode": {
                    "type": "string",
                    "description": "Reset mode: mixed, soft, hard (for git_reset)",
                },
                "index": {
                    "type": "integer",
                    "description": "Stash index for git_stash drop",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force flag for git_branch delete",
                },
                "base": {
                    "type": "string",
                    "description": "Base ref for git_branch create",
                },
                "file": {
                    "type": "string",
                    "description": "File path for git_blame",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line for git_blame range",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line for git_blame range",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                    },
                    "description": "List of edits for batch_edit: [{path, old_string, new_string}]",
                },
                "patch": {
                    "type": "string",
                    "description": "Unified diff content for apply_patch",
                },
                "source_service": {
                    "type": "string",
                    "description": "Source for copy_between: a filesystem service name OR 'FileStore' for server files",
                },
                "source_path": {
                    "type": "string",
                    "description": "Source file path for copy_between",
                },
                "dest_service": {
                    "type": "string",
                    "description": "Destination for copy_between: a filesystem service name OR 'FileStore' for server files",
                },
                "dest_path": {
                    "type": "string",
                    "description": "Destination file path for copy_between",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_available_services(self, services: List[Dict[str, Any]]) -> None:
        """Plan D: set list of available filesystem services for multi-service selection."""
        self._available_services = services

    def _find_service(self, service_name: str = ""):
        """Find a filesystem service by name or auto-detect.

        Search order: GlobalServiceRegistry → UserServiceRegistry.
        If service_name is given, resolve that specific service.
        If empty, find the first available filesystem service.
        Fallback: if service_name not found but only one FS exists, use it.
        """
        # "workspace" alias — always resolves to the first available FS
        if service_name.lower() in ("workspace", "ws", "local"):
            return self._find_service("")  # auto-detect

        def _set_uid(svc):
            if hasattr(svc, 'set_user_id') and self._user_id:
                svc.set_user_id(self._user_id)
            return svc

        # Search GlobalServiceRegistry
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            if service_name:
                svc = greg.get_live_instance(service_name)
                if svc:
                    return _set_uid(svc)
            else:
                for sid, sdef in greg.get_all_definitions().items():
                    if not getattr(sdef, "enabled", True):
                        continue
                    if getattr(sdef, "service_type", "") in self._FS_TYPES:
                        svc = greg.get_live_instance(sid)
                        if svc:
                            return _set_uid(svc)
        except Exception:
            pass

        # Search UserServiceRegistry
        if self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                if service_name:
                    svc = ureg.get_live_instance(self._user_id, service_name)
                    if svc:
                        return _set_uid(svc)
                else:
                    for fs_type in self._FS_TYPES:
                        compatible = ureg.get_compatible(fs_type, self._user_id)
                        for sdef in compatible:
                            if sdef.enabled:
                                svc = ureg.get_live_instance(self._user_id, sdef.service_id)
                                if svc:
                                    return _set_uid(svc)
            except Exception:
                pass

        # Fallback: if a specific name was requested but not found,
        # and there's exactly one FS service available, use it
        if service_name:
            only = self._find_service("")  # auto-detect (no name)
            if only:
                return only

        return None

    def execute(self, arguments: Dict[str, Any]) -> str:
        result = self._execute_inner(arguments)
        # Append service hint if a fallback was used
        if hasattr(self, '_last_service_hint') and self._last_service_hint:
            hint = self._last_service_hint
            self._last_service_hint = ""
            return result + hint
        return result

    def _execute_inner(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        path = arguments.get("path", ".")
        service_name = arguments.get("service", "")
        self._last_service_hint = ""

        # Parse fs:// URLs: fs://service_id/path/to/file
        if path.startswith("fs://"):
            parts = path[5:].split("/", 1)
            service_name = parts[0]
            path = parts[1] if len(parts) > 1 else "."

        # Plan D: try explicit service first, then injected, then search
        svc = None
        if service_name:
            svc = self._find_service(service_name)
            # Check if fallback was used (service found under different name)
            if svc:
                actual_id = getattr(svc, 'service_id', '') or getattr(svc, '_service_id', '')
                if actual_id and actual_id != service_name:
                    self._last_service_hint = f"\n[Note: '{service_name}' not found — using '{actual_id}'. Use service='{actual_id}' in future calls.]"
        if svc is None:
            svc = getattr(self, '_fs_service', None)
        if svc is None:
            return (
                "Error: No filesystem service configured. "
                "Install one with: /service install localFilesystem <name> "
                "host=localhost,port=9876,secret=<secret>,mode=readwrite\n"
                "Then run: python tools/pawflow_relay.py --port 9876 "
                "--dir <path> --secret <secret>"
            )

        # Normalize common LLM aliases
        _action_aliases = {
            "read": "read_file", "write": "write_file", "delete": "delete_file",
            "ls": "list_dir", "cat": "read_file", "rm": "delete_file",
        }
        action = _action_aliases.get(action, action)

        try:
            if action == "list_dir":
                entries = svc.list_dir(path)
                # Determine service name for fs:// URLs
                _svc_name = service_name or getattr(svc, 'service_id', '') or 'fs'
                _base = f"fs://{_svc_name}/{path.rstrip('/')}/" if path != "." else f"fs://{_svc_name}/"
                lines = []
                for e in entries:
                    kind = "📁" if e.kind == "directory" else "📄"
                    size = f" ({e.size} bytes)" if e.kind == "file" else ""
                    lines.append(f"{kind} {_base}{e.name}{size}")
                return "\n".join(lines) if lines else "(empty directory)"

            elif action == "read_file":
                data = svc.read_file(path)
                fname = path.rsplit("/", 1)[-1] if "/" in path else path
                # Images: store in FileStore and return viewable URL
                _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
                if any(fname.lower().endswith(ext) for ext in _img_exts):
                    from core.file_store import FileStore
                    import mimetypes
                    mime = mimetypes.guess_type(fname)[0] or "image/png"
                    fid = FileStore.instance().store(fname, data, mime,
                                                       user_id=self._user_id)
                    file_base = self.config.get("file_base_url", "") or ""
                    if file_base:
                        url = f"{file_base}/files/{fid}/{fname}"
                    else:
                        url = f"/files/{fid}/{fname}"
                    # Include base64 so agent loop can send as multimodal image
                    import base64 as _b64img
                    b64 = _b64img.b64encode(data).decode("ascii")
                    return f"Image: {url}\n__image_data__:{mime}:{b64}"
                # PDF: auto-redirect to read_pdf
                if fname.lower().endswith(".pdf"):
                    max_pages = arguments.get("max_pages", 50)
                    result = svc._request("read_pdf", path, max_pages=max_pages)
                    if isinstance(result, dict) and "pages" in result:
                        lines = [f"PDF: {result.get('total_pages', '?')} pages"]
                        for p_data in result["pages"]:
                            lines.append(f"\n--- Page {p_data['page']} ---\n{p_data['text']}")
                        return "\n".join(lines)
                    return json.dumps(result)
                # Notebook: auto-redirect to read_notebook
                if fname.lower().endswith(".ipynb"):
                    result = svc._request("read_notebook", path)
                    if isinstance(result, dict) and "cells" in result:
                        lines = [f"Notebook: {result.get('total_cells', '?')} cells "
                                 f"(kernel: {result.get('kernel', '?')})"]
                        for c in result["cells"]:
                            header = f"\n### Cell {c['index']} [{c['type']}]"
                            lines.append(header)
                            if c["source"]:
                                lines.append(f"```\n{c['source']}\n```")
                            if c.get("output"):
                                lines.append(f"Output:\n```\n{c['output']}\n```")
                        return "\n".join(lines)
                    return json.dumps(result)
                # Text files
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    import base64 as _b64
                    return f"(binary file, {len(data)} bytes)"

            elif action == "read_pdf":
                max_pages = arguments.get("max_pages", 50)
                result = svc._request("read_pdf", path, max_pages=max_pages)
                if isinstance(result, dict) and "pages" in result:
                    lines = [f"PDF: {result.get('total_pages', '?')} pages "
                             f"({result.get('extracted_pages', '?')} extracted)"]
                    for p_data in result["pages"]:
                        lines.append(f"\n--- Page {p_data['page']} ---\n{p_data['text']}")
                    return "\n".join(lines)
                return json.dumps(result)

            elif action == "edit_notebook":
                cell_index = arguments.get("cell_index")
                new_source = arguments.get("new_source", "")
                cell_type = arguments.get("cell_type", "")
                operation = arguments.get("operation", "edit")
                result = svc._request("edit_notebook", path,
                                       cell_index=cell_index, new_source=new_source,
                                       cell_type=cell_type, operation=operation)
                op = result.get("operation", operation)
                idx = result.get("cell_index", cell_index)
                total = result.get("total_cells", "?")
                return f"Notebook {op}: cell {idx} ({total} cells total)"

            elif action == "write_file":
                file_id = arguments.get("file_id", "")
                if file_id:
                    # Extract file_id from URL if the LLM passed one
                    # e.g. "http://host/files/abc123/file.png" → "abc123"
                    import re as _re_fid
                    url_match = _re_fid.search(r'/files/([^/]+)/', file_id)
                    if url_match:
                        file_id = url_match.group(1)
                    # Copy from FileStore to filesystem
                    from core.file_store import FileStore
                    store = FileStore.instance()
                    # Try file_id directly, then search by filename
                    entry = store.get(file_id)
                    if not entry:
                        found_id = store.find_by_name(file_id)
                        if found_id:
                            entry = store.get(found_id)
                    if not entry:
                        return f"Error: file_id '{file_id}' not found in FileStore"
                    fname, data, _ct = entry
                    svc.write_file(path, data)
                    return f"Copied {fname} ({len(data):,} bytes) to {path}"
                # Accept "content" (schema) or common LLM mistakes: "command", "data", "text"
                content = (arguments.get("content")
                           or arguments.get("command")
                           or arguments.get("data")
                           or arguments.get("text")
                           or "")
                if not content:
                    return f"Error: write_file requires 'content' or 'file_id' parameter"
                svc.write_file(path, content.encode("utf-8"))
                return f"Written {len(content)} chars to {path}"

            elif action == "delete_file":
                svc.delete_file(path)
                return f"Deleted: {path}"

            elif action == "mkdir":
                svc.mkdir(path)
                return f"Created directory: {path}"

            elif action == "stat":
                from dataclasses import asdict
                entry = svc.stat(path)
                return json.dumps(asdict(entry), default=str, indent=2)

            elif action == "exists":
                exists = svc.exists(path)
                return f"{'Exists' if exists else 'Does not exist'}: {path}"

            elif action == "search":
                pattern = arguments.get("pattern", "*")
                recursive = arguments.get("recursive", True)
                results = svc.search(path, pattern, recursive)
                return "\n".join(results) if results else "(no matches)"

            elif action == "grep":
                regex = arguments.get("regex", "")
                recursive = arguments.get("recursive", True)
                results = svc.grep(path, regex, recursive)
                lines = [f"{r['path']}:{r['line_number']}: {r['line']}" for r in results[:50]]
                total = len(results)
                if total > 50:
                    lines.append(f"... and {total - 50} more matches")
                return "\n".join(lines) if lines else "(no matches)"

            elif action == "find_replace":
                pattern = arguments.get("pattern", "")
                replacement = arguments.get("replacement", "")
                result = svc.find_replace(path, pattern, replacement)
                return f"Replaced {result.get('replacements', 0)} occurrences in {result.get('path', path)}"

            elif action == "edit":
                old_string = arguments.get("old_string", "")
                new_string = arguments.get("new_string", "")
                replace_all = arguments.get("replace_all", False)
                result = svc.edit(path, old_string, new_string, replace_all)
                # Format diff for display
                diff = result.get("diff", [])
                if diff:
                    diff_text = f"Edited {result.get('path', path)} (line {result.get('line', '?')}), " \
                                f"{result.get('replacements', 0)} replacement(s):\n"
                    for d in diff:
                        prefix = "- " if d["type"] == "remove" else "+ " if d["type"] == "add" else "  "
                        diff_text += f"{d['line']:4d} {prefix}{d['text']}\n"
                    return diff_text
                return f"Edited {result.get('path', path)}: {result.get('replacements', 0)} replacement(s)"

            elif action == "exec":
                command = arguments.get("command", "")
                timeout = arguments.get("timeout", 30)
                result = svc.exec(path, command, timeout)
                output = result.get("stdout", "")
                if result.get("stderr"):
                    output += "\nSTDERR:\n" + result["stderr"]
                if result.get("returncode", 0) != 0:
                    output += f"\n(exit code: {result['returncode']})"
                return output or "(no output)"

            # Git operations
            elif action == "git_status":
                result = svc.git_status(path)
                return json.dumps(result, indent=2)

            elif action == "git_log":
                count = arguments.get("count", 10)
                result = svc.git_log(path, count)
                lines = [f"{e['hash'][:8]} {e['date']} {e['message']}" for e in result]
                return "\n".join(lines) if lines else "(no commits)"

            elif action == "git_diff":
                ref = arguments.get("ref", "")
                return svc.git_diff(path, ref) or "(no changes)"

            elif action == "git_commit":
                message = arguments.get("message", "")
                files = arguments.get("files", [])
                amend = arguments.get("amend", False)
                result = svc.git_commit(path, message, files=files, amend=amend)
                return f"Committed: {result.get('hash', '')[:8]} — {result.get('message', '')}"

            elif action == "git_pull":
                result = svc.git_pull(path)
                return json.dumps(result, indent=2)

            elif action == "git_push":
                result = svc.git_push(path)
                return json.dumps(result, indent=2)

            elif action == "git_checkout":
                ref = arguments.get("ref", "")
                result = svc.git_checkout(path, ref)
                return f"Checked out: {result.get('branch', ref)}"

            elif action == "git_worktree_list":
                result = svc.git_worktree_list(path)
                if not result:
                    return "(no worktrees)"
                lines = []
                for wt in result:
                    branch = wt.get("branch", "detached")
                    lines.append(f"{wt['path']} [{branch}] HEAD={wt.get('head', '?')[:8]}")
                return "\n".join(lines)

            elif action == "git_worktree_add":
                branch = arguments.get("branch", "")
                worktree_path = arguments.get("worktree_path", "")
                create_new = arguments.get("create_new_branch", False)
                result = svc.git_worktree_add(path, branch, worktree_path, create_new)
                return f"Worktree created: {result.get('worktree_path', '')} (branch: {result.get('branch', '')})"

            elif action == "git_worktree_remove":
                worktree_path = arguments.get("worktree_path", "")
                result = svc.git_worktree_remove(path, worktree_path)
                return f"Worktree removed: {result.get('removed', '')}"

            elif action == "git_add":
                files = arguments.get("files", [])
                result = svc._request("git_add", path, files=files)
                return f"Staged: {', '.join(result.get('staged', []))}"

            elif action == "git_reset":
                files = arguments.get("files", [])
                ref = arguments.get("ref", "")
                mode = arguments.get("mode", "mixed")
                result = svc._request("git_reset", path, files=files, ref=ref, mode=mode)
                return result.get("output", "Reset done")

            elif action == "git_stash":
                operation = arguments.get("operation", "push")
                message = arguments.get("message", "")
                index = arguments.get("index", 0)
                result = svc._request("git_stash", path, operation=operation, message=message, index=index)
                output = result.get("output", "") if isinstance(result, dict) else str(result)
                if operation == "list":
                    return output or "(no stashes)"
                return output or f"Stash {operation} done"

            elif action == "git_branch":
                operation = arguments.get("operation", "list")
                branch = arguments.get("branch", "")
                base = arguments.get("base", "")
                force = arguments.get("force", False)
                if operation == "list":
                    result = svc._request("git_branch", path, operation=operation)
                    if isinstance(result, list):
                        lines = [f"{b['name']} {b.get('hash','')} {b.get('upstream','')}" for b in result]
                        return "\n".join(lines) if lines else "(no branches)"
                    return str(result)
                result = svc._request("git_branch", path, operation=operation, branch=branch, base=base, force=force)
                return result.get("output", f"Branch {operation} done")

            elif action == "git_merge":
                branch = arguments.get("branch", "")
                no_ff = arguments.get("no_ff", False)
                result = svc._request("git_merge", path, branch=branch, no_ff=no_ff)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", "Merge done")

            elif action == "git_rebase":
                onto = arguments.get("onto", "")
                operation = arguments.get("operation", "start")
                result = svc._request("git_rebase", path, onto=onto, operation=operation)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", f"Rebase {operation} done")

            elif action == "git_cherry_pick":
                commits = arguments.get("commits", [])
                result = svc._request("git_cherry_pick", path, commits=commits)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", "Cherry-pick done")

            elif action == "git_tag":
                operation = arguments.get("operation", "list")
                tag = arguments.get("tag", "")
                message = arguments.get("message", "")
                if operation == "list":
                    result = svc._request("git_tag", path, operation=operation)
                    if isinstance(result, list):
                        lines = [f"{t['name']} {t.get('hash','')}" for t in result]
                        return "\n".join(lines) if lines else "(no tags)"
                    return str(result)
                result = svc._request("git_tag", path, operation=operation, tag=tag, message=message)
                return result.get("output", f"Tag {operation} done")

            elif action == "git_blame":
                file = arguments.get("file", "") or path
                start_line = arguments.get("start_line", 0)
                end_line = arguments.get("end_line", 0)
                result = svc._request("git_blame", path, file=file, start_line=start_line, end_line=end_line)
                if isinstance(result, list):
                    lines = [f"{e.get('hash','?')} {e.get('author','?'):20s} L{e.get('line','?')}: {e.get('content','')}" for e in result[:50]]
                    total = len(result)
                    if total > 50:
                        lines.append(f"... and {total - 50} more lines")
                    return "\n".join(lines) if lines else "(no blame data)"
                return str(result)

            elif action == "project_init":
                force = arguments.get("force", False)
                result = svc._request("project_init", path, force=force)
                return f"Generated {result.get('path', '.pawflow.md')} ({result.get('size', 0)} bytes)"

            elif action == "batch_edit":
                edits = arguments.get("edits", [])
                result = svc._request("batch_edit", ".", edits=edits)
                n = result.get("edits_applied", 0)
                files = result.get("files_modified", [])
                return f"Batch edit: {n} edits applied across {len(files)} file(s): {', '.join(files)}"

            elif action == "apply_patch":
                patch = arguments.get("patch", "")
                result = svc._request("apply_patch", path, patch=patch)
                method = result.get("method", "?")
                if method == "git_apply":
                    return f"Patch applied (git): {result.get('stats', 'ok')}"
                files = result.get("files_modified", [])
                hunks = result.get("hunks_applied", 0)
                return f"Patch applied (manual): {hunks} hunks across {len(files)} file(s): {', '.join(files)}"

            elif action == "copy_to_store":
                data = svc.read_file(path)
                fname = path.rsplit("/", 1)[-1] if "/" in path else path
                import mimetypes as _mt_copy
                mime = _mt_copy.guess_type(fname)[0] or "application/octet-stream"
                from core.file_store import FileStore
                fid = FileStore.instance().store(fname, data, mime, user_id=self._user_id)
                return f"Stored '{fname}' ({len(data):,} bytes) in FileStore\nFile ID: {fid}\nURL: /files/{fid}/{fname}"

            elif action == "copy_between":
                source_service = arguments.get("source_service", "")
                source_path = arguments.get("source_path", "") or path
                dest_service = arguments.get("dest_service", "")
                dest_path = arguments.get("dest_path", "")
                if not source_service or not dest_service:
                    return "Error: copy_between requires 'source_service' and 'dest_service'"
                if not dest_path:
                    return "Error: copy_between requires 'dest_path'"
                from core.file_store import FileStore
                import mimetypes as _mt_cb
                _store = FileStore.instance()
                # Read from source (FileStore or filesystem service)
                _fs_aliases = ("filestore", "store", "server")
                if source_service.lower() in _fs_aliases:
                    # Source is FileStore — resolve file_id or filename
                    entry = _store.get(source_path, user_id=self._user_id)
                    if not entry:
                        fid = _store.find_by_name(source_path, user_id=self._user_id)
                        if fid:
                            entry = _store.get(fid, user_id=self._user_id)
                    if not entry:
                        return f"Error: file '{source_path}' not found in FileStore"
                    fname, data, _ct = entry
                else:
                    src_svc = self._find_service(source_service)
                    if not src_svc:
                        return f"Error: source service '{source_service}' not found"
                    data = src_svc.read_file(source_path)
                    fname = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
                # Write to dest (FileStore or filesystem service)
                if dest_service.lower() in _fs_aliases:
                    mime = _mt_cb.guess_type(fname)[0] or "application/octet-stream"
                    fid = _store.store(fname, data, mime, user_id=self._user_id)
                    return f"Copied '{fname}' ({len(data):,} bytes) from {source_service} to FileStore\nFile ID: {fid}\nURL: /files/{fid}/{fname}"
                else:
                    dst_svc = self._find_service(dest_service)
                    if not dst_svc:
                        return f"Error: destination service '{dest_service}' not found"
                    dst_svc.write_file(dest_path, data)
                    return f"Copied '{fname}' ({len(data):,} bytes) from {source_service}:{source_path} to {dest_service}:{dest_path}"

            elif action == "list_store":
                from core.file_store import FileStore
                store = FileStore.instance()
                files = store.list_files(user_id=self._user_id)
                if not files:
                    return "(no files in store)"
                lines = []
                for f in files:
                    fid = f.get("file_id", "?")
                    fname = f.get("filename", "?")
                    size = f.get("size", 0)
                    lines.append(f"{fid}  {fname}  ({size:,} bytes)")
                return "\n".join(lines)

            elif action == "delete_from_store":
                file_id = arguments.get("file_id", "") or path
                if not file_id:
                    return "Error: delete_from_store requires 'file_id' parameter"
                # Extract file_id from URL if needed
                import re as _re_del
                url_match = _re_del.search(r'/files/([^/]+)/', file_id)
                if url_match:
                    file_id = url_match.group(1)
                from core.file_store import FileStore
                deleted = FileStore.instance().delete(file_id, user_id=self._user_id)
                if deleted:
                    return f"Deleted file {file_id} from store"
                return f"Error: file {file_id} not found or access denied"

            else:
                return f"Unknown action: {action}"

        except PermissionError as e:
            return f"Permission denied: {e}"
        except FileNotFoundError as e:
            return f"Not found: {e}"
        except Exception as e:
            return f"Error: {e}"

    def set_fs_service(self, service):
        """Inject the filesystem service (called by agent_loop)."""
        self._fs_service = service


def _detect_related_tests(modified_file: str) -> list:
    """Given a modified file path, return likely related test file paths."""
    from pathlib import Path as _Path
    p = _Path(modified_file)
    if p.name.startswith("test_"):
        return []  # Already a test file
    stem = p.stem
    candidates = [
        f"test_{stem}.py",
        f"tests/test_{stem}.py",
        f"test/{stem}_test.py",
        f"{p.parent}/test_{stem}.py",
    ]
    return candidates


class RunTestsHandler(ToolHandler):
    """Run pytest on specified test files via filesystem service exec."""

    _user_id: str = ""

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run pytest on test files. Returns pass/fail summary with first failure details. "
            "Parameters: test_files (list), test_pattern (string, e.g. 'test_foo'), timeout (int, default 60)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "test_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of test file paths to run",
                },
                "test_pattern": {
                    "type": "string",
                    "description": "Pattern to match test functions (e.g. 'test_foo')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                },
                "service": {
                    "type": "string",
                    "description": "Filesystem service name (optional)",
                },
            },
            "required": ["test_files"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        test_files = arguments.get("test_files", [])
        test_pattern = arguments.get("test_pattern", "")
        timeout = arguments.get("timeout", 60)
        service_name = arguments.get("service", "")

        if not test_files:
            return "Error: no test files specified"

        # Find filesystem service (reuse FilesystemToolHandler's logic)
        fs_handler = FilesystemToolHandler()
        fs_handler._user_id = self._user_id
        svc = fs_handler._find_service(service_name)
        if not svc:
            svc = getattr(fs_handler, '_fs_service', None)
        if not svc:
            return "Error: no filesystem service available to run tests"

        # Build pytest command
        files_str = " ".join(f'"{f}"' for f in test_files)
        cmd = f"python -m pytest {files_str} -x -q --tb=short --no-header"
        if test_pattern:
            cmd += f" -k \"{test_pattern}\""

        try:
            result = svc.exec(".", cmd, timeout)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", -1)
            output = stdout
            if stderr:
                output += "\n" + stderr
            # Truncate to 3000 chars
            if len(output) > 3000:
                output = output[:3000] + "\n... (truncated)"
            status = "PASSED" if rc == 0 else "FAILED"
            return f"Tests {status} (exit code {rc}):\n{output}"
        except Exception as e:
            return f"Error running tests: {e}"


class ReadParentContextHandler(ToolHandler):
    """Read messages from the parent conversation (for sub-agents)."""

    _parent_conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "read_parent_context"

    @property
    def description(self) -> str:
        return (
            "Read recent messages from the parent conversation that spawned "
            "this agent. Use when you need more context about the overall "
            "discussion."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent messages to read (default 20)",
                },
            },
        }

    def set_parent_conversation_id(self, cid: str):
        self._parent_conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._parent_conversation_id:
            return ("No parent conversation available (this agent was not "
                    "spawned from a conversation).")

        last_n = arguments.get("last_n", 20)
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            raw = store.load(self._parent_conversation_id,
                             user_id=self._user_id) or []
            non_system = [m for m in raw if m.get("role") != "system"]
            recent = non_system[-last_n:]
            lines = []
            for m in recent:
                role = m.get("role", "?")
                content = m.get("content", "")[:300]
                lines.append(f"[{role}] {content}")
            return "\n\n".join(lines) if lines else (
                "(no messages in parent conversation)")
        except Exception as e:
            return f"Error reading parent context: {e}"


class GitHubHandler(ToolHandler):
    """GitHub operations via the `gh` CLI tool."""

    _user_id: str = ""

    @property
    def name(self) -> str:
        return "github"

    @property
    def description(self) -> str:
        return (
            "Interact with GitHub via the `gh` CLI. "
            "Actions: pr_create, pr_list, pr_view, pr_merge, "
            "issue_create, issue_list, issue_view, issue_close, "
            "run_list (CI checks), repo_view, search_code, search_issues. "
            "Requires `gh` CLI installed and authenticated on the relay filesystem."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "pr_create", "pr_list", "pr_view", "pr_merge",
                        "issue_create", "issue_list", "issue_view", "issue_close",
                        "run_list", "repo_view", "search_code", "search_issues",
                    ],
                },
                "title": {"type": "string", "description": "Title for PR or issue"},
                "body": {"type": "string", "description": "Body/description"},
                "number": {"type": "integer", "description": "PR or issue number"},
                "base": {"type": "string", "description": "Base branch for PR (default: main)"},
                "labels": {"type": "string", "description": "Comma-separated labels"},
                "query": {"type": "string", "description": "Search query"},
                "service": {"type": "string", "description": "Filesystem service to run gh on"},
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        service_name = arguments.get("service", "")

        # Find filesystem service to execute gh commands on
        fsh = FilesystemToolHandler()
        fsh._user_id = self._user_id
        svc = fsh._find_service(service_name)
        if not svc:
            return "Error: no filesystem service available to run gh CLI"

        def _gh(args: str, timeout: int = 30) -> str:
            try:
                result = svc.exec(".", f"gh {args}", timeout)
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                rc = result.get("returncode", -1)
                if rc != 0:
                    return f"Error (exit {rc}): {stderr or stdout}"
                return stdout.strip()
            except Exception as e:
                return f"Error: {e}"

        try:
            if action == "pr_create":
                title = arguments.get("title", "")
                body = arguments.get("body", "")
                base = arguments.get("base", "main")
                labels = arguments.get("labels", "")
                cmd = f'pr create --title "{title}" --body "{body}" --base {base}'
                if labels:
                    cmd += f' --label "{labels}"'
                return _gh(cmd, 60)

            elif action == "pr_list":
                return _gh("pr list --limit 20")

            elif action == "pr_view":
                number = arguments.get("number", "")
                return _gh(f"pr view {number}")

            elif action == "pr_merge":
                number = arguments.get("number", "")
                return _gh(f"pr merge {number} --merge", 60)

            elif action == "issue_create":
                title = arguments.get("title", "")
                body = arguments.get("body", "")
                labels = arguments.get("labels", "")
                cmd = f'issue create --title "{title}" --body "{body}"'
                if labels:
                    cmd += f' --label "{labels}"'
                return _gh(cmd, 30)

            elif action == "issue_list":
                return _gh("issue list --limit 20")

            elif action == "issue_view":
                number = arguments.get("number", "")
                return _gh(f"issue view {number}")

            elif action == "issue_close":
                number = arguments.get("number", "")
                return _gh(f"issue close {number}")

            elif action == "run_list":
                return _gh("run list --limit 10")

            elif action == "repo_view":
                return _gh("repo view")

            elif action == "search_code":
                query = arguments.get("query", "")
                return _gh(f'search code "{query}" --limit 20')

            elif action == "search_issues":
                query = arguments.get("query", "")
                return _gh(f'search issues "{query}" --limit 20')

            else:
                return f"Unknown github action: {action}"
        except Exception as e:
            return f"Error: {e}"


class SecurityScanHandler(ToolHandler):
    """Run security scans on code via bandit or semgrep."""

    _user_id: str = ""

    @property
    def name(self) -> str:
        return "security_scan"

    @property
    def description(self) -> str:
        return (
            "Run a security scan on Python code files. "
            "Uses bandit (Python-specific) or semgrep (multi-language) via the filesystem exec. "
            "Returns findings with severity, file, line, and description."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to scan"},
                "tool": {"type": "string", "description": "'bandit' (default) or 'semgrep'"},
                "service": {"type": "string", "description": "Filesystem service"},
            },
            "required": ["path"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        path = arguments.get("path", ".")
        tool = arguments.get("tool", "bandit")
        service_name = arguments.get("service", "")

        fsh = FilesystemToolHandler()
        fsh._user_id = self._user_id
        svc = fsh._find_service(service_name)
        if not svc:
            return "Error: no filesystem service available"

        try:
            if tool == "semgrep":
                result = svc.exec(".", f"semgrep scan --json {path}", 120)
            else:
                result = svc.exec(".", f"python -m bandit -r -f json {path}", 60)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", -1)
            if not stdout and stderr:
                return f"Scan error: {stderr[:500]}"
            # Parse JSON output for summary
            try:
                import json
                data = json.loads(stdout)
                if tool == "bandit":
                    results = data.get("results", [])
                    if not results:
                        return "No security issues found."
                    lines = [f"Found {len(results)} issue(s):"]
                    for r in results[:20]:
                        sev = r.get("issue_severity", "?")
                        fname = r.get("filename", "?")
                        line = r.get("line_number", "?")
                        text = r.get("issue_text", "?")
                        lines.append(f"  [{sev}] {fname}:{line} — {text}")
                    return "\n".join(lines)
                return stdout[:2000]
            except Exception:
                return stdout[:2000] if stdout else f"Exit {rc}: {stderr[:500]}"
        except Exception as e:
            return f"Error running {tool}: {e}"


def create_default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all builtin handlers registered."""
    registry = ToolRegistry()
    registry.register(ExecuteScriptHandler())
    registry.register(WebSearchHandler())
    registry.register(WebFetchHandler())
    registry.register(ScraplingFetchHandler())
    registry.register(ReadFileHandler())
    registry.register(CreateFileHandler())
    registry.register(ScheduleContinuationHandler())
    registry.register(ScheduleRecheckHandler())
    registry.register(LocalFilesHandler())
    registry.register(RemoteExecutorHandler())
    registry.register(ImageGenerationHandler())
    registry.register(VideoGenerationHandler())
    registry.register(RememberHandler())
    registry.register(RecallHandler())
    registry.register(SemanticRecallHandler())
    registry.register(AssignTaskHandler())
    registry.register(CompleteTaskHandler())
    registry.register(VerifyTaskHandler())
    registry.register(ForgetHandler())
    registry.register(CreatePlanHandler())
    registry.register(UpdatePlanHandler())
    registry.register(NotifyUserHandler())
    registry.register(AskUserHandler())
    registry.register(CreateToolHandler())
    registry.register(AskAgentHandler())
    registry.register(FlowManagerHandler())
    registry.register(PawFlowHelpHandler())
    registry.register(StoreSecretHandler())
    registry.register(ListSecretsHandler())
    registry.register(ManageResourceHandler())
    registry.register(SpawnAgentsHandler())
    registry.register(GetAgentResultsHandler())
    registry.register(UseSkillHandler())
    registry.register(ShowFileHandler())
    registry.register(ReadParentContextHandler())

    # Browser automation (conditional — requires playwright)
    try:
        from services.browser_service import BrowserService  # noqa: F401
        registry.register(BrowserActionHandler())
    except ImportError:
        pass

    # Identity linking
    registry.register(LinkIdentityHandler())

    # Filesystem
    registry.register(FilesystemToolHandler())

    # Test runner
    registry.register(RunTestsHandler())

    # GitHub CLI
    registry.register(GitHubHandler())

    # Security scanning
    registry.register(SecurityScanHandler())

    return registry


class BrowserActionHandler(ToolHandler):
    """Interactive browser control via Playwright."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Interactive browser. Actions: navigate (go to URL), click (click element), "
            "fill (fill input field), extract (get text content), screenshot (capture page — "
            "useful for visual debugging and verifying UI changes), "
            "scroll (scroll up/down), wait (wait for element), close (close browser). "
            "Tips: use screenshot to verify web pages visually; use extract with 'body' selector "
            "to get full page text; combine with filesystem(action=exec) to run local dev servers "
            "or build scripts before navigating."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "click", "fill", "extract", "screenshot",
                             "scroll", "wait", "close"],
                    "description": "Browser action to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for navigate action)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector (for click/fill/extract/wait)",
                },
                "value": {
                    "type": "string",
                    "description": "Value to fill (for fill action)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down)",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout in ms for wait action (default: 5000)",
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not action:
            return "Error: action is required"

        conv_id = self._conversation_id or "default"

        try:
            from services.browser_service import BrowserService
            svc = BrowserService.instance()

            if action == "navigate":
                url = arguments.get("url", "")
                if not url:
                    return "Error: url is required for navigate"
                return svc.navigate(conv_id, url)

            elif action == "click":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for click"
                return svc.click(conv_id, selector)

            elif action == "fill":
                selector = arguments.get("selector", "")
                value = arguments.get("value", "")
                if not selector:
                    return "Error: selector is required for fill"
                return svc.fill(conv_id, selector, value)

            elif action == "extract":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for extract"
                return svc.extract(conv_id, selector)

            elif action == "screenshot":
                return svc.screenshot(conv_id)

            elif action == "scroll":
                direction = arguments.get("direction", "down")
                return svc.scroll(conv_id, direction)

            elif action == "wait":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for wait"
                timeout_ms = int(arguments.get("timeout_ms", 5000))
                return svc.wait_for(conv_id, selector, timeout_ms)

            elif action == "close":
                svc.close_session(conv_id)
                return "Browser session closed."

            else:
                return f"Error: unknown action '{action}'"

        except ImportError:
            return "Error: Playwright not installed. Install with: pip install playwright"
        except Exception as e:
            return f"Browser error: {e}"


class LinkIdentityHandler(ToolHandler):
    """Generate a code to link identity across channels."""

    _pending_codes: Dict[str, Dict[str, str]] = {}  # code -> {user_id, channel, channel_id, expires}
    _codes_lock = threading.Lock()

    def __init__(self):
        self._user_id = ""
        self._channel = ""
        self._channel_id = ""

    @property
    def name(self) -> str:
        return "link_identity"

    @property
    def description(self) -> str:
        return (
            "Link your identity across channels (web, Telegram, Discord, Slack, WhatsApp). "
            "Generates a verification code. Send /link CODE on the other channel to complete."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate", "verify"],
                    "description": "generate = create link code, verify = verify a received code",
                },
                "code": {
                    "type": "string",
                    "description": "6-digit code to verify (for verify action)",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_channel_info(self, channel: str, channel_id: str):
        self._channel = channel
        self._channel_id = channel_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        import random
        import time as _time

        action = arguments.get("action", "generate")

        if action == "generate":
            if not self._user_id:
                return "Error: You must be authenticated to generate a link code."

            code = str(random.randint(100000, 999999))
            with self._codes_lock:
                # Clean expired codes
                now = _time.time()
                expired = [c for c, v in self._pending_codes.items()
                           if float(v.get("expires", 0)) < now]
                for c in expired:
                    del self._pending_codes[c]

                self._pending_codes[code] = {
                    "user_id": self._user_id,
                    "channel": self._channel,
                    "channel_id": self._channel_id,
                    "expires": str(_time.time() + 300),  # 5 min expiry
                }

            return (
                f"Link code: {code}\n"
                f"Send '/link {code}' on the other channel within 5 minutes to link your accounts."
            )

        elif action == "verify":
            code = arguments.get("code", "")
            if not code:
                return "Error: code is required for verify"

            with self._codes_lock:
                entry = self._pending_codes.pop(code, None)

            if not entry:
                return "Invalid or expired link code."

            if float(entry.get("expires", 0)) < _time.time():
                return "Link code has expired."

            # Link the identity
            try:
                from core.identity_service import IdentityService
                ids = IdentityService.instance()

                original_user = entry["user_id"]
                # Link current channel to the original user
                if self._channel and self._channel_id:
                    ok = ids.link(original_user, self._channel, self._channel_id)
                    if not ok:
                        return "This channel ID is already linked to another user."
                    return f"Identity linked! User '{original_user}' is now connected on {self._channel}."
                else:
                    return "Error: No channel information available for linking."
            except Exception as e:
                return f"Error linking identity: {e}"

        return f"Unknown action: {action}"


# ── Configurable handlers (for agent_tools) ──────────────────────────


class ConfigurableToolHandler(ToolHandler):
    """Base for tools configured via agent_tools dict (not hardcoded)."""

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any]):
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters or {
            "type": "object", "properties": {},
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._parameters


class HTTPToolHandler(ConfigurableToolHandler):
    """Tool that calls an external HTTP endpoint.

    Config example::

        {
            "type": "http",
            "endpoint": "http://localhost:8080/api/search",
            "method": "POST",
            "headers": {"Authorization": "Bearer xxx"},
            "timeout": 30,
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
        }

    The tool POSTs arguments as JSON body and returns the response text.
    For GET, arguments are sent as query parameters.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], endpoint: str,
                 method: str = "POST", headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._endpoint = endpoint
        self._method = method.upper()
        self._headers = headers or {}
        self._timeout = timeout

    def execute(self, arguments: Dict[str, Any]) -> str:
        parsed = urlparse(self._endpoint)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or "https"

        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    host, port, timeout=self._timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    host, port, timeout=self._timeout)

            headers = {"User-Agent": "PawFlow-Agent/1.0",
                       "Content-Type": "application/json"}
            headers.update(self._headers)

            path = parsed.path or "/"

            if self._method == "GET":
                # Encode arguments as query params
                from urllib.parse import urlencode
                qs = urlencode(arguments)
                if qs:
                    sep = "&" if "?" in path else "?"
                    path = f"{path}{sep}{qs}"
                conn.request("GET", path, headers=headers)
            else:
                body = json.dumps(arguments).encode("utf-8")
                headers["Content-Length"] = str(len(body))
                conn.request(self._method, path, body=body, headers=headers)

            response = conn.getresponse()
            response_body = response.read().decode("utf-8", errors="replace")
            conn.close()

            if len(response_body) > 10000:
                response_body = response_body[:10000] + "\n... (truncated)"

            return f"HTTP {response.status}\n{response_body}"
        except Exception as e:
            return f"Error calling {self._endpoint}: {e}"


class TaskToolHandler(ConfigurableToolHandler):
    """Tool that executes a PawFlow task inline.

    Config example::

        {
            "type": "task",
            "task_type": "executeSql",
            "config": {"connection_id": "my_db"},
            "parameter_mapping": {"sql": "sql_query"},
            "description": "Run a SQL query",
            "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}}
        }

    parameter_mapping maps tool argument names → task config keys.
    The tool creates a FlowFile with arguments as JSON content,
    sets mapped config values, executes the task, and returns the output.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], task_type: str,
                 task_config: Optional[Dict[str, Any]] = None,
                 parameter_mapping: Optional[Dict[str, str]] = None):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._task_type = task_type
        self._task_config = task_config or {}
        self._parameter_mapping = parameter_mapping or {}

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core import TaskFactory, FlowFile

        try:
            task_class = TaskFactory.get(self._task_type)
        except Exception as e:
            return f"Error: unknown task type '{self._task_type}': {e}"

        # Build config: base config + mapped arguments
        config = dict(self._task_config)
        for arg_key, config_key in self._parameter_mapping.items():
            if arg_key in arguments:
                config[config_key] = arguments[arg_key]

        # If no mapping, pass all arguments as config keys
        if not self._parameter_mapping:
            config.update(arguments)

        try:
            task = task_class(config)
            ff = FlowFile(content=json.dumps(arguments).encode("utf-8"))
            results = task.execute(ff)
            if results:
                return results[0].get_content().decode("utf-8", errors="replace")
            return "Task executed (no output)"
        except Exception as e:
            return f"Error executing task '{self._task_type}': {e}"


class MCPToolHandler(ConfigurableToolHandler):
    """Tool that calls a tool on an MCP server (HTTP transport).

    Config example::

        {
            "type": "mcp",
            "server_url": "http://localhost:3001/mcp",
            "tool_name": "web_search",
            "headers": {"Authorization": "Bearer xxx"},
            "timeout": 30,
            "description": "Search the web via MCP",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
        }

    Uses JSON-RPC over HTTP (MCP Streamable HTTP transport).
    Sends tools/call to the server and returns the text result.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], server_url: str,
                 mcp_tool_name: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._server_url = server_url
        self._mcp_tool_name = mcp_tool_name or tool_name
        self._headers = headers or {}
        self._timeout = timeout

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid as _uuid
        parsed = urlparse(self._server_url)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or "https"

        rpc_body = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": self._mcp_tool_name,
                "arguments": arguments,
            },
            "id": str(_uuid.uuid4()),
        }).encode("utf-8")

        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    host, port, timeout=self._timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    host, port, timeout=self._timeout)

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(rpc_body)),
            }
            headers.update(self._headers)

            path = parsed.path or "/"
            conn.request("POST", path, body=rpc_body, headers=headers)
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            conn.close()

            if response.status != 200:
                return f"MCP error (HTTP {response.status}): {body}"

            rpc_response = json.loads(body)
            if "error" in rpc_response:
                err = rpc_response["error"]
                return f"MCP error: {err.get('message', err)}"

            result = rpc_response.get("result", {})
            # MCP tools/call result has "content" array
            content_parts = result.get("content", [])
            texts = []
            for part in content_parts:
                if isinstance(part, dict):
                    texts.append(part.get("text", json.dumps(part)))
                else:
                    texts.append(str(part))
            return "\n".join(texts) if texts else json.dumps(result)

        except json.JSONDecodeError:
            return f"MCP error: invalid JSON response from {self._server_url}"
        except Exception as e:
            return f"Error calling MCP server {self._server_url}: {e}"


# ── MCP server discovery ─────────────────────────────────────────────


def discover_mcp_tools(server_url: str,
                       headers: Optional[Dict[str, str]] = None,
                       timeout: int = 10) -> List[Dict[str, Any]]:
    """Discover available tools from an MCP server via tools/list.

    Returns a list of dicts: [{"name": ..., "description": ..., "inputSchema": ...}]
    """
    import uuid as _uuid
    parsed = urlparse(server_url)
    host = parsed.hostname
    port = parsed.port
    scheme = parsed.scheme or "https"

    rpc_body = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": str(_uuid.uuid4()),
    }).encode("utf-8")

    try:
        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(
                host, port, timeout=timeout)

        req_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Length": str(len(rpc_body)),
        }
        if headers:
            req_headers.update(headers)

        path = parsed.path or "/"
        conn.request("POST", path, body=rpc_body, headers=req_headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        conn.close()

        if response.status != 200:
            logger.error(f"MCP tools/list failed (HTTP {response.status}): {body}")
            return []

        rpc_response = json.loads(body)
        if "error" in rpc_response:
            logger.error(f"MCP tools/list error: {rpc_response['error']}")
            return []

        return rpc_response.get("result", {}).get("tools", [])

    except Exception as e:
        logger.error(f"MCP discovery failed for {server_url}: {e}")
        return []


# ── Agent tools loader ───────────────────────────────────────────────


def load_agent_tools(agent_tools_config: Dict[str, Any]) -> ToolRegistry:
    """Build a ToolRegistry from a flow-level agent_tools configuration.

    Supports four tool types:
    - builtin: Reference to a builtin handler (execute_script, read_file, scrape_url)
    - http: Call an external HTTP endpoint
    - task: Execute a PawFlow task inline
    - mcp: Call a tool on an MCP server (single tool)

    Plus a special "mcp_server" entry that auto-discovers all tools::

        "agent_tools": {
            "_mcp_server": {
                "type": "mcp_server",
                "server_url": "http://localhost:3001/mcp",
                "headers": {}
            },
            "calculator": {"type": "builtin", "handler": "execute_script"},
            "search_api": {"type": "http", "endpoint": "...", ...}
        }
    """
    registry = ToolRegistry()
    default_builtins = None  # lazy

    for tool_name, tool_def in agent_tools_config.items():
        tool_type = tool_def.get("type", "http")
        handler = None

        if tool_type == "builtin":
            # Reference to a builtin handler
            handler_name = tool_def.get("handler", tool_name)
            if default_builtins is None:
                default_builtins = create_default_registry()
            builtin = default_builtins.get(handler_name)
            if builtin:
                handler = builtin
            else:
                logger.warning(f"agent_tools: unknown builtin '{handler_name}'")

        elif tool_type == "http":
            endpoint = tool_def.get("endpoint", "")
            if not endpoint:
                logger.warning(f"agent_tools: '{tool_name}' has no endpoint")
                continue
            handler = HTTPToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"HTTP tool: {tool_name}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                endpoint=endpoint,
                method=tool_def.get("method", "POST"),
                headers=tool_def.get("headers"),
                timeout=int(tool_def.get("timeout", 30)),
            )

        elif tool_type == "task":
            task_type = tool_def.get("task_type", "")
            if not task_type:
                logger.warning(f"agent_tools: '{tool_name}' has no task_type")
                continue
            handler = TaskToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"PawFlow task: {task_type}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                task_type=task_type,
                task_config=tool_def.get("config", {}),
                parameter_mapping=tool_def.get("parameter_mapping", {}),
            )

        elif tool_type == "mcp":
            server_url = tool_def.get("server_url", "")
            if not server_url:
                logger.warning(f"agent_tools: '{tool_name}' has no server_url")
                continue
            handler = MCPToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"MCP tool: {tool_name}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                server_url=server_url,
                mcp_tool_name=tool_def.get("tool_name", tool_name),
                headers=tool_def.get("headers"),
                timeout=int(tool_def.get("timeout", 30)),
            )

        elif tool_type == "mcp_server":
            # Auto-discover all tools from an MCP server
            server_url = tool_def.get("server_url", "")
            if not server_url:
                logger.warning(f"agent_tools: '{tool_name}' has no server_url")
                continue
            mcp_headers = tool_def.get("headers", {})
            mcp_timeout = int(tool_def.get("timeout", 30))
            discovered = discover_mcp_tools(
                server_url, headers=mcp_headers, timeout=10)
            for mcp_tool in discovered:
                mcp_name = mcp_tool.get("name", "")
                if not mcp_name:
                    continue
                h = MCPToolHandler(
                    tool_name=mcp_name,
                    tool_description=mcp_tool.get("description", ""),
                    tool_parameters=mcp_tool.get("inputSchema", {
                        "type": "object", "properties": {},
                    }),
                    server_url=server_url,
                    mcp_tool_name=mcp_name,
                    headers=mcp_headers,
                    timeout=mcp_timeout,
                )
                registry.register(h)
                logger.info(f"agent_tools: discovered MCP tool '{mcp_name}' "
                           f"from {server_url}")
            continue  # skip the register below

        else:
            logger.warning(f"agent_tools: unknown type '{tool_type}' "
                          f"for tool '{tool_name}'")
            continue

        if handler:
            # Attach allowed_roles for per-user tool filtering
            allowed_roles = tool_def.get("allowed_roles")
            if allowed_roles is not None:
                handler.allowed_roles = allowed_roles
            registry.register(handler)
            logger.info(f"agent_tools: loaded {tool_type} tool '{tool_name}'")

    return registry
