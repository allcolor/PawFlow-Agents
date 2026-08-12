import inspect
import threading
import time
from types import SimpleNamespace

from core.handlers.web_fetch import ExecuteScriptHandler, WebSearchHandler


def test_web_search_defaults_to_concurrent_free_provider_aggregation(monkeypatch):
    handler = WebSearchHandler()
    calls = []

    def fake_search(provider, query, max_results):
        calls.append((provider, query, max_results))
        if provider == "google":
            return [{
                "title": "AWS Color Guide",
                "url": "https://example.com/google-aws",
                "snippet": "Google result snippet.",
            }]
        if provider == "bing":
            return [{
                "title": "AWS Brand Color",
                "url": "https://example.com/aws",
                "snippet": "AWS orange is #FF9900.",
            }]
        return []

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers.web_fetch.WebSearchHandler._find_default_relay", lambda self: None)

    out = handler.execute({"query": "AWS brand color", "max_results": 5})

    assert set(calls) == {
        ("bing", "AWS brand color", 5),
        ("duckduckgo", "AWS brand color", 5),
        ("google", "AWS brand color", 5),
    }
    assert "providers: bing,duckduckgo,google" in out
    assert "AWS Color Guide" in out
    assert "AWS Brand Color" in out


def test_execute_script_isolates_pawflow_data_dir_for_relay_python():
    src = inspect.getsource(ExecuteScriptHandler._execute_remote)
    assert "PAWFLOW_DATA_DIR" in src
    assert "pawflow-exec-data" in src
    assert "setdefault(" in src


def test_web_search_provider_override_uses_only_requested_provider(monkeypatch):
    handler = WebSearchHandler()
    calls = []

    def fake_search(provider, query, max_results):
        calls.append(provider)
        return [{"title": "Result", "url": "https://example.com", "snippet": "Snippet"}]

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers.web_fetch.WebSearchHandler._find_default_relay", lambda self: None)

    out = handler.execute({"query": "test", "provider": "bing"})

    assert calls == ["bing"]
    assert "providers: bing" in out


def test_web_search_accepts_claude_style_query_aliases(monkeypatch):
    handler = WebSearchHandler()
    calls = []

    def fake_search(provider, query, max_results):
        calls.append((provider, query, max_results))
        return [{"title": "Result", "url": "https://example.com", "snippet": "Snippet"}]

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers.web_fetch.WebSearchHandler._find_default_relay", lambda self: None)

    out = handler.execute({"q": "test", "provider": "duckduckgo", "maxResults": 3})

    assert calls == [("duckduckgo", "test", 3)]
    assert "providers: duckduckgo" in out


def test_web_search_schema_allows_q_alias_without_query():
    handler = WebSearchHandler()

    assert "q" in handler.parameters_schema["properties"]
    assert "query" not in handler.parameters_schema.get("required", [])


def test_web_search_delegates_to_relay_when_available(monkeypatch):
    handler = WebSearchHandler()
    files = {}

    class FakeRelay:
        def write_file(self, path, content):
            files[path] = content.decode("utf-8")

        def exec(self, _path, command, env=None):
            assert command.startswith("python3 .pawflow_web_search_")
            assert env is None or isinstance(env, dict)
            script = next(iter(files.values()))
            assert '"provider": "bing,duckduckgo,google"' in script
            assert '"_pawflow_web_search_local": true' in script
            assert '"timeout": 8' in script
            return {
                "stdout": "Search results from relay\n",
                "stderr": "",
                "exit_code": 0,
            }

        def delete_file(self, path):
            files.pop(path, None)

    def fail_local_search(provider, query, max_results):
        raise AssertionError("local server search should not run when relay is available")

    monkeypatch.setattr(handler, "_search_provider", fail_local_search)
    monkeypatch.setattr("core.handlers._fs_base.get_tool_relay_env", lambda: {})
    handler.set_fs_resolver(lambda _svc_id: FakeRelay())

    out = handler.execute({"query": "AWS brand color", "max_results": 4})

    assert out == "Search results from relay"
    assert files == {}


