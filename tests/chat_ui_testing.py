"""Shared helpers for chat UI source-invariant tests.

The chat page is rendered from the Jinja template tree under
``tasks/io/chat_ui/templates`` (docs/CHAT_UI_TEMPLATES.md) and its
stylesheet is served as CSS modules under ``tasks/io/chat_ui/css``. Tests
assert on the page **as the browser ends up with it** — the rendered HTML
with each CSS module inlined where its ``<link>`` stands — never on a raw
template file, so moving a region or a rule to another file never breaks
them.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_UI_DIR = ROOT / "tasks" / "io" / "chat_ui"
TEMPLATES_DIR = CHAT_UI_DIR / "templates"
CSS_DIR = CHAT_UI_DIR / "css"

_CSS_LINK = re.compile(
    r'<link rel="stylesheet" href="/chat/js/css/(?P<name>[^"?]+)\?v=[0-9a-f]+">')


def rendered_chat_html(inline_css: bool = True, **context) -> str:
    """The chat page rendered through the real serving code.

    With ``inline_css`` (default) every ``<link>`` to a CSS module is replaced
    by ``<style data-css-module="<name>">`` holding that module's source, so
    CSS invariants can be asserted on the same string as the markup. Pass
    ``inline_css=False`` for the exact bytes the server sends. Keyword
    arguments override ``render_chat_page`` inputs (``agent_path``,
    ``theme_block``, ``extensions_block``, ``custom_css``...).
    """
    from tasks.io.serve_chat_ui import render_chat_page
    html = render_chat_page(**context)
    if not inline_css:
        return html

    def _inline(match):
        name = match.group("name")
        return ('<style data-css-module="' + name + '">\n' + chat_ui_css(name) + '</style>')

    return _CSS_LINK.sub(_inline, html)


def chat_ui_partial(name: str) -> str:
    """Raw source of one template partial, e.g. ``"composer/input_row.html"``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def chat_ui_css(name: str = "") -> str:
    """Source of one CSS module (``"30_mobile.css"``) or, with no name, of
    every module concatenated in cascade order."""
    if name:
        return (CSS_DIR / name).read_text(encoding="utf-8")
    from tasks.io.serve_chat_ui import _CSS_MODULES
    return "".join(chat_ui_css(module) for module in _CSS_MODULES)
