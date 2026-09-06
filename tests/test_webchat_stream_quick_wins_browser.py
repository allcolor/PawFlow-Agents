"""Browser regressions for BTW batching and agent-scoped SSE flushes."""

import pytest

from test_webchat_motion_browser import CHAT_UI, chromium_browser  # noqa: F401
from test_webchat_tiled_performance_browser import _two_conversations


def _wire_streams(page):
    page.evaluate(r"""() => {
      window.listeners = {};
      window.eventSource = {addEventListener(name, fn) {
        (listeners[name] ||= []).push(fn);
      }};
      if (typeof captureConversationSessionCallback !== 'function') {
        window.captureConversationSessionCallback = fn => fn;
      }
      window.frames = new Map(); window.timers = new Map();
      let sequence = 0;
      window.requestAnimationFrame = fn => {
        const id = ++sequence; frames.set(id, fn); return id;
      };
      window.cancelAnimationFrame = id => frames.delete(id);
      window.setTimeout = (fn, delay) => {
        const id = ++sequence; timers.set(id, {fn, delay}); return id;
      };
      window.clearTimeout = id => timers.delete(id);
      window.frame = () => {
        const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn());
      };
      window.timer = () => {
        const batch = [...timers.values()]; timers.clear(); batch.forEach(item => item.fn());
      };
      window.renders = []; window.scrolls = 0; window.deltas = [];
      window.originalMarkdown = renderMarkdown;
      window.renderMarkdown = text => {
        renders.push(text); return originalMarkdown(text);
      };
      window.scrollBottom = () => { scrolls++; };
      window.sourceBadge = window.buildMetaLine = () => '';
      window.makeTimeHtml = () => '<span class="msg-time">12:00</span>';
      window.t = window.displayAgentName = value => value;
      window.escapeHtml = s => String(s).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
      for (const name of ['finalizeThinking', 'finalizeThinkingFromEvent',
        'collapseTechnicalGroups', 'trackAgentStart', 'trackAgentDone',
        'trackAgentTool', 'trackAgentToolDone', '_noteLiveHistoryAppend',
        '_finalizeLiveToolCalls', 'syncActiveFromServer', 'updateActivePanel',
        'hideTyping', 'loadConversations', 'pawflowDebugLog', '_finalizeTaskBlock',
        '_insertMessageChronologically', 'applyTechnicalMessageGrouping']) {
        window[name] = () => {};
      }
      window.isNearBottom = () => false;
      window.isAgentTerminalCurrent = () => true;
      window._CONTEXT_ACKS = new Set(); window._seenMsgIds = new Set();
      window.activeInteractions = {}; window.activeTimer = null;
      window.btwElements = {}; window.btwTexts = {};
      window._taskBlocks = {}; window._delegateGroups = {}; window._TOOL_DISPLAY = {};
      window._attachPendingToolResult = window._queueUnmatchedToolResult = () => false;
      window._resultText = value => value;
      window.simplified = false;
      window.turnViewIsSimplified = () => simplified;
      window.boundaryRenders = [];
      window.turnViewFail = window.turnViewFinalize = window.turnViewRegisterUser = () => {
        boundaryRenders.push([...renders]);
      };
      window.turnViewIngest = (kind, data) => {
        if (kind === 'token') deltas.push(data.text);
      };
      window.renderThinkingContent = () => null;
      window.addMsg = (role, text, data = {}) => {
        if (data.msg_id && _seenMsgIds.has(data.msg_id)) return null;
        if (data.msg_id) _seenMsgIds.add(data.msg_id);
        const el = document.createElement('article');
        el.className = 'msg ' + role + (role === 'assistant' && !text ? ' streaming' : '');
        el.dataset.msgid = data.msg_id || '';
        el.innerHTML = '<span class="msg-content"></span>';
        document.getElementById('messages').appendChild(el);
        return el;
      };
      window.emit = (name, data = {}) =>
        (listeners[name] || []).forEach(fn => fn({data: JSON.stringify(data)}));
      window.token = (agent, text, msg_id = agent) =>
        emit('token', {agent_name: agent, text, msg_id});
      window.btw = (agent, text) => {
        emit('btw_thinking', {agent_name: agent});
        emit('btw_token', {agent_name: agent, text});
        return btwElements[agent.toLowerCase()];
      };
    }""")
    for source in ("sse_handlers_a.js", "sse_handlers_b.js"):
        page.add_script_tag(path=str(CHAT_UI / source))
    page.evaluate("_sseWireA(); _sseWireB()")


