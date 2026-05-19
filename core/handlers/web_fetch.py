"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import os
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

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

    _user_id: str = ""

    @property
    def description(self) -> str:
        return (
            "Execute Python code and return the result.\n\n"
            "Execution target (auto-detected):\n"
            "- If a relay is connected, code runs on the user's machine via the relay. "
            "This gives full access to the user's Python environment, installed packages, "
            "and filesystem.\n"
            "- If no relay is connected, code runs in a server-side sandbox with restricted "
            "imports. Force sandbox mode with destination='sandbox'.\n"
            "- You can specify a relay service name in destination to target a specific machine.\n\n"
            "Getting output:\n"
            "- Use print() to produce output — all printed text is captured and returned.\n"
            "- Set a variable named 'result' and its value will be returned (sandbox mode only).\n"
            "- If neither print() nor result is used, you get 'Script executed (no output)'.\n\n"
            "File I/O (sandbox mode):\n"
            "- open('filestore://name.zip', 'wb') — creates a downloadable file in FileStore.\n"
            "- open('fs://service_name/path', 'rb'/'wb') — reads/writes via a filesystem service.\n\n"
            "Key parameters:\n"
            "- code (required): Python code to execute. Can be a single expression ('2+2') "
            "or a full script with multiple statements.\n"
            "- destination: 'sandbox' (force server sandbox), a relay service name, or omit "
            "for auto-detection.\n"
            "- max_output: Max output characters (default 4000). Larger outputs are "
            "auto-saved to FileStore and a download link is returned.\n\n"
            "Available in sandbox: math, json, re, csv, datetime, collections, itertools, "
            "functools, statistics, zipfile, pathlib, textwrap, html, base64, hashlib, "
            "urllib.parse, and more. No network access or os/subprocess in sandbox mode."
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
                        "or statements. Use 'result' variable for output."
                    ),
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Where to execute: auto (default — relay if connected, else sandbox), "
                        "'sandbox' (force server sandbox), or relay service name"
                    ),
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output chars (default: 4000). Large outputs are auto-saved to FileStore.",
                },
            },
            "required": ["code"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        code = arguments.get("code", "")
        destination = arguments.get("destination", "")
        if not code:
            return "Error: no code provided"

        _secret_env = arguments.get("_secret_env") or {}

        # Explicit relay service name → execute remote
        _dest = destination.strip().lower()
        if _dest and _dest not in ("server", "sandbox", "local", ""):
            return self._execute_remote(code, _dest, env=_secret_env)

        # Explicit sandbox request
        if _dest in ("server", "sandbox"):
            return self._execute_sandbox(code, env=_secret_env)

        # Auto-detect: if a relay is connected, use it; else sandbox
        _relay_svc = self._find_default_relay()
        if _relay_svc:
            _svc_id = getattr(_relay_svc, '_service_id', '') or getattr(_relay_svc, 'name', '')
            if _svc_id:
                return self._execute_remote(code, _svc_id, env=_secret_env)

        # Fallback: server sandbox
        return self._execute_sandbox(code, env=_secret_env)

    def _execute_sandbox(self, code: str, env: dict = None) -> str:
        """Execute in server-side sandbox."""
        from core.sandbox import execute_sandboxed
        # Inject env vars as globals accessible via os.environ in sandbox
        # (sandbox doesn't allow os, so inject as pre-defined variables)
        _env_prefix = ""
        if env:
            for k, v in env.items():
                _env_prefix += f"{k} = {repr(v)}\n"
            code = _env_prefix + code
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
            output += "\n\nFiles created:\n"
            for url in created_files:
                import re as _re_fid
                _m = _re_fid.search(r'/files/([a-f0-9]+)', url)
                _fid = _m.group(1) if _m else ""
                output += f"- {url}" + (f" (file_id: {_fid})" if _fid else "") + "\n"
        return output

    def _find_default_relay(self):
        """Find the default relay service (same resolution as bash/fs tools)."""
        if self._fs_resolver:
            try:
                svc = self._fs_resolver("")  # empty = auto-detect default
                if svc and hasattr(svc, 'exec'):
                    return svc
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            from core.handlers._fs_base import find_fs_service
            svc = find_fs_service(self._user_id)
            if svc and hasattr(svc, 'exec'):
                return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    def _execute_remote(self, code: str, service_name: str, env: dict = None) -> str:
        """Execute code on a remote filesystem service via relay."""
        # Inject PawFlow SDK env vars so scripts can use `from pawflow import tools`
        from core.handlers._fs_base import get_tool_relay_env
        _sdk_env = get_tool_relay_env()
        if _sdk_env:
            env = {**_sdk_env, **(env or {})}
        svc_name = service_name.replace("fs:", "", 1) if service_name.startswith("fs:") else service_name
        svc = None
        if self._fs_resolver:
            svc = self._fs_resolver(svc_name)
        if not svc:
            try:
                from core.service_registry import ServiceRegistry
                svc = ServiceRegistry.get_instance().resolve(
                    svc_name, user_id=self._user_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not svc:
            return f"Error: filesystem service '{svc_name}' not found"
        try:
            if hasattr(svc, 'exec'):
                # Write to temp file then execute (avoids shell escaping issues)
                import os
                import tempfile
                import uuid as _uuid_exec
                _exec_id = _uuid_exec.uuid4().hex[:8]
                _fname = f".pawflow_exec_{_exec_id}.py"
                _exec_env = dict(env or {})
                _exec_env.setdefault(
                    "PAWFLOW_DATA_DIR",
                    os.path.join(tempfile.gettempdir(), "pawflow-exec-data", _exec_id))
                svc.write_file(_fname, code.encode("utf-8"))
                try:
                    result = svc.exec(".", f"python3 {_fname}", env=_exec_env)
                finally:
                    try:
                        svc.delete_file(_fname)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            else:
                result = svc.execute_command({
                    "action": "exec",
                    "command": f"python -c {repr(code)}",
                })
            if isinstance(result, dict):
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                output = stdout
                if stderr:
                    output += f"\nSTDERR: {stderr}"
                exit_code = result.get("exit_code", 0)
                if exit_code:
                    output += f"\n(exit code: {exit_code})"
                return output or "Script executed (no output)"
            return str(result)
        except Exception as e:
            return f"Error executing on '{svc_name}': {e}"


class WebSearchHandler(ToolHandler):
    """Search the web using a configurable provider chain."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._fs_resolver = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id or ""

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id or ""

    def set_fs_resolver(self, resolver):
        """Set filesystem service resolver: (service_id) -> relay service."""
        self._fs_resolver = resolver

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web using configurable providers and return titles, URLs, and snippets.\n\n"
            "Use this when you need to find current information, look up documentation, "
            "verify facts, or research topics that are beyond your training data. "
            "No API key required. By default PawFlow queries Google and Bing, "
            "then aggregates and deduplicates results. "
            "Override with provider/search_provider or the PawFlow variable "
            "web_search_providers.\n\n"
            "Key parameters:\n"
            "- query (required): The search query string. Be specific for better results.\n"
            "- max_results: Number of results to return (default 5).\n\n"
            "- provider/search_provider: Provider or comma-separated provider chain: "
            "google, bing, duckduckgo, or auto.\n\n"
            "Returns a list of results, each with title, URL, and a text snippet. "
            "To read the full content of a result page, use fetch with the URL. "
            "For API calls or raw HTTP requests, use fetch with mode='raw' instead."
        )

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
                "provider": {
                    "type": "string",
                    "description": (
                        "Search provider or comma-separated provider chain. "
                        "Supported: auto, google, bing, duckduckgo. "
                        "Default: PawFlow variable web_search_providers or google,bing."
                    ),
                },
                "search_provider": {
                    "type": "string",
                    "description": "Alias for provider.",
                },
            },
            "required": ["query"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        max_results = max(1, min(20, int(arguments.get("max_results", 5))))
        if not query:
            return "Error: no query provided"

        providers = self._provider_chain(arguments)
        if not providers:
            return "Error: unsupported search provider"

        if not arguments.get("_pawflow_web_search_local"):
            relay_result = self._execute_via_relay(arguments, providers, max_results)
            if relay_result is not None:
                return relay_result

        attempts = []
        collected = []
        for provider in providers:
            try:
                results = self._search_provider(provider, query, max_results)
            except Exception as e:
                attempts.append(f"{provider}: {e}")
                continue
            if results:
                for result in results:
                    result.setdefault("provider", provider)
                collected.extend(results)
                attempts.append(f"{provider}: {len(results)} result(s)")
                continue
            attempts.append(f"{provider}: no parseable results")

        results = self._dedupe_results(collected, max_results, query=query)
        if results:
            rendered = [self._format_result(r) for r in results]
            provider_list = ",".join(providers)
            return (
                f"Search results for '{query}' (providers: {provider_list}):\n\n"
                + "\n\n".join(rendered)
            )

        detail = "; ".join(attempts)
        suffix = f" Providers tried: {detail}." if detail else ""
        return f"No results found for: {query}.{suffix}"

    def _execute_via_relay(
        self,
        arguments: Dict[str, Any],
        providers: List[str],
        max_results: int,
    ) -> Optional[str]:
        """Run web search inside the default relay when one is connected."""
        relay = self._find_default_relay()
        if not relay:
            return None

        remote_args = {
            "query": arguments.get("query", ""),
            "max_results": max_results,
            "provider": ",".join(providers),
            "_pawflow_web_search_local": True,
        }
        code = "\n".join([
            "import json",
            "from core.handlers.web_fetch import WebSearchHandler",
            f"args = json.loads({json.dumps(remote_args, ensure_ascii=False)!r})",
            "h = WebSearchHandler()",
            f"h.set_user_id({self._user_id!r})",
            f"h.set_conversation_id({self._conversation_id!r})",
            "print(h.execute(args))",
        ])
        return self._execute_remote_code(relay, code)

    def _find_default_relay(self):
        """Find the default relay service, matching execute_script resolution."""
        if self._fs_resolver:
            try:
                svc = self._fs_resolver("")
                if svc and hasattr(svc, "exec"):
                    return svc
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            from core.handlers._fs_base import find_fs_service
            svc = find_fs_service(self._user_id)
            if svc and hasattr(svc, "exec"):
                return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    def _execute_remote_code(self, relay, code: str) -> str:
        from core.handlers._fs_base import get_tool_relay_env

        import uuid as _uuid_exec

        env = get_tool_relay_env() or None
        filename = f".pawflow_web_search_{_uuid_exec.uuid4().hex[:8]}.py"
        try:
            relay.write_file(filename, code.encode("utf-8"))
            try:
                result = relay.exec(".", f"python3 {filename}", env=env)
            finally:
                try:
                    relay.delete_file(filename)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception as e:
            return f"Error executing web_search on relay: {e}"

        if isinstance(result, dict):
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            output = stdout.rstrip()
            if stderr:
                output += f"\nSTDERR: {stderr.rstrip()}"
            exit_code = result.get("exit_code", 0)
            if exit_code:
                output += f"\n(exit code: {exit_code})"
            return output or "Error: relay web_search returned no output"
        return str(result)

    def _provider_chain(self, arguments: Dict[str, Any]) -> List[str]:
        raw = (
            arguments.get("provider")
            or arguments.get("search_provider")
            or self._configured_provider_chain()
            or "google,bing"
        )
        raw = str(raw).strip().lower()
        if raw in ("", "auto", "default"):
            raw = "google,bing"
        parts = [p.strip().replace("-", "_") for p in re.split(r"[,\s]+", raw) if p.strip()]
        aliases = {"ddg": "duckduckgo", "duck_duck_go": "duckduckgo"}
        valid = {"google", "bing", "duckduckgo"}
        providers = []
        for part in parts:
            provider = aliases.get(part, part)
            if provider in valid and provider not in providers:
                providers.append(provider)
        return providers

    def _configured_provider_chain(self) -> str:
        try:
            from core.expression import resolve_expression
            for key in ("web_search_providers", "web_search_provider"):
                template = "$" + "{" + key + ":default(\"\")" + "}"
                value = resolve_expression(
                    template,
                    owner=self._user_id,
                    conversation_id=self._conversation_id,
                )
                if value and value != template and not str(value).startswith("${"):
                    return str(value).strip()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return (
            os.environ.get("PAWFLOW_WEB_SEARCH_PROVIDERS")
            or os.environ.get("PAWFLOW_WEB_SEARCH_PROVIDER")
            or ""
        )

    def _search_provider(self, provider: str, query: str, max_results: int) -> List[Dict[str, str]]:
        if provider == "google":
            return self._search_google(query, max_results)
        if provider == "bing":
            return self._search_bing(query, max_results)
        if provider == "duckduckgo":
            return self._search_duckduckgo(query, max_results)
        return []

    def _format_result(self, result: Dict[str, str]) -> str:
        title = result.get("title", "").strip()
        url = result.get("url", "").strip()
        snippet = result.get("snippet", "").strip()
        provider = result.get("provider", "").strip()
        provider_suffix = f" [{provider}]" if provider else ""
        return f"- {title}{provider_suffix}\n  {url}\n  {snippet}"

    def _dedupe_results(
        self,
        results: List[Dict[str, str]],
        max_results: int,
        query: str = "",
    ) -> List[Dict[str, str]]:
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        query_terms = {
            token for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) > 2
        }

        def content_type_priority(result: Dict[str, str]) -> int:
            kind = result.get("type") or self._infer_search_result_type(result)
            if kind == "image":
                return 1
            if kind == "video":
                return 2
            return 0

        def query_match_score(result: Dict[str, str]) -> int:
            if not query_terms:
                return 0
            text = " ".join([
                result.get("title", ""),
                result.get("url", ""),
                result.get("snippet", ""),
            ]).lower()
            return sum(1 for term in query_terms if term in text)

        def interleaved_results() -> List[Dict[str, str]]:
            groups: Dict[str, List[tuple[int, Dict[str, str]]]] = {}
            provider_order: Dict[str, int] = {}
            for index, result in enumerate(results):
                provider = result.get("provider") or ""
                if provider not in provider_order:
                    provider_order[provider] = len(provider_order)
                groups.setdefault(provider, []).append((index, result))

            candidates = []
            for provider, provider_results in groups.items():
                ordered = sorted(
                    provider_results,
                    key=lambda item: (
                        content_type_priority(item[1]),
                        -query_match_score(item[1]),
                        item[0],
                    ),
                )
                for slot, (index, result) in enumerate(ordered):
                    candidates.append((
                        slot,
                        content_type_priority(result),
                        -query_match_score(result),
                        provider_order[provider],
                        index,
                        result,
                    ))
            return [candidate[-1] for candidate in sorted(candidates)]

        def key_for(url: str, title: str) -> str:
            parsed = urlparse(url.strip())
            if parsed.scheme and parsed.netloc:
                query = urlencode([
                    (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                    if not k.lower().startswith("utm_")
                ])
                return urlunparse((
                    parsed.scheme.lower(),
                    parsed.netloc.lower().removeprefix("www."),
                    parsed.path.rstrip("/"),
                    "",
                    query,
                    "",
                ))
            return re.sub(r"\s+", " ", title.lower()).strip()

        deduped = []
        seen = {}
        for result in interleaved_results():
            title = (result.get("title") or "").strip()
            url = (result.get("url") or "").strip()
            if not title or not url:
                continue
            key = key_for(url, title)
            if key in seen:
                existing = seen[key]
                existing_providers = [p for p in (existing.get("provider") or "").split(",") if p]
                for provider in (result.get("provider") or "").split(","):
                    if provider and provider not in existing_providers:
                        existing_providers.append(provider)
                if existing_providers:
                    existing["provider"] = ",".join(existing_providers)
                continue
            seen[key] = result
            deduped.append(result)
            if len(deduped) >= max_results:
                break
        return deduped

    def _infer_search_result_type(self, result: Dict[str, str]) -> str:
        from urllib.parse import urlparse

        url = (result.get("url") or "").strip().lower()
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.lower()
        text = " ".join([
            result.get("title", ""),
            result.get("snippet", ""),
        ]).lower()
        image_exts = (".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp")
        video_exts = (".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv")
        if path.endswith(image_exts) or any(token in text for token in ("image result", "images result")):
            return "image"
        video_hosts = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "tiktok.com")
        if (
            path.endswith(video_exts)
            or host in video_hosts
            or any(token in text for token in ("video result", "videos result"))
        ):
            return "video"
        return "text"

    def _fetch_https(self, host: str, path: str, accept: str = "text/html",
                     redirects_left: int = 3) -> str:
        import http.client
        import ssl
        from urllib.parse import urlparse

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        }
        if host.endswith("google.com"):
            headers["Cookie"] = "CONSENT=YES+; SOCS=CAESHAgBEhIaAB"

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, timeout=15, context=ctx)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("location") or ""
                parsed = urlparse(loc)
                if redirects_left > 0 and parsed.scheme == "https" and parsed.netloc:
                    next_path = parsed.path + ("?" + parsed.query if parsed.query else "")
                    return self._fetch_https(parsed.netloc, next_path, accept, redirects_left - 1)
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
            return body
        finally:
            conn.close()

    def _clean_html_text(self, text: str) -> str:
        import html as html_mod

        text = html_mod.unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _fetch_search_browser(self, url: str, locale: str = "en-US",
                              timezone_id: str = "America/New_York") -> str:
        """Fetch a search results page with Patchright/Playwright stealth mode."""
        from patchright.sync_api import sync_playwright
        from urllib.parse import urlparse

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    locale=locale,
                    timezone_id=timezone_id,
                    viewport={"width": 1365, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                parsed = urlparse(url)
                if parsed.netloc.endswith("google.com"):
                    context.add_cookies([
                        {
                            "name": "CONSENT",
                            "value": "YES+",
                            "domain": ".google.com",
                            "path": "/",
                        },
                        {
                            "name": "SOCS",
                            "value": "CAESHAgBEhIaAB",
                            "domain": ".google.com",
                            "path": "/",
                        },
                    ])
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                body_text = page.locator("body").inner_text(timeout=5000)
                lowered = body_text.lower()
                if "unusual traffic" in lowered or "captcha" in lowered:
                    logger.debug("search browser challenge for %s", url)
                    return ""
                return page.content()
            finally:
                browser.close()

    def _parse_google_html(self, body: str, max_results: int) -> List[Dict[str, str]]:
        from bs4 import BeautifulSoup
        from urllib.parse import parse_qs, unquote, urlparse

        soup = BeautifulSoup(body, "html.parser")
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if href.startswith("/url?"):
                href = parse_qs(urlparse(href).query).get("q", [""])[0]
            elif href.startswith("http"):
                href = unquote(href)
            else:
                continue
            parsed = urlparse(href)
            host = (parsed.netloc or "").lower()
            if not href.startswith("http") or "google." in host:
                continue
            if parsed.fragment.startswith(":~:text="):
                continue

            title = self._clean_html_text(a.get_text(" ", strip=True))
            parent = a.parent
            parent_text = self._clean_html_text(parent.get_text(" ", strip=True)) if parent else ""
            if len(title) < 8:
                title = parent_text.split(" Table_title:", 1)[0].strip() or title
            snippet = parent_text
            if title and snippet.startswith(title):
                snippet = snippet[len(title):].strip(" -")

            key = (host.removeprefix("www."), parsed.path.rstrip("/"))
            if not title or key in seen:
                continue
            seen.add(key)
            results.append({"title": title[:180], "url": href, "snippet": snippet[:360]})
            if len(results) >= max_results:
                break
        return results

    def _parse_bing_html(self, body: str, max_results: int) -> List[Dict[str, str]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(body, "html.parser")
        results = []
        for item in soup.select("li.b_algo"):
            link = item.select_one("h2 a") or item.find("a", href=True)
            if not link:
                continue
            title = self._clean_html_text(link.get_text(" ", strip=True))
            url = self._decode_bing_url((link.get("href") or "").strip())
            caption = item.select_one(".b_caption p") or item.select_one("p")
            snippet = self._clean_html_text(caption.get_text(" ", strip=True) if caption else item.get_text(" ", strip=True))
            if title and url.startswith("http"):
                results.append({"title": title[:180], "url": url, "snippet": snippet[:360]})
            if len(results) >= max_results:
                break
        return results

    def _decode_bing_url(self, url: str) -> str:
        import base64
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url or "")
        if "bing.com" not in (parsed.netloc or ""):
            return url
        raw = parse_qs(parsed.query).get("u", [""])[0]
        if raw.startswith("a1"):
            encoded = raw[2:]
            padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8", "replace")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return url

    def _search_google(self, query: str, max_results: int) -> List[Dict[str, str]]:
        from urllib.parse import parse_qs, urlencode, unquote, urlparse

        path = "/search?" + urlencode({"q": query, "hl": "en", "num": str(max_results)})
        body = self._fetch_https("www.google.com", path)
        results = self._parse_google_html(body, max_results)
        if results:
            return results

        results = []
        pattern = re.compile(
            r'<a[^>]+href="(?P<href>/url\?q=[^"]+|https?://[^"]+)"[^>]*>.*?'
            r'<h3[^>]*>(?P<title>.*?)</h3>(?P<rest>.*?)(?=<a[^>]+href="(?:/url\?q=|https?://)|</body>)',
            re.DOTALL,
        )
        for match in pattern.finditer(body):
            href = match.group("href")
            if href.startswith("/url?"):
                qs = parse_qs(urlparse(href).query)
                url = qs.get("q", [""])[0]
            else:
                url = unquote(href)
            title = self._clean_html_text(match.group("title"))
            snippet = self._clean_html_text(match.group("rest"))[:320]
            if title and url.startswith("http") and "google." not in urlparse(url).netloc:
                results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        if results:
            return results
        return self._search_google_stealth(query, max_results)

    def _search_google_stealth(self, query: str, max_results: int) -> List[Dict[str, str]]:
        """Use Patchright/Playwright for Google when static HTML is a JS shell."""
        from urllib.parse import quote_plus

        try:
            urls = [
                "https://www.google.com/search?q=" + quote_plus(query) + f"&hl=en&num={max_results}&udm=14",
                "https://www.google.com/search?q=" + quote_plus(query) + f"&hl=en&num={max_results}&filter=0",
                "https://www.google.com/search?q=" + quote_plus(query) + f"&hl=en&num={max_results}",
            ]
            for _attempt in range(3):
                for url in urls:
                    body = self._fetch_search_browser(url)
                    results = self._parse_google_html(body, max_results) if body else []
                    if results:
                        return results
            return []
        except Exception as e:
            logger.debug("google stealth search exception: %s", e)
            return []

    def _search_bing(self, query: str, max_results: int) -> List[Dict[str, str]]:
        from urllib.parse import quote_plus, urlencode

        try:
            variants = [
                ("https://www.bing.com/search?q=" + quote_plus(query), "fr-BE", "Europe/Brussels"),
                ("https://www.bing.com/search?q=" + quote_plus(query), "en-US", "America/New_York"),
            ]
            for url, locale, timezone_id in variants:
                body = self._fetch_search_browser(url, locale=locale, timezone_id=timezone_id)
                results = self._parse_bing_html(body, max_results) if body else []
                if results:
                    return results
        except Exception as e:
            logger.debug("bing browser search failed: %s", e)

        path = "/search?" + urlencode({"q": query, "format": "rss"})
        body = self._fetch_https("www.bing.com", path, "application/rss+xml,text/xml,*/*")
        return self._parse_bing_rss(body, max_results)

    def _parse_bing_rss(self, body: str, max_results: int) -> List[Dict[str, str]]:
        import defusedxml.ElementTree as ET

        root = ET.fromstring(body)
        results = []
        for item in root.findall(".//item"):
            title = self._clean_html_text(item.findtext("title") or "")
            url = self._clean_html_text(item.findtext("link") or "")
            snippet = self._clean_html_text(item.findtext("description") or "")
            if title and url.startswith("http"):
                results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results

    def _search_duckduckgo(self, query: str, max_results: int) -> List[Dict[str, str]]:
        from urllib.parse import parse_qs, urlencode, urlparse as _urlparse

        body = self._fetch_https("html.duckduckgo.com", f"/html/?{urlencode({'q': query})}")
        blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            body, re.DOTALL,
        )
        results = []
        for raw_url, title, snippet in blocks[:max_results]:
            url = raw_url
            try:
                qs = parse_qs(_urlparse(raw_url).query)
                if "uddg" in qs:
                    url = qs["uddg"][0]
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            title_clean = self._clean_html_text(title)
            snippet_clean = self._clean_html_text(snippet)
            if title_clean and url.startswith("http"):
                results.append({"title": title_clean, "url": url, "snippet": snippet_clean})
        return results


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
        arguments = resolve_value(arguments, owner=getattr(self, '_user_id', ''))

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
