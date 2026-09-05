"""Real browser regressions for header popover placement and pointer routing."""

import mimetypes
import re
from urllib.parse import urlsplit

import pytest

from chat_ui_testing import rendered_chat_html
from tasks.io import serve_chat_ui as ui
from test_webchat_motion_browser import CHAT_UI, _shell_html, chromium_browser  # noqa: F401


HEADER_SCRIPTS = {"ui_motion.js", "ui_floating_layer.js", "state.js", "tooltips.js"}


@pytest.fixture
def header_page(chromium_browser):
    contexts = []

    def create(width=1280, atmosphere=False):
        context = chromium_browser.new_context(
            viewport={"width": width, "height": 800}, reduced_motion="reduce",
        )
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(1500)
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate("""
            () => {
              window.LOGIN_URL = '';
              const values = new Map();
              Object.defineProperty(window, 'localStorage', {
                configurable: true,
                value: {
                  getItem: key => values.get(key) || null,
                  setItem: (key, value) => values.set(key, String(value)),
                },
              });
              window.__clicks = [];
              document.getElementById('activeRows').innerHTML = [
                '<div class="active-row"><span class="a-name">assistant</span>',
                '<span class="a-status">iter 1 · 1 tools · [codex_native_commandExecution]</span>',
                '<span class="a-time">22s</span><span class="a-actions">',
                '<button id="agent-action" type="button">Pause</button></span></div>',
                '<div class="active-row"><span class="a-name">claude</span>',
                '<span class="a-status">running</span><span class="a-time">3m33s</span></div>'
              ].join('');
              document.getElementById('agent-action').onclick = () => __clicks.push('agent');
              document.getElementById('sidebarToggle').onclick = () => __clicks.push('sidebar');
              document.getElementById('input').disabled = false;
            }
        """)
        for script in ui._JS_MODULES:
            if script not in HEADER_SCRIPTS:
                continue
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate(
            "enabled => document.documentElement.dataset.pfAtmosphere = enabled ? 'on' : 'off'",
            atmosphere,
        )
        return page

    yield create
    for context in contexts:
        context.close()


