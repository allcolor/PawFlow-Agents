# Multi-Conversation Tiled Workspace Implementation Plan

Status: approved for implementation
Priority: 1 — first implementation workstream after this planning batch
Date: 2026-08-30
Owner: PawFlow Webchat UI
Requested outcome: a live multi-conversation cockpit

## 1. Outcome

Turn the existing tiled workspace into a multi-conversation cockpit. A user can
open several or all desired conversations at once, see them simultaneously when
the selected layout allows it, and act on each without replacing the others.

Every workspace surface is bound to a conversation. Selecting a tile makes that
conversation the target of the shared composer, prompt actions, active-agent
display, and left conversation/resources panel. Other conversation tiles remain
mounted, visible, live, and independently scrollable.

Simple is not a legacy replacement mode. It is layout 1: one full-size tile per
viewport in the same horizontally persistent board.

## 2. User interaction contract

### 2.1 Opening a conversation

Clicking a conversation in the left panel:

1. resolves an existing authorized conversation tile or creates one;
2. inserts a new tile immediately to the right of the currently selected tile;
3. binds the tile permanently to that conversation_id;
4. selects the tile;
5. scrolls the board until the whole tile is in view;
6. loads the composer/agent/resources context for that conversation;
7. leaves every previously opened tile mounted and unchanged.

A repeated click focuses the existing tile and never creates a duplicate.

### 2.2 Selecting any tile

Pointer, keyboard, taskbar, or programmatic focus of any surface:

- updates selectedSurfaceId;
- derives focusedConversationId from that surface;
- highlights the corresponding conversation row;
- loads the left panel for that conversation;
- updates active agent, permission, cost/context, confirmations, and prompt
  controls;
- routes subsequent composer actions to that conversation;
- does not close another conversation SSE, clear another transcript, or reset an
  unrelated surface.

The selected tile's title bar uses the accent surface and border so focus is
visible independently of tile content. A title bar is also the tile's drag
handle: dragging it onto another tile or an empty layout slot reorders the
surfaces, while title-bar action buttons never start a drag. The resulting DOM
order and slot assignments are saved in `pawflow.workspace.state.v2` and restored
on the next workspace load.

### 2.3 Simple layout

Layout 1 means:

- one column and one row visible;
- every tile is exactly the workspace free width and height;
- the board is a horizontal ordered strip;
- all tiles remain mounted;
- the selected tile is scrolled fully into view;
- horizontal wheel/trackpad/scrollbar movement to the left returns to the
  preceding tile;
- a new conversation or tool surface is inserted immediately after the selected
  tile, then focused;
- the header remains available so the tile can be closed, targeted, or restored;
- changing from layout 2–6 to 1 preserves tile order and selected tile.

Layouts 2–6 change only visible grid geometry. They do not change identity,
lifecycle, ordering, or routing semantics.

### 2.4 Tool surfaces

Terminal, tmux, Desktop, VS Code, browser, audio, OpenSpace, agent-filter, and
task-filter surfaces inherit the focused conversation at creation and retain it.

Clicking such a tile routes the shared composer and left panel to its bound
conversation. A global/user-only surface must explicitly declare that it is not
conversation-bound; ordinary workspace surface registration cannot silently omit
conversation identity.

## 3. Verified current behavior

The live frontend currently has:

- one workspace surface registered under tab ID chat;
- one canonical messages element;
- one global conversationId;
- one global selectedAgent;
- one global eventSource and global SSE rendering state;
- resumeConv as the only conversation switch path;
- resumeConv clearing the transcript, interaction state, streams, filters, agent,
  and SSE before loading another conversation;
- send reading conversationId and selectedAgent after asynchronous preparation;
- loadResources reading the mutable global conversationId in debounced and
  parallel callbacks;
- filtered agent/task tiles cloning the one current transcript and being deleted
  on conversation change;
- horizontal grid/scroll CSS enabled only for layouts greater than one.

The server event bus already supports concurrent subscriptions keyed by
conversation_id and client_id. The primary limitation is frontend state
ownership, not a missing backend conversation primitive.

