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
  `trim_blocks` + `lstrip_blocks`. A changed partial is re-read on the next
  request (hotpatch workflow).
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
tasks/io/chat_ui/
  templates/
    chat.html        # skeleton (WP0: the former template.html; WP1 splits it into partials)
  css/               # CSS modules (WP2)
```

The partial map is filled in as the split lands (WP1/WP2 of the plan).

## Tests

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
