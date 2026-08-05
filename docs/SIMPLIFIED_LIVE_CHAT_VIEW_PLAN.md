# Simplified Live Chat View — Design and Implementation Plan

Status: **implemented**

Decided: 2026-07-29 — the web chat will gain a conversation-scoped simplified
view that presents each normal user turn as a user message, one live activity
block, and one separate final assistant message. Expanding the activity block
shows the same live stream in four continuously updating tabs: Messages,
Thinking, Tool calls, and Artifacts.

## Goal

Provide a calmer and more playful chat presentation without hiding PawFlow's
agent activity. While an agent works, the collapsed turn block displays short
ephemeral activity text with a zoom/dezoom/fade animation accompanied by
synchronized semantic icons that pop into the block. At any time, including while
the turn is still running, the user can expand the block and inspect the full live
detail organized by event type.

For an ordinary completed turn, the top-level transcript must be:

```text
User message
Turn activity block
Final assistant message
```

The final assistant message continues to use the existing renderer and remains
a normal standalone message with Markdown, attachments, metadata, actions,
text-to-speech controls, cost, and context information.

## Required user experience

### Collapsed while live

```text
+--------------------------------------------------+
| agent-name via llm-service              Working |
|                                                  |
|        [message icon] Inspecting the code...      |
|        icon pop + text zoom -> dezoom -> fade     |
+--------------------------------------------------+
```

Text and thinking activity use a short sanitized excerpt. A tool call displays
only `Calling tool...` in the ephemeral surface; the tool name, arguments,
status, and controls remain available in the Tool calls tab. A successful
`show_file` result is presented as a file card in the Artifacts tab.

Each ephemeral item has a matching visual family: a speech bubble for Messages,
a brain/neural symbol for Thinking, a small set of tool silhouettes for Tool
calls, and a document/paperclip symbol for Artifacts. The icon pop and text
animation start as one synchronized cue, not as independent queues.

A tool cue carries a **copy** of the rendered call rather than a label, and that
copy is decoration only: `_turnStripCueIdentity` removes `id`, `data-msgid` and
`data-tc-id` from it and from everything nested inside it, and
`_finalizeLiveToolCalls` skips any pending bullet inside a
`.simple-turn-cue-copy`. Both are load-bearing. The cue surface sits above the
tabs, so an addressable copy is what a lookup by call id finds first: the
tool_result attaches to a node that is about to fade, the canonical row never
receives its output, and the end of the turn stamps that row `[Stopped]` beside
a cue showing the very result it never got.

### Expanded while live

```text
+--------------------------------------------------+
| agent-name via llm-service              Working |
| [Messages] [Thinking] [Tool calls] [Artifacts]   |
|--------------------------------------------------|
| Scrollable live content for the selected tab     |
| New events continue to arrive without a reload   |
+--------------------------------------------------+
```

Expanding the block is not a snapshot and does not pause ingestion. All four
tabs continue to receive events. The selected tab updates immediately; inactive
tabs retain their scroll position and show a subtle unread indicator when new
content arrives.

The expanded view replaces the ephemeral animation area. Collapsing the block
while the turn is still live restores the animated presentation for subsequent
events.

### When the final answer arrives

The activity block changes from `Working` to `Completed`. The terminal assistant
message is removed from the Messages tab if its streaming placeholder was
there, then the same DOM node becomes the standalone final message immediately
after the turn block. It must not be cloned or rendered twice.

If the user expanded the block, it stays expanded after completion. All
intermediate messages, thinking, tool calls, and presented artifacts remain
inspectable.

### Error, cancellation, and force stop

An error, cancellation, or force stop finalizes the block with the matching
status and preserves all detail received so far. It never manufactures a final
assistant message. Pending tool calls are finalized through the existing tool
finalization paths.

## Product decisions

1. The simplified view is a real rendering mode, not CSS applied to the existing
   technical grouping.
2. One activity block represents one user turn, not one block per intermediate
   assistant message or per sub-agent.
   Autonomous scheduled wakeups have no user row, so the poller assigns each one
   a distinct runtime turn UUID. If the preceding block is already terminal, the
   first resumed row opens a new positionally anchored activity block that stays
   `Working` until that wakeup emits its own terminal event.
3. The header identifies the primary agent and LLM service. Delegated agent
   identity remains visible inside detail rows.
4. The tabs are live from the first event until terminal completion.
5. The final answer is always a separate existing-style assistant message.
6. Tool results live with their matching tool call in the Tool calls tab.
7. Successful `show_file` results are the one exception: the call and its status
   remain in Tool calls, while the rendered file result lives in Artifacts.
8. Artifacts means files explicitly presented to the user through `show_file`,
   not every file created in FileStore and not every raw `fs://` URL mentioned by
   another tool.
9. Actionable UI such as approvals and `ask_user` remains immediately visible;
   it is never hidden in a collapsed tab.
10. Switching between classic and simplified mode performs an authoritative
   conversation reload instead of attempting to transform an already grouped
   DOM in place.
11. The existing classic view and its technical/task/delegate grouping remain
   unchanged.
12. V1 keeps PawFlow's current thinking persistence policy: all received
    thinking is available live, while reload shows the consolidated thinking
    that was durably stored.
13. Collapsed activity text and semantic icon pops are one atomic animation cue.
    Icons never create an independent backlog or continue after their text is
    discarded.

### Shared chat chrome layout

The classic and simplified message views share the same compact surrounding
controls:

- The header keeps status and usage first, followed by the global theme and
  language selectors. Language options include their national flags.
- Conversation actions formerly hidden behind the `+` menu form an always-visible,
  vertically scrollable dock on the right. `Link account`, `Logout`, and
  administration live in this dock, and extension-provided header/action slots
  remain inside it. The administration panel is a fixed sibling of the scrollable
  dock so opening the gear cannot clip the menu outside the dock's narrow bounds.
- View mode, TTS, STT, and tool permission mode share a compact floating panel
  above the prompt on the left, mirroring the active-agents panel on the right,
  because they affect the current conversation or its next turn.
- The task-tab dock uses a separate right offset so the two docks never overlap.
  On narrow screens the action dock becomes a bounded horizontal strip above the
  composer.

These are presentation moves only. Existing element IDs and event handlers remain
the behavioral contract for authentication, themes, localization, permissions,
view selection, speech, administration, actions, and UI extensions.

## Non-goals

- Replacing the existing message, Markdown, tool call, diff, media, task, or
  delegate renderers.
- Persisting every token or every thinking delta.
- Treating the conversation FileStore panel as the artifact source or showing
  files that were created but never passed through `show_file`.
- Automatically treating outputs from `generate_image`, `screen`, `see`, or any
  other media-producing tool as artifacts unless a later `show_file` call
  explicitly presents that file.
- Changing the agent execution pipeline or tool approval behavior.
- Hiding errors, approvals, questions, notifications, or system status needed
  for user action.
- Introducing a frontend framework or a new external icon/animation dependency.
- Changing non-web clients such as Telegram, PawCode, or the VS Code extension.

