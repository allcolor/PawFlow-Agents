"""Chat page rendering (docs/CHAT_UI_TEMPLATES.md).

The page is rendered by Jinja from tasks/io/chat_ui/templates/; the DOM
contract (ids, PFP slot hosts, i18n keys) is pinned to a snapshot taken from
the former monolithic template.html so that splitting the skeleton into
partials never drops a hook the JS modules rely on.
"""

import json
import re
from pathlib import Path

from chat_ui_testing import CHAT_UI_DIR, CSS_DIR, TEMPLATES_DIR, chat_ui_css, rendered_chat_html

SNAPSHOT = json.loads(
    (Path(__file__).parent / "fixtures" / "chat_ui_dom_snapshot.json").read_text(encoding="utf-8"))


def _ids(html):
    return sorted(set(re.findall(r'\sid="([^"]+)"', html)))


def _slots(html):
    return sorted(set(re.findall(r'data-pf-slot="([^"]+)"', html)))


def _i18n_keys(html):
    return sorted(set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html)))


def test_rendered_page_keeps_the_dom_contract_of_the_monolithic_template():
    html = rendered_chat_html()
    assert _ids(html) == SNAPSHOT["ids"]
    assert _slots(html) == SNAPSHOT["slots"]
    assert _i18n_keys(html) == SNAPSHOT["i18n_keys"]


def test_boot_scripts_follow_js_modules_order_with_one_asset_version():
    from tasks.io.serve_chat_ui import _JS_MODULES
    html = rendered_chat_html()
    tags = re.findall(
        r'<script defer src="/chat/js/([^"?]+)\?v=([0-9a-f]{8})"'
        r' onerror="window.__pawflowAssetLoadFailed\(\)"></script>', html)
    assert [mod for mod, _ in tags] == [m for m in _JS_MODULES if (CHAT_UI_DIR / m).exists()]
    assert len({version for _, version in tags}) == 1
    assert "window.PAWFLOW_ASSET_VERSION=" in html
    assert "window.PAWFLOW_I18N_CATALOGS=" in html
    assert "window.PAWFLOW_EXTENSIONS=" in html


def test_no_replace_markers_survive():
    html = rendered_chat_html()
    for marker in ("JS_PLACEHOLDER", "{{AGENT_PATH}}", "{{SSE_PATH}}", "{{LOGIN_URL}}",
                   "__PAWFLOW_EXTENSIONS_PLACEHOLDER__", "{% ", "{{ "):
        assert marker not in html, marker
    assert not (CHAT_UI_DIR / "template.html").exists()


def test_server_values_are_json_encoded_and_blocks_land_where_they_did():
    html = rendered_chat_html(
        agent_path='/x"y</script>', sse_path="/sse", login_url="/login?next=<a>",
        theme_block='<style id="custom-theme">a{}</style>',
        extensions_block="<script>window.PAWFLOW_EXTENSIONS=[];</script>",
        custom_css=".c{color:red}</style><script>x()</script>")
    # Paths are JSON in <script>: quotes escaped, angle brackets neutralised.
    assert 'const AGENT_PATH = "/x\\"y\\u003c/script\\u003e";' in html
    assert 'const SSE_URL = window.location.origin + "/sse";' in html
    assert 'const LOGIN_URL = "/login?next=\\u003ca\\u003e";' in html
    # Theme block just before </head>; extension manifest before the modules.
    assert '<style id="custom-theme">a{}</style>\n</head>' in html
    assert html.index("window.PAWFLOW_EXTENSIONS=[]") < html.index('<script defer src="/chat/js/i18n.js')
    # Custom CSS in its own <style> after the CSS modules, </style> neutralised.
    assert ('<style id="custom-css">\n/* Custom theme */\n'
            '.c{color:red}<\\/style><script>x()</script>\n</style>') in html
    # (the helper inlined the modules: the last one precedes the custom CSS)
    assert html.rindex('data-css-module=') < html.index('id="custom-css"') < html.index('id="custom-theme"')
    assert 'id="custom-css"' not in rendered_chat_html()