@pytest.fixture
def stream_page(chromium_browser):
    context = chromium_browser.new_context()
    page = context.new_page()
    page.set_content(
        '<div id="messages"></div><div id="status"></div><button id="sendBtn"></button>'
    )
    state = (CHAT_UI / "state.js").read_text(encoding="utf-8")
    page.add_script_tag(content=state[
        state.index("// Per-agent streaming state"):
        state.index("let permissionMode =")
    ])
    page.add_script_tag(path=str(CHAT_UI / "messages_markdown.js"))
    _wire_streams(page)
    yield page
    context.close()


@pytest.mark.parametrize("clock", ["frame", "timer"])
def test_btw_burst_batches_markdown_scroll_and_keeps_main_stream_separate(stream_page, clock):
    result = stream_page.evaluate(r"""clock => {
      const text = '# Heading\n\n' + '**bold** and <escaped>.\n\n'.repeat(64);
      const a = btw('Alpha', ''), b = btw('Beta', '');
      scrolls = 0;
      for (const char of text) {
        emit('btw_token', {agent_name:'Alpha', text:char});
        emit('btw_token', {agent_name:'Beta', text:char});
        token('Alpha', char);
      }
      const before = {renders:renders.length, scrolls, frames:frames.size,
        delays:[...timers.values()].map(item => item.delay),
        text:btwTexts.alpha === text && btwTexts.beta === text};
      window[clock](); frame(); timer();
      return {before, renders:renders.length, scrolls,
        main:document.querySelector('.msg-content').innerHTML === originalMarkdown(text),
        a:a.innerHTML.endsWith(originalMarkdown(text)),
        b:b.innerHTML.endsWith(originalMarkdown(text)),
        text:getStream('Alpha').text === text && deltas.join('') === text,
        frames:frames.size, timers:timers.size};
    }""", clock)
    assert result == {
        "before": {"renders": 0, "scrolls": 0, "frames": 3, "delays": [50, 50, 50], "text": True},
        "renders": 3, "scrolls": 3, "main": True, "a": True, "b": True,
        "text": True, "frames": 0, "timers": 0,
    }


@pytest.mark.parametrize("outcome", ["streamed", "error", "fallback"])
def test_btw_done_flushes_before_cleanup_and_cannot_be_overwritten(stream_page, outcome):
    result = stream_page.evaluate("""outcome => {
      const el = btw('Alpha', outcome === 'fallback' ? '' : '**complete**');
      emit('btw_done', {agent_name:'Alpha',
        response: outcome === 'fallback' ? '**fallback**' : 'unused response',
        error: outcome === 'error' ? '<failed>' : ''});
      const final = el.innerHTML, count = renders.length;
      frame(); timer();
      return {final, unchanged:el.innerHTML === final && renders.length === count,
        clean:!btwElements.alpha && !btwTexts.alpha && !getStream('Alpha').btw,
        frames:frames.size, timers:timers.size};
    }""", outcome)
    assert result["unchanged"] and result["clean"]
    assert result["frames"] == result["timers"] == 0
    expected = {"streamed": "<strong>complete</strong>",
                "fallback": "<strong>fallback</strong>", "error": "&lt;failed&gt;"}[outcome]
    assert expected in result["final"]


