// Behavioural tests for the conversation load path, run under Node with the
// local DOM stub. These are the browser-free twin of the three integrations in
// tests/test_webchat_durable_state_behavior.py that drive conversations.js:
// backend cursor units, A/B/A runtime hydration, and live-window eviction.
// That file skips wholesale wherever headless Chromium renders nothing (the
// GitHub runners), so without this copy those three had no cover at all.
//
// The fixtures and the assertions are the same; only the harness changed --
// the page prelude runs in a vm context instead of a <script> tag, and the
// sources are the real files from tasks/io/chat_ui.
//
// Run directly: node tests/js/conversations_spec.js
// Run via pytest: tests/test_conversations_js.py

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');
const STUB = path.join(__dirname, 'dom_stub.js');

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; }
  catch (err) { failures.push(name + ': ' + (err && err.message ? err.message : err)); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(actual, expected, msg) {
  if (actual !== expected) {
    throw new Error((msg ? msg + ': ' : '') + 'expected ' + JSON.stringify(expected)
      + ' but got ' + JSON.stringify(actual));
  }
}
// Values cross the vm boundary, so compare their JSON shape rather than
// identity -- an array from the context is not the host realm's Array.
function jsonEq(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error((msg ? msg + ': ' : '') + 'expected ' + b + ' but got ' + a);
}

// The ids conversations.js reaches for by name. Three of them (messages,
// status, sidebar) are dereferenced without a guard, so a missing one is a
// TypeError rather than a skipped branch.
const BODY_IDS = ['messages', 'status', 'sidebar', 'sendBtn', 'stopBtn', 'input',
                  'viewMenuWrap', 'viewItemClassic', 'viewItemSimplified',
                  'viewClassicOptions'];

function env(prelude, sources, ids) {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  for (const id of (ids || BODY_IDS)) {
    const el = dom.document.createElement('div');
    el.id = id;
    dom.documentElement.appendChild(el);
  }
  const logs = { warn: [], error: [] };
  const ctx = {
    document: dom.document,
    setTimeout: dom.setTimeout,
    clearTimeout: dom.clearTimeout,
    setInterval: dom.setInterval,
    clearInterval: dom.clearInterval,
    Date: dom.Date,
    console: {
      log: () => {},
      warn: (...a) => logs.warn.push(a.join(' ')),
      error: (...a) => logs.error.push(a.join(' ')),
    },
    CSS: { escape: s => String(s).replace(/["\\\]]/g, '\\$&') },
  };
  vm.createContext(ctx);
  // The sources read feature flags off `window`; in a page that is the global
  // object, and here it has to be said out loud.
  vm.runInContext('globalThis.window = globalThis;', ctx, { filename: 'window.js' });
  vm.runInContext(prelude, ctx, { filename: 'prelude.js' });
  for (const file of sources) {
    vm.runInContext(fs.readFileSync(path.join(CHAT_UI, file), 'utf8'), ctx, { filename: file });
  }
  return {
    ctx, dom, logs,
    run: body => vm.runInContext('(() => {' + body + '})()', ctx, { filename: 'test.js' }),
  };
}

// Everything conversations.js expects from the rest of the page. Each stub is
// the smallest thing that keeps the load path honest: the ones that matter to
// a test are re-declared by that test's own prelude and win, because a later
// function declaration in the same script replaces the earlier one.
const CONVERSATION_PRELUDE = `
  let conversationId = null, eventSource = null, sseReconnectTimer = null;
  let serverMsgCount = 0, currentOffset = 0, hasMoreMessages = false;
  let historyCursor = {offset:0,before_msg_id:''};
  let loadingMore = false, displayWindow = 50, sending = false;
  let pendingAgent = null, selectedAgent = '', nicknameMap = {};
  let sseEverConnected = false, sseHadError = false, _expectingClear = false;
  let activeInteractions = {};
  let _seenMsgIds = new Set(), _liveCountedMsgIds = new Set(), _selectedMsgIds = new Set();
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
`;

// -- Pagination is counted in the backend's units, never in DOM rows -----
//
// message_count is a raw transcript-row count. A rendered row is presentation:
// it can be id-less, classified away, or grouped many-to-one. Reading the next
// cursor off the DOM was how a delegate box worth two transcript rows became
// one, and the history walked backwards a row at a time.

test('resume, gap recovery and load-more all page in backend cursor units', () => {
  const e = env(CONVERSATION_PRELUDE + `
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
  `, ['conversation_sessions.js', 'conversations.js']);

  const value = e.run(`
    resumeConv('C');
    reconcileMissedMessages();
    const showingAfterGap=document.getElementById('loadMoreBanner').textContent;
    const fake=document.createElement('div'); fake.dataset.msgid='DOM-MUST-NOT-BE-CURSOR';
    document.getElementById('messages').insertBefore(fake, document.getElementById('messages').firstChild);
    loadMoreMessages();
    loadMoreMessages();
    return {calls,showingAfterGap,currentOffset,hasMoreMessages,
      delegateUnits:document.querySelector('[data-group-key="delegates"]')?.dataset.historyUnits || ''};
  `);

  const loadCalls = [];
  for (const c of value.calls) if (c.action === 'load_history') loadCalls.push(c.args);
  jsonEq(loadCalls.map(c => c.offset), [0, 0, 50, 100], 'offsets come from history_cursor');
  eq(loadCalls[2].before_msg_id, 'gap0', 'the tie-breaker id is the backend cursor id');
  assert(!JSON.stringify(loadCalls).includes('DOM-MUST-NOT-BE-CURSOR'),
    'a DOM row must never become a pagination cursor');
  assert(String(value.showingAfterGap).includes('showing 50 of 130'),
    'the banner counts raw transcript rows: got ' + value.showingAfterGap);
  eq(value.currentOffset, 130);
  eq(value.hasMoreMessages, false);
  eq(value.delegateUnits, '2', 'a delegate box worth two rows advances the cursor by two');
});

test('two simplified load-more pages preserve order, live status, last message and scroll anchor', () => {
  const e = env(CONVERSATION_PRELUDE + `
    const calls=[], scrollWrites=[];
    const pages=[
      {conversation_id:'C',messages:[
        {role:'user',content:'live question',msg_id:'u-live',turn_id:'u-live'},
        {role:'assistant',content:'09:25',msg_id:'a-0925',turn_id:'u-live'}],
       message_count:6,raw_count:2,offset:0,has_more:true,
       history_cursor:{offset:2,before_msg_id:'u-live'},active_agent:'bot',
       view_mode:'simplified',group_delegate_messages:false,
       active_turns:[{turn_id:'u-live',started_at:10,duration:5,status:'running'}]},
      {conversation_id:'C',messages:[
        {role:'user',content:'old question',msg_id:'u-old'},
        {role:'assistant',content:'09:12',msg_id:'a-0912'}],
       message_count:6,raw_count:2,offset:2,has_more:true,
       history_cursor:{offset:4,before_msg_id:'u-old'}},
      {conversation_id:'C',messages:[
        {role:'user',content:'oldest question',msg_id:'u-oldest'},
        {role:'assistant',content:'09:00',msg_id:'a-0900'}],
       message_count:6,raw_count:2,offset:4,has_more:false,
       history_cursor:{offset:6,before_msg_id:'u-oldest'}}
    ];
    function action$(action,args){ calls.push({action,args:{...args}}); return {subscribe(next){
      const fn=typeof next==='function'?next:next.next; fn(pages.shift());
      if(next && next.complete) next.complete(); }}; }
    function connectSSE(){}
    function setMessagesScrollTop(value){
      scrollWrites.push(value); document.getElementById('messages').scrollTop=value;
    }
  `, ['turn_view.js', 'conversation_sessions.js', 'conversations.js']);

  const value = e.run(`
    const box=document.getElementById('messages');
    Object.defineProperty(box,'scrollHeight',{configurable:true,get(){return this.children.length*20;}});
    Object.defineProperty(box,'clientHeight',{configurable:true,value:80});
    resumeConv('C');
    box.scrollTop=0;
    loadMoreMessages();
    box.scrollTop=0;
    loadMoreMessages();

    const blocks=Array.from(box.querySelectorAll('.simple-turn-block'));
    const liveBlock=blocks.find(block=>block.dataset.turnId==='u-live');
    const ids=()=>Array.from(box.children)
      .filter(el=>el.id!=='loadMoreBanner')
      .map(el=>el.dataset.msgid || (el.classList.contains('simple-turn-block')?'BLOCK':'?'));
    const beforeIds=ids();
    const beforeStatus=liveBlock.querySelector('.simple-turn-status').textContent;
    const beforeDetail=liveBlock.querySelector('#turn-panel-u-live-messages .simple-turn-panel-scroll').children.length;
    const atBottom=box.scrollHeight-box.scrollTop-box.clientHeight<=5;

    const newest=addMsg('assistant','09:26',{msg_id:'a-0926',turn_id:'u-live'});
    turnViewIngest('assistant',{msg_id:'a-0926',turn_id:'u-live',content:'09:26'},newest);
    return {beforeIds,afterIds:ids(),beforeStatus,
      afterStatus:liveBlock.querySelector('.simple-turn-status').textContent,
      beforeDetail,
      afterDetail:liveBlock.querySelector('#turn-panel-u-live-messages .simple-turn-panel-scroll').children.length,
      scrollWrites,atBottom,currentOffset};
  `);

  jsonEq(value.beforeIds, [
    'u-oldest','BLOCK','a-0900',
    'u-old','BLOCK','a-0912',
    'u-live','BLOCK','a-0925',
  ], 'history pages stay before the current window in transcript order');
  jsonEq(value.afterIds.slice(-3), ['u-live','BLOCK','a-0926'],
    'the live last advances without historical rows replacing it');
  eq(value.beforeStatus, 'Working');
  eq(value.afterStatus, 'Working', 'pagination and live updates do not close the runtime turn');
  eq(value.beforeDetail, 1, 'the one-message live block contains its last detail row');
  eq(value.afterDetail, 2, 'the live block contains both messages after the update');
  jsonEq(value.scrollWrites, [60, 60], 'each prepend preserves the current visual anchor');
  eq(value.atBottom, false, 'the second load-more does not jump back to the bottom');
  eq(value.currentOffset, 6);
});

// -- Runtime turns belong to the conversation being opened ---------------
//
// A -> B -> A: each load publishes its own active turns, hydrates them, and
// only then opens the stream. B has none; coming back to A must show A's
// again rather than whatever the previous conversation left behind.

test('A/B/A publishes and hydrates runtime turns before opening the stream', () => {
  const e = env(CONVERSATION_PRELUDE + `
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
  `, ['conversation_sessions.js', 'conversations.js']);

  const value = e.run(`
    resumeConv('A'); resumeConv('B'); resumeConv('A');
    return {sequence,calls,conversationId};
  `);

  // Returning to A refocuses its live ConversationSession: the tile kept its
  // transcript, runtime turns and SSE, so no third load or replay happens.
  jsonEq(value.calls, ['A', 'B']);
  eq(value.conversationId, 'A');
  jsonEq(value.sequence, [
    'runtime:A', 'hydrate:A:A-turn', 'sse:A:A-turn:true',
    'runtime:B', 'hydrate:B:', 'sse:B::true',
  ]);
});

// -- Eviction forgets every id it removed, grouped ones included ---------
//
// A delegate box is one node holding several ids and worth several transcript
// rows. Dropping it while remembering its children left those messages unable
// to come back: addMsg deduped them against ids nothing on screen carried.

test('trimming evicts every grouped id and lets ungrouped traces reload', () => {
  const e = env(`
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
  `, ['messages_render.js'], ['messages']);

  const value = e.run(`
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
  `);

  jsonEq(value, {
    evicted: true, currentOffset: 200,
    cursor: { offset: 200, before_msg_id: '' },
    reloaded: true, rows: 2,
  });
});

if (failures.length) {
  console.error('FAILED (' + failures.length + '):');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passing');
