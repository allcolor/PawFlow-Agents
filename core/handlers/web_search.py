"""WebSearchHandler — extracted from web_fetch.py to keep files <=800 lines.

Re-exported from core.handlers.web_fetch for import stability (remote-exec
payloads also import WebSearchHandler from core.handlers.web_fetch).
"""

import json
import logging
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


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
                "q": {
                    "type": "string",
                    "description": "Alias for query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max number of results to return (default 5)",
                },
                "maxResults": {
                    "type": "integer",
                    "description": "Alias for max_results.",
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
            "required": [],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query") or arguments.get("q") or ""
        raw_max_results = arguments.get("max_results")
        if raw_max_results is None:
            raw_max_results = arguments.get("maxResults", 5)
        max_results = max(1, min(20, int(raw_max_results)))
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
            "query": arguments.get("query") or arguments.get("q") or "",
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
            svc = find_fs_service(self._user_id,
                                  conversation_id=self._conversation_id)
            if svc and hasattr(svc, "exec"):
                return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    def _execute_remote_code(self, relay, code: str) -> Optional[str]:
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
            logger.debug("relay web_search failed, falling back to local: %s", e)
            return None

        if isinstance(result, dict):
            stdout = (result.get("stdout") or "").rstrip()
            stderr = (result.get("stderr") or "").rstrip()
            exit_code = result.get("exit_code", 0)
            if exit_code or not stdout:
                # The relay payload imports PawFlow's core package, which only
                # exists when the relay workspace is the PawFlow repo itself.
                # On any other workspace the script dies (ModuleNotFoundError)
                # -- fall back to the local provider chain instead of returning
                # the traceback as the search result.
                logger.debug("relay web_search unusable (exit=%s), falling "
                             "back to local: %s", exit_code, stderr[-500:])
                return None
            if stderr:
                stdout += f"\nSTDERR: {stderr}"
            return stdout
        return str(result) or None

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
        import os
        from patchright.sync_api import sync_playwright
        from urllib.parse import urlparse

        executable_path = (
            os.environ.get("PAWFLOW_CHROMIUM_EXECUTABLE")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or None
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=executable_path,
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


