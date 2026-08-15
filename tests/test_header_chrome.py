"""Header chrome: collapsible panels behind grips + icon/popover widgets.

The three chrome zones (header bar, left sidebar, composer drawer) each fold
completely behind a small grip (a square showing 3 vertical lines) and are
CLOSED by default so the transcript takes almost the whole screen on load.

Three header widgets became compact icon buttons whose full content lives in
a click-toggled popover (click shows, click again hides):
- Active agents: person icon + active-count badge; the Active Agents box
  mounts inside its popover (it left the composer entirely).
- Pending actions: animated glyph while actions run, idle checkmark
  otherwise; label + progress bar in the popover.
- Context gauge: battery-style icon whose fill mirrors the percentage; the
  full agent badge (label + gauge) in the popover.
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
    for grip_id in ("headerGrip", "sidebarToggle", "composerDrawerHandle"):
        start = TEMPLATE.index(f'id="{grip_id}"')
        button = TEMPLATE[TEMPLATE.rindex("<button", 0, start):
                          TEMPLATE.index("</button>", start)]
        assert "pf-grip" in button, grip_id
        assert 'class="pf-grip-bars"' in button, grip_id


def test_header_bar_folds_behind_a_top_grip_and_defaults_closed():
    # Markup starts collapsed (no flash before DOMContentLoaded).
    assert '<div class="header collapsed" id="headerBar">' in TEMPLATE
    assert ".header.collapsed { display: none; }" in TEMPLATE
    assert 'onclick="toggleHeaderBar()"' in TEMPLATE
    assert ".pf-grip-top { position: fixed; top: 0; left: 50%;" in TEMPLATE
    # Open only when the stored flag is explicitly '1': fresh browser = CLOSED.
    assert "localStorage.getItem(_HEADER_BAR_KEY) === '1'" in STATE_JS
    assert "localStorage.setItem(_HEADER_BAR_KEY" in STATE_JS
    assert "document.addEventListener('DOMContentLoaded', _applyHeaderBar)" in STATE_JS


def test_sidebar_grip_is_an_edge_tab_and_sidebar_defaults_closed():
    assert '<div class="sidebar collapsed" id="sidebar">' in TEMPLATE
    assert ".sidebar-toggle { position: fixed; top: 50%; left: 0;" in TEMPLATE
    # toggleSidebar keeps the grip glued to the drawer's edge.
    assert "btn.style.left = collapsed ? '0px' : '260px'" in STATE_JS
    assert "&#9776;" not in TEMPLATE  # old hamburger glyph is gone


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
    assert 'id="actionStatusIdle"' in TEMPLATE
    # The loading label + progress bar moved inside the popover.
    pop = TEMPLATE[TEMPLATE.index('id="actionStatusPop"'):
                   TEMPLATE.index('id="ctxGaugeWrap"')]
    assert 'id="actionLoading"' in pop
    assert ".hdr-icon-btn.working .hdr-action-icon { animation: spin" in TEMPLATE
    # rxbus drives icon state + idle text together with the label.
    assert "statusBtn.classList.toggle('working', isWorking)" in RXBUS_JS
    assert "statusIcon.innerHTML = isWorking ? '\\u273B' : '\\u2713'" in RXBUS_JS
    assert "statusIdle.hidden = isWorking" in RXBUS_JS


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
    assert "gaugeFill.style.height = pctInt + '%'" in CMD_AGENT_JS
    assert "gaugePct.textContent = pctInt + '%'" in CMD_AGENT_JS
    assert "gaugeWrap.style.display = ''" in CMD_AGENT_JS


def test_i18n_keys_present_in_all_languages():
    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        for key in ("headerGripTitle", "sidebarGripTitle", "actionStatusBtnTitle",
                    "actionStatusIdle", "ctxGaugeBtnTitle"):
            assert data[key], (lang, key)
