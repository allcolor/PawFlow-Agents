"""Browser-level invariants for durable webchat turn and history state."""

import html
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"
CHROMIUM = shutil.which("chromium") or shutil.which("google-chrome")


def _browser_result(tmp_path, *, body, prelude, sources, test):
    if not CHROMIUM:
        pytest.skip("Chromium is required for webchat behavior tests")
    scripts = []
    for source in sources:
        text = (CHAT_UI / source).read_text(encoding="utf-8")
        scripts.append("<script>" + text.replace("</script", r"<\/script") + "</script>")
    page = tmp_path / "webchat-test.html"
    page.write_text(
        "<!doctype html><html><body>" + body
        + '<pre id="result">pending</pre><script>' + prelude + "</script>"
        + "".join(scripts)
        + "<script>try { const value = (() => {" + test
        + "})(); document.getElementById('result').textContent = JSON.stringify({ok:true,value});"
        + "} catch (error) { document.getElementById('result').textContent = "
        + "JSON.stringify({ok:false,error:String(error && error.stack || error)}); }</script></body></html>",
        encoding="utf-8",
    )
    # Every flag here is about determinism in CI, where this hung for 20s and
    # took the build down while passing in a second locally:
    #   * a private user-data-dir -- the default profile is shared, and two of
    #     these tests running at once (xdist) block on its lock;
    #   * dev-shm -- a container's 64 MB /dev/shm wedges the renderer;
    #   * the networking/first-run switches -- a runner with no egress waits on
    #     component update and sync before it ever renders the page;
    #   * a virtual time budget so the page cannot outlive the process timeout.
    proc = subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage",
         f"--user-data-dir={tmp_path / 'chromium-profile'}",
         "--no-first-run", "--no-default-browser-check",
         "--disable-background-networking", "--disable-component-update",
         "--disable-sync", "--disable-extensions",
         "--virtual-time-budget=5000",
         "--allow-file-access-from-files", "--dump-dom", page.as_uri()],
        text=True, capture_output=True, timeout=120, check=True,
    )
    match = re.search(r'<pre id="result">(.*?)</pre>', proc.stdout, re.S)
    assert match, proc.stdout[-2000:]
    result = json.loads(html.unescape(match.group(1)))
    assert result["ok"], result.get("error")
    return result["value"]


def test_turn_controller_keeps_positional_boundaries_and_rehydrates_live(tmp_path):
    value = _browser_result(
        tmp_path,
        body='<div id="messages"></div>',
        prelude=r"""
          window.matchMedia = () => ({matches:false});
          HTMLCanvasElement.prototype.getContext = () => ({
            fillRect(){}, fillText(){}, set fillStyle(_v){}, set font(_v){},
            set textBaseline(_v){}, set globalAlpha(_v){}
          });
          function t(k){ return k; }
          function escapeHtml(s){ return String(s || ''); }
          function displayAgentName(s){ return String(s || ''); }
          function findToolCallElement(){ return null; }
          function parseShowFileArtifact(){ return null; }
        """,
        sources=["turn_view.js"],
        test=r"""
          const box = document.getElementById('messages');
          const row = (id, role, text) => {
            const el = document.createElement('div');
            el.className = 'msg'; el.dataset.msgid = id;
            el.dataset.messageRole = role; el.dataset.rawText = text || '';
            el.textContent = text || role; box.appendChild(el); return el;
          };
          turnViewSetMode('simplified');
          turnViewSetRuntimeTurns([
            {turn_id:'turn-A', started_at:Date.now()/1000 - 12, duration:12,
             status:'thinking', agent_name:'alpha', message_preview:'still working'},
            {turn_id:'turn-B', started_at:Date.now()/1000 - 4, duration:4,
             status:'running', agent_name:'beta'}
          ]);
          // Rows in the order the reader sees them: each partial sits under
          // the user message it followed.
          const uA = row('turn-A', 'user', 'A');
          turnViewRegisterUser({msg_id:'turn-A', turn_id:'turn-A', _history:true}, uA);
          const partialA = row('partial-A', 'assistant', 'partial A');
          turnViewIngest('assistant', {msg_id:'partial-A', turn_id:'turn-A',
            content:'partial A', _history:true}, partialA);
          const uB = row('turn-B', 'user', 'B');
          turnViewRegisterUser({msg_id:'turn-B', turn_id:'turn-B', _history:true}, uB);
          const partialB = row('partial-B', 'assistant', 'partial B');
          turnViewIngest('assistant', {msg_id:'partial-B', turn_id:'turn-B',
            content:'partial B', _history:true}, partialB);
          turnViewReconcile();
          turnViewHydrateRuntimeTurns();
          const a = simplifiedTurns.get('turn-A');
          const b = simplifiedTurns.get('turn-B');
          const hydrated = {
            aWorking:a.status === 'working', aNotFinal:!a.finalEl,
            aElapsed:Date.now() - a.startedAt >= 11000,
            aTimer:!!a.elapsedTimer, aRain:!!a.rainEl, aCue:a.transient.cues.length > 0,
            bPulse:!!b.idleEl,
            filedA:a.tabs.messages.bodyEl.contains(partialA),
            filedB:b.tabs.messages.bodyEl.contains(partialB)
          };
          // The done carries turn-A's id while turn-B is the one on screen.
          // An id NAMES a turn, it never selects one: the open block closes.
          const finalB = row('final-B', 'assistant', 'final B');
          turnViewIngest('assistant', {msg_id:'final-B', turn_id:'turn-A',
            content:'final B'}, finalB);
          turnViewFinalize({turn_id:'turn-A', final_msg_id:'final-B'});
          return {hydrated, aStatus:a.status, bStatus:b.status,
            bFinal:b.finalEl === finalB, aUntouched:!a.finalEl};
        """,
    )
    assert all(value["hydrated"].values())
    assert value | {"hydrated": None} == {
        "hydrated": None, "aStatus": "working", "bStatus": "completed",
        "bFinal": True, "aUntouched": True,
    }


