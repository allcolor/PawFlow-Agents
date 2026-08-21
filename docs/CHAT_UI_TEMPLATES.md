# Chat UI templates

The chat page served at `/chat` is rendered by `tasks/io/serve_chat_ui.py`
from a Jinja2 template tree under `tasks/io/chat_ui/templates/`. Plan and
rationale: [CHAT_UI_TEMPLATE_PLAN.md](CHAT_UI_TEMPLATE_PLAN.md).

## Rendering contract

```python
from tasks.io.serve_chat_ui import render_chat_page
html = render_chat_page(agent_path="/api/agent", sse_path="/api/agent/events",
                        login_url="", theme_block="", extensions_block="",
                        custom_css="")
```

- One `jinja2.Environment` per process: `FileSystemLoader(chat_ui/templates)`,
  `autoescape=True`, `undefined=StrictUndefined`, `auto_reload=True`,
  `trim_blocks` + `lstrip_blocks`, `keep_trailing_newline` (an include keeps
  its final newline, so partial boundaries never glue two lines). A changed
  partial is re-read on the next request (hotpatch workflow).
- The page is rendered **per request**; the context differs per request
  (theme cookie, installed extensions). The i18n boot block — the only costly
  piece — is cached per i18n file signature.
- Context passed to `chat.html`:

  | Key | Type | Meaning |
  | --- | ---- | ------- |
  | `asset_version` | str | 8-char hash of the asset signature (templates, CSS modules, JS modules, i18n); used as `?v=` on every `<script defer>` / `<link>` |
  | `js_modules` | list | `_JS_MODULES` entries that exist on disk, in load order |
  | `css_modules` | list | `_CSS_MODULES` entries, in cascade order |
  | `i18n_block` | str, `|safe` | `<script>` with `PAWFLOW_I18N_LANGUAGES` / `PAWFLOW_I18N_CATALOGS` |
  | `theme_block` | str, `|safe` | `<style id="custom-theme">` + `PAWFLOW_INITIAL_THEME_REF`, or empty |
  | `extensions_block` | str, `|safe` | `PAWFLOW_EXTENSION_CONTEXT` / `PAWFLOW_EXTENSIONS` boot manifest (the empty manifest when the caller passes none) |
  | `template_slots` | dict | `{slot: [fragment html, ...]}` — the enabled PFP packages' server-rendered fragments (see below) |
  | `agent_path`, `sse_path`, `login_url` | str | serveChatUI task parameters, emitted with `tojson` inside `<script>` |
  | `custom_css` | str | serveChatUI `custom_css` (+ `custom_css_file`), inserted inside the main `<style>` with `|safe` after `</style` neutralisation |

  Only the server-built blocks are inserted with `|safe`, visibly in the
  skeleton; every other value is autoescaped. `StrictUndefined` turns a
  missing key into an error instead of an empty string.
- The asset signature (`_asset_signature()`) covers `templates/**/*.html`,
  `css/*.css`, `_JS_MODULES` and `i18n/*.json`: editing any of them changes
  `asset_version`, so browsers fetch fresh modules.

## Layout

```text
tasks/io/chat_ui/templates/
  chat.html                         # skeleton: doctype, <head>/<body> structure, includes, extension points
  head/styles.html                  # <link> per CSS module (cascade order) + the operator custom_css <style>
  head/vendor.html                  # rxjs UMD, highlight.js + its DOMContentLoaded bootstrap
  sidebar/sidebar.html              # sidebar grip, #sidebar, Conversations section (+ new/import menu)
  sidebar/resources.html            # #resourcesPanel, resources_collection/resources_panel/sidebar_* slot hosts
  dialogs/conversation_settings.html# #conversationSettingsDialog (expiry, sharing, controls)
  header/tab_bar.html               # #tabBar, tab_bar slot host, audio tab buttons
  header/header_bar.html            # header grip, #headerBar: logo, status, gauges, active agents, user info
  header/action_dock.html           # #actionMenuWrap: action menu items, action_menu/header_actions/gear_menu hosts
  chat/panels.html                  # confirmations / plans / scheduled tasks / files panels
  chat/messages.html                # conversation_stage host, #messages, OpenSpace wrap, scroll nav, active agents panel
  chat/task_tabs.html               # #taskTabDock, #taskTabPanel
  composer/controls.html            # composer drawer grip, #promptControlsPanel, view menu, composer action mount
  composer/input_row.html           # attach button, composer_accessory host, #input, send button
  ext/hosts.html                    # #pf-ext-modal-host, CSS tooltip portal, #pf-ext-panel-host
  boot/config.html                  # AGENT_PATH / API / SSE_URL / LOGIN_URL constants (tojson)
  boot/scripts.html                 # asset-version guard, i18n block, extensions block, <script defer> loop
tasks/io/chat_ui/css/               # CSS modules, served by serveAssets at /chat/js/css/<file>?v=<asset_version>
  00_base.css                       # reset, :root, app layout, sidebar, sharing
  10_chrome.css                     # collapsible grips, header status widgets
  20_messages.css                   # gauges, messages, simplified live view, send button
  30_mobile.css                     # narrow-viewport overrides
  40_delegates.css                  # delegate blocks, cancel, ask_parent
  50_composer.css                   # composer drawer, cognitive panel chrome
  60_openspace.css                  # OpenSpace 3D view
  70_grab.css                       # terminal grab mode
  80_dialogs.css                    # exec approval + generic dialogs
  85_terminal_files.css             # terminal output, file explorer
  90_tabs.css                       # tab bar, tab panels, desktop/audio tabs
  95_action_dock.css                # action menu + conversation dock
  99_theme_bridge.css               # --pf-* variable bridge (last)
```

