"""Compact scheduled-wakeup rows use the production CSS and tooltip portal."""

import pytest

from test_webchat_motion_browser import CHAT_UI, _shell_html, chromium_browser  # noqa: F401


@pytest.mark.parametrize("width,atmosphere", [(390, True), (1280, False)])
def test_wakeup_is_one_small_line_with_full_hover_text(chromium_browser, width, atmosphere):
    context = chromium_browser.new_context(viewport={"width": width, "height": 800})
    try:
        page = context.new_page()
        page.set_default_timeout(2000)
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate("""
            () => {
              document.body.innerHTML = '<div id="messages" class="messages"></div>'
                + '<div id="pfCssTooltip" class="pf-css-tooltip" aria-hidden="true"></div>';
              document.body.style.display = 'block';
              window._seenMsgIds = new Set();
              window._selectedMsgIds = new Set();
              window.displayWindow = 50;
              window.hasMoreMessages = false;
              window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = false;
              window.PAWFLOW_GROUP_DELEGATE_MESSAGES = false;
              window.collapseTechnicalGroups = () => {};
              window._messageSortTs = extra => extra.ts;
              window._hasRealSortTs = () => true;
              window.makeTimeHtml = () => '';
              window.sourceBadge = () => '';
              window._authorBadgeHtml = () => '';
              window.t = key => key;
              window.escapeHtml = text => {
                const el = document.createElement('span');
                el.textContent = text; return el.innerHTML;
              };
              window.isNearBottom = () => false;
              window.scrollBottom = () => {};
              window.pawflowDebugLog = () => {};
              window._insertMessageChronologically = (box, row) => box.appendChild(row);
            }
        """)
        page.evaluate(
            "enabled => document.documentElement.dataset.pfAtmosphere = enabled ? 'on' : 'off'",
            atmosphere,
        )
        for name in ("messages_render.js", "ui_floating_layer.js", "tooltips.js"):
            page.add_script_tag(path=str(CHAT_UI / name))
        full_text = '[System: Scheduled wake-up]\nCheck the complete result. <b>Literal text</b>\n' + 'detail ' * 80
        page.evaluate(
            """text => addMsg('user', text, {msg_id:'wake', ts:123,
              source:{type:'scheduled_wakeup', target_agent:'assistant'}})""",
            full_text,
        )
        row = page.locator('[data-msgid="wake"]')
        assert row.inner_text() == "wake up"
        assert row.get_attribute("aria-label") == full_text
        geometry = row.evaluate("""el => {
          const css = getComputedStyle(el);
          return {height:el.getBoundingClientRect().height, font:css.fontSize,
            background:css.backgroundColor, border:css.borderLeftWidth,
            before:getComputedStyle(el, '::before').borderTopWidth};
        }""")
        assert geometry == {
            "height": 16, "font": "11px", "background": "rgba(0, 0, 0, 0)",
            "border": "0px", "before": "1px",
        }
        row.hover()
        page.wait_for_function(
            "document.getElementById('pfCssTooltip').classList.contains('visible')"
        )
        tooltip = page.locator("#pfCssTooltip")
        assert tooltip.text_content() == full_text.strip()
        assert tooltip.locator("b").count() == 0
        assert row.bounding_box()["height"] == 16
    finally:
        context.close()
