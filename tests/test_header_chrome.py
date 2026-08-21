"""Header chrome: collapsible panels behind grips + icon/popover widgets.

The three chrome zones (header bar, left sidebar + tab rail, composer
drawer) each fold completely behind a small grip (a square showing 3
vertical lines) that rides the separation line itself. Sidebar and composer
start CLOSED so the transcript takes almost the whole screen on load; the
header starts OPEN and folding it is the reader's persisted choice.

Three header widgets became compact icon buttons whose full content lives in
a click-toggled popover (click shows, click again hides):
- Active agents: person icon + active-count badge; the Active Agents box
  mounts inside its popover (it left the composer entirely).
- Pending actions: animated glyph while actions run, idle checkmark
  otherwise; label + progress bar in the popover.
- Context gauge: battery-style icon whose fill mirrors the percentage LEFT
  (100 - used %, display only); the full agent badge (label + gauge) in the
  popover.
"""

import json
from pathlib import Path

TEMPLATE = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
STATE_JS = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")
ACTIVE_JS = Path("tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
CMD_AGENT_JS = Path("tasks/io/chat_ui/cmd_agent.js").read_text(encoding="utf-8")
RXBUS_JS = Path("tasks/io/chat_ui/rxbus.js").read_text(encoding="utf-8")


def test_shared_grip_visual_and_the_three_grips():
    assert ".pf-grip {" in TEMPLATE
    assert ".pf-grip-bars {" in TEMPLATE
    # 3 vertical lines: vertical bars drawn by a horizontal repeating gradient.
    assert "repeating-linear-gradient(90deg, currentColor 0 2px, transparent 2px 5px)" in TEMPLATE
    # Vertical menu -> horizontal bars; horizontal menu -> vertical bars.
    assert ".pf-grip-bars-h {" in TEMPLATE
    for grip_id, bars in (("headerGrip", "pf-grip-bars"),
                          ("sidebarToggle", "pf-grip-bars-h"),
                          ("composerDrawerHandle", "pf-grip-bars")):
        start = TEMPLATE.index(f'id="{grip_id}"')
        button = TEMPLATE[TEMPLATE.rindex("<button", 0, start):
                          TEMPLATE.index("</button>", start)]
        assert "pf-grip" in button, grip_id
        assert f'class="{bars}"' in button, grip_id


def test_header_bar_folds_behind_a_top_grip_and_defaults_open():
    assert '<div class="header" id="headerBar">' in TEMPLATE
    assert ".header.collapsed { display: none; }" in TEMPLATE
    assert 'onclick="toggleHeaderBar()"' in TEMPLATE
    assert ".pf-grip-top { position: fixed; top: 0; left: 50%;" in TEMPLATE
    # Closed only when the stored flag is explicitly '0': fresh browser = OPEN.
    assert "localStorage.getItem(_HEADER_BAR_KEY) !== '0'" in STATE_JS
    assert "localStorage.setItem(_HEADER_BAR_KEY" in STATE_JS
    assert "document.addEventListener('DOMContentLoaded', _applyHeaderBar)" in STATE_JS
    # When open, the grip follows the header's bottom separation line.
    assert "bar.getBoundingClientRect().bottom - 8" in STATE_JS
    assert "window.addEventListener('resize', _applyHeaderBar)" in STATE_JS


def test_header_shows_the_pawflow_logo_linking_to_the_site():
    logo = TEMPLATE[TEMPLATE.index('<h1 class="header-logo">'):
                    TEMPLATE.index('</h1>')]
    assert 'href="https://pawflow.allcolor.org/"' in logo
    assert 'target="_blank"' in logo
    assert 'rel="noopener"' in logo
    assert "pawflow-logo-32.png" in logo
    assert "PawFlow Agent</h1>" not in TEMPLATE


def test_sidebar_grip_is_an_edge_tab_and_sidebar_defaults_closed():
    assert '<div class="sidebar collapsed" id="sidebar">' in TEMPLATE
    assert ".sidebar-toggle { position: fixed; top: 50%; left: 0;" in TEMPLATE
    # The tab rail folds together with the sidebar: collapsed, only the grip
    # remains visible at the left edge, glued to the boundary line.
    assert '<div class="tab-bar collapsed" id="tabBar">' in TEMPLATE
    assert ".tab-bar.collapsed { display: none; }" in TEMPLATE
    assert "tabBar.classList.toggle('collapsed', collapsed)" in STATE_JS
    assert "const boundary = collapsed ? 0 : 260 + (tabBar ? tabBar.offsetWidth : 0)" in STATE_JS
    assert "btn.style.left = Math.max(0, boundary - 8) + 'px'" in STATE_JS
    assert "&#9776;" not in TEMPLATE  # old hamburger glyph is gone


def test_composer_grip_rides_the_separation_line():
    # Absolute on the input area's top border: it adds no height of its own.
    assert (".composer-drawer-handle { position: absolute; top: -8px; left: 50%;"
            in TEMPLATE)
    assert ".input-area { position: relative;" in TEMPLATE


def test_header_icon_widgets_share_the_dock_hover_zoom():
    assert (".hdr-icon-btn:hover, .header-logo a:hover { transform: scale(1.4);"
            in TEMPLATE)
    # The grips zoom too, composing with their centering translation.
    assert ".pf-grip-top:hover { transform: translateX(-50%) scale(1.4); }" in TEMPLATE
    assert ".sidebar-toggle:hover { transform: translateY(-50%) scale(1.4); }" in TEMPLATE
    assert (".composer-drawer-handle:hover { transform: translateX(-50%) scale(1.4); }"
            in TEMPLATE)


def test_every_native_title_is_adopted_by_the_shared_css_tooltip():
    tooltips_js = Path("tasks/io/chat_ui/tooltips.js").read_text(encoding="utf-8")
    # One tooltip look everywhere: grips, header icon widgets, and any element
    # carrying a native title are all rendered by the shared CSS tooltip.
    assert ".pf-grip, .hdr-icon-btn, [data-pf-title]" in tooltips_js
    assert "function adoptNativeTitles(node)" in tooltips_js
    assert "el.removeAttribute('title')" in tooltips_js
    assert "target.dataset.pfTitle" in tooltips_js
    # The tab bar's home-grown attr(title) tooltip is gone.
    assert ".tab-btn[title]::after" not in TEMPLATE
    # The portal lives at body level: inside the header it went display:none
    # with it, killing every tooltip while the header was folded.
    header = TEMPLATE[TEMPLATE.index('<div class="header" id="headerBar">'):
                      TEMPLATE.index("<!-- Chat tab content -->")]
    assert 'id="pfCssTooltip"' not in header
    assert 'id="pfCssTooltip"' in TEMPLATE


def test_header_popover_pattern_toggles_and_closes_the_others():
    assert ".hdr-pop-wrap { position: relative;" in TEMPLATE
    assert ".hdr-pop.open { display: block; }" in TEMPLATE
    assert "function toggleHeaderPop(popId, btn)" in STATE_JS
    assert "document.querySelectorAll('.hdr-pop.open')" in STATE_JS


def test_active_agents_is_a_header_icon_with_count_and_popover():
    start = TEMPLATE.index('id="activeAgentsBtn"')
    button = TEMPLATE[TEMPLATE.rindex("<button", 0, start):
                      TEMPLATE.index("</button>", start)]
    assert "<svg" in button  # person icon
    assert 'id="activeAgentsCount"' in button
    assert "toggleHeaderPop('activeAgentsPop', this)" in button
    # The Active Agents box mounts inside the popover, not the composer.
    assert 'id="activeAgentsPop"' in TEMPLATE
    assert "activePop.appendChild(activePanel)" in STATE_JS
    assert "composerActiveMount" not in STATE_JS
    # Inside the popover the box is always laid out.
    assert ".hdr-pop .active-panel { display: block; position: static;" in TEMPLATE
    # The count badge tracks the number of active agents (hidden at zero).
    assert "function _updateActiveAgentsCount(count)" in ACTIVE_JS
    assert "badge.hidden = count === 0" in ACTIVE_JS
    assert "_updateActiveAgentsCount(names.length)" in ACTIVE_JS


def test_action_status_is_an_icon_animated_while_working():
    assert 'id="actionStatusBtn"' in TEMPLATE
    assert 'id="actionStatusIcon"' in TEMPLATE
    # The status text and the loading label + progress bar all moved inside
    # the popover; the header lead shows only the icon.
    pop = TEMPLATE[TEMPLATE.index('id="actionStatusPop"'):
                   TEMPLATE.index('id="ctxGaugeWrap"')]
    assert 'id="actionLoading"' in pop
    assert 'id="status"' in pop
    assert ".hdr-icon-btn.working .hdr-action-icon { animation: spin" in TEMPLATE
    # rxbus drives icon state + idle text together with the label.
    assert "statusBtn.classList.toggle('working', isWorking)" in RXBUS_JS
    assert "statusIcon.innerHTML = isWorking ? '\\u273B' : '\\u2713'" in RXBUS_JS


def test_context_gauge_is_a_battery_icon_with_the_badge_in_its_popover():
    assert 'id="ctxGaugeBtn"' in TEMPLATE
    assert 'id="ctxGaugeFill"' in TEMPLATE
    assert 'id="ctxGaugePct"' in TEMPLATE  # percentage shown beside the battery
    assert ".ctx-gauge-icon {" in TEMPLATE
    assert ".ctx-gauge-icon-fill {" in TEMPLATE
    # The full agent badge (label + gauge) lives inside the popover.
    pop = TEMPLATE[TEMPLATE.index('id="ctxGaugePop"'):
                   TEMPLATE.index('id="usageCostBadge"')]
    assert 'id="activeAgentBadge"' in pop
    # updateActiveAgentBadge mirrors the gauge onto the battery fill.
    assert "gaugeFill.style.height = leftInt + '%'" in CMD_AGENT_JS
    assert "gaugePct.textContent = leftInt + '%'" in CMD_AGENT_JS
    assert "gaugeWrap.style.display = ''" in CMD_AGENT_JS


def test_every_gauge_displays_the_remaining_percentage():
    # Batteries drain: every gauge SHOWS 100 - used %, orange once less than
    # 20% is left. Display only — the cached usage values stay "used".
    assert "const leftInt = 100 - pctInt;" in CMD_AGENT_JS
    assert "pct >= 0.80 ? '#f0ad4e' : '#4ecdc4'" in CMD_AGENT_JS
    assert "const leftInt = 100 - pctInt;" in ACTIVE_JS
    assert "Math.round((1 - pct) * width)" in ACTIVE_JS
    assert "pct: leftInt" in ACTIVE_JS
    assert "leftInt + '%</span>'" in ACTIVE_JS
    scene = Path("tasks/io/chat_ui/openspace_scene.js").read_text(encoding="utf-8")
    assert "const leftInt = 100 - Math.round(pct * 100);" in scene
    assert "rec.battFill.style.width = leftInt + '%'" in scene
    assert "(100 - Math.round(pct * 100)) + '%'" in scene
    editor = Path("tasks/io/chat_ui/context_editor.js").read_text(encoding="utf-8")
    assert "Math.round((1 - pct) * 1000) / 10" in editor
    assert "t('contextRemainingPct', { pct: leftTxt })" in editor
    for lang in ("en", "fr", "es"):
        catalog = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json").read_text(encoding="utf-8"))
        assert "{pct}" in catalog["contextRemainingPct"]
        assert "{pct}" in catalog["contextGaugeTitle"]


def test_update_available_icon_opens_the_updates_screen():
    start = TEMPLATE.index('id="updateAvailableBtn"')
    button = TEMPLATE[TEMPLATE.rindex("<button", 0, start):
                      TEMPLATE.index("</button>", start)]
    assert 'onclick="openUpdatesDialog()"' in button
    assert "header-dock-item" in button  # dock tooltip + hover zoom
    assert 'style="display:none"' in button  # hidden until an update exists
    # It sits right beside the notification icon.
    assert (TEMPLATE.index('id="updateAvailableBtn"')
            < TEMPLATE.index('id="notificationCenterBtn"'))
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")
    assert "function refreshUpdateBadge()" in admin_js
    assert "admin_check_updates" in admin_js
    assert "c.update_available" in admin_js


def test_send_is_an_icon_button_sized_like_attach_with_hover_zoom():
    start = TEMPLATE.index('id="sendBtn"')
    button = TEMPLATE[TEMPLATE.rindex("<button", 0, start):
                      TEMPLATE.index("</button>", start)]
    assert "<svg" in button
    assert 'data-i18n-aria-label="send"' in button
    assert 'data-i18n="send"' not in button
    assert "#sendBtn { padding: 10px 12px; font-size: 18px;" in TEMPLATE
    assert "#fileAttachBtn:hover, #sendBtn:hover { transform: scale(1.4);" in TEMPLATE
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")
    assert "_setText('#sendBtn'" not in i18n_js


def test_i18n_keys_present_in_all_languages():
    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        for key in ("headerGripTitle", "sidebarGripTitle", "actionStatusBtnTitle",
                    "ctxGaugeBtnTitle"):
            assert data[key], (lang, key)
