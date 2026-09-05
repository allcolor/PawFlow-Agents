"""Regression gates for simultaneous tiled conversation scroll work."""

from test_webchat_motion_browser import CHAT_UI, _shell_html, chromium_browser  # noqa: F401

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
