"""Chromium startup gates using actual deferred vendors, i18n and lazy UI code."""

import json
import mimetypes
import re
from urllib.parse import urlsplit

import pytest

from tasks.io import serve_chat_ui as ui
from test_webchat_motion_browser import chromium_browser  # noqa: F401


@pytest.fixture
def startup_page(chromium_browser):
    contexts = []

    def create(*, language='en', server_language=None, stored='', full=False, init=''):
        context = chromium_browser.new_context(locale=language)
        contexts.append(context)
        requests, errors = [], []
        html = ui.render_chat_page(language=server_language or language)
        if not full:
            keep = {'i18n.js', 'startup_optional.js', 'rxbus.js', 'usage_cost.js'}
            html = re.sub(
                r'<script defer src="/chat/js/([^"?]+)[^>]*></script>',
                lambda match: match[0] if match[1] in keep or match[1].startswith('vendor/') else '',
                html,
            )

        def route(request):
            path = urlsplit(request.request.url).path
            requests.append(path)
            if path == '/chat':
                request.fulfill(status=200, content_type='text/html', body=html)
            elif path.startswith('/chat/js/'):
                file = ui._CHAT_UI_DIR / path.removeprefix('/chat/js/')
                request.fulfill(status=200, content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream',
                                body=file.read_bytes())
            else:
                request.fulfill(status=200, content_type='application/json', body='{}')

        context.route('**/*', route)
        context.add_init_script("""
            window.__actions = [];
            window.__startupErrors = [];
            window.addMsg = (role, text) => window.__startupErrors.push(text);
            window.escapeHtml = value => String(value);
            window.escapeAttr = value => String(value);
            window.conversationId = '';
            window.EventSource = class extends EventTarget {
              static OPEN = 1;
              readyState = 1;
              close() {}
            };
            const nativeOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url, async = true, ...rest) {
              if (async === false) throw new Error('Synchronous XHR is forbidden');
              return nativeOpen.call(this, method, url, async, ...rest);
            };
        """ + ('localStorage.setItem("pawflow.language", ' + json.dumps(stored) + ');' if stored else '') + init)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://startup.test/chat', wait_until='domcontentloaded')
        page.evaluate('window.PAWFLOW_I18N_READY')
        if not full:
            page.evaluate("""
                window.action$ = (action, args) => ({
                  subscribe: callback => {
                    __actions.push(action);
                    callback(action === 'usage_dashboard'
                      ? {kpis: {}, timeseries: [], top_conversations: [], top_agents: []}
                      : {budgets: []});
                  }
                });
            """)
        return page, requests, errors

    yield create
    for context in contexts:
        context.close()


def test_deferred_real_vendors_before_consumers_and_selected_locale(startup_page):
    page, requests, errors = startup_page(language='fr')
    assert page.evaluate('typeof rxjs.ReplaySubject') == 'function'
    assert page.evaluate('typeof _commandResult$.subscribe') == 'function'
    assert page.evaluate('hljs.highlight("const answer = 42;", {language: "javascript"}).value').count('hljs-') > 0
    assert page.evaluate('getLanguage()') == 'fr'
    assert page.evaluate('t("ready")') != 'ready'
    assert page.locator('html').get_attribute('lang') == 'fr'
    assert not any(path.endswith('/fr.json') for path in requests)
    assert not any(path.endswith('usage_dashboard.js') for path in requests)
    assert errors == []


def test_stored_locale_migration_fetches_only_selected_and_keeps_cookie(startup_page):
    page, requests, errors = startup_page(language='en', stored='es')
    assert page.evaluate('getLanguage()') == 'es'
    assert requests.count('/chat/js/i18n/es.json') == 1
    assert '/chat/js/i18n/fr.json' not in requests
    assert 'pawflow_language=es' in page.evaluate('document.cookie')
    assert errors == []


@pytest.mark.parametrize('unavailable', [False, True])
def test_server_cookie_preference_survives_without_local_storage(startup_page, unavailable):
    init = ("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});"
            if unavailable else '')
    page, requests, errors = startup_page(language='en', server_language='fr', init=init)
    assert page.evaluate('navigator.language') == 'en'
    assert page.evaluate('getLanguage()') == 'fr'
    assert 'pawflow_language=fr' in page.evaluate('document.cookie')
    assert not any(path.endswith('.json') and '/i18n/' in path for path in requests)
    assert errors == []


def test_early_user_selection_cancels_stored_locale_migration(startup_page):
    page, _, errors = startup_page(stored='fr', init="""
        let ready;
        Object.defineProperty(window, 'PAWFLOW_I18N_READY', {
          get() { return ready; },
          set(value) { ready = value; queueMicrotask(() => setLanguage('en')); }
        });
    """)
    assert page.evaluate('getLanguage()') == 'en'
    assert page.evaluate("localStorage.getItem('pawflow.language')") == 'en'
    assert errors == []


