# Chat UI templates

The chat page served at `/chat` is rendered by `tasks/io/serve_chat_ui.py`
from a Jinja2 template tree under `tasks/io/chat_ui/templates/`. Plan and
rationale: [CHAT_UI_TEMPLATE_PLAN.md](CHAT_UI_TEMPLATE_PLAN.md).

## Rendering contract

```python
from tasks.io.serve_chat_ui import render_chat_page
html = render_chat_page(agent_path="/api/agent", sse_path="/api/agent/events",
                        login_url="", theme_block="", extensions_block=None,
                        template_slots=None, custom_css="")
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
  | `custom_css` | str | serveChatUI `custom_css` (+ `custom_css_file`), emitted as `<style id="custom-css">` after the CSS modules, `</style` neutralised |

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
  dialogs/appearance.html           # inherited/global or conversation appearance controls
  dialogs/search.html               # Ctrl/Cmd+K conversation-search overlay
  dialogs/conversation_settings.html# #conversationSettingsDialog (expiry, sharing, controls)
  header/tab_bar.html               # #tabBar, tab_bar slot host, audio tab buttons
  header/header_bar.html            # header grip, #headerBar: logo, status, gauges, active agents, user info
  header/action_dock.html           # #actionMenuWrap: action menu items, action_menu/header_actions/gear_menu hosts
  chat/panels.html                  # confirmations / plans / scheduled tasks / files panels
  chat/messages.html                # conversation_stage host, #messages, OpenSpace wrap, scroll nav, active agents panel
  chat/task_tabs.html               # #taskTabDock, #taskTabPanel
  composer/controls.html            # composer drawer grip, #promptControlsPanel, view menu, composer action mount
  composer/input_row.html           # unified attach/search/slash/@/STT/grab/#input/send shell
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
  55_appearance.css                 # scoped appearance, atmosphere media and translucent surfaces
  58_modern_ui.css                  # composer shortcuts, search, code headers and memory records
  60_openspace.css                  # OpenSpace 3D view
  70_grab.css                       # terminal grab mode
  75_composer_shell.css             # unified responsive prompt component and picker
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

## Personal appearance and compact AI surfaces

The header Appearance button opens a user-owned preference panel. The selected
scale (75–150%), background source and atmosphere effects are independent from
themes. An authenticated user owns one global record inherited by every
conversation; a conversation can own an override.
`core/appearance_store.py` persists those small records per user, while
appearance uploads (image/video, maximum 80 MiB) use a private, non-expiring
FileStore category. The client loads its namespaced localStorage/IndexedDB cache
first for instant paint and offline use, then silently hydrates from the server.
It migrates pre-sync browser preferences and blobs once per authenticated user;
after that marker exists, the server remains authoritative so stale devices
cannot resurrect a cleared override. Superseded private uploads are deleted when
no appearance record references them. Remote backgrounds require HTTPS and
contact their host directly. Image/video motion is disabled when
`prefers-reduced-motion` is active, and videos pause while the page is hidden.

The surrounding chat surfaces remain theme-neutral:

- the prompt row exposes compact search, slash-command and agent-mention
  shortcuts without changing message submission; below 768 px, only the
  secondary-action toggle, prompt, selected agent, and Send stay visible, while
  attach, search, slash, mention, Micro, Grab, and extension actions are stacked
  in an accessible full-width panel above the composer. Its action rows override
  the compact icon dimensions with equal selector specificity so their labels
  remain inside the mobile viewport. Above that breakpoint, Micro and Grab
  return to the trailing controls immediately before Send. The selected agent
  remains visible in a
  thin, localized `Selected agent: <name>` button at the prompt row's right
  edge, immediately before Send;
  activating it opens a conversation-aware quick selector. On narrow screens
  the button truncates and the selector becomes a
  full-width touch-friendly panel that stays inside the viewport;
- the thin conversation-controls strip puts Refresh first and exposes compact
  permission, conversation-theme, conversation-appearance, and OpenSpace
  controls. Its buttons share the action dock's dimensions, resting surface,
  accent border, spring hover zoom, and shadow while respecting reduced-motion
  preferences. Header dock buttons, header status buttons, conversation controls,
  and action-dock items all use `--pf-sidebar` as their resting background; the
  generic themed `button` surface must not create a different-colored subset.
  Global and conversation Appearance controls use the same palette glyph
  (`U+1F3A8`). Permission is a real button opening an accessible menu rather than
  a visible native combo; permission and theme controls show only their compact
  icons while closed but retain current-value tooltips and accessible labels;
- activating OpenSpace always resets its camera to the general home view after
  the scene is ready; close-up and manually moved camera state is never restored
  when returning from Webchat;
- above 768 px, the right tab rail is independent from the
  Conversations/Resources sidebar: a persistent themed edge hint reveals the
  fixed overlay on pointer approach or keyboard focus, while the sidebar grip
  controls only the sidebar. Atmosphere mode never puts this rail back in the
  body flex, so it reserves no width in the header, transcript, or composer;
  narrow layouts retain the coupled overlay behavior;
- `Ctrl/Cmd+K` and `/search <query>` share the same overlay over the latest
  500 conversation messages;
- fenced Markdown code has a language header and accessible copy action;
- the memory browser uses tokenized record/card classes rather than a fixed
  dark inline palette.

All these rules use existing `--pf-*` variables. A custom theme therefore
continues to work unchanged; atmosphere overrides apply only while a personal
background is active.

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

## How to

- **Add a partial**: create the file under its region directory, `{% include
  "region/file.html" %}` it from the skeleton (or from the region partial it
  belongs to), and move the markup verbatim. Nothing else to register — the
  asset signature globs `templates/**`.
- **Add a CSS module**: create `css/NN_name.css` **and** add its name to
  `_CSS_MODULES` in `serve_chat_ui.py` at the right position; the `NN_`
  prefix must mirror that position. The contract test fails on a file that
  is not listed, or a listed name with no file.
- **Add a JS module**: unchanged — append to `_JS_MODULES` in load order.
- **Add an extension slot**: see the last bullet of the section above.
- **Never** reintroduce a string-replace marker: everything the server
  injects is a named context key.

## Dialog visual contract

All built-in and extension dialogs use the shared theme bridge rather than a
private palette. A dialog surface must use one of `.dialog`, `.exec-dialog`,
`.cog-dialog`, or `.pf-ext-modal-box`; the legacy resource editor is also
covered through `#resourceEditorOverlay`. The bridge provides the themed
surface and overlay colors, fields, rounded Beautiful UI tables, keyboard
focus, and dialog-button motion. Buttons use the dock's spring transition at
a smaller scale suitable for text labels, while `prefers-reduced-motion`
removes that motion. New dialogs must keep positive and destructive semantics
through `.btn-primary`/`.exec-approve` and `.btn-danger`/`.exec-deny`.
The contract also covers dynamically-created direct body overlays/dialogs, so
their buttons cannot fall back to a module-private shape or interaction style.
Dynamic dialog modules use only `--pf-*` tokens (including semantic accent,
success, warning, and danger tokens); literal hex/RGB palettes are forbidden.
Long server transactions use `showOperationProgress()` from `dialogs.js`.
The modal exposes real phase labels with indeterminate progress (never a fake
percentage), blocks duplicate/destructive input while busy, and becomes an
explicit dismissible error state on failure. Skill-draft promotion and both
conversation-import phases are the reference integrations.

## Hotpatching a running server

Copy the changed file(s) under `/app/tasks/io/chat_ui/...` with the same
relative path. Templates are re-read on the next request; JS/CSS modules get
a new `?v=` because the signature changed; `serve_chat_ui.py` itself needs a
restart.