## Existing foundations

| Existing piece | Current location | Reuse |
|---|---|---|
| Message rendering and `msg_id` dedupe | `tasks/io/chat_ui/messages_render.js` | Keep existing DOM and actions |
| Thinking blocks | `tasks/io/chat_ui/sse_state.js` | Route the live block into the Thinking tab |
| Token, message, tool, and turn SSE handlers | `tasks/io/chat_ui/sse_handlers_a.js`, `sse_handlers_b.js` | Feed the turn controller |
| Per-agent stream state | `tasks/io/chat_ui/sse_state.js` | Preserve streaming placeholder behavior |
| Technical grouping | `tasks/io/chat_ui/messages.js` | Disable only while simplified mode is active |
| History rendering, pagination, and reconnect recovery | `tasks/io/chat_ui/conversations.js` | Call one turn-ingestion path after rendering rows |
| Conversation-scoped view parameters | `tasks/ai/actions/_conv_core.py`, `conversations.js` | Persist `chat.view_mode` |
| Writer-before-SSE invariant | `core/conversation_writer.py`, `tasks/ai/_alc_closures1.py` | Persist turn metadata before live delivery |
| Terminal turn correlation | `tasks/ai/agent_emitter.py` | Reuse `request_msg_id`, `turn_id`, and `all_msg_ids` |
| Display classification | `tasks/ai/agent_serialization.py` | Propagate turn metadata to history rows |
| `show_file` result marker | `core/handlers/show_file.py` | Reuse durable `__show_file__`, `file_id`, filename, MIME, size, and FileStore URL |
| File/media rendering and viewer | `tasks/io/chat_ui/messages_markdown.js`, `messages_tools.js` | Reuse existing inline media and `openFileViewer` behavior |

The existing technical group is visually similar but insufficient. It groups
only selected technical rows between visible boundaries, does not own
intermediate assistant narration, and has no durable turn identity. Using it as
the implementation would make the live view appear correct while reload,
pagination, reconnect recovery, or concurrent turns could reconstruct the wrong
groups.

## Architecture

```text
message-level SSE or history row
               |
               v
       canonical existing renderer
               | returns one DOM node
               v
       SimplifiedTurnView.ingest(...)
               |
       +-------+---------+
       |                 |
       v                 v
turn state/tabs   ephemeral animator
(always updated)  (collapsed live only)
               |
               v
terminal done identifies the final node
               |
               v
move the same node after the turn block
```

The existing renderer remains the single source of truth for message and tool
DOM. The simplified view reparents existing nodes; it does not clone them and
does not create a second tool renderer. This preserves tool result attachment,
diffs, inline media, background/kill buttons, metadata, message actions,
selection, and `msg_id` deduplication.

## Durable turn contract

### Turn identity

The user message's `msg_id` is the canonical `turn_id`. Every assistant or tool
message produced for that request must persist and emit the same non-empty
`turn_id`.

Example stored message:

```json
{
  "role": "assistant",
  "content": "I am inspecting the renderer.",
  "msg_id": "assistant-message-uuid",
  "turn_id": "user-message-uuid",
  "turn_final": false,
  "ts": 1785300000.0,
  "source": {
    "type": "agent",
    "name": "assistant",
    "llm_service": "codex_appserver_llm_service"
  }
}
```

`turn_id` is a top-level correlation field. `source` continues to describe the
producer and must not become the turn identity store.

#### Boundaries are positional; `turn_id` is a refinement

The view groups on **boundaries in the rendered stream, not on correlation**:

```text
user message          <- opens a turn and its block
  ...everything...    <- goes in the block's tabs
terminal answer       <- lifted out, placed after the block
next user message     <- closes the turn, opens the next one
```

Every row belongs to the turn currently open. **`turn_id` is never consulted for
placement**, and when the two could disagree, position wins. The case that
decides it is a user message arriving before the answer:

```text
user message
turn activity block
user message          <- closes the first turn, opens a second
turn activity block
terminal answer       <- under the LAST block
```

The `done` event still carries the first turn's id, so correlating on it would
lift the answer back under the first block — above content the reader saw
arrive after it. The answer belongs where the reader is looking: at the bottom.
`turn_id` names a turn; it does not route rows. A turn nobody stamped groups
identically.

**This has been reverted into correlation once**, in beta.55, by an audit that
read the ignored `turn_id` as a bug. It is not a bug, it is the product rule,
and it was reverted back in the release that followed. Two clients writing at
once IS a real limitation of the rule; the answer is to decide what the second
writer should see, not to make ids route rows. `tests/js/turn_view_spec.js`
("a user message before the answer gives user / block / user / block / answer")
is the executable statement of this rule.

This matters because correlation fails silently. The first implementation made
`turn_id` load-bearing: `turnViewIngest` rejected every row without one, so a
submitting path that did not set `agent.request_msg_id` produced no block, no
reparenting, no tabs and no animation, while the View menu still reported
Simplified. There was no error anywhere — just a feature doing nothing. Nothing
in the grouping path may depend on metadata that one missing assignment can
empty.

Durable `turn_id`/`turn_final` still earn their place: they mark the terminal
answer so a reloaded turn puts it outside the block, and they correlate turns
for non-web clients. Every submitting path stamps it:

| Path | Turn id |
|---|---|
| Web chat (`/api/agent`) | `stamp_turn_identity()` in `tasks/ai/agent_streaming.py`, from the client-generated `msg_id` in the request body |
| Programmatic runtime API | `agent_runtime_api.py` sets `agent.request_msg_id` from the request's `turn_id` |

The web chat generates the user `msg_id` client-side and registers it as the
turn anchor in the same call that renders the user row, so the id the browser
groups on and the id the server stamps are the same value by construction. A
caller that already chose a turn id keeps it; a submission without a message id
gets no turn id rather than an invented one, and reload falls back to deriving
one from the user boundary.

### Events that must carry `turn_id`

- `thinking`
- `thinking_delta`
- `thinking_content`
- `token`
- `new_message`
- `tool_call`
- `tool_result`
- `turn_complete`
- `message_meta`
- `done`
- `error_event`
- cancellation/interruption/active-release terminal events when scoped to one
  turn

### History cursors and active runtime hydration

Pagination is transcript state, not DOM state. `resume_conversation` returns a
backend `history_cursor`, and every successful page advances that cursor even
when message-id deduplication renders no new node. The tab controller may reparent
rows freely; it must never be asked to identify the oldest transcript row.

The same response carries `active_turns` before `noReplay` suppresses historical
SSE. The browser hydrates those turns as working, restores their start time and
status, and only then reconciles history. Conversation switching and pagination
must not call a terminal finalizer for those blocks. Grouped rows retain every
`msg_id` and unit, so deduplication and cursor advancement remain independent of
the presentation tree.

`active_turns` is **liveness, not routing**: it answers "is this turn still
running", never "which turn does this row belong to". A turn drops out of it the
moment it ends — otherwise a finished block stays open and its last message
stays buried at the next reconciliation.

