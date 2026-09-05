"""Real-browser reload contract for conversation-only, per-tab workspaces."""

from test_webchat_motion_browser import CHAT_UI, _shell_html, chromium_browser  # noqa: F401


def _boot(page, conversations):
    page.evaluate("""rows => {
      window.conversationId = null;
      window.action$ = () => ({subscribe: callback => callback({conversations: rows})});
      window.renderConvList = () => {};
      window._setInputEnabled = () => {};
      window.loadResources = () => {};
      window.loadedConversations = [];
      window.loadConversationSession = session => {
        session.loaded = true; loadedConversations.push(session.conversationId);
      };
      window.resumeConv = id => openWorkspaceConversation(id);
    }""", conversations)
    for source in ("workspace.js", "messages_markdown.js", "conversation_sessions.js"):
        page.add_script_tag(path=str(CHAT_UI / source))
    boot = (CHAT_UI / "file_explorer.js").read_text(encoding="utf-8")
    page.evaluate(boot[boot.index("// Load conversations and auto-resume the first one"):])


def _workspace(page):
    return page.evaluate("""() => ({
      layout: workspaceLayout(), selected: focusedConversationId(),
      conversations: Array.from(_conversationSessions.keys()).sort(),
      surfaces: Object.values(_workspaceSurfaces).map(entry => ({
        type: entry.type, conversation: entry.conversationId, slot: entry.slot
      })).sort((a, b) => a.slot - b.slot),
      loaded: loadedConversations.slice().sort()
    })""")



def test_reload_restores_only_open_conversations_and_keeps_native_tabs_independent(chromium_browser):
    context = chromium_browser.new_context(viewport={"width": 1280, "height": 800})
    context.route("http://workspace.test/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=_shell_html()))
    first, second = context.new_page(), context.new_page()
    rows = [{"conversation_id": name, "title": "Conversation " + name} for name in ["A", "B", "C"]]
    try:
        first.goto("http://workspace.test/chat")
        _boot(first, rows)
        first.evaluate("""async () => {
          await workspaceSetLayout(4);
          const panel = document.createElement('div');
          workspaceRegisterSurface(panel, {
            tabId: 'terminal-A', type: 'terminal', conversationId: 'A'
          });
          openWorkspaceConversation('B');
          workspaceFocusSurface('terminal-A');
        }""")
        second.goto("http://workspace.test/chat")
        _boot(second, rows)
        second.evaluate("workspaceSetLayout(2)")
        assert _workspace(first)["layout"] == 4
        first.reload()
        _boot(first, rows)
        assert _workspace(first) == {
            "layout": 4, "selected": "A", "conversations": ["A", "B"],
            "surfaces": [
                {"type": "webchat", "conversation": "A", "slot": 0},
                {"type": "webchat", "conversation": "B", "slot": 2},
            ],
            "loaded": ["A", "B"],
        }
        second.reload()
        _boot(second, rows)
        assert _workspace(second)["layout"] == 2
        assert _workspace(second)["conversations"] == ["A"]
        first.evaluate("workspaceFocusSurface(getConversationSession('B').surfaceId)")
        first.reload()
        _boot(first, rows)
        assert _workspace(first)["selected"] == "B"
        assert _workspace(first)["layout"] == 4
    finally:
        context.close()
