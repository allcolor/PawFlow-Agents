# Chat UI Template Modularisation Plan

Status: **approved for implementation** (reviewed 2026-08-21). Answers to
the review questions of section 9: Q1 external `<link>` CSS modules; Q2 the
12 template slots of decision 6; Q3 the `rendered_chat_html()` test helper;
Q4 all work packages, in order.

## 1. Outcome

`tasks/io/chat_ui/template.html` stops being one monolithic file. The chat
page becomes a Jinja2 skeleton that only *includes* small, single-purpose
partials, and that skeleton exposes named **server-side extension points**
that installed `.pfp` UI extensions can fill with markup, in addition to the
existing browser-side `ui.v1` slots.

The rendered page must stay functionally identical for users: same DOM ids,
same `data-pf-slot` hosts, same CSS cascade order (base → theme → custom
CSS), same boot scripts, same cache-busting behaviour.

## 2. Current state (measured 2026-08-21, commit `45f08642`)

| Fact | Value |
| ---- | ----- |
| `template.html` | 1 690 lines, 132 KB |
| inline `<style>` | lines 12–1244 (**~1 230 lines**, 73 % of the file) |
| HTML body | lines 1256–1679 (~425 lines), 71 inline `on*=` handlers, 121 `data-i18n*` attributes |
| inline scripts | hljs bootstrap (head) + 5 config constants (`{{AGENT_PATH}}`, `{{SSE_PATH}}`, `{{LOGIN_URL}}`) |
| rendering | `serve_chat_ui.py`: `read_text()` + a chain of `str.replace` (`/* JS_PLACEHOLDER */`, `</body>`, `</head>`, `</style>`, `<!--__PAWFLOW_EXTENSIONS_PLACEHOLDER__-->`, `{{…}}`) |
| caching | base page cached per asset signature (mtime/size of `template.html`, the 86 `_JS_MODULES`, `i18n/*.json`); theme block, extension manifest, paths and custom CSS applied per request |
| PFP `ui.v1` | 10 DOM slot hosts `data-pf-slot="<slot>_ext"` in the template; `ext_runtime.js` renders package contributions into them at boot (client side only); `.html` assets are refused |
| Jinja2 | already a runtime dependency (`jinja2>=3.1.0`, 3.1.6 installed), unused by the chat UI |
| packaging | `pyproject.toml` ships `tasks.io.chat_ui` `**/*.html|js|css|json` recursively — a `templates/` / `css/` sub-tree needs no packaging change |
| tests | **25** test files read `template.html` as raw text (21 × `_text("tasks/io/chat_ui/template.html")`) and assert source invariants (ids, CSS rules, slot hosts, i18n attributes) |
| private gateway skins | have their **own** `template.html` (`test_private_gateway_skins.py`, `test_install_bootstrap.py`) — out of scope |

The three pain points behind the request:

1. a 1 690-line file where CSS, layout regions and boot code are interleaved;
   every UI change edits the same file and the per-file hotpatch workflow
   moves 132 KB for a one-line change;
2. string-replace rendering with magic markers (`/* JS_PLACEHOLDER */` is a
   CSS comment that lives *outside* any `<style>`);
3. extension points exist only in the browser: a package cannot contribute
   markup that is present before JS boot (no-flicker stages, `<template>`
   definitions, `<link rel="preload">`, CSS-only contributions).

## 3. Decisions (binding once approved)

1. **Jinja2 rendering.** One `jinja2.Environment(loader=FileSystemLoader(chat_ui/templates), autoescape=True, undefined=StrictUndefined, auto_reload=True)` built once per process. The page is rendered **per request** (the context already differs per request: theme, extension manifest, paths). Rendering a 130 KB template whose static parts are pre-compiled string constants costs well under a millisecond; the current `_html_cache` string cache disappears, the asset signature stays (it feeds the `?v=` cache-busting hash and must now cover `templates/**` and `css/**`).
2. **The skeleton only includes.** `templates/chat.html` contains the
   `<html>/<head>/<body>` structure, `{% include %}` lines and extension
   points — no markup of its own beyond that. Each partial owns one region
   and stays small (target ≤ 150 lines; hard limit 300).
