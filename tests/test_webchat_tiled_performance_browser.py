"""Regression gates for simultaneous tiled conversation scroll work."""

import pytest

from test_webchat_motion_browser import CHAT_UI, _shell_html, chromium_browser  # noqa: F401


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("atmosphere", [False, True])
def test_composer_grip_hit_area_stays_above_dock_gutter(
        chromium_browser, width, atmosphere):
    context = chromium_browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate("""atmosphere => {
          const values = new Map([['pawflow.composerDrawerOpen', '1']]);
          Object.defineProperty(window, 'localStorage', {
            configurable: true,
            value: {
              getItem: key => values.get(key) || null,
              setItem: (key, value) => values.set(key, String(value)),
            },
          });
          window.LOGIN_URL = '';
          if (atmosphere) document.documentElement.dataset.pfAtmosphere = 'on';
        }""", atmosphere)
        for source in ("ui_motion.js", "state.js"):
            page.add_script_tag(path=str(CHAT_UI / source))
        page.evaluate("""async () => {
          mountComposerChrome();
          document.getElementById('actionMenuWrap').style.display = 'block';
          document.getElementById('promptControlsPanel').classList.add('visible');
          await _applyComposerDrawer(false);
        }""")
        for y_fraction in (0.8, 0.2, 0.8):
            hits = page.evaluate("""() => {
              const handle = document.getElementById('composerDrawerHandle');
              const rect = handle.getBoundingClientRect();
              const missed = [];
              for (const x of [0.2, 0.5, 0.8]) {
                for (const y of [0.2, 0.5, 0.8]) {
                  const hit = document.elementFromPoint(rect.x + x * rect.width, rect.y + y * rect.height);
                  if (!handle.contains(hit)) missed.push({x, y, blocker: hit && (hit.id || hit.className)});
                }
              }
              return {missed, width: rect.width, height: rect.height};
            }""")
            assert hits["missed"] == [], hits
            assert hits["width"] >= 36 and hits["height"] >= 15, hits
            handle = page.locator('#composerDrawerHandle')
            was_open = handle.get_attribute('aria-expanded') == 'true'
            rect = handle.bounding_box()
            page.mouse.click(rect['x'] + rect['width'] / 2,
                             rect['y'] + rect['height'] * y_fraction)
            page.wait_for_function("""wasOpen => {
              const handle = document.getElementById('composerDrawerHandle');
              const area = document.querySelector('.input-area');
              return handle.getAttribute('aria-expanded') === String(!wasOpen)
                && area.classList.contains('composer-drawer-collapsed') === wasOpen
                && !area.querySelector('.composer-context-row').getAnimations().length;
            }""", arg=was_open)
            page.mouse.move(0, 0)
    finally:
        context.close()