def test_web_search_falls_back_to_local_when_relay_payload_cannot_import_core(monkeypatch):
    """The relay payload imports PawFlow's core package, which only exists when
    the relay workspace is the PawFlow repo. On a user-project relay the script
    dies with ModuleNotFoundError -- the handler must run the local provider
    chain instead of returning the traceback as the search result."""
    handler = WebSearchHandler()

    class BrokenRelay:
        def write_file(self, path, content):
            pass

        def exec(self, _path, _command, env=None):
            return {
                "stdout": "",
                "stderr": (
                    "Traceback (most recent call last):\n"
                    "  File \".pawflow_web_search_x.py\", line 2, in <module>\n"
                    "    from core.handlers.web_fetch import WebSearchHandler\n"
                    "ModuleNotFoundError: No module named 'core'"
                ),
                "exit_code": 1,
            }

        def delete_file(self, path):
            pass

    def fake_search(provider, query, max_results):
        return [{"title": "Local result", "url": "https://example.com",
                 "snippet": "from local fallback"}]

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers._fs_base.get_tool_relay_env", lambda: {})
    handler.set_fs_resolver(lambda _svc_id: BrokenRelay())

    out = handler.execute({"query": "quaternius cc0 assets"})

    assert "Local result" in out
    assert "ModuleNotFoundError" not in out


def test_web_search_falls_back_to_local_when_relay_exec_raises(monkeypatch):
    handler = WebSearchHandler()

    class DeadRelay:
        def write_file(self, path, content):
            raise RuntimeError("relay disconnected")

        def exec(self, _path, _command, env=None):
            raise AssertionError("unreachable")

        def delete_file(self, path):
            pass

    def fake_search(provider, query, max_results):
        return [{"title": "Local result", "url": "https://example.com",
                 "snippet": "from local fallback"}]

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers._fs_base.get_tool_relay_env", lambda: {})
    handler.set_fs_resolver(lambda _svc_id: DeadRelay())

    out = handler.execute({"query": "test"})

    assert "Local result" in out
    assert "Error executing web_search on relay" not in out


def test_web_search_deduplicates_results(monkeypatch):
    handler = WebSearchHandler()

    def fake_search(provider, query, max_results):
        return [{
            "title": f"Result from {provider}",
            "url": "https://www.example.com/page/?utm_source=x",
            "snippet": provider,
        }]

    monkeypatch.setattr(handler, "_search_provider", fake_search)
    monkeypatch.setattr("core.handlers.web_fetch.WebSearchHandler._find_default_relay", lambda self: None)

    out = handler.execute({"query": "test", "provider": "google,bing"})

    assert out.count("https://www.example.com/page/?utm_source=x") == 1
    assert "[google,bing]" in out or "[bing,google]" in out


def test_web_search_uses_server_search_cli_before_relay(monkeypatch):
    handler = WebSearchHandler()

    class SearchService:
        available = True
        fallback_to_free = True

        def search(self, query, **kwargs):
            assert query == "fast search"
            assert kwargs["providers"] == "brave"
            return {
                "results": [{
                    "title": "Server result",
                    "url": "https://example.com/server",
                    "snippet": "Executed by the PawFlow server image.",
                    "source": "brave",
                }],
                "metadata": {"elapsed_ms": 42},
            }

    monkeypatch.setattr(
        handler, "_resolve_search_service", lambda _arguments: (SearchService(), ""))
    handler.set_fs_resolver(
        lambda _service_id: (_ for _ in ()).throw(
            AssertionError("search-cli must not be routed through the relay")))

    out = handler.execute({
        "query": "fast search",
        "search_cli_providers": "brave",
    })

    assert "Server result" in out
    assert "search-cli, 42 ms" in out