def test_header_first_callable_click_waits_for_delayed_controller(chromium_browser):
    # Keep the actual rendered defer tags and their production order. A load
    # listener clicks at the first opportunity after state exposes its handler.
    tags = [
        match[0] for match in re.finditer(
            r'<script defer src="/chat/js/([^"?]+)[^>]*></script>',
            ui.render_chat_page(),
        ) if match[1] in HEADER_SCRIPTS
    ]
    # Remove comments first: the boot comment itself contains a <script> tag.
    html = re.sub(r"<!--.*?-->", "", rendered_chat_html(), flags=re.DOTALL)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = html.replace("</body>", "".join(tags) + "</body>")
    modules = [name for name in ui._JS_MODULES if name in HEADER_SCRIPTS]
    before_controller = modules[:modules.index("ui_floating_layer.js")]
    context = chromium_browser.new_context(reduced_motion="reduce")
    held, errors = [], []

    def route(request):
        path = urlsplit(request.request.url).path
        if path == "/chat":
            request.fulfill(content_type="text/html", body=html)
        elif path == "/chat/js/ui_floating_layer.js":
            held.append(request)
        elif path.startswith("/chat/js/"):
            file = CHAT_UI / path.removeprefix("/chat/js/")
            request.fulfill(
                content_type=mimetypes.guess_type(file)[0] or "application/octet-stream",
                body=file.read_bytes(),
            )
        else:
            request.fulfill(status=204)

    try:
        context.route("**/*", route)
        context.add_init_script("""
            window.LOGIN_URL = '';
            window.__headerLoaded = [];
            window.__headerClickDuringLoading = false;
            document.addEventListener('load', event => {
              if (event.target.tagName !== 'SCRIPT') return;
              const name = new URL(event.target.src).pathname.split('/').pop();
              __headerLoaded.push(name);
              if (name === 'state.js') {
                __headerClickDuringLoading = document.readyState !== 'complete';
                document.getElementById('activeAgentsBtn').click();
              }
            }, true);
        """)
        page = context.new_page()
        page.set_default_timeout(3000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        with page.expect_request("**/ui_floating_layer.js?*"):
            page.goto("http://header.test/chat", wait_until="commit")
        page.wait_for_function("document.readyState === 'interactive'")
        page.wait_for_function(
            "names => names.every(name => __headerLoaded.includes(name))",
            arg=before_controller,
        )
        assert len(held) == 1
        assert page.evaluate("typeof window.pfFloatingLayer") == "undefined"
        held[0].fulfill(
            content_type="application/javascript",
            body=(CHAT_UI / "ui_floating_layer.js").read_bytes(),
        )
        page.wait_for_load_state("domcontentloaded")
        assert page.evaluate("__headerClickDuringLoading")
        assert errors == []
        page.wait_for_function("""
            () => {
              const r = document.getElementById('activeAgentsPop').getBoundingClientRect();
              return r.width > 0 && r.left >= 8 && r.right <= innerWidth - 8
                && r.top >= document.getElementById('headerBar').getBoundingClientRect().bottom
                && r.bottom <= innerHeight - 8;
            }
        """)
        trigger = page.locator("#activeAgentsBtn")
        assert trigger.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert trigger.get_attribute("aria-expanded") == "false"
        assert not page.locator("#activeAgentsPop").is_visible()
        assert trigger.evaluate("el => el === document.activeElement")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("atmosphere", [False, True])
def test_active_agents_popover_fits_viewport_and_buttons_receive_clicks(
        header_page, width, atmosphere):
    page = header_page(width, atmosphere)
    page.locator("#activeAgentsBtn").click()
    page.wait_for_function("""
        () => {
          const pop = document.getElementById('activeAgentsPop');
          const r = pop.getBoundingClientRect();
          return r.width > 0 && r.left >= 0 && r.right <= innerWidth
            && r.top >= document.getElementById('headerBar').getBoundingClientRect().bottom
            && r.bottom <= innerHeight;
        }
    """)
    page.locator("#agent-action").click()
    assert page.evaluate("__clicks") == ["agent"]
    assert page.locator("#activeAgentsBtn").get_attribute("aria-expanded") == "true"
    page.locator("#input").click()
    page.keyboard.type("still clickable")
    assert page.locator("#input").input_value() == "still clickable"
    assert page.locator("#activeAgentsBtn").get_attribute("aria-expanded") == "false"


@pytest.mark.parametrize("width", [390, 1280])
def test_open_popover_does_not_cover_sidebar_hit_target(header_page, width):
    page = header_page(width, True)
    page.locator("#activeAgentsBtn").click()
    page.locator("#sidebarToggle").click()
    assert page.evaluate("__clicks") == ["sidebar"]


def test_header_popovers_close_on_escape_resize_and_header_collapse(header_page):
    page = header_page()
    trigger = page.locator("#activeAgentsBtn")
    trigger.click()
    page.keyboard.press("Escape")
    assert trigger.get_attribute("aria-expanded") == "false"
    assert trigger.evaluate("el => el === document.activeElement")
    trigger.click()
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_function("document.getElementById('activeAgentsBtn').getAttribute('aria-expanded') === 'false'")
    trigger.click()
    page.evaluate("toggleHeaderBar()")
    page.wait_for_function("document.getElementById('activeAgentsBtn').getAttribute('aria-expanded') === 'false'")
    assert not page.locator("#activeAgentsPop").is_visible()


def test_popover_replacement_and_live_rows_keep_controls_on_small_screen(header_page):
    page = header_page(320, True)
    page.set_viewport_size({"width": 320, "height": 260})
    page.locator("#activeAgentsBtn").click()
    page.evaluate("""
        () => {
          const rows = document.getElementById('activeRows');
          const row = rows.firstElementChild;
          row.querySelector('.a-name').textContent = 'agent_' + 'x'.repeat(160);
          for (let i = 0; i < 10; i++) {
            const copy = row.cloneNode(true);
            copy.querySelector('button').removeAttribute('id');
            rows.appendChild(copy);
          }
        }
    """)
    page.wait_for_function("""
        () => {
          const pop = document.getElementById('activeAgentsPop');
          const rect = pop.getBoundingClientRect();
          return rect.left >= 8 && rect.right <= innerWidth - 8
            && rect.bottom <= innerHeight - 8 && pop.scrollWidth <= pop.clientWidth;
        }
    """)
    page.locator("#agent-action").click()
    assert page.evaluate("__clicks") == ["agent"]
    page.locator("#actionStatusBtn").click()
    assert page.locator("#activeAgentsBtn").get_attribute("aria-expanded") == "false"
    assert page.locator(".hdr-pop.open").count() == 1
    page.locator("#actionStatusBtn").click()
    assert page.locator(".hdr-pop.open").count() == 0


def test_header_scroll_dismisses_popover_without_leaving_click_interception(header_page):
    page = header_page(320, True)
    page.evaluate("""
        () => {
          const spacer = document.createElement('span');
          spacer.style.minWidth = '500px';
          document.getElementById('headerBar').appendChild(spacer);
        }
    """)
    page.locator("#activeAgentsBtn").click()
    page.locator("#headerBar").evaluate("el => el.scrollLeft = 100")
    page.wait_for_function("document.querySelectorAll('.hdr-pop.open').length === 0")
    page.locator("#sidebarToggle").click()
    assert page.evaluate("__clicks") == ["sidebar"]
