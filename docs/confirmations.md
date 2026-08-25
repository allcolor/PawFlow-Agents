# Durable User Interactions, Timers, and Flow Wait/Notify

Typed user interactions and durable flow continuations share one SQLite store
(`data/confirmations.db`, `core/confirmation_store.py`).

The versioned `pawflow.user-interaction.v1` record is the durable semantic
contract. Webchat, PawCode, and VS Code render that record according to their
capabilities; presentation is never the authority for the answer or continuation.
Every client restores pending records after reconnect and submits answers through
the generic `respond_interaction` server action.

## Confirmation requests

An **agent** or a **flow** asks the user something — yes/no, a single choice,
or several choices from a list. The request is **durable**: it survives page
reloads and server restarts, and the user answers **whenever they want**
(minutes, hours, or days later). Answering resumes the requester.

### Asking

- **Agent tool** `request_confirmation(message, mode=confirm|choice|multi,
  options?, title?, expires_in?, wait_seconds?)` — approval-exempt (it is a
  user interaction). Default behavior is fully asynchronous: the tool
  returns "pending" and the agent ends its turn; when the user answers, the
  agent is **woken** through the PollScheduler with the answer in its wake
  reason and continues from where it left off. `wait_seconds` (max 120)
  optionally polls for an immediate answer first. `expires_in` accepts
  `"2h"`, `"3d"`, `"1mo"`...
  `ask_user` remains for quick live questions in an active exchange;
  `request_confirmation` is for decisions that must not be lost.
- **Flow task** `requestConfirmation` (message, mode, options
  comma-separated, title, expires_in) — publishes into the conversation of
  the deploy runtime context (or an explicit `conversation_id`), stamps
  `confirmation.request_id` and `confirmation.signal_id` on the FlowFile,
  and passes it on. Chain a `durableWait` to suspend the branch until the
  answer (see below).

## Typed user input

The `requestUserInput` flow task extends confirmations without creating a second
store or continuation mechanism. It supports `confirm`, `choice`, `multi`,
`text`, `multiline`, `integer`, `decimal`, `date`, `datetime`, `file`, and
structured `form` input. `response_schema` carries validation constraints such as
text length, numeric bounds, choice options, required form fields, and field
types. The server validates and normalizes every answer before atomically moving
the request out of `pending`; invalid answers do not resume the requester.

The task accepts only the user and conversation scope injected by the flow
runtime. Configuration and FlowFile attributes cannot redirect a request to
another user or conversation. It stamps `interaction.request_id` and
`interaction.signal_id`; chain `durableWait` on that signal when the branch must
pause for the response.

Generic actions are `list_interactions`, `respond_interaction`, and
`cancel_interaction`. The legacy confirmation actions remain aliases for the
three original input kinds. Reads are scoped to the authenticated owner; writes
also allow conversation collaborators with write access. Unknown and
unauthorized IDs both return 404 so the endpoint is not an existence oracle.

The schema migration is in-place and additive: existing confirmation rows are
preserved, backfilled as contract version 1, and retain their
`confirmation:<request_id>` signals. Initialization verifies row count, foreign
keys, and that every pending row has a kind and signal before recording the
migration marker.

### Client behavior

- Webchat, PawCode, and VS Code hydrate pending interactions from durable server
  state after reload or SSE reconnect.
- Each client renders the supported semantic input kinds and returns the typed
  value through `respond_interaction`; server-side validation remains canonical.
- Rich UI surfaces use the separate generic `pawflow.ui-surface.v1` capability
  contract. A client that cannot safely render a rich surface uses its declared
  semantic fallback or shows an explicit handoff instead of silently accepting.

## User notifications

`notifyUser` publishes a non-blocking notification in the runtime-injected
conversation. It never parks the FlowFile and routes to `sent` when a live client
is subscribed, `queued` when durable replay will deliver it later, or `failure`
on a delivery error. `urgency` is `low`, `normal`, or `high`.

### Answering (webchat)

- The request renders as an **actionable inline block** in the conversation
  (buttons for confirm/choice, checkboxes + validate for multi) — live via
  the `confirmation_request` SSE event AND re-rendered from the store after
  every history load, so it stays actionable after reloads and restarts.
- The **pending panel** (header ✅ button with badge, `/confirmations`, or
  the openspace poster) lists every pending request of the user across all
  conversations — the durable inbox for answering days later.