`AgentEmitter._emit()` should set `turn_id` and `request_msg_id` from the current
context when absent. Message-level events created directly in
`_alc_closures1.py` bypass that emitter and must be stamped explicitly before
being queued through `ConversationWriter`.

### Persisting the terminal message identity

The live `done` payload already knows the last assistant `msg_id`, but history
cannot replay a transient `done` event. The terminal assistant row must therefore
be marked durably with:

```json
{
  "turn_id": "user-message-uuid",
  "turn_final": true
}
```

Add an ordered writer operation such as
`ConversationWriter.enqueue_patch_message(...)`. For a successful terminal
answer, queue operations in this order:

```text
all message writes
ordered patch: turn_final=true on final_msg_id
done SSE
```

This maintains the existing asynchronous writer-before-SSE invariant. The agent
hot path must not block on a store rewrite. `ConversationStore.patch_message()`
already targets the relevant segmented stream and can be used by the writer
operation.

The terminal producer must provide an explicit `final_msg_id`/`is_final`
decision. Do not infer success from non-empty text alone. A continuing round,
discarded acknowledgement, error, cancellation, interruption without an answer,
or force stop must not set `turn_final`.

### History classification

`AgentSerializationMixin._classify_messages_for_display()` must copy
`turn_id` and `turn_final` to every derived display row, including thinking,
tool calls, and tool results. The existing task metadata propagation pattern is
a suitable precedent.

Reconstruction must not depend on the durable marker existing. The stored
`turn_final` is written by a patch that can legitimately never land: rows
recorded before this feature carry no marker at all, a terminal path that
leaves `final_msg_id` empty produces none, and a patch that matches no row is
logged but cannot be retried. Because the view lifts the marked row out of the
activity block, a turn with no marker renders as a user message followed by a
collapsed block hiding the answer.

The classifier therefore closes the gap after propagation: for every turn whose
rows carry no `turn_final`, it marks the last visible assistant row —
`type == "assistant"`, not `display_only`, non-empty text, so error rows and
tool traffic are never promoted — and flags it `turn_final_derived: true` to
keep a missed patch diagnosable. A turn that produced no visible answer keeps
no final row: cancellation and force stop still never manufacture one.

The invariant to test is per-turn, not per-row: **every turn that contains a
visible assistant answer exposes exactly one `turn_final` row.**

### A derived final may never end a running turn

`turn_final_derived` is a reconstruction, and the client treats it as one. The
server keeps the guess away from a live turn through `active_turn_ids`, but that
set is not always populated when a page is built — a tmux capture owns the
`_active_turns` key without carrying a turn id, and the gap-recovery path
re-reads the transcript tail while a turn is in flight. When the guess got
through, the block said `Completed` with a frozen clock and no cues over an
agent that was visibly still working: the one thing the elapsed and the rain
exist to deny.

The view therefore holds the invariant itself, on two pieces of evidence it
owns:

- a turn the runtime snapshot names, or one the live channel is feeding — a row
  that arrived outside a replayed page and does not itself claim to end the
  turn — refuses a derived final outright;
- a derived final that did close a turn is remembered as a guess, and the next
  live row of that turn reopens it. A `done` is the server stating the turn
  ended: it never carries the flag, and nothing after it reopens the block.

Gap recovery adopts the `active_turns` snapshot of the page it renders, like a
full load does: it is the path that runs precisely while a turn is in flight,
and judging liveness on a picture taken at the last full load is what let the
guess through.

For pre-feature rows without `turn_id`, history loading may derive a display-only
turn id from the nearest preceding visible user message. This compatibility path
must be deterministic and limited to display classification; it must never
rewrite encrypted or historical transcripts silently. Orphan rows with no user
boundary remain normal top-level classic rows rather than being assigned to an
unrelated turn.

## View mode contract

Use one conversation-scoped enum:

```text
chat.view_mode = classic | simplified
```

`load_history` returns:

```json
{
  "view_mode": "simplified"
}
```

The rollout is done: `simplified` is the default the cascade falls back to, and
an unreadable value falls back to it as well. A conversation that never chose
follows the default; an explicit `classic` at any scope still wins, and the UI
rejects anything that is neither.

The View menu gains an exclusive mode selector:

```text
View mode
  Classic
  Simplified

Classic options
  Group technical details
  Group task messages
  Group delegate messages
```

Classic grouping options are disabled or hidden in simplified mode because
applying both grouping systems would double-wrap the same nodes. Selecting a new
mode persists the parameter and calls the canonical conversation reload path.

## Frontend state model

Create `tasks/io/chat_ui/turn_view.js`, loaded after the message renderer and
before the SSE state/handlers. It owns only simplified-view state and DOM.

```js
{
  turnId: '',
  userMsgId: '',
  agentName: '',
  llmService: '',
  status: 'working',       // working | completed | stopped | cancelled | error
  expanded: false,
  activeTab: 'messages',
  finalMsgId: '',
  userEl: null,
  blockEl: null,
  finalEl: null,
  tabs: {
    messages: { bodyEl: null, unread: 0 },
    thinking: { bodyEl: null, unread: 0 },
    tools: { bodyEl: null, unread: 0 },
    artifacts: { bodyEl: null, unread: 0 }
  },
  elementsByMsgId: new Map(),
  toolElementsByCallId: new Map(),
  artifactElementsByFileId: new Map(),
  artifactFileIdByCallId: new Map(),
  transient: {
    current: null,
    queue: [],
    timer: null,
    pendingText: '',
    iconLayerEl: null
  }
}
```

Global state:

```js
const simplifiedTurns = new Map(); // turn_id -> state
```

State is cleared on canonical conversation reset/switch, `/clear`, encryption
lock, and full history reload. SSE reconnect must not clear already rendered turn
state unless the conversation itself is reloaded.

## Turn block DOM

Each activity block is one top-level `.msg.simple-turn-block` with:

- `data-turn-id`
- a button-like header containing agent, service, status, and expand state
- an ephemeral live surface for collapsed mode with separate text and decorative
  icon layers
- a tablist with four icon buttons
- four tab panels with independent scroll containers
- no duplicate `data-msgid` on the outer block

Use inline SVG icons from local markup: speech bubble for Messages, brain or
spark for Thinking, wrench/terminal for Tool calls, and file/paperclip for
Artifacts. Icons need visible or ARIA labels; no external icon package is
required.

The header is keyboard-activatable and exposes `aria-expanded`. Tabs use
`role=tablist`, `role=tab`, `role=tabpanel`, `aria-selected`, and roving keyboard
focus. Left/right arrows change tab, Enter/Space activates, and Escape collapses
the block.

## Event classification

