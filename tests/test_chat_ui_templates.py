"""Chat page rendering (docs/CHAT_UI_TEMPLATES.md).

The page is rendered by Jinja from tasks/io/chat_ui/templates/; the DOM
contract (ids, PFP slot hosts, i18n keys) is pinned to a snapshot taken from
the former monolithic template.html so that splitting the skeleton into
partials never drops a hook the JS modules rely on.
"""

import hashlib
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
    assert [mod for mod, _ in tags] == [
        m for m in _JS_MODULES
        if (CHAT_UI_DIR / m).exists() and m != "plans_panel.js"
    ]
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
    assert html.index('<style id="custom-theme">a{}</style>') < html.index(
        'id="component-contract-css"'
    ) < html.index("</head>")
    assert html.index("window.PAWFLOW_EXTENSIONS=[]") < html.index('<script defer src="/chat/js/i18n.js')
    # Custom CSS in its own <style> after the CSS modules, </style> neutralised.
    assert ('<style id="custom-css">\n/* Custom theme */\n'
            '.c{color:red}<\\/style><script>x()</script>\n</style>') in html
    # (the helper inlined the modules: the last one precedes the custom CSS)
    assert (html.rindex('data-css-module=') < html.index('id="custom-css"')
            < html.index('id="custom-theme"')
            < html.index('id="component-contract-css"'))
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
    assert (html.index('/chat/js/css/99_theme_bridge.css')
            < html.index("github-dark.min.css")
            < html.index('id="custom-theme"')
            < html.index('id="component-contract-css"'))
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
    # Only the component-contract stylesheet lives in the skeleton itself;
    # the workspace partial owns every conversation-stage wrapper.
    assert _ids(skeleton) == ["component-contract-css"]
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
    assert '  </div>\n</div>\n</div><!-- /main -->' in rendered_chat_html()


# ── PFP ui.v1 template fragments (docs/CHAT_UI_TEMPLATES.md) ───────────────

def _fragment_record(root, package, fragments, *, bad_digest=False):
    """An installed-extension record (as list_installed_ui_extensions returns
    it) whose content_dir holds the given (slot, file name, text) fragments."""
    content = Path(root) / "pkg"
    assets = [{"kind": "script", "path": "content/ui/extension.js",
               "sha256": "sha256:" + "a" * 64, "size": 1}]
    for slot, name, text in fragments:
        path = content / "content" / "ui" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        digest = "0" * 64 if bad_digest else hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append({"kind": "template", "path": "content/ui/" + name,
                       "sha256": "sha256:" + digest, "size": path.stat().st_size,
                       "slot": slot})
    return {"package": package, "version": "1.0.0", "scope": "user",
            "content_dir": str(content), "version_compat": "ui.v1",
            "assets": assets, "slots": [], "hooks": [], "i18n": {}}


def test_template_fragments_render_verbatim_into_hosts_and_page_points(tmp_path):
    from tasks.io.serve_chat_ui import _fragment_cache, _initial_extensions_block, _template_fragments
    _fragment_cache.clear()
    rec = _fragment_record(tmp_path, "examples.stage", [
        ("conversation_stage", "stage.html", '<section id="stage">{{ 7*7 }}</section>'),
        ("head", "preload.html", '<link rel="preload" href="/x.glb" as="fetch">'),
        ("body_end", "tpl.html", '<template id="stage-tpl"><b>x</b></template>'),
        ("sidebar_top", "top.html", '<em class="top">hi</em>'),
    ])
    slots = _template_fragments([rec])
    assert set(slots) == {"conversation_stage", "head", "body_end", "sidebar_top"}
    html = rendered_chat_html(
        template_slots=slots,
        extensions_block=_initial_extensions_block(user_id="alice", records=[rec]))
    # Verbatim (never compiled as a template) and wrapped for teardown; the
    # conditional host that received a fragment is visible, the others not.
    assert ('<div data-pf-slot="conversation_stage_ext"><div data-pf-ext="examples.stage" '
            'data-pf-template-slot="conversation_stage"><section id="stage">{{ 7*7 }}</section></div></div>') in html
    assert 'data-pf-slot="composer_accessory_ext" hidden>' in html
    assert 'data-pf-slot="resources_collection_ext" hidden>' in html
    assert ('<div class="sidebar-settings" data-pf-slot="sidebar_top_ext" style="order:-1">'
            '<div data-pf-ext="examples.stage" data-pf-template-slot="sidebar_top"><em class="top">hi</em></div></div>') in html
    # head: comment-bracketed just before </head>; body_end: after the
    # extension hosts and before the boot config script.
    assert ('<!-- pf-ext:examples.stage:head -->\n<link rel="preload" href="/x.glb" as="fetch">\n'
            '<!-- /pf-ext:examples.stage -->\n</head>') in html
    assert html.index('id="pf-ext-panel-host"') < html.index('<template id="stage-tpl">') < html.index("const AGENT_PATH")
    # The boot manifest lists the fragments without a URL.
    manifest = json.loads(html.split("window.PAWFLOW_EXTENSIONS=")[1].split(";</script>")[0])
    entry = manifest[0]
    assert [asset["path"] for asset in entry["assets"]] == ["content/ui/extension.js"]
    assert entry["templates"] == [
        {"slot": "conversation_stage", "path": "content/ui/stage.html"},
        {"slot": "head", "path": "content/ui/preload.html"},
        {"slot": "body_end", "path": "content/ui/tpl.html"},
        {"slot": "sidebar_top", "path": "content/ui/top.html"},
    ]