3. **CSS leaves the HTML.** The inline `<style>` is split by its existing
   section comments into `chat_ui/css/*.css`, listed in order in a
   `_CSS_MODULES` tuple (like `_JS_MODULES`) and emitted as
   `<link rel="stylesheet" href="/chat/js/css/<file>?v=<hash>">` by the
   skeleton. `serveAssets` already serves any file under `chat_ui/` at
   `/chat/js/{path}` with the right MIME type, so **no flow/route change** is
   needed on deployed installs. The theme `<style id="custom-theme">` and the
   task's `custom_css` stay inline *after* the links, preserving today's
   cascade. (Alternative A — `{% include "css/x.css" %}` inside one inline
   `<style>` — keeps a single request but keeps shipping 100 KB of CSS on
   every page load; see Q1.)
4. **No magic markers.** `/* JS_PLACEHOLDER */`, `{{AGENT_PATH}}`-style
   tokens and `_EXTENSIONS_PLACEHOLDER` go away; the skeleton receives
   `agent_path`, `sse_path`, `login_url`, `asset_version`, `js_modules`,
   `css_modules`, `i18n_block`, `theme_block`, `extensions_block`,
   `custom_css`, `template_slots` as context. Server-built HTML/JS blocks are
   the only values marked `|safe`; everything else is autoescaped.
5. **Server-side extension points for PFP (`ui.v1`, additive).** A
   `ui_extension` may declare
   `"assets": {"templates": [{"slot": "<slot>", "path": "content/ui/<file>.html"}]}`.
   Each fragment is **inert HTML**: it is inserted verbatim at render time,
   never evaluated by Jinja (no server-side template injection surface), and
   wrapped in `<div data-pf-ext="<package>" data-pf-template-slot="<slot>">`.
   Fragments are reviewed at install like scripts (they can carry inline
   `<script>`; the package already ships executable JS, so the consent
   surface is unchanged), hash-verified, limited to 64 KiB each, and emitted
   only for packages that pass the same gates as the boot manifest (kill
   switch, `version_compat == ui.v1`, per-conversation enablement, install
   scope). A fragment counts as a contribution: a conditional host
   (`conversation_stage`, `resources_collection`, `composer_accessory`) that
   receives one is rendered without `hidden`.
6. **Template slot names** are the 10 existing `ui.v1` slots plus two
   page-level points: `head` (before `</head>`: `<link rel=preload>`, `<meta>`)
   and `body_end` (before the boot scripts: `<template>` definitions, hidden
   stages). Adding slots is additive and stays `ui.v1`.
7. **Tests read the rendered page, not a file.** A helper
   `tests/_chat_ui.py` exposes `rendered_chat_html()` (renders `chat.html`
   through the real `serve_chat_ui` code with a default context) and
   `chat_ui_partial(name)`. The 21 `_text("tasks/io/chat_ui/template.html")`
   call sites switch to `rendered_chat_html()` mechanically; their assertions
   are untouched. Region-specific tests may later move to
   `chat_ui_partial()` but that is not part of this plan.
8. **Zero backward compatibility.** `template.html` is deleted in WP1; the
   replace chain and the string cache are deleted in WP0. The `serveChatUI`
   task parameters (`agent_path`, `login_url`, `sse_path`, `custom_css`,
   `custom_css_file`) keep their names and meaning.
9. **Not a JS refactor.** The 71 inline handlers, the 86 JS modules and
   their load order are untouched. Moving handlers out of markup is a
   separate decision.

## 4. Target layout

```text
tasks/io/chat_ui/
  templates/
    chat.html                 # skeleton: doctype, head/body, includes, extension points
    head/vendor.html          # rxjs, highlight.js + its 6-line bootstrap
    sidebar/sidebar.html      # #sidebar grip, conversations section
    sidebar/resources.html    # #resourcesPanel + resources_* slot hosts + sidebar_top/bottom
    dialogs/conversation_settings.html
    header/tab_bar.html       # #tabBar + tab_bar slot host
    header/header_bar.html    # #headerBar, status/gauge/active-agents popovers, user info
    header/action_dock.html   # #actionMenuWrap, menu items, action_menu / header_actions / gear_menu hosts
    chat/panels.html          # confirmations, plans, scheds, files panels
    chat/messages.html        # #messages, openspace wrap/overlay, scrollNav, active panel, conversation_stage host
    chat/task_tabs.html       # #taskTabDock / #taskTabPanel
    composer/controls.html    # #promptControlsPanel, view menu, composer action mount
    composer/input_row.html   # attach, composer_accessory host, textarea, send
    ext/hosts.html            # #pf-ext-modal-host, tooltip portal, #pf-ext-panel-host
    boot/config.html          # AGENT_PATH / API / SSE_URL / LOGIN_URL constants (tojson)
    boot/scripts.html         # asset-version guard, i18n block, extensions block, <script defer> loop
  css/
    00_base.css               # :root bridge, layout, dvh notes
    10_sidebar.css
    20_header.css             # status widgets, gauges, grips
    30_messages.css           # messages, delegate blocks, confirmations
    35_simplified_view.css    # cues / rain / stage
    40_composer.css           # drawer, input row, grab mode
    50_openspace.css
    60_dialogs.css            # exec approval, generic dialog, cognitive panel chrome
    70_terminal_files.css     # terminal output, file explorer
    80_tabs_dock.css          # tab bar, tab panels, desktop/audio tabs, action dock
    90_mobile.css             # narrow-viewport overrides (must stay last before theme)
    95_theme_bridge.css       # --pf-* variable bridge
```