| Input | Tab | Ephemeral collapsed presentation |
|---|---|---|
| Streaming token/text delta | Messages | Coalesced plain-text excerpt |
| Durable intermediate assistant message | Messages | Plain-text excerpt |
| `thinking_delta` / `thinking_content` | Thinking | Coalesced plain-text excerpt |
| `tool_call` | Tool calls | Literal `Calling tool...` |
| Ordinary `tool_result` | Tool calls, attached to matching call | Optional completion pulse; no raw result in animation |
| Successful `show_file` result with a valid `__show_file__` marker | Artifacts; matching call remains in Tool calls | Optional `Presented <filename>` pulse |
| Failed or malformed `show_file` result | Tool calls as an ordinary result/error | No artifact and no success pulse |
| Terminal assistant message | Standalone final | Stop/flush animation |
| Error/cancel/stop | No forced tab move | Stop animation and update status |
| Approval / `ask_user` | Top-level actionable UI | No hiding |
| Notification/system status | Existing top-level behavior | No hiding |

Thinking and message excerpts use `textContent`, never `innerHTML`. Markdown is
rendered only by existing durable renderers inside tabs/final messages.

Every ephemeral classification also resolves an `iconKey`: `message`,
`thinking`, `tools`, or `artifact`. Status/error icons are allowed only for a
terminal transition and must not turn terminal events into queued activity.

## Live ingestion API

Expose a small global API used by existing handlers:

```js
turnViewSetMode(mode);
turnViewRegisterUser(extra, element);
turnViewIngest(kind, data, element);
turnViewFinalize(data);
turnViewFail(turnId, status, message);
turnViewReset();
turnViewReconcile();
```

`turnViewReconcile` reads the DOM top-down and rebuilds `USER > BLOCK > last
message` from whatever is there, so it is what a load-more page relies on:
`loadMoreMessages` renders its page with `deferTurnView: true` — replaying an
older USER through the live path would close the running turn — prepends it,
and calls this pass.

That walk starts at the TOP while `_turnOpen` still describes the live turn at
the BOTTOM, and a load-more page usually starts mid-turn: its first rows are
the tail of a turn whose user message was not loaded. Seeding those rows from
`_turnOpen` filed them into the live turn's block, far below where they sit,
and left the fragment's own answer at top level with no block above it. Rows
encountered before the live turn's user row therefore open their own turn, as
they would if nothing were running; only rows past it may claim it. The symptom
appeared only while a turn was running, because `_turnOpen` is what a running
turn leaves behind.

`turnViewIngest` performs these operations synchronously from the user's
perspective:

1. Resolve and validate `turn_id`.
2. Get or create the block immediately after its user message.
3. Learn/update the primary agent and LLM service from event/source metadata.
4. Reparent the existing durable DOM node into the correct tab when present.
5. Parse a `show_file` result before ordinary tool-result attachment and route a
   valid artifact by `file_id`.
6. Append or reconcile the state entry by `msg_id`, `tc_id`, or artifact
   `file_id`.
7. Update unread state for inactive tabs.
8. If collapsed and live, offer a coalesced item to the animator.
9. Preserve autoscroll only when the transcript or active tab was already near
   its bottom.

An event without a valid turn id is left to the existing renderer. The simplified
controller must never guess by currently active agent in the live path; multiple
agents can run concurrently.

### Every row creator hands its row over

Top level holds three kinds of node only: a user message, its turn block, and
the terminal answer. Any code path that creates a transcript row must therefore
call `turnViewRegisterUser` (user messages, which are boundaries) or
`turnViewIngest` (everything else) before it returns. A path that renders a row
and returns without doing so leaves it stranded at top level — it looks like a
rendering bug in the block, but it is a missing hand-off in the creator.

The non-obvious creators, each found stranding rows in a browser:

| Creator | Kind |
|---|---|
| `sse_state.js` `_queueUnmatchedToolResult` 750 ms fallback | a result whose `tool_call` row never arrived |
| `sse_handlers_a.js` `new_message` with `role === 'user'` | a user message this tab did not submit — a boundary, not content |
| `sse_handlers_a.js` delegate fallback, `sse_handlers_b.js` agent response | assistant narration with no delegate or task frame |

Rows that own an enclosing frame — task blocks, delegate blocks — stay in that
frame, and system/error notices stay top level by design (see Actionable and
exceptional UI).

### Live tabs

The tabs update for the full duration of the live turn:

- Messages receives streaming text and intermediate assistant rows.
- Thinking receives each live thinking block and continues to update its text.
- Tool calls receives the existing tool call row immediately in pending state;
  the existing `_attachToolResult` path updates that same row when the result
  arrives.
- Artifacts receives a file card immediately when a successful `show_file`
  result arrives. The matching call remains in Tool calls and changes from
  pending to completed without retaining a second rendered copy of the file.
- Inactive tabs continue ingesting. Their panel scroll position is not changed.
- The active panel autoscrolls only if it was near the bottom before the update.
- Opening the block hides the animation surface but does not stop, buffer, or
  defer event ingestion.

### Artifacts contract

The artifact source of truth is the persisted tool result returned by
`ShowFileHandler`:

```json
{
  "__show_file__": true,
  "url": "fs://filestore/abc123def456/report.pdf",
  "filename": "report.pdf",
  "content_type": "application/pdf",
  "size_kb": 42.1,
  "file_id": "abc123def456"
}
```

No separate artifact registry or transcript row is required. The marker already
survives reload as the `show_file` tool result. Add a pure parser such as
`parseShowFileArtifact(resultText)` that returns an artifact only when all of the
following hold:

- the result is valid JSON with `__show_file__ === true`;
- `file_id` is non-empty;
- `url` is a canonical `fs://filestore/...` URL for that file id;
- `filename` is non-empty;
- the result belongs to a tool call whose resolved tool name is `show_file`.

The parser must not scan arbitrary prose for `fs://` URLs. Errors such as `File
ID not found`, malformed markers, or results from other tools remain ordinary
tool results and never create artifact cards.

For a valid marker, split presentation without cloning the file renderer:

1. Keep the canonical `show_file` call row in Tool calls and finalize its pending
   controls/status.
2. Render one artifact card in Artifacts using the existing image, audio, video,
   and file-viewer primitives.
3. Do not also append the full `.tc-result` media subtree under the tool call. A
   compact translated `Presented in Artifacts` affordance may activate the
   Artifacts tab and focus the card.
4. Preserve `tc_id`, result `msg_id`, and `file_id` as data attributes so late
   results, history reconciliation, and deletion states can find the same card.

Within one turn, `file_id` is the canonical displayed-file dedupe key. Replayed
SSE, history reconciliation, or two `show_file` calls for the same FileStore
object update/focus one card rather than duplicating it. The same file explicitly
shown in a later user turn appears in that later turn's Artifacts tab as well.
Track every contributing `tc_id` as an alias so either tool result can complete
its own call row.

Artifact cards show filename, type, formatted size, and the best existing preview:
inline image/audio/video where supported, otherwise a file card that opens
`openFileViewer(url)`. All URL-bearing attributes and labels use the existing
escaping/normalization helpers. Cards are lazy where the current media renderer
is lazy, especially video. A missing/deleted FileStore object remains as a
disabled `Unavailable` card after reload instead of making the entire turn fail;
the UI may discover this only when preview/open returns 404.