## 4. Non-goals

- Do not implement static screenshots that pretend to be live conversations.
- Do not duplicate transcript authority in localStorage.
- Do not run one full Webchat application iframe per tile.
- Do not create a second conversation store or SSE backend.
- Do not route messages from whichever global changed last.
- Do not preserve unauthorized tiles after ACL loss.
- Do not make layout count a limit on the number of open tiles.
- Do not reconnect terminal/desktop/browser surfaces on focus.
- Do not change selectedAgent rules on the server.

## 5. Architectural decisions

1. WorkspaceSurface owns surfaceId, type, conversationId, order, panel, title,
   lifecycle, and optional agent/task filter.
2. ConversationSession owns live state for one open conversation.
3. WorkspaceFocus owns the selected surface and focused conversation.
4. Focus and lifecycle are separate: losing focus never disposes a session.
5. A conversation can have one canonical conversation surface and several
   conversation-bound tool/filter surfaces.
6. The transcript store/session is authoritative for browser rendering; DOM
   projections are views.
7. Every asynchronous request captures its conversation ID and verifies it before
   updating a focus-scoped panel.
8. Every outgoing action captures target conversation and target agent before its
   first await.
9. One conversation SSE connection serves every local view of that conversation.
10. Existing server ConversationStore remains authoritative across reload.
11. Open tile order and layout may persist locally, but access is revalidated on
    restore.
12. No tile silently becomes stale. A disconnected/paused state is explicit.

## 6. Frontend state contracts

### 6.1 WorkspaceSurfaceV2

Fields:

- surfaceId;
- type;
- conversationId;
- title and icon;
- order;
- panel/body;
- closable;
- selected and targeted flags;
- optional agentName/taskId;
- lifecycle: mounted, disconnected, closing;
- createdAt and lastFocusedAt.

Conversation surface IDs derive from conversation ID, not display title:
webchat:<conversation_id>.

### 6.2 ConversationSessionV1

Fields:

- conversationId;
- access generation and title;
- activeAgent;
- EventSource, client ID, reconnect state, and health timestamps;
- transcript container/store and seen message IDs;
- history cursor/count;
- streaming elements and task/delegate groups;
- active interactions and confirmations;
- context/cost/permission projections;
- view/grouping settings;
- pending sends and attachments owned by the session where required;
- connection and error status;
- reference count from open surfaces.

State is in memory. Only safe workspace restore metadata is stored in
localStorage.

### 6.3 WorkspaceFocusV1

Fields:

- selectedSurfaceId;
- focusedConversationId;
- focusGeneration;
- selectedAgent projection;
- previousSurfaceId;
- reason and timestamp.

All focus-scoped async rendering uses focusGeneration to reject stale completion.

## 7. Conversation session manager

Add a conversation_sessions.js module with narrow APIs:

- ensureConversationSession(conversationId);
- getConversationSession(conversationId);
- retain/releaseConversationSession;
- loadConversationSession;
- connect/disconnectConversationSession;
- focusConversationSession;
- closeConversationSession;
- routeConversationEvent;
- renderConversationView.

Session creation:

1. validate conversation access through the normal load_history/list path;
2. allocate per-session state and transcript root;
3. load authoritative history;
4. adopt the exact active agent and view settings;
5. connect SSE with a stable per-tab/per-conversation client ID;
6. publish ready/error state to its surfaces.

Closing the last surface releases the SSE and volatile state. It never deletes
the conversation.

## 8. SSE architecture

Refactor connectSSE into a session-owned connection rather than a global.

Requirements:

- client ID is stable per browser tab and conversation, for example
  <tab-client>:conv:<digest>;
- same-conversation views share the session connection;
- different conversations maintain independent connections;
- every handler receives or resolves the owning session;
- event rendering targets that session transcript;
- reconnect, replay, gap recovery, health, usage, LiveKit, and extension hooks are
  scoped;