File names are indicative; the split follows the existing section comments
of the `<style>` block and the `<!-- ... -->` region comments of the body.
The `NN_` prefixes make the cascade order visible in the tree; the
authoritative order is `_CSS_MODULES`.

## 5. Rendering contract (`serve_chat_ui.py`)

```python
_env = Environment(loader=FileSystemLoader(_CHAT_UI_DIR / "templates"),
                   autoescape=True, undefined=StrictUndefined, auto_reload=True)

def render_chat_page(*, agent_path, sse_path, login_url, theme_block,
                     extensions_block, template_slots, custom_css) -> str:
    sig = _asset_signature()            # templates/**, css/**, js modules, i18n
    return _env.get_template("chat.html").render(
        asset_version=_compute_js_version(sig),
        js_modules=[m for m in _JS_MODULES if (_CHAT_UI_DIR / m).exists()],
        css_modules=_CSS_MODULES,
        i18n_block=Markup(_initial_i18n_block()),
        theme_block=Markup(theme_block), extensions_block=Markup(extensions_block),
        template_slots=template_slots,  # {slot: [Markup(fragment), ...]}
        agent_path=agent_path, sse_path=sse_path, login_url=login_url,
        custom_css=custom_css)
```

- `_initial_i18n_block()` output is cached per i18n signature (it is the only
  expensive piece: three JSON catalogs serialised).
- `execute()` keeps computing `theme_block`, `extensions_block` and the new
  `template_slots` per request, exactly where it does today.
- A skeleton extension point is one Jinja macro call:
  `{{ ext_slot('conversation_stage', hidden=True) }}` renders the host
  `<div data-pf-slot="conversation_stage_ext">` with the package fragments
  inside and drops the `hidden` attribute when a fragment is present.
- The response headers (COOP/COEP, no-cache) are unchanged.

## 6. PFP template fragments (`ui.v1`, additive)

Manifest addition (`core/pfp_package` validation):

```json
"assets": {
  "scripts": ["content/ui/extension.js"],
  "templates": [
    {"slot": "conversation_stage", "path": "content/ui/stage.html"},
    {"slot": "head", "path": "content/ui/preload.html"}
  ]
}
```

Rules:

- `slot` ∈ the 10 `ui.v1` slots ∪ {`head`, `body_end`}; `path` ends with
  `.html`, lives under `content/`, ≤ 64 KiB, UTF-8; duplicates refused.
- Install review treats a fragment as executable content (same reviewer
  path as `scripts`); the install record stores `kind: "template"`,
  `slot`, `path`, `sha256`, `size`.
- Serve time: `list_installed_ui_extensions()` already returns the assets;
  `serve_chat_ui` reads each fragment once per `(package, sha256)` into a
  small in-memory cache, verifies the digest, and passes
  `{slot: [Markup(wrapped_fragment), ...]}` to the template. Mismatch,
  missing file or oversize ⇒ the fragment is skipped and logged once; the
  rest of the page renders normally (an extension never breaks the page).
- The fragment text is **never** passed through Jinja. Its `data-i18n`
  attributes are resolved by the existing boot pass against the global
  catalogs; package-namespaced strings keep going through `pfp.t()` in the
  package's JS.
- `ext_runtime.js` change: `_slotEl()` already finds the host; the only
  addition is that the runtime must not re-hide a conditional host that the
  server rendered visible (it currently toggles `hidden` from the client
  manifest — it will OR the two sources).
- Teardown (`pawflow.unregister`) removes the package's fragments
  (`[data-pf-ext="<pkg>"]`) like it removes its slot entries.
- `.html` stays refused in `assets.files` (a document served from
  `/chat/ext/...` is a different threat than markup rendered by the server
  into the authenticated page); `templates` entries are **not** served as
  URLs.