def test_css_modules_are_linked_in_cascade_order_then_custom_css_then_theme():
    from tasks.io.serve_chat_ui import _CSS_MODULES
    html = rendered_chat_html(inline_css=False, theme_block='<style id="custom-theme">a{}</style>')
    links = re.findall(r'<link rel="stylesheet" href="/chat/js/css/([^"?]+)\?v=([0-9a-f]{8})">', html)
    assert [name for name, _ in links] == list(_CSS_MODULES)
    assert len({version for _, version in links}) == 1
    assert _CSS_MODULES[-1] == "99_theme_bridge.css" and "30_mobile.css" in _CSS_MODULES
    # Served page carries no inline stylesheet of its own any more; the
    # highlight.js theme and the custom theme come after the modules.
    assert "<style>" not in html
    assert html.rindex('href="/chat/js/css/') < html.index("github-dark.min.css") < html.index('id="custom-theme"')
    for module in _CSS_MODULES:
        assert (CSS_DIR / module).is_file(), module
        assert len(chat_ui_css(module).splitlines()) <= 300, module
    # The test helper inlines the modules where their <link> stood.
    inlined = rendered_chat_html()
    assert '<style data-css-module="00_base.css">' in inlined
    assert "--pf-bg" in inlined and "/* Theme variable bridge" in inlined


def test_extensions_manifest_defaults_to_the_empty_boot_block():
    html = rendered_chat_html()
    assert "window.PAWFLOW_EXTENSION_CONTEXT=" in html
    assert "window.PAWFLOW_EXTENSIONS=[]" in html


def test_asset_signature_covers_every_template_and_css_module():
    from tasks.io.serve_chat_ui import _CSS_DIR, _CSS_MODULES, _asset_signature
    names = {item[0] for item in _asset_signature()}
    templates = list(TEMPLATES_DIR.rglob("*.html"))
    assert templates
    for path in templates:
        assert "templates/" + path.relative_to(TEMPLATES_DIR).as_posix() in names
    for css in _CSS_MODULES:
        assert (_CSS_DIR / css).is_file()
        assert "css/" + css in names
    assert any(name.startswith("i18n/") for name in names)


def test_css_modules_are_served_by_serve_assets_as_text_css():
    from tasks import register_all_tasks
    register_all_tasks()
    from core import FlowFile
    from tasks.io.serve_assets import ServeAssetsTask
    from tasks.io.serve_chat_ui import _CSS_MODULES
    task = ServeAssetsTask({"assets_prefix": "chat_ui"})
    for module in _CSS_MODULES:
        ff = FlowFile(content=b"")
        ff.set_attribute("http.path.path", "css/" + module)
        out = task.execute(ff)[0]
        assert out.get_attribute("http.response.status") == "200", module
        assert out.get_attribute("http.response.header.Content-Type") == "text/css"
        assert out.get_content().decode("utf-8") == chat_ui_css(module)


def test_skeleton_only_includes_and_partials_stay_small():
    skeleton = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    assert len(skeleton.splitlines()) <= 60
    includes = re.findall(r'{% include "([^"]+)" %}', skeleton)
    assert len(includes) >= 12
    for name in includes:
        assert (TEMPLATES_DIR / name).is_file(), name
    # No element with an id lives in the skeleton itself except the tab
    # content wrapper whose children are the includes.
    assert _ids(skeleton) == ["tabContentChat"]
    for path in TEMPLATES_DIR.rglob("*.html"):
        rel = path.relative_to(TEMPLATES_DIR).as_posix()
        if rel == "chat.html":
            continue
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 300, rel


def test_environment_is_strict_and_autoescaping():
    from jinja2 import StrictUndefined
    from tasks.io.serve_chat_ui import _env
    assert _env.undefined is StrictUndefined
    assert _env.autoescape is True
    assert _env.auto_reload is True
    assert _env.trim_blocks is True and _env.lstrip_blocks is True
    # An include keeps its final newline: the composer's closing tags stay on
    # their own lines exactly as in the monolithic page.
    assert _env.keep_trailing_newline is True
    assert '  </div>\n</div>\n</div><!-- /tab-content chat -->' in rendered_chat_html()