def test_async_locale_races_deduplicate_cache_and_cancel_to_current(startup_page):
    page, _, errors = startup_page()
    page.evaluate("""
        window.__catalogFetches = [];
        window.__catalogReplies = {};
        window.fetch = url => {
          __catalogFetches.push(url);
          return new Promise(resolve => { __catalogReplies[url] = resolve; });
        };
        window.__fr1 = setLanguage('fr');
        window.__fr2 = setLanguage('fr');
        window.__es = setLanguage('es');
        void 0;
    """)
    assert page.evaluate('__catalogFetches.length') == 2
    page.evaluate("""
        __catalogReplies[PAWFLOW_I18N_URLS.es]({ok:true, json:async()=>({ready:'Listo'})});
    """)
    assert page.evaluate('__es') is True
    page.evaluate("""
        __catalogReplies[PAWFLOW_I18N_URLS.fr]({ok:true, json:async()=>({ready:'Prêt'})});
    """)
    assert page.evaluate('Promise.all([__fr1, __fr2])') == [False, False]
    assert page.evaluate('getLanguage()') == 'es'
    assert page.evaluate("setLanguage('fr')") is True
    assert page.evaluate('__catalogFetches.length') == 2
    assert page.evaluate('t("ready")') == 'Prêt'
    assert page.evaluate('t("pageTitle")') == 'PawFlow Agent Chat'
    page.evaluate("""
        delete PAWFLOW_I18N_CATALOGS.es;
        window.__pendingEs = setLanguage('es');
        window.__stayFr = setLanguage('fr');
        __catalogReplies[PAWFLOW_I18N_URLS.es]({ok:true, json:async()=>({ready:'Listo'})});
    """)
    assert page.evaluate('__pendingEs') is False
    assert page.evaluate('getLanguage()') == 'fr'
    assert page.evaluate("localStorage.getItem('pawflow.language')") == 'fr'
    assert errors == []


@pytest.mark.parametrize('failure', [
    'Promise.reject(new Error("offline"))',
    'Promise.resolve({ok:false})',
    'Promise.resolve({ok:true,json:async()=>[]})',
])
def test_locale_failure_retains_current_selection_and_allows_retry(startup_page, failure):
    page, _, errors = startup_page()
    page.evaluate('window.fetch = () => ' + failure + '; void 0;')
    assert page.evaluate("setLanguage('fr')") is False
    assert page.evaluate('getLanguage()') == 'en'
    assert page.locator('#languageSelect').input_value() == 'en'
    page.evaluate("window.fetch = async () => ({ok:true,json:async()=>({ready:'Prêt'})}); void 0;")
    assert page.evaluate("setLanguage('fr')") is True
    assert page.evaluate('t("ready")') == 'Prêt'
    assert errors == []


def test_optional_dashboard_clicks_retry_once_then_reopen_without_download(startup_page):
    page, requests, errors = startup_page()
    failures = []

    def fail_once(route):
        failures.append(route.request.url)
        route.abort()

    page.route('**/usage_dashboard.js?*', fail_once)
    assert page.evaluate('Promise.all([showUsageDashboard(), showUsageDashboard()])') == [False, False]
    assert len(failures) == 1
    assert page.locator('#usageDashOverlay').count() == 0
    assert page.evaluate('__startupErrors.length') == 1
    page.unroute('**/usage_dashboard.js?*', fail_once)
    page.evaluate('Promise.all([showUsageDashboard(), showUsageDashboard()])')
    assert page.locator('#usageDashOverlay').count() == 1
    assert requests.count('/chat/js/usage_dashboard.js') == 1
    assert page.evaluate('__actions.filter(a => a === "usage_dashboard").length') == 1
    page.evaluate("""
        window.eventSource = new EventTarget();
        _usageWireSSE();
        eventSource.dispatchEvent(new Event('budget.updated'));
        closeUsageDashboard();
        eventSource.dispatchEvent(new Event('budget.updated'));
        showUsageDashboard();
    """)
    assert page.locator('#usageDashOverlay').count() == 1
    assert requests.count('/chat/js/usage_dashboard.js') == 1
    assert page.evaluate('__actions.filter(a => a === "budget_list").length') == 3
    assert errors == []


def test_optional_parse_failure_uses_existing_reload_recovery(startup_page):
    page, _, _ = startup_page()
    page.evaluate("window.__reloads = 0; window.__pawflowAssetLoadFailed = () => __reloads++; void 0;")
    page.route('**/usage_dashboard.js?*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body='function broken('))
    assert page.evaluate('showUsageDashboard()') is False
    assert page.evaluate('__reloads') == 1
    assert page.locator('#usageDashOverlay').count() == 0


def test_entire_shipped_classic_script_chain_boots_with_local_vendors(startup_page):
    page, requests, errors = startup_page(full=True)
    assert page.evaluate('typeof connectSSE') == 'function'
    assert page.evaluate('typeof showUsageDashboard') == 'function'
    assert not any(path.endswith('usage_dashboard.js') for path in requests)
    assert errors == []