def test_web_search_preserves_paid_backend_failure_note_after_relay_fallback(
    monkeypatch,
):
    handler = WebSearchHandler()

    class BrokenSearchService:
        available = True
        fallback_to_free = True

        def search(self, *_args, **_kwargs):
            raise RuntimeError("provider timeout")

    monkeypatch.setattr(
        handler,
        "_resolve_search_service",
        lambda _arguments: (BrokenSearchService(), ""),
    )
    monkeypatch.setattr(
        handler,
        "_execute_via_relay",
        lambda *_args: "Search results from relay",
    )

    out = handler.execute({"query": "fallback test"})

    assert out.startswith("Search results from relay")
    assert "search-cli unavailable (provider timeout)" in out
    assert "used PawFlow's no-key fallback" in out


def test_web_search_auto_selects_most_specific_service_scope(monkeypatch):
    from core.service_registry import ServiceRegistry

    handler = WebSearchHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv-1")
    definitions = [
        SimpleNamespace(
            service_id="aaa-global", scope="global", scope_id="global"),
        SimpleNamespace(
            service_id="bbb-user", scope="user", scope_id="alice"),
        SimpleNamespace(
            service_id="zzz-conv", scope="conv", scope_id="conv-1"),
    ]
    selected = object()

    class Registry:
        def resolve_by_type(self, service_type, **kwargs):
            assert service_type == "webSearchConnection"
            assert kwargs == {"user_id": "alice", "conv_id": "conv-1"}
            return definitions

        def get_live_instance(self, scope, scope_id, service_id):
            assert (scope, scope_id, service_id) == (
                "conv", "conv-1", "zzz-conv")
            return selected

    monkeypatch.setattr(
        ServiceRegistry, "get_instance", classmethod(lambda cls: Registry()))

    service, error = handler._resolve_search_service({})

    assert service is selected
    assert error == ""


def test_web_search_free_backend_returns_at_global_deadline(monkeypatch):
    handler = WebSearchHandler()
    release = threading.Event()

    def slow_search(_provider, _query, _max_results):
        release.wait(0.5)
        return []

    monkeypatch.setattr(handler, "_search_provider", slow_search)
    started = time.monotonic()
    attempts, results = handler._search_free_concurrently(
        ["bing", "duckduckgo", "google"], "test", 5, 0.03)
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.2
    assert results == []
    assert all("deadline exceeded" in attempt for attempt in attempts)


def test_web_search_renders_search_cli_answers_without_url_results():
    handler = WebSearchHandler()

    out = handler._render_cli_response("question", {
        "results": [],
        "answers": [{"provider": "perplexity_sonar", "text": "Grounded answer."}],
        "metadata": {"elapsed_ms": 12},
    })

    assert "Answer [perplexity_sonar]" in out
    assert "Grounded answer." in out


def test_web_search_orders_text_before_image_and_video():
    handler = WebSearchHandler()

    results = handler._dedupe_results([
        {"title": "Video", "url": "https://cdn.example.com/clip.mp4", "snippet": ""},
        {"title": "Platform Video", "url": "https://www.youtube.com/watch?v=abc", "snippet": ""},
        {"title": "Image", "url": "https://cdn.example.com/image.png", "snippet": ""},
        {"title": "Article", "url": "https://example.com/article", "snippet": ""},
        {"title": "Second Article", "url": "https://example.com/second", "snippet": ""},
    ], 10)

    assert [result["title"] for result in results] == [
        "Article",
        "Second Article",
        "Image",
        "Video",
        "Platform Video",
    ]


def test_web_search_uses_query_terms_for_text_result_ordering():
    handler = WebSearchHandler()

    results = handler._dedupe_results([
        {"title": "Unrelated support page", "url": "https://example.com/help", "snippet": "Generic download help."},
        {"title": "Windows accent palette", "url": "https://example.com/windows", "snippet": "Blue UI palette."},
    ], 10, query="Windows blue accent")

    assert [result["title"] for result in results] == [
        "Windows accent palette",
        "Unrelated support page",
    ]