- one session failure cannot clear another;
- logout/session-expiry closes all connections once.

All open conversations are live by default. If operational telemetry later
requires a connection budget, suspension must be explicit, preserve persisted
history, show a visible paused state, and resume with gap reconciliation. No
silent cap is part of V1.

### 8.1 Focus-owned browser runtimes

Transcript state and EventSource connections remain live per session, but the
browser exposes only one microphone, one realtime voice/LiveKit attachment, one
TTS playback pipeline, and one OpenSpace room. Tile focus is therefore the
authority for these global runtimes:

- changing to a different conversation cancels legacy voice, LiveKit, and both
  live and one-shot TTS work under the previous session's captured context;
- acquisition and playback callbacks carry an owner plus generation and cannot
  mutate, report errors into, or stop a newer focused session;
- STT captures the recording session, conversation ID, configuration, and
  composer state before microphone acquisition; transcription and optional
  auto-send return to that session even if focus changes;
- OpenSpace keeps a history cache only for open conversation sessions and
  projects only the focused conversation. Durable `new_message` events update
  their owner session's cache even in the background, but no background event
  may mutate the visible room or take ownership. History refreshes merge by
  message ID so a stale response cannot erase newer SSE rows.

Releasing the last surface closes its SSE state and evicts its OpenSpace cache.
Refocusing the same conversation is idempotent and does not interrupt its media.

## 9. Transcript and rendering refactor

Replace document-global transcript access with a ConversationViewContext.

Functions that currently use messages, seen IDs, streams, task blocks, offsets,
or selected message sets receive a context/session or execute through
withConversationContext(session, callback) during migration.

Each canonical conversation tile owns a unique transcript root. IDs inside cloned
or secondary projections are removed or namespaced.

Filtered agent/task views become read-only projections of their owning
ConversationSession, not of whichever conversation is focused. They survive
focus changes and close only when explicitly closed or access is lost.

An agent-filtered projection is fail-closed at event granularity: every visible
message, thinking cue, tool call, and tool result must carry the selected agent's
identity. Mixed task, delegate, technical, and simplified-turn containers may be
retained as structure, but foreign and unidentified event branches are removed.
Cloned simplified-turn controls are rehydrated after every projection refresh,
and filtered history pagination always executes in the owning
ConversationSession rather than the currently focused conversation.

OpenSpace mirrors the transcript of its bound session.

## 10. Workspace board and ordering

Upgrade workspace.js to one board model for layouts 1–6.

Rules:

- workspaceIsTiled no longer decides whether surfaces coexist;
- every registered surface remains display:flex and participates in board order;
- layout 1 uses one visible column/row and full workspace tile dimensions;
- layout 2–6 retain their current grid geometry;
- board width stays within the viewport while the tile count fits the visible
  columns × rows capacity;
- max-content width and horizontal scrolling activate only when the tile count
  exceeds that visible capacity;
- selected surface uses scrollIntoView or exact offset correction;
- insertion target defaults to the selected surface;
- new surface inserts after, not before, that target;
- target-arm behavior remains available for explicit placement;
- tile order is stable across layout changes and reload restore;
- closing the selected tile focuses the nearest right tile, otherwise the left.

Wheel behavior must preserve vertical transcript/terminal scrolling. Horizontal
navigation consumes horizontal wheel/trackpad input and only converts vertical
wheel input when the child cannot scroll in that direction and the configured
workspace gesture permits it.

## 11. Conversation list behavior

Replace sidebar calls to resumeConv with openWorkspaceConversation.

The list marks:

- focused conversation;
- conversations already open in tiles;
- active/blocked runtime status;
- optional unread/live activity.

Click focuses/opens. A separate context action may replace/close if desired, but
normal click never removes another tile.

New conversation creation produces a new conversation tile to the right of the
selected tile. Delete closes all associated surfaces only after the server
confirms deletion.

## 12. Composer and action routing

The composer is shared and stays outside the workspace board.

Before any await, send captures:

- selectedSurfaceId;
- targetConversationId;
- focusGeneration;
- targetAgent from that conversation;
- attachments and reply target owned by that conversation.

Local echo is rendered into the target ConversationSession. A response that
creates a new conversation binds/renames the pending tile atomically.

The same explicit routing applies to:

- slash commands and server command fallback;
- attachments/upload/delete;
- Stop, interrupt, background tool controls;
- confirmations and durable forms;
- permissions;
- agent selection;
- search, export, rename, compact, fork, and delete;
- TTS/STT and realtime media;
- Grab/tmux input;
- UI extension actions;
- FileStore and schedule panels.

An operation aborts with a visible stale-target error if its tile/session closes
before dispatch. It never falls through to a newly focused conversation.

## 13. Agent and left panel

The conversation remains the source of activeAgent. Focusing a tile adopts the
session active agent and updates the shared badge. Selecting another agent calls
the server for that exact conversation and updates only that session.

Resource loading accepts an explicit conversation ID and request generation.
Parallel responses render only when:

- their requested conversation still matches focusedConversationId;
- their focus generation is current;
- the user retains access.

Debounce is keyed per conversation or cancels the previous focus request.
Resources, services, packages, relays, files, schedules, plans, permissions,
theme, context, and cost never read a mutable global after dispatch.

The panel header shows the focused conversation title to make the routing
boundary visible.

## 14. Persistence and restore

Use workspace state schema v2 containing:

- layout;
- ordered surface descriptors with type and conversation ID;
- selected surface ID;
- optional scroll position;
- no transcript content, tokens, secrets, service definitions, or permissions.

On reload:

1. parse and validate the bounded descriptor list;
2. restore layout and safe surface shells;
3. revalidate each conversation/resource through the server;
4. discard unavailable surfaces with a notification;
5. load sessions with bounded concurrency;
6. focus the saved surface or the first valid surface.

Migration from layout v1 keeps the layout and creates one conversation tile for
the currently selected conversation. Old chat remains a compatibility alias only
during the migration and is removed after tests pass.

## 15. Accessibility and mobile

- Tiles have accessible conversation/type/title labels.
- Selected/focused/open states are not conveyed by color alone.
- Keyboard commands move focus to previous/next tile and open the conversation
  picker.
- Focus does not jump into a tile merely because background SSE updates.
- Screen-reader announcements identify the new composer target.
- Reduced motion disables smooth board scrolling.
- Mobile remains a 1×1 horizontal board with swipe/explicit previous-next
  navigation.
- Tablet layouts may show two tiles without changing state ownership.
- Each transcript preserves its independent scroll position.

## 16. Security and concurrency

- Revalidate ACL on open, restore, mutation, and reconnect.
- Never expose one conversation transcript/resources in another tile.
- Capture target identity before asynchronous upload, command, or send.
- Use generation guards for history, resources, agent, permission, and UI
  extension responses.
- Bound restored surface descriptors and concurrent initial loads.
- Close session SSE immediately on access revocation/deletion/logout.
- Keep FileStore references conversation/user scoped.
- Do not persist sensitive runtime state in browser storage.
- Treat titles, agent names, events, and extension content as untrusted.
- Force stop targets the exact conversation/agent and never the focused value at
  callback time.

## 17. Implementation sequence

### WP0 — Red tests and compatibility seam

Add runtime tests proving:

- opening B currently clears A;
- layout 1 hides/replaces instead of scrolling;
- send/resources can observe mutable global targets;
- filtered tiles are deleted on conversation switch.

Introduce accessors for focused conversation/agent without changing behavior.

### WP1 — Unified board layouts

Make layouts 1–6 use the persistent horizontal board. Implement full-size 1×1
tiles, insert-after-selected ordering, scroll-to-focus, close-neighbor behavior,
storage v2, and tool-surface conversation binding.

This visual/lifecycle foundation ships before multi-session rendering.

### WP2 — Workspace focus and sidebar routing

Add WorkspaceSurfaceV2 and WorkspaceFocusV1. Replace sidebar resume with
open/focus behavior. Route left-panel title and agent projection by focus.

