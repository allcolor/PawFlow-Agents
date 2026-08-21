"""Shared helpers for chat UI source-invariant tests.

The chat page is rendered from the Jinja template tree under
``tasks/io/chat_ui/templates`` (docs/CHAT_UI_TEMPLATES.md). Tests assert on
the **rendered** page — the same HTML a browser receives — never on a raw
template file, so splitting a region into another partial never breaks them.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_UI_DIR = ROOT / "tasks" / "io" / "chat_ui"
TEMPLATES_DIR = CHAT_UI_DIR / "templates"


def rendered_chat_html(**context) -> str:
    """The chat page rendered through the real serving code.

    Keyword arguments override ``render_chat_page`` inputs (``agent_path``,
    ``theme_block``, ``extensions_block``, ``custom_css``...).
    """
    from tasks.io.serve_chat_ui import render_chat_page
    return render_chat_page(**context)


def chat_ui_partial(name: str) -> str:
    """Raw source of one template partial, e.g. ``"composer/input_row.html"``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")