### Final node handoff

`turnViewFinalize(data)` uses `final_msg_id` (or the terminal `msg_id` contract)
to locate the exact element newest-first within that turn. It then:

1. Removes streaming/transient classes.
2. Removes the node from the Messages panel if present.
3. Inserts the same node immediately after the activity block.
4. Preserves `data-msgid`, actions, metadata, and listeners.
5. Marks the turn completed and flushes pending transient animation.
6. Leaves the block's expanded state unchanged.

If the final message SSE was missed, `done` must not synthesize a duplicate if a
node with the final id exists. If no node exists and `done.response` is valid,
the existing fallback creates it once, after which the turn controller performs
the same handoff. Reconnect reconciliation can later replace or enrich missing
content through the normal `msg_id` dedupe path.

## Ephemeral animation scheduler

Animating every token would create an unbounded backlog and show stale activity
after the turn completed. Use one scheduler per turn with bounded coalescing.

Cues do not take turns. They share one spot and stack in depth: the newest zooms
in at the front, sharp and opaque, and every cue already on screen is pushed back
a step — smaller, dimmer, blurrier — until it falls off the back of the stack.
Several cues are visible at once and each one leaves at its own moment, either
because newer arrivals displaced it or because its own lifetime ran out.

A cue's pose is a pure function of its current depth, so depth is recomputed on
every arrival and never accumulated. Motion is transition-driven rather than
keyframed: a keyframe would have to guess when the next cue arrives, and the
effect being asked for is precisely that the arrival moves everything behind it.

Constants:

```js
const TURN_TEXT_COALESCE_MS = 300;
const TURN_CUE_LIFETIME_MS = 2600;
const TURN_TRANSIENT_MAX_CHARS = 180;
const TURN_TRANSIENT_MAX_STACK = 4;
```

Rules:

- Merge text/thinking deltas received within the coalescing window; one window
  produces one cue, and the next window produces the next cue in front of it.
- Keep at most `TURN_TRANSIENT_MAX_STACK` cues alive; the oldest retires early
  rather than growing the surface.
- The surface is a fixed-height clipped stage, so a burst never shifts layout.
- Tool-call activity uses the fixed translated label `Calling tool...`.
- Do not animate raw tool arguments or output.
- Treat `{kind, text, iconKey}` as one cue. A cue owns its icon: they enter,
  recede, and are removed together, never on separate schedules.
- On terminal completion, cancel timers, clear pending items, and transition the
  status immediately.
- While expanded, do not run hidden animations. New events still update tabs;
  the next event after collapse may animate, but old hidden events are not
  replayed.

### Semantic icon choreography

Use small local inline SVGs, never emoji glyphs or an external icon package. The
collapsed transient surface contains one primary icon and, where useful, up to
two lightweight secondary particles:

| Cue kind | Primary icon | Optional secondary pops |
|---|---|---|
| Messages | Speech bubble | One smaller bubble or three dots |
| Thinking | Brain/neural outline | Two small neural nodes/sparks |
| Tool call | Wrench | Gear and screwdriver/hammer silhouettes |
| Artifact presented | Document | Paperclip and small sparkle |

The primary icon starts in the same animation frame as the text. It scales from
approximately `0.55` to `1.10`, settles at `1.0`, then fades with the text. A
small rotation or vertical drift may differentiate the semantic families.
Secondary icons may be staggered by 60-90 ms, but the complete icon sequence must
finish within the current text cue and must never extend its lifetime.

Icon positions can vary within a bounded central region of the block to create a
playful pop effect, but they must not overlap the status/header, intercept pointer
events, or cause layout shifts. Use transforms and opacity only. The controller
creates a fresh bounded icon layer for the active cue, removes the previous layer
before starting the next cue, and removes it on expand, finalization, cancellation,
error, conversation reset, or DOM eviction.

All transient icons are decorative and use `aria-hidden="true"`; the associated
text remains the single accessible announcement. Tab icons remain separately
labelled as already specified. Under `prefers-reduced-motion: reduce`, show one
static semantic icon beside the latest activity label, with no particles, scale,
rotation, drift, or repeated announcement.

Suggested keyframe progression:

```text
0%   opacity 0, scale 0.94
20%  opacity 1, scale 1.04
55%  opacity 1, scale 1.00
100% opacity 0, scale 0.98
```

Under `prefers-reduced-motion: reduce`, remove scaling and use either a short
opacity transition or a static latest-activity label. The feature must remain
fully usable with animation disabled.

## History, pagination, and reconnect behavior

### Initial history render

During `_renderHistory`, keep technical grouping suspended. Render canonical
message elements first, then ingest them into simplified turns in chronological
order. After all rows are ingested:

- place each block after its user message;
- place every `turn_final` node after its block;
- reconstruct Artifacts solely from valid persisted `show_file` tool results;
- mark blocks completed when a final exists;
- leave partial live turns in working state if server active state confirms them;
- run one final reconciliation before scrolling.

### Load older

A page boundary can split one turn. `loadMoreMessages()` must ingest older rows
by durable `turn_id`, merge them into an existing block when applicable, and
retain chronological order inside each tab. It must preserve transcript scroll
position using the existing pre/post height calculation.

Top-level `.msg` count can no longer represent the number of loaded transcript
rows because one block contains many nodes. The load-more banner should count
loaded unique `data-msgid` values or use existing raw history counters.

### Reconnect gap recovery

`reconcileMissedMessages()` renders missing persisted rows and sends each through
the same ingestion function. `msg_id` and `tc_id` maps make reconciliation
idempotent; artifact `file_id` and `tc_id` maps extend the same guarantee to file
cards. It must not recreate a block, increment unread counts twice, replay old
animation, duplicate an artifact, or move an already finalized answer back into
Messages.

### Window trimming

Live display trimming currently removes top-level rows and releases their
`msg_id` dedupe entries. In simplified mode, trimming one activity block must
release every nested `data-msgid` exactly as the current nested scan does. A user
message, its block, and its final should be treated as one eviction unit when
possible so the viewport never retains an orphan block or final.

## Tasks, delegates, and concurrent agents

- The primary block key is `turn_id`, never agent name.
- The header uses the primary target agent/service learned from the turn start or
  first primary event.
- Delegated-agent messages retain their existing source badge and delegate
  structure inside detail.
- Task/delegate containers may remain nested presentation components, but the
  simplified turn block is the top-level owner for rows carrying the parent
  turn id.
- Do not flatten a mixed task block into the wrong tab. Where a nested component
  contains several event kinds, retain it as a nested activity component and
  route its individual direct rows when the existing renderer exposes them.
- Independent concurrent user turns remain separate because every event carries
  a durable turn id.
- An event from a scheduled/background turn without a visible originating user
  message remains in the existing top-level presentation unless its contract
  supplies an explicit visible turn anchor.

## Actionable and exceptional UI

The following remain outside collapsed detail unless a later dedicated design
provides an equally visible action surface:

- `ask_user` questions and options
- tool approval prompts
- execution risk/permission dialogs
- authentication/login prompts
- fatal errors requiring user action
- notifications
- conversation encryption lock UI

They may carry `data-turn-id` for ordering, but cannot require the user to expand
a block before noticing or answering them.

## Styling and responsive behavior

Add styles to `template.html` using existing PawFlow theme variables. Avoid
hard-coded theme-specific foreground/background colors where a variable exists.

Required states/classes:

- `.simple-turn-block`
- `.simple-turn-header`
- `.simple-turn-status.working|completed|stopped|cancelled|error`
- `.simple-turn-ephemeral`
- `.simple-turn-ephemeral-text`
- `.simple-turn-ephemeral-icons`
- `.simple-turn-ephemeral-icon` with semantic modifier classes
- `.simple-turn-icon-primary` and `.simple-turn-icon-particle`
- `.simple-turn-tabs`
- `.simple-turn-tab` and `.has-unread`
- `.simple-turn-panel`
- `.simple-turn-panel-scroll`
- `.simple-turn-artifact-grid`
- `.simple-turn-artifact-card` and `.is-unavailable`
- `.simple-turn-transient-active`

Desktop and mobile requirements:

- never exceed the message column width;
- tab labels can compact to icon plus short label on narrow screens;
- panel height is bounded (recommended `min(50vh, 520px)`) and independently
  scrollable;
- touch targets are at least 40 CSS pixels;
- long service names ellipsize but remain available as a title/accessible label;
- final message alignment remains unchanged.

## Internationalization

Add matching keys to every shipped locale, including English, French, and
Spanish. Required concepts include:

- Simplified view
- Classic view
- Messages
- Thinking
- Tool calls
- Artifacts
- Presented in Artifacts
- Unavailable
- Calling tool...
- Working
- Completed
- Stopped
- Cancelled
- Error
- Expand turn details
- Collapse turn details
- New activity

Locale key parity tests must remain green.

## Files to change

### Backend/runtime

- `tasks/ai/agent_emitter.py`
  - stamp turn correlation on emitter events;
  - expose explicit terminal/final message identity.
- `tasks/ai/_alc_closures1.py`
  - persist `turn_id`;
  - add it to writer-published message-level SSE events.
- `tasks/ai/agent_serialization.py`
  - propagate `turn_id` and `turn_final` to classified display rows.
- `core/conversation_writer.py`
  - add an ordered asynchronous message-patch queue operation.
- `core/_conversation_store_transcript.py` or the existing store patch path
  - reuse targeted `patch_message` support; avoid whole-transcript rewrites.
- `tasks/ai/actions/_conv_core.py`
  - resolve and return `chat.view_mode`.

### Web chat

- new `tasks/io/chat_ui/turn_view.js`
  - state, DOM, routing, live tabs, animation, terminal handoff, reconciliation.
- `tasks/io/serve_chat_ui.py`
  - load `turn_view.js` after message rendering and before SSE handlers.
- `tasks/io/chat_ui/conversations.js`
  - view-mode menu behavior;
  - initial history, pagination, and reconnect ingestion/reconciliation.
- `tasks/io/chat_ui/messages_render.js`
  - expose/schedule canonical rendered nodes to the turn controller;
  - preserve existing renderer behavior in classic mode.
- `tasks/io/chat_ui/messages_tools.js`
  - parse `show_file` results before generic result attachment;
  - finalize the call while handing one canonical file card to the turn
    controller;
  - reuse lazy media/viewer primitives without duplicating the result subtree.
- `tasks/io/chat_ui/messages_markdown.js`
  - share or extract the existing `__show_file__` parsing/rendering primitives so
    classic rendering and simplified artifact cards retain identical escaping
    and viewer behavior.
- `tasks/io/chat_ui/messages.js`
  - prevent technical grouping while simplified mode owns the DOM.
- `tasks/io/chat_ui/sse_state.js`
  - send thinking block creation/update/finalization to the controller.
- `tasks/io/chat_ui/sse_handlers_a.js`
  - send live token/message/tool activity to the controller.
- `tasks/io/chat_ui/sse_handlers_b.js`
  - terminal finalization, cancellation, stop, and error status.
- `tasks/io/chat_ui/template.html`
  - mode selector markup and styles.
- `tasks/io/chat_ui/i18n/*.json`
  - labels and accessibility text.

### Tests and documentation

- new `tests/test_simplified_live_chat_view.py`
- extend the existing `ShowFileHandler` tests for the durable marker contract
- extend `tests/test_chat_ui_message_order.py`
- extend `tests/test_conversation_history.py`
- writer ordering tests near existing `ConversationWriter` coverage
- update `docs/02_REFERENCE_TASKS_SERVICES.md` when implementation ships

## Implementation phases

### Phase 1 — Durable turn identity

1. Add `turn_id` to persisted assistant/tool rows.
2. Add `turn_id` to all relevant SSE events.
3. Propagate fields through display classification.
4. Introduce ordered writer patching and durable `turn_final`.
5. Add backend tests for normal, tool-loop, CLI-provider, error, cancelled, and
   concurrent turns.

Exit criterion: a reloaded transcript identifies every new turn and exactly one
terminal assistant row for every successful completed turn.

### Phase 2 — Simplified state and static history

1. Add `turn_view.js` and module load order.
2. Implement turn block DOM and four accessible tabs.
3. Route already-persisted history rows into blocks.
4. Implement final node handoff on history reconstruction.
5. Add the conversation-scoped mode selector and authoritative reload.

Exit criterion: a completed stored conversation renders deterministically as
user, block, final and switches losslessly back to classic.

### Phase 3 — Full live ingestion

1. Wire token/message activity.
2. Wire live thinking creation and updates.
3. Wire tool calls and late tool results.
4. Parse successful `show_file` results and route their cards to Artifacts while
   completing the matching Tool calls row.
5. Keep all tabs updating while expanded.
6. Implement terminal, cancellation, error, stop, and reconnect behavior.

Exit criterion: expanding a running turn provides the current detailed live
experience inside tabs with no pause, missing event, or duplicate.

### Phase 4 — Animation and polish

1. Add bounded coalescing and animation scheduler.
2. Add synchronized semantic SVG icon pops for message, thinking, tool, and
   artifact cues.
3. Add inactive-tab unread indicators.
4. Add reduced-motion behavior, including one static cue icon and no particles.
5. Validate independent transcript/tab autoscroll.
6. Finish mobile, theme, keyboard, and screen-reader behavior.

Exit criterion: high-rate token streams cannot create an animation backlog and
the feature is usable without motion or a pointer.

### Phase 5 — Edge cases, documentation, and rollout

1. Validate history pagination across split turns.
2. Validate SSE gap recovery and late results.
3. Validate tasks, delegates, concurrent agents, and background events.
4. Update reference documentation.
5. Ship behind `chat.view_mode=simplified`, classic default for the first
   releases (beta.47-50), then make simplified the default a conversation gets
   when nobody chose.

Exit criterion: targeted tests, full suite, and manual browser matrix pass.