def test_web_search_interleaves_contributing_providers():
    handler = WebSearchHandler()

    results = handler._dedupe_results([
        {"title": "Google first", "url": "https://google.example/one", "snippet": "needle", "provider": "google"},
        {"title": "Google second", "url": "https://google.example/two", "snippet": "needle", "provider": "google"},
        {"title": "Bing first", "url": "https://bing.example/one", "snippet": "needle", "provider": "bing"},
        {"title": "Bing second", "url": "https://bing.example/two", "snippet": "needle", "provider": "bing"},
    ], 4, query="needle")

    assert [result["title"] for result in results] == [
        "Google first",
        "Bing first",
        "Google second",
        "Bing second",
    ]


def test_web_search_provider_chain_resolves_pawflow_variable(monkeypatch):
    import core.expression

    handler = WebSearchHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    monkeypatch.delenv("PAWFLOW_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.delenv("PAWFLOW_WEB_SEARCH_PROVIDER", raising=False)

    seen = []

    def fake_resolve(template, owner="", conversation_id="", **_kwargs):
        seen.append((template, owner, conversation_id))
        if "web_search_providers" in template:
            return "bing"
        return ""

    monkeypatch.setattr(core.expression, "resolve_expression", fake_resolve)

    assert handler._provider_chain({}) == ["bing"]
    assert seen[0][0].startswith("${web_search_providers")
    assert seen[0][1:] == ("user-1", "conv-1")


def test_bing_rss_parser_extracts_results():
    handler = WebSearchHandler()
    rss = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel>
  <item>
    <title>Amazon Orange - #FF9900</title>
    <link>https://color-register.org/color/amazon-orange</link>
    <description>Amazon Orange uses hexadecimal code #FF9900.</description>
  </item>
</channel></rss>"""

    results = handler._parse_bing_rss(rss, 5)

    assert results == [{
        "title": "Amazon Orange - #FF9900",
        "url": "https://color-register.org/color/amazon-orange",
        "snippet": "Amazon Orange uses hexadecimal code #FF9900.",
    }]


def test_bing_html_parser_decodes_redirect_urls():
    handler = WebSearchHandler()
    html = """
<ol><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly93d3cuYnJhbmRjb2xvcmNvZGUuY29tL3dpbmRvd3MtMTE=">Windows 11 Brand Color Codes</a></h2>
<div class="b_caption"><p>Closest numbers based on official color codes.</p></div></li></ol>
"""

    results = handler._parse_bing_html(html, 5)

    assert results == [{
        "title": "Windows 11 Brand Color Codes",
        "url": "https://www.brandcolorcode.com/windows-11",
        "snippet": "Closest numbers based on official color codes.",
    }]


def test_google_html_parser_uses_parent_text_for_rich_results():
    handler = WebSearchHandler()
    html = """
<div><a href="https://www.brandcolorcode.com/windows-11"></a>
Windows 11 Brand Color Codes » BrandColorCode.com Table_title: Windows 11 brand hex, RGB, CMYK and Pantone color codes</div>
"""

    results = handler._parse_google_html(html, 5)

    assert results[0]["title"] == "Windows 11 Brand Color Codes » BrandColorCode.com"
    assert results[0]["url"] == "https://www.brandcolorcode.com/windows-11"


def test_google_stealth_retries_after_challenge_page(monkeypatch):
    handler = WebSearchHandler()
    html = """
<div><a href="https://www.brandcolorcode.com/amazon-web-services-aws"></a>
Amazon Web Services (AWS) Brand Color Codes Brand Color Code</div>
"""
    calls = []

    def fake_fetch(url, locale="en-US", timezone_id="America/New_York"):
        calls.append(url)
        return "" if len(calls) == 1 else html

    monkeypatch.setattr(handler, "_fetch_search_browser", fake_fetch)

    results = handler._search_google_stealth("AWS brand color orange hex FF9900 official", 4)

    assert len(calls) == 2
    assert results[0]["url"] == "https://www.brandcolorcode.com/amazon-web-services-aws"


def test_google_browser_fallback_uses_system_chromium():
    src = inspect.getsource(WebSearchHandler._fetch_search_browser)

    assert "PAWFLOW_CHROMIUM_EXECUTABLE" in src
    assert "shutil.which(\"chromium\")" in src
