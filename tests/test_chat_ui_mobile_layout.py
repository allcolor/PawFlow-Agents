"""Structural assertions for the chat UI's mobile (narrow viewport) layout."""

import re
from pathlib import Path

TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")


def _mobile_block() -> str:
    """The body of the `@media (max-width: 768px)` rule."""
    start = TEMPLATE_HTML.index("@media (max-width: 768px) {")
    depth = 0
    for i in range(TEMPLATE_HTML.index("{", start), len(TEMPLATE_HTML)):
        if TEMPLATE_HTML[i] == "{":
            depth += 1
        elif TEMPLATE_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                return TEMPLATE_HTML[start:i + 1]
    raise AssertionError("unterminated mobile media query")


def test_full_height_elements_use_dvh_so_the_header_stays_on_screen():
    """Regression: with plain `100vh` + `overflow: hidden`, mobile browsers size
    the layout to the URL-bar-hidden height, pushing .header above the visible
    viewport with no way to scroll it back — the top bar simply vanished."""
    for selector in ("body {", ".sidebar {"):
        start = TEMPLATE_HTML.index(selector)
        rule = TEMPLATE_HTML[start:TEMPLATE_HTML.index("}", start)]
        assert "height: 100dvh" in rule, f"{selector} must size to the dynamic viewport"
        # The plain vh declaration stays as the fallback, and must come first
        # so dvh wins wherever it is supported.
        assert rule.index("height: 100vh") < rule.index("height: 100dvh")


def test_mobile_bumps_the_type_scale_above_the_desktop_sizes():
    """Desktop uses 11-12px for technical/tool output, which is unreadable on a
    phone. Every size the mobile block sets must be larger than the desktop
    default for the same selector."""
    mobile = _mobile_block()

    def desktop_size(selector: str) -> int:
        # Last desktop declaration wins; the mobile block is excluded because
        # it is matched separately and always sits after these rules.
        body = TEMPLATE_HTML.replace(mobile, "")
        pattern = re.escape(selector) + r"[^{]*\{[^}]*?font-size:\s*(\d+)px"
        found = re.findall(pattern, body)
        assert found, f"no desktop font-size found for {selector}"
        return int(found[-1])

    def mobile_size(selector: str) -> int:
        pattern = re.escape(selector) + r"[^{]*\{[^}]*?font-size:\s*(\d+)px"
        found = re.search(pattern, mobile)
        assert found, f"{selector} not sized in the mobile block"
        return int(found.group(1))

    for selector in (".msg", ".msg code", ".msg.tool", ".tc-md"):
        assert mobile_size(selector) > desktop_size(selector), selector


def test_mobile_input_is_at_least_16px_to_avoid_focus_zoom():
    """iOS Safari zooms the whole page when a focused input is under 16px."""
    mobile = _mobile_block()
    match = re.search(r"\.input-row textarea[^{]*\{[^}]*?font-size:\s*(\d+)px", mobile)
    assert match and int(match.group(1)) >= 16


def test_mobile_bubbles_use_the_available_width():
    mobile = _mobile_block()
    assert re.search(r"\.msg\s*\{[^}]*max-width:\s*9\d%", mobile)
    assert re.search(r"\.technical-group,\s*\.delegate-block\s*\{[^}]*max-width:\s*100%", mobile)