## Test plan

### Backend unit tests

- Every persisted assistant/tool row receives the request user `msg_id` as
  `turn_id`.
- Every message-level SSE event carries the same turn id.
- A normal text-only turn marks one final row.
- A tool loop with several assistant narrations marks only the terminal answer.
- CLI-provider callbacks spanning several messages retain one turn id.
- Continuing rounds do not mark an intermediate row final.
- Error, cancellation, discard, interruption, and force stop do not mark a final.
- Ordered writer patch happens after message write and before `done`.
- Concurrent turns in one conversation never share ids.
- Display classification preserves turn metadata on messages, thinking, calls,
  and results.
- `ShowFileHandler` success retains the stable marker fields required for reload:
  `__show_file__`, `file_id`, filename, content type, size, and canonical URL.

### Frontend structural/unit coverage

- `turn_view.js` is loaded in the required order.
- The View menu exposes exclusive Classic/Simplified choices.
- Simplified mode disables technical grouping.
- The block contains one tablist and four labelled panels.
- Only a valid successful `show_file` result creates an artifact.
- A `show_file` call remains visible and completed in Tool calls while its file
  renderer exists only once, in Artifacts.
- Artifact reconciliation deduplicates by `file_id` within the turn and still
  completes every aliased `tc_id`.
- Transient output uses `textContent`.
- Every transient queue item carries one semantic `iconKey`; there is no separate
  icon queue.
- Icon layers are bounded, decorative, pointer-transparent, and removed on every
  terminal/reset/expand path.
- Reduced-motion CSS exists.
- Reduced motion disables particle, scale, rotation, and drift animation while
  retaining one static semantic icon.
- Finalization moves rather than clones the final node.
- Every terminal SSE path calls the turn finalizer/failure path.

Where practical, keep event classification and queue reduction as pure functions
so they can be tested without a browser DOM. Structural source assertions alone
are not sufficient for ordering and dedupe invariants.

#### Behavioural JS harness

Ordering, coalescing, and eviction are stateful and cannot be asserted from the
source text, so `tests/js/turn_view_spec.js` drives the real controller under
Node against `tests/js/dom_stub.js` — a small DOM implementation covering only
what `turn_view.js` and the live-window trim in `messages_render.js` touch. No
npm dependency is required. `tests/test_turn_view_js.py` puts it on the pytest
gate and skips when Node is absent.

The stub supplies a deterministic clock, so timer-driven behaviour (the
coalescing window, the animation cadence) is exercised without sleeping. What
it covers today:

- Classic mode is inert: no block, no reparenting, single-node eviction groups.
- A turn carrying no `turn_id` at all — not on the user row, not on any event —
  groups exactly like a stamped one, including a user row with no id whatsoever.
  A second user message closes the open turn and opens its own block, and an
  answer whose `done` still names the first turn lands under the last block, not
  back under the first. A user message nothing followed gets no empty block, and
  rows that precede any user message stay top level.
- A reloaded turn rebuilds from classifier rows to exactly user / block / answer,
  files narration, thinking and tool traffic into their tabs, and the block is
  expandable. Rows with no `turn_id` are left top-level rather than half-grouped.
- The final answer lands immediately after the block; intermediate rows do not.
- A derived `turn_final` never displaces an established one, and the rejected
  row is filed into a tab rather than left floating; an authoritative marker
  displaces a derived one and reclaims it.
- Streamed tokens produce one cue per coalescing window, not one per token,
  while discrete tool cues are not delayed.
- Turn identity is not re-rendered per token, but a changed identity still is.
- Eviction never destroys a turn whose block is still live, and classic-mode
  trimming is unchanged.
- A cancelled turn keeps its status and manufactures no answer.

When changing this controller, neutralize each guard and confirm the suite
fails. A test that passes against the broken code is worse than no test.

#### Conversation load path harness

`tests/js/conversations_spec.js` is the same harness pointed at
`conversations.js`, and `tests/test_conversations_js.py` puts it on the gate.
It exists because those invariants used to live only in the browser tests of
`tests/test_webchat_durable_state_behavior.py`, which skip wherever headless
Chromium renders nothing -- the GitHub runners among them -- and a skipped
gate is no gate. The stub gained a document fragment, a `scrollHeight`, and
document-level listeners, which is all `conversations.js` asks of a page
beyond what the controller already used.

What it covers today:

- Resume, gap recovery and load-more page in the backend's cursor units. The
  offsets come from `history_cursor`, the tie-breaker id is the backend's, and
  a DOM row never becomes a cursor -- rows are presentation and may be id-less,
  classified away, or grouped many-to-one. A delegate box worth two transcript
  rows advances the cursor by two, and the banner counts raw rows.
- Switching A -> B -> A publishes each conversation's own active turns,
  hydrates them, and only then opens the stream. B carries none, so it must
  show none rather than inherit A's.
- Live-window trimming forgets every id it removed, grouped children included,
  so an evicted delegate box can be rendered again instead of being deduped
  against ids nothing on screen carries.

The same rule applies here: neutralize the guard, confirm the test fails.

### Browser integration matrix

1. Text-only streamed answer.
2. Thinking followed by a final answer.
3. Narration, multiple tool calls, late results, final answer.
4. `show_file` for image, PDF, text/code, audio, and video while the turn is live.
5. Replayed `show_file` SSE and repeated calls for the same `file_id` produce one
   card in that turn.
6. A FileStore file that was created but never sent through `show_file` does not
   appear.
7. Failed/malformed `show_file` remains a Tool calls result and creates no card.
8. Expand during thinking; switch tabs while events arrive.
9. Collapse again while still live; verify only new events animate.
10. High-rate tokens coalesce into bounded message-bubble cues rather than one
    icon pop per token.
11. Thinking, tool, and artifact activity display their respective synchronized
    icon families.
12. Expand or finalize mid-pop; all transient icon nodes disappear immediately.
13. Final arrives while Messages is active.
14. Final arrives while another tab is active.
15. Stop during a pending tool call.
16. Agent error before any final.
17. SSE disconnect/reconnect during a tool result.
18. Reload during a live turn and after completion; artifacts reconstruct once.
19. Delete a shown FileStore object, reload, and verify an unavailable card.
20. Load older history where a page boundary splits a turn.
21. Two concurrent agents/turns.
22. Delegate and task activity.
23. Actionable approval and `ask_user` remain visible.
24. Switch simplified -> classic -> simplified.
25. Mobile viewport, keyboard-only navigation, light/dark/custom theme, and
    reduced motion.

## Acceptance criteria

- A normal completed simplified turn has exactly three top-level semantic pieces:
  user message, activity block, final message.
- During a live turn, expanding the block immediately shows live Messages,
  Thinking, Tool calls, and Artifacts tabs.
- All tabs continue ingesting while one tab is selected.
- Tool calls appear pending and receive their actual result in place.
- A valid successful `show_file` result creates exactly one artifact card per
  `file_id` within that turn, live and after reload.