CONVERSATION_BODY = """
<div id="messages"></div><div id="status"></div><div id="sidebar"></div>
<button id="sendBtn"></button><button id="stopBtn"></button><input id="input">
<div id="viewMenuWrap"></div><div id="viewItemClassic"></div>
<div id="viewItemSimplified"></div><div id="viewClassicOptions"></div>
"""


CONVERSATION_PRELUDE = r"""
  let conversationId = null, eventSource = null, sseReconnectTimer = null;
  let serverMsgCount = 0, currentOffset = 0, hasMoreMessages = false;
  let historyCursor = {offset:0,before_msg_id:''};
  let loadingMore = false, displayWindow = 50, sending = false;
  let pendingAgent = null, selectedAgent = '', nicknameMap = {};
  let sseEverConnected = false, sseHadError = false, _expectingClear = false;
  let activeInteractions = {};
  const _seenMsgIds = new Set(), _liveCountedMsgIds = new Set(), _selectedMsgIds = new Set();
  window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = false;
  window.PAWFLOW_GROUP_TASK_MESSAGES = false;
  window.PAWFLOW_GROUP_DELEGATE_MESSAGES = false;
  function t(k){ return k; } function escapeHtml(s){ return String(s || ''); }
  function displayAgentName(s){ return String(s || ''); }
  function _setInputEnabled(){} function highlightConv(){} function updateDeleteBtn(){}
  function stopSSEHealthTimer(){} function startSSEHealthTimer(){} function clearAllStreams(){}
  function updateActiveAgentBadge(){} function updateActivePanel(){} function hideTyping(){}
  function _syncToggleBtn(){} function loadResources(){} function loadPermissionMode(){}
  function loadThemeSelector(){ return null; } function hydrateContextUsage(){}
  function hydrateUsageCost(){} function scrollBottom(){} function setMessagesScrollTop(){}
  function suspendTechnicalMessageGrouping(){} function resumeTechnicalMessageGrouping(){}
  function applyTechnicalMessageGrouping(){} function setTechnicalMessageGrouping(v){ window.PAWFLOW_GROUP_TECHNICAL_MESSAGES=!!v; }
  function setTaskMessageGrouping(v){ window.PAWFLOW_GROUP_TASK_MESSAGES=!!v; }
  function setDelegateMessageGrouping(v){ window.PAWFLOW_GROUP_DELEGATE_MESSAGES=!!v; }
  function turnViewReset(){} function turnViewIsSimplified(){ return true; }
  function turnViewSetMode(){} function turnViewRegisterUser(){} function turnViewIngest(){}
  function turnViewReconcile(){} function turnViewHydrateRuntimeTurns(){}
  function addMsg(role, text, extra){
    const id = extra && extra.msg_id; if (id && _seenMsgIds.has(id)) return null;
    if (id) _seenMsgIds.add(id);
    let el = extra && extra.group_key
      ? document.querySelector('[data-group-key="' + extra.group_key + '"]') : null;
    if (!el) { el=document.createElement('div'); el.className='msg';
      if (extra && extra.group_key) el.dataset.groupKey=extra.group_key;
      document.getElementById('messages').appendChild(el); }
    if (id && !el.dataset.msgid) el.dataset.msgid=id;
    el.dataset.messageRole=role; el.dataset.rawText=String(text || ''); return el;
  }
"""


