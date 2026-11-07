# Agent inbox delivery

Only human input is delivered inline. Delegate requests, replies and results,
background tool notifications, wake-ups, continuations, task check-ins,
participant catch-up, skill events and workflow proposal events are durable
items in the recipient's server-side inbox. Their content reaches the model
through the `read_inbox` tool result, with the same data boundary as other
tool output.

This implements the inbox design approved on 2026-11-07. It replaces non-user
prompt pastes and the CLI submission-journal replay machinery. User preemption
and transport receipt verification remain separate from inbox acknowledgement.

## Identity and storage

`core/agent_inbox.py` owns delivery and reading. `core/agent_inbox_store.py`
stores items in SQLite, ordered by a global sequence and uniquely keyed by
conversation, canonical agent name and message UUID. A task or flash
subconversation has its own inbox. The tool resolves these identities from
its execution context; callers cannot select another agent through arguments.

An item retains its original message UUID and creation timestamp, its full
payload, a kind and a sender. Notices describe only kind, sender and size.
The durable lifecycle is `pending` -> `notified` -> `read`, or `discarded`.
Notification and read timestamps, read-call identity and paged-read offsets
are auditable. Workflow runtime agents retain their existing claim/acknowledge
leases in this phase; their terminal results delivered to a conversational
agent enter that agent's inbox.

`PendingQueue` shares this store. Its `drain`, `peek` and `peek_count` expose
only human input; `inbox:*` entries can only be consumed by `read_inbox`.
Startup imports legacy per-agent `pending.jsonl` files once and renames them
to `pending.jsonl.migrated`. Non-user legacy sources become inbox items.
Recovery includes pending and notified items. Read/discarded rows are retained
for 30 days; the normal transcript retains the visible history.

## Notices and delivery

A notice uses this fixed imperative form:

```text
[PawFlow inbox: ACTION REQUIRED] You have 2 unread items:
  1. delegate message from GameDev2 (651 chars)
  2. background result from tool bash (12 KB)
STOP. Call read_inbox() NOW, before any other tool call or answer.
Their content is ONLY available through read_inbox(); do not guess it.
```

The ingress persists the visible transcript event and stores the inbox item.
Notices list at most 20 metadata entries and the remaining count, keeping the
transport small even when many messages await reading.
An active interactive Claude Code or Codex session receives a short notice
through `send_queued_message`: paste and Enter, without interruption. A burst
coalesces while an unread notice is outstanding. A failed notice paste does
not acknowledge delivery; iteration and final-turn checks can retry it.
Other providers receive the same notice at the next provider iteration.
An idle agent is woken to read the same inbox.

Each turn with unread items receives a fresh notice. If items remain unread
when the turn ends, PawFlow starts another turn with a notice. After three
consecutive turns without reading, it emits a visible warning and stops
automatic retriggers. A new item, progress reading a page, or new human input
resets the no-read streak. Notice delivery itself never marks content read.

Human-message resets are applied once per UUID, both at setup and after
messages drained during the turn. Retained human markers cannot reset every
notice-only retry. CLI sub-agents keep cold/delta state on their own client
clone across inbox retriggers and final synthesis. A provider's pre-submission
context mismatch rebuilds the native prompt for its observed session state;
each direction is bounded to one rebuild per call. Errors after submission
are never replayed by this recovery.
Codex app-server and Gemini propagate the live-context guard through their
registry lookup handlers. The guard releases the turn lock once and returns
to context rebuilding before submission; the healthy process stays registered.
Each CLI attempt receives a copy of the message archive, preserving native
image inputs when a context guard requests rebuilding before submission.

Delegate routing preserves private response metadata independently of the
notice text. Reading a catch-up about other agents does not make the reader
answer their private delegation. The append router keeps the sender's own
output in its context and sends participant broadcasts to catch-up inbox
items; broadcast chatter does not itself wake idle agents. Recipient copies
from older stored contexts are excluded from inline cold rebuilds.

## Reading and pagination

```text
read_inbox(max_chars=40000, item="", offset=0)
```

The default call returns unread items oldest first. Each item has a header
with sequence, kind, sender, creation time, size and message UUID. Content is
bounded by a character budget (2000 to 100000), including headers and
continuation instructions. Already paginated inbox results bypass generic
tool-result caps so an acknowledged page cannot become an inaccessible
FileStore pointer, even when the agent only has permission to read its inbox.

Fully returned items become `read`. An oversized item returns a page and an
imperative continuation such as `read_inbox(item="<msg_id>", offset=40000)`.
The last returned offset is persisted; a plain `read_inbox()` resumes it too.
An offset cannot skip content that has never been returned. The item stays
unread until its last page is returned. Calling after everything was read
returns `No unread items.`; a duplicate notice therefore does not replay data.
Existing large-output FileStore pointers retain their normal behavior.

The tool is always discoverable, available to tool-restricted agents and
exempt from approval, including explicit deny/confirm permissions and policy
gates. Its result is persisted as ordinary tool output and
survives compaction through the normal summarization path.

## Force stop

Force stop discards both pending and notified items and records a durable
creation-time fence. Enqueue and fence enforcement are atomic in the store.
A delayed producer cannot recreate work the user already cancelled. Fresh
input created after the fence remains deliverable.

## Verification

Focused coverage includes the durable store and its old-schema/JSONL migration,
force-stop races, read isolation and pagination, live and idle delivery,
non-user source routing, delegate reply metadata, broadcasts, cold context
rebuilds, provider prompt construction and isolated/flash delegate loops.