### WP3 — ConversationSession and transcript ownership

Create one session/transcript state per open conversation. Port history rendering,
seen IDs, streams, grouping, interactions, filtered views, and OpenSpace
projection.

### WP4 — Multi-conversation SSE

Make event source, reconnect, health, gap recovery, usage, and extension hooks
session-owned. Verify concurrent live conversations.

### WP5 — Composer and action routing

Capture explicit targets, route local echo, attachments, commands, approvals,
agent changes, permissions, TTS/STT, realtime, and stop actions.

### WP6 — Resource panel and async race safety

Pass explicit conversation IDs and generations through all panel/resource/service
requests. Add stale-response rejection and access-loss behavior.

### WP7 — Restore, responsive UX, and observability

Restore authorized surfaces, add unread/connection state, keyboard/mobile
navigation, metrics, and accessibility.

### WP8 — Documentation and rollout

Update task/service reference, UI templates, operations, extension contracts,
translations, and release notes. Remove compatibility globals only after all
consumers migrate.

## 18. Test matrix

Required automated tests:

1. Simple is layout 1×1 of the persistent board;
2. every tile equals the free workspace size in layout 1;
3. opening a surface inserts immediately after selected;
4. new surface becomes focused and scrolls fully into view;
5. horizontal scroll left reaches the preceding tile;
6. changing layout preserves order, sessions, and focus;
7. opening conversation B leaves A mounted and unchanged;
8. repeated B click focuses the existing tile;
9. A and B histories render into separate roots;
10. A and B SSE events never cross;
11. disconnect/reconnect of A does not affect B;
12. selecting B routes composer and agent to B;
13. clicking an A-bound terminal routes the composer back to A;
14. a send captures A before an await even if B is clicked;
15. local echo appears only in the target tile;
16. attachments/replies/commands/interrupt target the captured conversation;
17. resource response from old focus cannot overwrite current panel;
18. selected agent is stored and rendered per conversation;
19. filtered agent/task tiles survive another conversation focus;
20. OpenSpace mirrors its bound session;
21. close releases only the last reference to a session;
22. delete closes exact associated surfaces after confirmation;
23. restore revalidates access and drops unauthorized tiles;
24. no transcript/secret is persisted locally;
25. logout/access revocation closes all affected SSE;
26. keyboard/mobile/reduced-motion behavior passes;
27. terminal/Desktop/VS Code/browser sessions do not reconnect on focus;
28. existing simplified/classic grouping and tool rendering remain correct;
29. Node runtime tests cover ordering, focus, races, and session isolation;
30. existing chat UI, SSE, resource, OpenSpace, terminal, and security tests pass.

Manual browser acceptance:

1. open at least four conversations with active agents;
2. see simultaneous independent token streaming in a multi-tile layout;
3. act on each by clicking its tile and using the shared composer;
4. switch to Simple and navigate the full-size horizontal sequence;
5. insert a terminal to the right of the selected conversation;
6. return left without losing any transcript or process;
7. reload and restore authorized layout/order/focus;
8. revoke/delete one conversation without affecting the others.

## 19. Observability

Add client diagnostics and bounded metrics for:

- open surfaces and conversation sessions;
- SSE state per conversation;
- focus changes and route target;
- stale async responses rejected;
- session restore failures;
- cross-session invariant violations;
- memory and DOM counts by open conversation.

Debug output uses shortened IDs and never message text or secrets.

## 20. Definition of done

The feature is done when a user can keep multiple conversations open and live,
see several simultaneously, directly act on any one by selecting its tile, and
trust that the shared composer, agent, actions, and left panel target exactly that
conversation.

In Simple mode, multiple full-size tiles form one horizontal sequence; opening
any conversation or tool inserts it to the right of the selected tile, focuses
and reveals it, and scrolling left returns to the previous tile.

No existing conversation, stream, transcript, terminal, desktop, browser, or
media session is removed or reconnected merely because another tile is opened or
focused.