def test_resume_gap_recovery_and_load_more_use_backend_cursor_units(tmp_path):
    value = _browser_result(
        tmp_path, body=CONVERSATION_BODY, prelude=CONVERSATION_PRELUDE + r"""
          const calls=[];
          const pages=[
            {conversation_id:'C',messages:[{role:'user',content:'old',msg_id:'old'}],
             message_count:80,raw_count:50,offset:0,has_more:true,
             history_cursor:{offset:50,before_msg_id:'old'},active_agent:'bot',view_mode:'classic',
             group_delegate_messages:false},
            {conversation_id:'C',messages:Array.from({length:50},(_,i)=>({role:'assistant',content:'gap'+i,msg_id:'gap'+i})),
             message_count:130,raw_count:50,offset:0,has_more:true,
             history_cursor:{offset:50,before_msg_id:'gap0'},active_agent:'bot',view_mode:'classic'},
            {conversation_id:'C',messages:[{role:'assistant',content:'idless'},
               {role:'sub_agent_trace',content:'one',msg_id:'trace1',group_key:'delegates'},
               {role:'sub_agent_trace',content:'two',msg_id:'trace2',group_key:'delegates'}],
             message_count:130,raw_count:50,offset:50,has_more:true,
             history_cursor:{offset:100,before_msg_id:''},active_agent:'bot',view_mode:'classic'},
            {conversation_id:'C',messages:[{role:'assistant',content:'oldest',msg_id:'oldest'}],
             message_count:130,raw_count:30,offset:100,has_more:false,
             history_cursor:{offset:130,before_msg_id:'oldest'},active_agent:'bot',view_mode:'classic'}
          ];
          function action$(action,args){ calls.push({action,args:{...args}}); return {subscribe(next){
            const fn=typeof next==='function'?next:next.next; fn(pages.shift());
            if(next && next.complete) next.complete(); }}; }
          function connectSSE(){}
        """, sources=["conversations.js"], test=r"""
          resumeConv('C');
          reconcileMissedMessages();
          const showingAfterGap=document.getElementById('loadMoreBanner').textContent;
          const fake=document.createElement('div'); fake.dataset.msgid='DOM-MUST-NOT-BE-CURSOR';
          document.getElementById('messages').insertBefore(fake, document.getElementById('messages').firstChild);
          loadMoreMessages();
          loadMoreMessages();
          return {calls,showingAfterGap,currentOffset,hasMoreMessages,
            delegateUnits:document.querySelector('[data-group-key="delegates"]')?.dataset.historyUnits || ''};
        """,
    )
    load_calls = [c["args"] for c in value["calls"] if c["action"] == "load_history"]
    assert [c["offset"] for c in load_calls] == [0, 0, 50, 100]
    assert load_calls[2]["before_msg_id"] == "gap0"
    assert "DOM-MUST-NOT-BE-CURSOR" not in json.dumps(load_calls)
    assert "showing 50 of 130" in value["showingAfterGap"]
    assert value["currentOffset"] == 130
    assert value["hasMoreMessages"] is False
    assert value["delegateUnits"] == "2"