- Actions: `list_confirmations`, `respond_confirmation`, and
  `cancel_confirmation` (owner or conversation collaborators with write
  access; unknown and unauthorized ids get the same 404).
- On answer/cancel/expiry the `confirmation_answered` SSE event closes the
  block everywhere, and the durable signal `confirmation:<request_id>`
  fires with `{status, answer}`.

### Resuming

- **Agent requester**: a wake is scheduled (`[agent:<name>]
  [confirmation:<id>] The user answered ...: <answer JSON>`), delivered as a
  system-marked turn by the poller — the standard scheduled-wakeup path.
- **Flow requester**: the durable signal `confirmation:<request_id>`
  resolves any `durableWait` parked on it (answered, cancelled, and expired
  all release the waiter; the status travels in `durable.wait.value`).

## Durable wait/notify for flows

`waitForSignal`/`notify` (SignalRegistry) stay the fast **in-memory**
primitives for short intra-process synchronization. The durable pair is for
everything longer:

- **`durableWait`** parks the FlowFile (content + attributes serialized to
  the store) on a signal for as long as `timeout` allows — `"90s"`,
  `"12h"`, `"30d"`, `"6mo"`, `"2y"`, or absent/0 = **forever**. The task
  emits nothing at park time; when the signal fires (or the timeout
  expires), the FlowFile is restored and **re-injected at the wait task
  itself**, which recognizes the `durable.wait.status` attribute and passes
  it through with:
  - `durable.wait.status` = `signaled` | `timeout`
  - `durable.wait.signal_id`
  - `durable.wait.value` = the JSON value delivered by the notify
  Route downstream with `routeOnAttribute`. The signal id comes from
  `signal_id` (static/expression) or from a FlowFile attribute
  (`signal_id_attribute`, default `confirmation.signal_id`).
- **`durableNotify`** fires a signal (`signal_id` or `signal_id_attribute`,
  value from `value` or `value_attribute`) — every parked FlowFile on it
  resumes, across flows and restarts. With **no waiter**, the latest value
  is remembered: the next `durableWait` on that signal passes through
  immediately (no lost-notify race). `notify_signal` is also callable from
  any Python code.
- **Restart-safe delivery**: a resolved wait whose flow instance is not
  running stays `resolved` and is retried by the background sweeper (15 s)
  until the instance is running again (`ExecutorRegistry` +
  `executor.inject(ff, entry_task_id=<wait task>)`). Backpressure at
  injection is also retried.
- `durableWait` requires a **deployed continuous flow** — a one-shot batch
  run cannot receive the re-injection and fails at execute time with an
  explicit error.

### Durable timers

`durableTimer` parks a FlowFile until exactly one configured deadline: a
relative `duration` (`30s`, `5m`, `2h`, and the same long-duration units as
`durableWait`) or an absolute timezone-aware ISO-8601 `until` timestamp. It
does not sleep in a task worker. The SQLite continuation is swept, restored,
and re-injected at the timer task with `durable.timer.status=elapsed` and
`route.relationship=elapsed`; cancellation uses the `cancelled` relationship.
An undelivered timer or signal continuation prevents idle auto-stop.

### Canonical pattern

```text
requestConfirmation ──> durableWait ──> routeOnAttribute(durable.wait.status /
                                        durable.wait.value)
```

The user answers from the webchat pending panel — 24 hours later if they
want — and the branch resumes with the answer.

## Implementation

- `core/confirmation_store.py` — versioned typed interaction validation and
  migration, store, sweeper (expiry, wait timeouts, delivery retries), agent wake
  routing, and timeout parsing.
- `core/handlers/user_interaction.py` — `request_confirmation` tool.
- `tasks/control/durable_confirm.py` — typed input, notification, confirmation,
  timer, wait, and notify flow tasks.
- `tasks/ai/actions/confirmations.py` — authenticated generic interaction and
  compatibility confirmation actions.
- `tasks/io/chat_ui/confirmations_panel.js` — inline blocks, pending panel,
  badge, hydration.
- `pawflow_cli/` and `pawflow-vscode/` — terminal and editor interaction
  restoration/rendering/response paths.
- Tests: `tests/test_confirmation_store.py`,
  `tests/test_chat_ui_confirmations.py`, `tests/test_pawcode_event_dispatch.py`,
  and `tests/test_ui_surface_clients.py`.
