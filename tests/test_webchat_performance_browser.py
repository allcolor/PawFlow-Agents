"""Browser proofs for batched streaming and offscreen OpenSpace work."""

from test_webchat_motion_browser import CHAT_UI, chromium_browser  # noqa: F401


def test_offscreen_openspace_pauses_frames_and_flow_requests(chromium_browser):
    context = chromium_browser.new_context(viewport={"width": 420, "height": 500})
    page = context.new_page()
    try:
        page.set_content("""
          <div id="scroller" style="width:300px;height:300px;overflow:auto">
            <div style="width:900px;height:300px;position:relative">
              <div id="openspaceWrap" style="position:absolute;left:600px;width:300px;height:300px"></div>
            </div>
          </div>""")
        for source in ("openspace.js", "openspace_runtime.js", "openspace_flow.js"):
            page.add_script_tag(path=str(CHAT_UI / source))
        page.evaluate("""() => {
          window.counts = {frames: 0, polls: 0};
          _osActive = true;
          _osRenderer = {render() { counts.frames++; }};
          _osTick = _osAdaptPixelRatio = _osRenderScreenOcclusion = () => {};
          _osFlow = {id:'fixture', stack:[{}], timer:null};
          _osFlowApply = () => {};
          window.action$ = () => ({subscribe(observer) {
            counts.polls++; observer.next({nodes:{}, edges:[]});
          }});
          _osObserveVisibility(document.getElementById('openspaceWrap'));
        }""")
        page.wait_for_timeout(100)
        assert page.evaluate("counts") == {"frames": 0, "polls": 0}
        page.evaluate("document.getElementById('scroller').scrollLeft = 600")
        page.wait_for_function("counts.frames >= 3 && counts.polls === 1")
        assert page.evaluate("document.activeElement.id") != "openspaceWrap"
        page.evaluate("document.getElementById('scroller').scrollLeft = 0")
        page.wait_for_function("!_osSurfaceVisible && !_osRaf && !_osFlow.timer")
        stopped = page.evaluate("({...counts})")
        page.evaluate("_osFlowPoll()")
        page.wait_for_timeout(100)
        assert page.evaluate("counts") == stopped
        assert page.evaluate("_osFlow.id") == "fixture"
        page.evaluate("document.getElementById('scroller').scrollLeft = 600")
        page.wait_for_function("counts.polls === 2 && !!_osRaf && !!_osFlow.timer")
        page.evaluate("""() => {
          Object.defineProperty(document, 'hidden', {configurable:true, value:true});
          _osVisibility(); _osFlowPoll();
        }""")
        hidden = page.evaluate("({...counts})")
        page.wait_for_timeout(100)
        assert page.evaluate("counts") == hidden
        assert page.evaluate("!_osRaf && !_osFlow.timer")
        page.evaluate("""() => {
          delete document.hidden; _osVisibility();
        }""")
        page.wait_for_function("counts.polls === 3 && !!_osRaf")
    finally:
        context.close()


def test_streaming_browser_keeps_complete_markdown_and_final_boundary(chromium_browser):
    context = chromium_browser.new_context()
    page = context.new_page()
    try:
        page.set_content('<div id="messages"></div><div id="status"></div>')
        state = (CHAT_UI / "state.js").read_text(encoding="utf-8")
        page.add_script_tag(content=state[
            state.index("// Per-agent streaming state"):
            state.index("let permissionMode =")
        ])
        page.evaluate(r"""() => {
          window.listeners = {};
          window.eventSource = {addEventListener(name, fn) {
            (listeners[name] ||= []).push(fn);
          }};
          window.captureConversationSessionCallback = fn => fn;
          window.sourceBadge = () => '';
          window.t = key => key;
          window.escapeHtml = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
          window.finalizeThinking = window.finalizeThinkingFromEvent = () => {};
          window.collapseTechnicalGroups = window.turnViewIngest = () => {};
          window.isNearBottom = () => false;
          window._noteLiveHistoryAppend = () => {};
          window._CONTEXT_ACKS = new Set();
          window._seenMsgIds = new Set();
          window.addMsg = (role, text, data) => {
            const el = document.createElement('article');
            el.className = 'msg streaming'; el.dataset.msgid = data.msg_id;
            el.innerHTML = '<span class="msg-content"></span>';
            document.getElementById('messages').appendChild(el); return el;
          };
          window.emit = (name, data) =>
            (listeners[name] || []).forEach(fn => fn({data:JSON.stringify(data)}));
        }""")
        page.add_script_tag(path=str(CHAT_UI / "messages_markdown.js"))
        page.add_script_tag(path=str(CHAT_UI / "sse_handlers_a.js"))
        result = page.evaluate(r"""() => {
          window.scrollBottom = () => {};
          _sseWireA();
          const original = renderMarkdown;
          let renders = 0;
          window.renderMarkdown = text => { renders++; return original(text); };
          const tick = String.fromCharCode(96);
          const text = '# Heading\n\n' + ('**bold** and ' + tick + 'code' + tick + ' with <escaped>.\n\n').repeat(64);
          const expected = original(text);
          for (const char of text) emit('token', {agent_name:'alpha', msg_id:'m1', text:char});
          const before = renders;
          emit('turn_complete', {agent_name:'alpha'});
          return {before, renders, expected,
            actual:document.querySelector('.msg-content').innerHTML,
            complete:getStream('alpha').lastText === text,
            pending:!!getStream('alpha').pendingRender};
        }""")
        assert result["before"] == 0
        assert result["renders"] == 1
        assert result["actual"] == result["expected"]
        assert result["complete"] and not result["pending"]
    finally:
        context.close()