def test_template_fragments_skip_tampered_oversize_escaping_or_missing_files(tmp_path):
    from tasks.io.serve_chat_ui import _fragment_cache, _template_fragments
    _fragment_cache.clear()
    tampered = _fragment_record(tmp_path / "a", "examples.a", [("tab_bar", "t.html", "<i>x</i>")], bad_digest=True)
    assert _template_fragments([tampered]) == {}
    oversize = _fragment_record(tmp_path / "b", "examples.b", [("tab_bar", "t.html", "x" * (64 * 1024 + 1))])
    assert _template_fragments([oversize]) == {}
    escaping = _fragment_record(tmp_path / "c", "examples.c", [("tab_bar", "t.html", "<i>x</i>")])
    escaping["assets"][1]["path"] = "../../t.html"
    assert _template_fragments([escaping]) == {}
    missing = _fragment_record(tmp_path / "d", "examples.d", [("tab_bar", "t.html", "<i>x</i>")])
    Path(missing["content_dir"], "content/ui/t.html").unlink()
    assert _template_fragments([missing]) == {}
    no_slot = _fragment_record(tmp_path / "e", "examples.e", [("", "t.html", "<i>x</i>")])
    assert _template_fragments([no_slot]) == {}
    # The package id is attribute-escaped in the wrapper.
    quoted = _fragment_record(tmp_path / "f", 'x"y', [("tab_bar", "t.html", "<i>x</i>")])
    assert _template_fragments([quoted])["tab_bar"] == [
        '<div data-pf-ext="x&#34;y" data-pf-template-slot="tab_bar"><i>x</i></div>']


def test_serve_chat_ui_task_renders_fragments_only_for_enabled_records(tmp_path, monkeypatch):
    from core import FlowFile
    from tasks.io.serve_chat_ui import ServeChatUITask, _fragment_cache
    _fragment_cache.clear()
    rec = _fragment_record(tmp_path, "examples.stage",
                           [("conversation_stage", "stage.html", '<section id="stage"></section>')])
    monkeypatch.setattr("tasks.io.serve_chat_ui._enabled_ui_extension_records",
                        lambda user_id="", conversation_id="": [rec] if user_id == "alice" else [])
    task = ServeChatUITask({})
    ff = FlowFile(content=b"")
    ff.set_attribute("http.auth.principal", "alice")
    html = task.execute(ff)[0].get_content().decode("utf-8")
    assert 'data-pf-template-slot="conversation_stage"><section id="stage"></section></div>' in html
    assert '"package": "examples.stage"' in html
    other = task.execute(FlowFile(content=b""))[0].get_content().decode("utf-8")
    assert "data-pf-template-slot" not in other
    assert 'data-pf-slot="conversation_stage_ext" hidden>' in other


def test_every_template_slot_has_an_extension_point_in_the_partials():
    from core.pfp_package import _UI_KNOWN_SLOTS, _UI_TEMPLATE_SLOTS
    sources = {path.relative_to(TEMPLATES_DIR).as_posix(): path.read_text(encoding="utf-8")
               for path in TEMPLATES_DIR.rglob("*.html")}
    joined = "\n".join(sources.values())
    for slot in _UI_TEMPLATE_SLOTS:
        assert joined.count("{{ ext_fragments('" + slot + "')|safe }}") == 1, slot
    for slot in _UI_KNOWN_SLOTS:
        assert 'data-pf-slot="' + slot + '_ext"' in joined, slot
    for slot in ("conversation_stage", "resources_collection", "composer_accessory"):
        assert "{{ ext_hidden('" + slot + "') }}" in joined, slot
    # No newline of their own: an empty page point must leave the page
    # byte-identical to one with no extension installed.
    assert "{{ ext_fragments('head')|safe }}</head>" in sources["chat.html"]
    assert "{{ ext_fragments('body_end')|safe }}{% include \"boot/config.html\" %}" in sources["chat.html"]