def test_resume_a_b_a_hydrates_runtime_before_no_replay_sse(tmp_path):
    value = _browser_result(
        tmp_path, body=CONVERSATION_BODY, prelude=CONVERSATION_PRELUDE + r"""
          const sequence=[], calls=[]; let runtime=[];
          const page=(cid,active)=>({conversation_id:cid,
            messages:[{role:'user',content:cid,msg_id:cid+'-turn'},
              {role:'assistant',content:'partial',msg_id:cid+'-partial',turn_id:cid+'-turn'}],
            message_count:2,raw_count:2,offset:0,has_more:false,
            history_cursor:{offset:2,before_msg_id:cid+'-turn'},active_agent:'bot',
            view_mode:'simplified',group_delegate_messages:false,
            active_turns:active?[{turn_id:cid+'-turn',started_at:10,duration:5,status:'thinking'}]:[]});
          const pages={A:[page('A',true),page('A',true)],B:[page('B',false)]};
          function action$(action,args){ calls.push(args.conversation_id); return {subscribe(next){
            const fn=typeof next==='function'?next:next.next; fn(pages[args.conversation_id].shift()); }}; }
          function turnViewSetRuntimeTurns(turns){ runtime=turns; sequence.push('runtime:'+conversationId); }
          function turnViewHydrateRuntimeTurns(){ sequence.push('hydrate:'+conversationId+':'+(runtime[0]?.turn_id||'')); }
          function connectSSE(cid,_cb,opts){ sequence.push('sse:'+cid+':'+(runtime[0]?.turn_id||'')+':'+opts.noReplay); }
        """, sources=["conversations.js"], test=r"""
          resumeConv('A'); resumeConv('B'); resumeConv('A');
          return {sequence,calls,conversationId};
        """,
    )
    assert value["calls"] == ["A", "B", "A"]
    assert value["conversationId"] == "A"
    assert value["sequence"] == [
        "runtime:A", "hydrate:A:A-turn", "sse:A:A-turn:true",
        "runtime:B", "hydrate:B:", "sse:B::true",
        "runtime:A", "hydrate:A:A-turn", "sse:A:A-turn:true",
    ]


def test_trim_evicts_every_grouped_id_and_ungrouped_traces_reload(tmp_path):
    value = _browser_result(
        tmp_path, body='<div id="messages"></div>', prelude=r"""
          let displayWindow=50, hasMoreMessages=false, currentOffset=202;
          let serverMsgCount=300, historyCursor={offset:202,before_msg_id:'group-1'};
          const _seenMsgIds=new Set(['group-1','group-2']);
          const _selectedMsgIds=new Set();
          window.PAWFLOW_GROUP_DELEGATE_MESSAGES=false;
          window.PAWFLOW_GROUP_TECHNICAL_MESSAGES=false;
          function turnViewEvictionGroup(el){ return [el]; }
          function turnViewForgetElement(){}
          function _rewindHistoryCursor(n){ currentOffset-=n; historyCursor={offset:currentOffset,before_msg_id:''}; }
          function _updateLoadMoreBanner(){}
          function _messageSortTs(){ return 0; } function _hasRealSortTs(){ return false; }
          function makeTimeHtml(){ return ''; } function displayAgentName(s){ return String(s||''); }
          function sourceBadge(){ return ''; } function _authorBadgeHtml(){ return ''; }
          function renderMarkdown(s){ return String(s||''); } function buildMetaLine(){ return ''; }
          function escapeHtml(s){ return String(s||''); } function t(k){ return k; }
          function collapseTechnicalGroups(){} function isNearBottom(){ return false; }
          function scrollBottom(){} function pawflowDebugLog(){} function renderUserAttachments(){ return ''; }
          function _insertMessageChronologically(container,el){ container.appendChild(el); }
          function _hasCompleteMcpDisplayedToolCall(){ return true; }
          function findToolCallElement(){ return null; }
          function applyTechnicalMessageGrouping(){}
        """, sources=["messages_render.js"], test=r"""
          const box=document.getElementById('messages');
          const group=document.createElement('div'); group.className='msg delegate-block';
          group.dataset.msgid='group-1'; group.dataset.historyUnits='2';
          const child=document.createElement('span'); child.dataset.msgid='group-2'; group.appendChild(child);
          box.appendChild(group);
          for(let i=0;i<200;i++){ const row=document.createElement('div'); row.className='msg'; box.appendChild(row); }
          trimLiveDisplayWindowIfAutoscrolling(true);
          const evicted=!group.isConnected && !_seenMsgIds.has('group-1') && !_seenMsgIds.has('group-2');
          const one=addMsg('sub_agent_trace','one',{msg_id:'group-1',source:{type:'agent'}});
          const two=addMsg('sub_agent_trace','two',{msg_id:'group-2',source:{type:'agent'}});
          return {evicted,currentOffset,cursor:historyCursor,
            reloaded:!!one && !!two && one.isConnected && two.isConnected,
            rows:box.querySelectorAll('[data-msgid]').length};
        """,
    )
    assert value == {
        "evicted": True, "currentOffset": 200,
        "cursor": {"offset": 200, "before_msg_id": ""},
        "reloaded": True, "rows": 2,
    }