def test_btw_restart_flushes_previous_row_and_removed_row_stays_removed(stream_page):
    result = stream_page.evaluate("""() => {
      const old = btw('Alpha', 'old');
      const next = btw('Alpha', 'new');
      const previous = old.innerHTML;
      next.remove(); frame(); timer();
      return {previous, unchanged:old.innerHTML === previous,
        renders, frames:frames.size, timers:timers.size};
    }""")
    assert result["previous"].endswith("old")
    assert result["unchanged"]
    assert result["renders"] == ["old"]
    assert result["frames"] == result["timers"] == 0


@pytest.mark.parametrize("event,identity", [
    ("thinking", {"agent_name": "ALPHA"}),
    ("thinking_delta", {"agent_name": "ALPHA"}),
    ("thinking_content", {"source": {"from": "ALPHA"}}),
    ("turn_complete", {"agent_name": "ALPHA"}),
])
def test_agent_local_events_do_not_repaint_interleaved_other_agent(stream_page, event, identity):
    result = stream_page.evaluate("""({event, identity}) => {
      for (let i = 0; i < 20; i++) {
        token('alpha', 'A', event === 'turn_complete' ? 'alpha-' + i : 'alpha');
        token('beta', 'B'); emit(event, identity);
      }
      const before = {renders:[...renders], betaPending:!!getStream('beta').pendingRender};
      frame(); timer();
      return {before, beta:renders.filter(text => text.includes('B')), deltas:deltas.join('')};
    }""", {"event": event, "identity": identity})
    assert result["before"]["betaPending"]
    assert all("B" not in text for text in result["before"]["renders"])
    assert result["beta"] == ["B" * 20]


@pytest.mark.parametrize("event,data", [
    ("thinking", {}),
    ("new_message", {"role": "user", "content": "next", "agent_name": "alpha"}),
    ("new_message", {"role": "assistant", "content": "persisted", "agent_name": "alpha", "msg_id": "alpha"}),
    ("tool_call", {"agent_name": "alpha", "tool": "read"}),
    ("tool_result", {"agent_name": "alpha", "result": "result"}),
    ("task_progress", {"agent": "alpha", "stage": "assigned"}),
    ("error", {}),
])
def test_shared_boundaries_and_missing_agent_flush_all_streams(stream_page, event, data):
    result = stream_page.evaluate("""({event, data}) => {
      token('alpha', 'A'); token('beta', 'B');
      emit(event, data);
      const before = [...renders]; frame(); timer();
      return {before, renders};
    }""", {"event": event, "data": data})
    assert result["before"][:2] == ["A", "B"]
    assert result["renders"] == result["before"]


@pytest.mark.parametrize("event,data", [
    ("done", {"source": {"name": "alpha"}}),
    ("discard", {"agent_name": "alpha"}),
    ("active_released", {"agent_name": "alpha"}),
    ("cancelled", {"agent_name": "alpha"}),
    ("error_event", {"agent_name": "alpha", "message": "failed"}),
    ("task_stopped", {"agent_name": "alpha", "task_id": "task"}),
])
def test_terminal_events_flush_btw_and_main_without_losing_final_text(stream_page, event, data):
    result = stream_page.evaluate("""({event, data}) => {
      token('alpha', 'main A'); token('beta', 'main B');
      const a = btw('alpha', 'BTW A'), b = btw('beta', 'BTW B');
      const main = getStream('alpha').el;
      emit(event, data);
      const before = [...renders], final = a.innerHTML;
      frame(); timer();
      return {before, renders, final, unchanged:a.innerHTML === final,
        main:main.querySelector('.msg-content').innerHTML,
        retained:event === 'discard' || main.isConnected,
        b:b.innerHTML.endsWith('BTW B')};
    }""", {"event": event, "data": data})
    assert result["before"][:2] == ["BTW A", "main A"]
    if event != "task_stopped":
        assert result["before"] == ["BTW A", "main A"]
    assert sorted(result["renders"]) == ["BTW A", "BTW B", "main A", "main B"]
    assert result["final"].endswith("BTW A")
    assert result["unchanged"] and result["retained"] and result["b"]
    assert result["main"] == "main A"