## 7. Work packages (one commit each on `main`, no release)

| WP | Scope | Done when |
| -- | ----- | --------- |
| **WP0** Jinja render, same page | `templates/chat.html` = today's `template.html` moved verbatim with the five injection points turned into Jinja expressions; `_CSS_MODULES` empty; `serve_chat_ui.py` renders through Jinja; `tests/_chat_ui.py` helper; the 21 call sites switch to `rendered_chat_html()`; `template.html` deleted. | A new test renders the page and asserts the set of `id=` attributes, `data-pf-slot` hosts and `<script defer src>` URLs equals a snapshot taken from the current page; the 25 test files pass unchanged in their assertions. |
| **WP1** HTML partials | Body split into the partials of section 4; skeleton only includes. | Same snapshot test; every partial ≤ 300 lines; `docs/CHAT_UI_TEMPLATES.md` lists partial → region. |
| **WP2** CSS modules | `<style>` split into `css/*.css`, `_CSS_MODULES`, `<link>` emission, signature covers `css/**`; `custom_css`/theme stay inline after the links. | A test asserts the `<link>` order equals `_CSS_MODULES` and that `custom-theme` / custom CSS come after; `test_chat_themes.py` cascade assertions pass; manual check of dark theme + mobile layout in the browser. |
| **WP3** PFP template fragments | Manifest validation, install review, record shape, serve-time collection, `ext_slot()` macro, `ext_runtime.js` host-visibility OR, teardown; docs `PFP_DEVELOPER_GUIDE.md` (ui.v1 section) + `PFP_PACKAGES.md`. | `test_pfp_ui_extension.py`: valid/invalid manifests, oversize, wrong slot, digest mismatch skipped; `test_ui_extensions_runtime.py`: rendered page contains the fragment for an enabled package and not for a disabled one / other conversation; example `examples/*.pfpdir` updated. |
| **WP4** docs/changelog | `docs/CHAT_UI_TEMPLATES.md` (rendering contract, partial map, how to add a partial/CSS module/slot), `02_REFERENCE_TASKS_SERVICES.md` serveChatUI entry, `CHANGELOG.md`. | Reviewed with the last WP. |

WP0–WP2 carry no user-visible change and can ship independently of WP3.

## 8. Risks and mitigations

- **Stale page after hotpatch.** Today the signature covers one file; it
  must glob `templates/**` and `css/**` (and `auto_reload=True` re-reads a
  changed partial). A test asserts every partial and CSS module is in the
  signature.
- **Cascade order.** CSS order is now a Python tuple; the mobile overrides
  and the theme bridge must stay last. The WP2 test pins the order; a
  browser check on mobile width is part of WP2.
- **XSS via autoescape gaps.** Only server-built blocks are `Markup`; the
  path parameters are emitted with `|tojson` inside `<script>`. `StrictUndefined`
  turns a typo in a context key into a 500 in tests rather than an empty string in production.
- **SSTI.** Package fragments are inserted as `Markup` after the digest check
  and are never compiled by Jinja.
- **Per-request render cost.** Measured in WP0 (target < 2 ms for the
  skeleton; the i18n block stays cached). If it ever matters, the static
  part can be cached per signature again without changing the template.
- **CSP.** `services/_http_base.py` sets the policy; inline scripts are
  already used by the page, so fragments with inline `<script>` behave like
  today's inline boot scripts. WP3 documents that fragments should prefer
  registering behaviour from the package's `scripts`.
- **Test churn.** 21 mechanical call-site edits (`batch_edit`), no assertion
  changes; region tests keep passing because they assert on the rendered
  page.
- **Hotpatch workflow on `/app`.** Per-file copies keep working; the new
  directories must exist on the server (the release tarball ships them via
  `package-data`).

## 9. Open questions for review

- **Q1 — CSS delivery:** external `<link>` modules (decision 3, recommended:
  cacheable, 100 KB less per page load, per-file hotpatch) or inline
  `{% include %}` inside one `<style>` (single request, no serving change)?
- **Q2 — Template slot list:** the 10 `ui.v1` slots + `head` + `body_end`
  (decision 6), or a smaller initial set (`conversation_stage`, `head`,
  `body_end`) grown on demand?
- **Q3 — Test strategy:** the `rendered_chat_html()` helper with untouched
  assertions (decision 7) is the low-risk path; moving region tests to
  partials is deferred. Agreed?
- **Q4 — Sequencing:** WP0→WP2 first (pure modularisation), WP3 after, or
  everything in one series?