CSS cascade: the modules are linked in `_CSS_MODULES` order (the `NN_`
prefix mirrors it; the list in `serve_chat_ui.py` is authoritative), then
`<style id="custom-css">` (serveChatUI `custom_css`), then the highlight.js
theme, then `<style id="custom-theme">` (the user's theme). Adding a module
means adding the file **and** its entry in `_CSS_MODULES`; the contract test
checks both. The old single inline `<style>` is gone: a CSS-only change now
ships one small cacheable file.

Rules for partials:

- one region per file, ≤ 300 lines (target ≤ 150); a region that grows is
  split again, the skeleton never regains markup of its own;
- partials are plain HTML plus Jinja includes/expressions; they never define
  blocks or macros that another partial depends on;
- every element the JS modules address by id stays in exactly one partial
  (`tests/test_chat_ui_templates.py` pins the id / slot / i18n-key sets).

## Tests

## Extension points (PFP `ui.v1` template fragments)

An installed `ui_extension` may declare `assets.templates:
[{slot, path}]` — inert HTML fragments the server renders into the page
before JS boot (PFP_DEVELOPER_GUIDE.md, "Server-rendered template
fragments"). Slots: the ten DOM slots (`action_menu`, `gear_menu`,
`resources_panel`, `sidebar_top`, `sidebar_bottom`, `header_actions`,
`tab_bar`, `conversation_stage`, `resources_collection`,
`composer_accessory`) plus `head` and `body_end`.

- `serve_chat_ui._enabled_ui_extension_records()` is the single gate (user,
  install records, kill switch, `ui.v1`, per-conversation toggle) for both
  the boot manifest and the fragments; `_template_fragments()` reads each
  fragment once per `(package, sha256)`, checks containment, size (64 KiB)
  and digest against the signed install record, and wraps it in
  `<div data-pf-ext="<package>" data-pf-template-slot="<slot>">` (`head`:
  comment markers). A bad fragment is skipped and logged once.
- Templates call two environment globals: `{{ ext_fragments('slot')|safe }}`
  inside every slot host and at the `head` / `body_end` points of the
  skeleton, and `{{ ext_hidden('slot') }}` on the conditional hosts
  (`conversation_stage`, `resources_collection`, `composer_accessory`), which
  drops the `hidden` attribute when a fragment is present. The fragment text
  is never compiled by Jinja: `|safe` is the only thing that happens to it.
- `ext_runtime.js` re-renders only its own `[data-pf-slot-entry]` children,
  keeps a host visible while it holds a `[data-pf-template-slot]` node, and
  removes a package's fragments on `unregister()`.
- Adding a slot: add the host (or point) in the partial with the two
  globals, the name in `core/pfp_package/_pp_base.py` (`_UI_TEMPLATE_SLOTS`,
  plus `_UI_KNOWN_SLOTS` and `ext_runtime.js` `KNOWN_SLOTS` for a DOM slot),
  the snapshot fixture and the developer guide. Additive changes stay `ui.v1`.

Tests never read a template file: `tests/chat_ui_testing.py` exposes
`rendered_chat_html(**context)` (the page rendered through
`render_chat_page`) and `chat_ui_partial(name)` (raw source of one partial
for region-specific invariants). Assert on the rendered page unless the
invariant is about a partial's own source.

## Hotpatching a running server

Copy the changed file(s) under `/app/tasks/io/chat_ui/...` with the same
relative path. Templates are re-read on the next request; JS/CSS modules get
a new `?v=` because the signature changed; `serve_chat_ui.py` itself needs a
restart.