@pytest.mark.parametrize("event", ["done", "discard", "active_released", "cancelled", "error_event"])
def test_simplified_terminal_flushes_shared_turn_before_finalization(stream_page, event):
    result = stream_page.evaluate("""event => {
      simplified = true;
      token('alpha', 'A'); token('beta', 'B');
      emit(event, {agent_name:'alpha'});
      return {renders, boundaryRenders};
    }""", event)
    assert result["renders"] == ["A", "B"]
    assert result["boundaryRenders"]
    assert all(snapshot == ["A", "B"] for snapshot in result["boundaryRenders"])


def test_cancel_all_flushes_every_btw_before_retiring_streams(stream_page):
    result = stream_page.evaluate("""() => {
      const a = btw('alpha', 'A'), b = btw('beta', 'B');
      emit('cancelled', {});
      frame(); timer();
      return {renders, streams:Object.keys(streams), a:a.innerHTML, b:b.innerHTML};
    }""")
    assert result["renders"] == ["A", "B"]
    assert result["streams"] == []
    assert result["a"].endswith("A") and result["b"].endswith("B")


def test_message_rotation_and_durable_reconciliation_keep_other_agent_pending(stream_page):
    result = stream_page.evaluate("""() => {
      token('alpha', 'old', 'old'); token('beta', 'B', 'beta');
      const old = getStream('alpha').el;
      token('alpha', 'draft', 'new');
      const rotated = {renders:[...renders], betaPending:!!getStream('beta').pendingRender};
      emit('new_message', {role:'assistant', agent_name:'alpha',
        msg_id:'durable', content:'corrected'});
      frame(); timer();
      return {rotated, old:old.querySelector('.msg-content').innerHTML,
        final:document.querySelector('[data-msgid="durable"] .msg-content').innerHTML,
        renders, beta:getStream('beta').text};
    }""")
    assert result["rotated"] == {"renders": ["old"], "betaPending": True}
    assert result["old"] == "old" and result["final"] == "corrected"
    assert result["beta"] == "B"
    assert result["renders"] == ["old", "draft", "B", "corrected"]


def test_btw_callbacks_follow_real_conversation_sessions_and_reload_cleanup(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        _wire_streams(page)
        result = page.evaluate("""() => {
          let aBtw, bBtw;
          withConversationSession(a, () => { aBtw = btw('alpha', 'session A'); });
          withConversationSession(b, () => { bBtw = btw('alpha', 'session B'); });
          focusConversationSession(b, {project:false});
          frame(); timer();
          const rendered = {a:aBtw.innerHTML, b:bBtw.innerHTML,
            focused:focusedConversationId(), active:captureConversationSession().conversationId};
          withConversationSession(a, () => {
            emit('btw_token', {agent_name:'alpha', text:' final'});
            clearAllStreams();
          });
          frame(); timer();
          return {rendered, a:aBtw.innerHTML, b:bBtw.innerHTML,
            renders, frames:frames.size, timers:timers.size,
            focused:focusedConversationId()};
        }""")
        assert result["rendered"]["a"].endswith("session A")
        assert result["rendered"]["b"].endswith("session B")
        assert result["rendered"]["focused"] == result["rendered"]["active"] == "B"
        assert result["a"].endswith("session A final")
        assert result["b"].endswith("session B")
        assert result["renders"] == ["session A", "session B", "session A final"]
        assert result["frames"] == result["timers"] == 0
        assert result["focused"] == "B"
    finally:
        context.close()


def test_closed_conversation_cancels_btw_render_callback(chromium_browser):
    context, page = _two_conversations(chromium_browser)
    try:
        _wire_streams(page)
        result = page.evaluate("""() => {
          withConversationSession(b, () => btw('alpha', 'closed'));
          closeConversationSession('B');
          frame(); timer();
          return {renders, frames:frames.size, timers:timers.size,
            sessions:_conversationSessions.size};
        }""")
        assert result == {"renders": [], "frames": 0, "timers": 0, "sessions": 1}
    finally:
        context.close()