- Files merely present in FileStore, or referenced by other tools without
  `show_file`, never appear in Artifacts.
- The `show_file` call remains auditable in Tool calls; its preview/viewer DOM is
  rendered only once in Artifacts.
- The collapsed view displays bounded, current animation and never replays a
  stale backlog after completion.
- Each collapsed message, thinking, tool, or artifact cue displays its matching
  semantic icon animation synchronized with the text.
- Icon particles are bounded, do not affect layout or pointer input, and cannot
  outlive, duplicate, or backlog independently from their cue.
- Reduced motion retains a clear static semantic icon without pop, scale,
  rotation, drift, or particle effects.
- `Calling tool...` is the only collapsed tool-call text.
- The final answer is the same canonical DOM/message node, appears once, and
  remains outside the block.
- Reload, pagination, and reconnect reconstruct the same turn boundaries.
- Error/cancel/stop preserves detail and never invents a final.
- Actionable UI is never hidden behind expansion.
- Classic mode has no visual or behavioral regression.
- `msg_id` dedupe, tool-result attachment, message actions, task/delegate
  controls, cost/context metadata, and scroll behavior remain functional.
- Keyboard, screen reader, mobile, theme, and reduced-motion requirements pass.
- Targeted tests and the full test suite pass.

## Revisions after use

What the plan specified and what surviving contact with a live server changed.

**The visible answer is positional, not declared.** The plan hoisted the row the
done payload marked final. A provider that ends a turn without naming one --
the CLI providers do -- left its answer inside a collapsed tab under a header
still reading "working". The rule is now: the last message row of a turn sits
outside the block, and hands that place back when a newer one arrives. Replayed
history is exempt, because an older page arrives after rows that precede it and
"last ingested" is not "last of the turn" there.

**A named final is a name, and a promotion is a move.** `final_msg_id` is
resolved with a lookup that can reach any row on screen, and `_turnPromoteLast`
*moves* what it is given. A `done` for a turn that produced nothing of its own
can still carry an id from an earlier turn; obeying it tore that message out of
the order the reader had already read it in and dropped it at the bottom, under
a fresh block that never produced it -- reported from a real session after a
server restart, where the turn died on an auth error. The promotion is now
refused unless the row is already filed inside this turn's block or sits after
it at top level. Position decides here as everywhere else.

**A turn ends when the turn ends.** Closing the block required `is_final` and
`final_msg_id` together; it now happens on any done that is not `continuing`,
and on the next user message for a turn nobody closed. What the server names
still decides which row is hoisted, never whether the turn is over.

**The ephemeral surface is a column over a glyph rain, not a depth stack.**
Cues sharing one spot reads well for four words and falls apart the moment one
carries a tool call rendered in full: three cues blurred through each other on
the same square inch. They now form a column, newest on top. Nothing retires on
a timer -- a cue holds its place until a newer one arrives, because a surface
that blanks itself after 2.6s reads as a stall during a long tool call. Behind
them, a themed glyph rain runs while the turn runs, and each cue condenses out
of it character by character. One shared 15 fps ticker drives every canvas on
the page, and it stops itself when the last block finishes.

**A tool cue shows the call.** "Calling tool..." named neither the tool nor its
arguments, on the one surface whose whole purpose is to say what is happening.
The cue carries a copy of the canonical row, rendered as the classic view draws
it; the original stays in its tab.

**A tool cue shows the work, not the transport.** A code-mode turn is a
sequence of groups: one native `exec(<code-mode script, N chars>)` per group,
and underneath it the four or five MCP calls the relay reports for that script.
Cueing every row alike put the wrapper in front and the work behind. Three
things keep the surface on the calls:

- A native row is held for `TURN_NATIVE_TOOL_DEFER_MS`, and an MCP call
  arriving in that window takes its place. The window is 1500 ms, not 500:
  the row is drawn when the model *finishes emitting* the call, while the first
  MCP row still waits on the TUI, the relay round trip and the script's own
  preamble, so half a second lost that race on nearly every group.
- Once a turn has produced one MCP call, its native **wrappers** are dropped
  from the cue for the rest of the turn. Deferring each wrapper only against
  the calls that follow it let every *later* script win its own race — one
  deferral fixed, the pattern unchanged. A wrapper is recognised by the mark
  the provider leaves on it (`<code-mode script, N chars>`), not by "native in
  a turn that has reached MCP": that reading also hid a real `local_shell_call`
  or `apply_patch` in a mixed turn, which is a tool the agent ran in its own
  right and exactly what the surface is for.
  The same mark decides the fate of a row still *inside* its deferral window
  when the first MCP call arrives: the wrapper is dropped, a genuine native
  call is emitted behind the MCP cue. The window asks whether the row was
  transport; it is not a sentence on whatever happens to be waiting in it.
- A row is cued once, when the call appears. The `tool_result` offers the same
  row a second time, by then grown to hold its output; for a code body that is
  the whole `Script completed / Wall time / ...` block. That second offer is
  also the only completion signal this surface gets — see below.

**A tool that is still running holds its place.** A cue is normally pushed off
the back of the column by the four newer ones behind it. For a call that has
not answered yet that is backwards: the thinking and the message the agent
produces *while it waits* pushed the call out of sight, so the one moment the
reader wants to know what is running was the moment it stopped being shown. A
tool cue whose row carries no `.tc-result` is pinned: newer cues push it back
and dim it, but cannot push it out. It is released when its result lands on
the row (the second offer), and dropped with everything else when the turn
ends. Bounded like the rest — `TURN_TRANSIENT_MAX_PINNED_STACK` — so a script
that fires a dozen calls at once does not turn the column into a wall, and a
pinned cue never fades past the last visible step: it is held to be read.

A `show_file` result never produces that second offer: `turnViewHandleToolResult`
claims it for the Artifacts tab and the caller stops there. The release is
therefore raised where the artifact is claimed, not only where the second offer
is seen — both paths are the result landing on the row, and the pin means the
same thing on either.

The suppressed rows are unaffected in Tool calls — this is the animated surface
only, and it is the one place that has to name what is happening now.

**The header counts.** Elapsed seconds, ticking while the turn runs, frozen at
the total once it ends.

Two layout facts, measured in a browser against the real stylesheet rather than
reasoned about (`tests/js/bench_simplified_block.py` builds the bench): a cue
body with no flex basis of its own computes to zero width in its row, and the
same body with `overflow: hidden` measured 442x0 with a scrollHeight of 41 --
on the newest cue only. The clipping lives on the cue above it instead.

## Delivery strategy

Deliver in two reviewable implementation changes:

1. **Turn data contract** — durable `turn_id`, terminal message marker, writer
   ordering, serialization, and backend tests.
2. **Simplified live view** — mode selection, turn controller, live tabs,
   artifact classification, animation, history/reconnect integration, i18n,
   frontend tests, and docs.

Do not ship the frontend mode before the durable contract. A DOM-only grouping
can look correct during one live session but cannot reliably survive reload,
pagination, or concurrent activity.