def test_closed_tile_releases_scroll_listeners_and_resource_timer(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        result = page.evaluate("""() => {
          const nativeAdd = window.addEventListener;
          const nativeRemove = window.removeEventListener;
          const nativeSet = window.setInterval;
          const nativeClear = window.clearInterval;
          const listeners = new Set();
          const intervals = new Set();
          window.addEventListener = function(type, callback, options) {
            if (type === 'pointerup') listeners.add(callback);
            return nativeAdd.call(this, type, callback, options);
          };
          window.removeEventListener = function(type, callback, options) {
            if (type === 'pointerup') listeners.delete(callback);
            return nativeRemove.call(this, type, callback, options);
          };
          window.setInterval = function(callback, delay) {
            const id = nativeSet(callback, delay); intervals.add(id); return id;
          };
          window.clearInterval = function(id) { intervals.delete(id); nativeClear(id); };
          for (let i = 0; i < 12; i++) {
            const session = ensureConversationSession('closed-' + i);
            withConversationSession(session, () => {
              resourcesTimer = setInterval(() => {}, 60000);
              scrollBottom();
            });
            closeConversationSession(session.conversationId);
          }
          const result = {listeners: listeners.size, intervals: intervals.size,
                          sessions: _conversationSessions.size};
          for (const id of intervals) nativeClear(id);
          window.addEventListener = nativeAdd; window.removeEventListener = nativeRemove;
          window.setInterval = nativeSet; window.clearInterval = nativeClear;
          return result;
        }""")
        assert result == {"listeners": 0, "intervals": 0, "sessions": 2}
    finally:
        context.close()


@pytest.mark.parametrize("focused", ["A", "B"])
@pytest.mark.parametrize("surface", ["desktop", "conversation"])
@pytest.mark.parametrize("reduced", [False, True])
def test_maximize_clicked_tile_remains_visible_and_mounted(
        chromium_browser, focused, surface, reduced):
    context, page = _two_conversations(chromium_browser)
    try:
        page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
        for source in ("ui_motion.js", "tabs.js"):
            page.add_script_tag(path=str(CHAT_UI / source))
        target = page.evaluate("""({focused, surface}) => {
          _workspaceSurfaces[b.surfaceId].slot = 2;
          const panel = document.createElement('section');
          panel.id = 'tabContent_desktop-test';
          panel.className = 'tab-content';
          const iframe = document.createElement('iframe');
          iframe.srcdoc = '<p>Persistent desktop</p>';
          panel.appendChild(iframe);
          workspaceRegisterSurface(panel, {
            tabId: 'desktop-test', type: 'desktop', title: 'Desktop',
            conversationId: 'B',
          });
          _workspaceSurfaces['desktop-test'].slot = 3;
          _workspaceRenderSlots();
          _workspaceResize();
          window.desktopFrame = iframe;
          window.desktopWindow = iframe.contentWindow;
          switchTab(focused === 'A' ? a.surfaceId : b.surfaceId);
          return surface === 'desktop' ? 'desktop-test' : b.surfaceId;
        }""", {"focused": focused, "surface": surface})
        button = page.locator(
            f'[data-tab="{target}"] > .workspace-surface-header .workspace-maximize-btn'
        )
        button.click()
        result = page.evaluate("""async target => {
          await _workspaceLayoutTransition;
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const panel = _workspaceSurfaces[target].panel;
          const tile = panel.getBoundingClientRect();
          const viewport = document.getElementById('workspaceScroller').getBoundingClientRect();
          return {
            selected: workspaceSelectedTab(), layout: workspaceLayout(),
            focused: focusedConversationId(),
            visible: tile.left >= viewport.left - 1 && tile.right <= viewport.right + 1,
            fullWidth: Math.abs(tile.width - viewport.width) <= 2,
            mounted: desktopFrame.isConnected && desktopFrame.contentWindow === desktopWindow,
          };
        }""", target)
        assert result == {
            "selected": target, "layout": 1, "focused": "B",
            "visible": True, "fullWidth": True, "mounted": True,
        }, result
        button.click()
        restored = page.evaluate("""async target => {
          await _workspaceLayoutTransition;
          const tile = _workspaceSurfaces[target].panel.getBoundingClientRect();
          const viewport = document.getElementById('workspaceScroller').getBoundingClientRect();
          return {
            layout: workspaceLayout(), selected: workspaceSelectedTab(),
            visible: tile.left >= viewport.left - 1 && tile.right <= viewport.right + 1,
            mounted: desktopFrame.isConnected && desktopFrame.contentWindow === desktopWindow,
            slots: [a.surfaceId, b.surfaceId, 'desktop-test'].map(id => _workspaceSurfaces[id].slot),
          };
        }""", target)
        assert restored == {
            "layout": 4, "selected": target, "visible": True, "mounted": True,
            "slots": [0, 2, 3],
        }, restored
    finally:
        context.close()

def test_dock_hover_paints_above_its_background(chromium_browser):
    context = chromium_browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate("""() => {
          const dock = document.getElementById('actionMenuWrap');
          document.body.appendChild(dock);
          dock.style.cssText = 'display:block;position:fixed;left:250px;bottom:100px;max-width:360px';
        }""")
        item = page.locator('#actionMenu > .action-menu-item').nth(1)
        item.hover()
        page.wait_for_timeout(250)
        result = item.evaluate("""item => {
          const rect = item.getBoundingClientRect();
          const dock = document.getElementById('actionMenuWrap').getBoundingClientRect();
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.top + 2);
          const menu = document.getElementById('actionMenu');
          return {above: rect.top < dock.top, hit: item === hit || item.contains(hit),
                  scrollable: menu.scrollWidth > menu.clientWidth};
        }""")
        assert result == {"above": True, "hit": True, "scrollable": True}, result
    finally:
        context.close()


def _two_conversations(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.set_content(_shell_html(), wait_until="domcontentloaded")
    state = (CHAT_UI / "state.js").read_text(encoding="utf-8")
    page.add_script_tag(content=state[
        state.index("// Per-agent streaming state"):
        state.index("let permissionMode =")
    ])
    for source in ("workspace.js", "messages_markdown.js", "conversation_sessions.js"):
        page.add_script_tag(path=str(CHAT_UI / source))
    page.evaluate("""async () => {
      await workspaceSetLayout(4);
      window.a = ensureConversationSession('A');
      focusConversationSession(a, {project: false});
      window.b = ensureConversationSession('B');
      focusConversationSession(b, {project: false});
      for (const session of [a, b]) {
        for (let i = 0; i < 150; i++) {
          const row = document.createElement('article');
          row.className = 'msg';
          row.id = 'row-' + i;
          row.dataset.msgid = session.conversationId + '-' + i;
          row.textContent = 'A representative message in a running conversation ' + i;
          session.messagesRoot.appendChild(row);
        }
        withConversationSession(session, () => scrollBottom(true));
      }
      focusConversationSession(a, {project: false});
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }""")
    return context, page


def test_background_event_does_not_force_scroll_layout(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        result = page.evaluate("""() => {
          const reads = [];
          for (const session of [a, b]) {
            for (const property of ['scrollTop', 'scrollHeight']) {
              const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, property);
              Object.defineProperty(session.messagesRoot, property, {
                configurable: true,
                get() { reads.push(property); return descriptor.get.call(this); },
                set(value) { reads.push(property + '='); descriptor.set.call(this, value); }
              });
            }
          }
          withConversationSession(b, () => {
            b.messagesRoot.lastElementChild.textContent += ' streamed';
            selectedAgent = 'agent-b';
          });
          for (const session of [a, b]) {
            delete session.messagesRoot.scrollTop;
            delete session.messagesRoot.scrollHeight;
          }
          return {reads, agent: b.selectedAgent, focused: focusedConversationId()};
        }""")
        assert result['reads'] == [], result
        assert result['agent'] == 'agent-b'
        assert result['focused'] == 'A'
    finally:
        context.close()


def test_two_tiles_scroll_events_do_not_rewrite_transcript_identity(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        result = page.evaluate("""() => {
          const observer = new MutationObserver(() => {});
          observer.observe(document.getElementById('workspaceBoard'), {
            attributes: true, subtree: true, attributeFilter: ['id']
          });
          b.messagesRoot.dispatchEvent(new WheelEvent('wheel', {deltaY: -100}));
          b.messagesRoot.scrollTop = 300;
          b.messagesRoot.dispatchEvent(new Event('scroll'));
          window.dispatchEvent(new PointerEvent('pointerup'));
          const rewrites = observer.takeRecords().length;
          observer.disconnect();
          return {rewrites, position: b.messagesRoot.scrollTop, following: b.autoScroll,
                  focused: focusedConversationId()};
        }""")
        assert result["rewrites"] == 0, result
        assert result["position"] == 300, result
        assert result["following"] is False
        assert result["focused"] == "A"
    finally:
        context.close()


def test_two_tiles_suspended_scroll_work_is_bounded_and_keeps_user_intent(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        result = page.evaluate("""() => {
          const nativeRaf = window.requestAnimationFrame;
          const nativeCancel = window.cancelAnimationFrame;
          const queued = new Map();
          let sequence = 0;
          window.requestAnimationFrame = callback => {
            queued.set(++sequence, callback); return sequence;
          };
          window.cancelAnimationFrame = id => queued.delete(id);
          for (let i = 0; i < 100; i++) {
            for (const session of [a, b]) {
              withConversationSession(session, () => scrollBottom());
            }
          }
          const pending = queued.size;
          b.messagesRoot.dispatchEvent(new WheelEvent('wheel', {deltaY: -100}));
          b.messagesRoot.scrollTop = 250;
          b.messagesRoot.dispatchEvent(new Event('scroll'));
          let callbacks = 0;
          for (let frame = 0; frame < 3; frame++) {
            const batch = Array.from(queued.values()); queued.clear();
            for (const callback of batch) { callbacks++; callback(performance.now()); }
          }
          window.requestAnimationFrame = nativeRaf;
          window.cancelAnimationFrame = nativeCancel;
          return {pending, callbacks, remaining: queued.size,
                  bTop: b.messagesRoot.scrollTop, following: b.autoScroll,
                  aBottom: a.messagesRoot.scrollHeight - a.messagesRoot.clientHeight,
                  aTop: a.messagesRoot.scrollTop, focused: focusedConversationId()};
        }""")
        assert result["pending"] <= 2, result
        assert result["callbacks"] <= 4, result
        assert result["remaining"] == 0
        assert result["bTop"] == 250, result
        assert result["following"] is False
        assert abs(result["aTop"] - result["aBottom"]) <= 1
        assert result["focused"] == "A"
    finally:
        context.close()
