# WebMCP Integration Plan

Status: PLANNED — no implementation yet. v8.2, revised 2026-09-01 after
the token-scheme review pass (self-addressing handles, pinned key
versions, two-deadline model). v8 closed the protocol (opaque `attach_token` /
`batch_token`, secrets out of query strings, owner credential with
idempotent claim/renew, closed begin/deposit state matrix, per-call
catalogue identity checks, restart-safe relay fence at the true effect
boundary, named atomic transactions, publication-fixed managed mode).
The v8.1 erratum settles the three remaining schema-level contracts:
deterministically derived tokens (nothing bearer-like stored, yet
re-issuable), the `reserved_pre_effect → execution_committed` batch
reservation split at first `begin`, and `claim_generation`-bound
credential revocation with explicit race outcomes.

Three directions, in priority order:

- **Direction B — the PRIMARY product: agentize a web application.**
  The generalization of the site help bot (which today only talks): an
  embeddable chat widget (`pawflow-widget.js`) connects any web
  application to a published PawFlow agent (AG-UI protocol), and the
  *PawFlow agent* can call tools the page exposes, exactly like it
  calls PawFlow tools. The page declares its tools **once, against the
  standard** (`document.modelContext.registerTool`); the widget
  consumes them via the standard
  (`getTools()`/`executeTool()`/`toolchange`), with a bootstrap script
  polyfilling the standard where absent. The standard cannot transport
  anything to a remote PawFlow agent — the widget is always that
  channel.
- **Direction A — the site as a tool provider**: pawflow.allcolor.org
  registers standard WebMCP tools so visitors' browser agents can call
  the site's capabilities — including the live help bot.
- **Direction C — optional, opt-in per host app**: webmcp.dev-style
  pairing for a visitor's own MCP client. Off by default.

§8 sketches the longer-term consumer direction (`browser` tool). The
webmcp.dev library (pre-standard, different API) stays out of scope.

---

## 1. Standard background and the pinned profile

Unchanged from v7:

- All on **`document.modelContext`**: `registerTool({name, description,
  inputSchema, execute, annotations}, {signal, exposedTo})`
  (AbortSignal unregistration; `execute(args, {signal})`); the
  unregister/re-register race means names and schema fingerprints are
  never execution identity. `annotations` are unverified hints —
  presentation only. **`getTools({fromOrigins})`** (names not unique
  across frames; `window` not persistent). **`executeTool(tool, input,
  {signal})`** — result treated defensively as string-or-null (upstream
  IDL publishes non-nullable `Promise<DOMString>` while its algorithms
  and Chrome 153 use null-on-navigation; input type also diverges:
  object upstream vs JSON string in Chrome 153). `toolchange` event;
  `tools` Permissions-Policy; secure context; declarative `<form>` API
  (younger).
- **`[SameObject]`**: bootstrap = absent → pinned-profile polyfill
  (top-document only); partial → fail closed (`ready` rejects,
  diagnostic); full-surface detection; sentinel-tool input probe.
  Profile recorded in `docs/web_widget.md`, re-validated each release.

## 2. Existing PawFlow pieces and verified gaps

| Piece | Where | State |
| ----- | ----- | ----- |
| AG-UI protocol server (`POST /agui/{publication_id}`, SSE) | `services/agui_server_endpoint.py`, `docs/agui_integration.md` | shipped, tested |
| AG-UI frontend tools / state / interrupts | `core/agui_runtime.py`, `core/agui_tools.py`, `tasks/ai/_agentctx_p1.py` | shipped, tested |
| Client-chosen thread → lazy isolated conversation | `core/a2a_store.py` `ensure_named_context()` | shipped |
| Public web help bot | `http_bots.web_help_bot` (`POST /api/help`), `pawflow-website/site.js` | shipped |
| Per-conversation sliding TTL | `core/flow_pawflow_api.py` (`_meta_expires_at`) | shipped |
| Inbound MCP server host | `services/mcp_server_endpoint.py` (coupled; Direction C extracts a generic layer first) | shipped |
| JS behavioral test harness | `tests/js/` + Chromium webchat tests | shipped |

Verified gaps (cumulative review line references):

- No TTL in `a2a_store`; stable `context_id` across rotations
  (`core/a2a_store.py:344`); A2A and AG-UI rows share `a2a_contexts`.
- Runs connection-bound (`core/agui_runtime.py:568`); HTTP status set
  before generator validation (`services/agui_server_endpoint.py:60`,
  `core/agui_runtime.py:436`).
- Random `turn_id` (`core/agui_runtime.py:482`); non-idempotent ingress
  (`tasks/ai/agent_streaming.py:400`).
- Any trailing `role:"tool"` result accepted
  (`core/agui_runtime.py:139/221`); provider `tc_id` passed through,
  not globally unique (`core/agui_runtime.py:329`).
- Force-stop / active registries keyed `(conversation, agent)` across
  `tasks/ai/agent_loop.py:105/247`, `tasks/ai/agent_core.py:214`,
  `tasks/ai/agent_streaming.py:184`, relay cancel-all
  `services/_tool_relay_cache_req.py:513`; **the true effect boundary
  is `_execute_tool_calls()` (`tasks/ai/agent_tool_exec.py:28`, dispatch
  at `:357`) with `_inflight` keyed by provider `tc_id`** — the fence
  guard and run-handle indexing must land there (B1-O).
- Declarations read `parameters` (`core/agui_runtime.py:189`);
  `core/agui_tools.py:184` one-call-then-end-turn, ignores annotations.
- Router `fullmatch` (`services/_http_base.py:348`).
- AG-UI `RunAgentInput`: `runId` required, `parentRunId` available, no
  `abort` field.

---

## 3. Direction B — embeddable widget: the PawFlow agent calls page tools

### B0. Architecture and the identities

```
Web application page
  ├─ pawflow-webmcp-bootstrap-<ver>.js  (FIRST script; polyfill-or-fail-closed)
  ├─ page code: await pawflowModelContext.ready; registerTool({...})
  ├─ pawflow-widget.js (may load late)
  │    ├─ catalogue: getTools() + toolchange → per-run snapshot
  │    ├─ acquire-then-SSE via proxy (409s BEFORE the stream opens)
  │    ├─ RUN_FINISHED(batch_token) → claim batch (idempotent, owner
  │    │    token + receipts) → per call: catalogue check → consent
  │    │    (renewing) → begin (CAS) → executeTool → deposit
  │    └─ follow-up run references the server-held batch (parentRunId)
  └─ reverse proxy: exact-path rewrite + key injection (B4)
PawFlow server: ONE SQLite commit domain — contexts, journal, ledger,
  outbox, watermarks, batches/receipts (hashes) — + run state machine
```

| Identity / credential | Scope | Transport & storage |
| --------------------- | ----- | ------------------- |
| Subscriber epoch | SSE tailing only | server-assigned at attach |
| `attach_token` | resume/attach one run | issued in acquire response & `RUN_STARTED`; header; derived (see token scheme) |
| `batch_token` | designate one finished run's batch | issued in `RUN_FINISHED` & terminal snapshot; header; derived |
| Batch `owner_token` | claim/renew/act on a batch | issued at claim; header; derived, bound to `claim_generation` |
| Execution receipt (per call) | begin/deposit one call | issued at claim; header; derived, bound to `claim_generation`; epoch-independent, reload-stable |
| Run fence token | server+relay effect gate | internal, monotonic per context/agent |

**No secret ever travels in a query string** (public proxies log URLs):
query parameters carry only non-secret action discriminators
(`?action=attach|claim_batch|begin|deposit|renew`); tokens/receipts go
in the `X-PawFlow-Exec-Token` header (or POST body). Client-chosen
`runId` is never an addressing key: `attach_token`/`batch_token`
canonicalize `(publication, key, thread, generation, run)` exactly as
receipts do for calls.

**Token scheme (v8.2 — self-addressing, pinned-key, re-issuable)**:
every credential is **deterministically derived**, not
minted-and-stored:

`token = v<K> . <handle> . MAC` where
`MAC = HMAC(server_key_K, handle || usage || canonical_identity ||
credential_generation)` over a canonical, unambiguous encoding.

- **`handle`** is a non-secret opaque id, stored and indexed to the
  credential's canonical identity — `attach`/`begin`/`deposit` resolve
  their target row by handle lookup, never by scanning; the MAC then
  authenticates the caller. Cross-usage replay is rejected (usage is
  MACed).
- **`K` is pinned at credential creation** (`token_key_version` stored
  on the run/batch/claim row): replays and re-issuance always derive
  with the pinned key, so re-emitted tokens are byte-identical across
  key rotations and restarts. New credentials take the current key.
  The keyring is durable, shared by all workers, and retains every key
  still referenced by a live row; a missing referenced key at startup
  is a **fail-closed** error.
- **Generation scoping**: `claim_generation` parameterizes ONLY
  `owner_token` and receipts. `attach_token`, `batch_token` and
  `cancel_token` are derived over their own fixed identity (run/batch)
  — the `batch_token` does NOT change when a claim increments
  `claim_generation`.

The server stores no bearer bytes — only handles, canonical
identities, generations and key versions; audit rows may keep a MAC
hash for correlation.

### B1. Server: the AG-UI turn state machine

**B1-D. One commit domain** — with the two composite transactions named
explicitly:

- **T-freeze**: run success terminal + batch freeze + frontend-
  execution lease creation + `RUN_FINISHED` (carrying `batch_token`) +
  `committed_sequence` advance — one transaction. A crash can never
  publish `RUN_FINISHED` without a claimable batch.
- **T-complete**: final deposit (or batch-deadline timeout) + batch
  completion + frontend lease release + TTL arming — one transaction. A
  crash can never leave a completed batch with a pinned thread.

All other single transitions as before (`TOOL_CALL_*` + pending row;
non-success terminal + abandonment; lease release + TTL re-arm;
sequence advances).

**B1-T. Thread handle & generations** (unchanged from v7): `GET
?thread_id=` creates generation 0 and returns it; every POST presents a
generation; stale/closed generation → `409 thread_rotated` (checked
first); `closed_before_generation` monotonic watermark;
generation-aware `context_id` and `turn_id`.

**B1-A. Acquire — synchronous, before SSE** (unchanged): generation
check → key lookup (replay/attach | `idempotency_conflict` |
`idempotency_expired`) → new-run admission (never rotate under any
lease; `parentRunId` + batch completion required in managed mode;
client-carried `role:"tool"` ignored in managed mode). The acquire
response and `RUN_STARTED` carry the run's `attach_token` and
`cancel_token`.

**B1-G. Retention** (unchanged): terminal-only pruning; per-run quota
with an out-of-quota reserve for the terminal event and SSE close;
replay watermark; `replay_expired` → terminal snapshot (incl.
`batch_token` when a batch is still open); run tombstones +
`closed_before_generation`.

**B1-J. Journal, attach, cancellation**: attach requires
`?action=attach` + the run's `attach_token` (header) — it can never
admit; admission requires a full body through B1-A. Takeover = new
subscriber epoch, gapless replay from `committed_sequence`.
Cancellation: `DELETE` + `X-PawFlow-Cancel-Token` header; hash-stored;
idempotent; journaled.

**B1-O. Run lifecycle, claim/ack, fence — enforced and restart-safe**:
states `reserved → dispatching → accepted → running → terminal |
orphaned`; outbox claim `{owner, deadline}` + CAS reclaim + idempotent
ingress (`accepted` = durably persisted). Fence:
- **Guard at the true effect boundary**: the fence check lives in
  `_execute_tool_calls()` (`tasks/ai/agent_tool_exec.py:28`),
  immediately before `execute_prepared/execute` (`:357`); `_inflight`
  entries are keyed `(run_handle, call_id)` — never provider `tc_id`
  alone.
- **Relay high-water, restart-safe**: the relay keeps a monotonic
  high-water fence token per context/agent and atomically refuses lower
  tokens. On every relay (re)connection the server resynchronizes the
  current high-waters and requires acknowledgement BEFORE any effect
  request is allowed through; the resync is idempotent. A relay restart
  therefore cannot reopen the fence.
- Run-handle granularity across ALL `(conversation, agent)` registries
  (`agent_loop.py:105/247`, `agent_core.py:214`,
  `agent_streaming.py:184`, `_tool_relay_cache_req.py:513`): zombie and
  successor coexist; force-stop targets one handle. Heartbeat expiry →
  `orphaned` → journaled `run_lost`; never relaunched.

**B1-P. Non-success terminals** (unchanged): pending emitted calls →
`abandoned` atomically; deposits for them → `call_abandoned`.

**B1-X. Managed frontend execution — closed protocol**. Managed vs
classic is a **publication-level setting, announced in the descriptor;
a request can never select the mode** (no bypass of receipts/deposits).
Classic mode keeps the v5 rules for plain AG-UI clients.

1. **T-freeze** publishes `RUN_FINISHED` with the `batch_token`.
2. **Idempotent batch claim (generation-bound)**: the widget persists a
   self-generated `batch_claim_id` BEFORE requesting
   `?action=claim_batch` (+ `batch_token` header, `batch_claim_id` in
   body). Each successful claim increments the batch's monotonic
   **`claim_generation`**; all credentials of that claim (owner token,
   receipts) are derived over it. CAS
   `unclaimed → reserved_pre_effect(owner, generation, deadline)`:
   - same `batch_claim_id` retried → the SAME `owner_token` and the
     SAME receipts are re-derived and returned (lost responses safe);
   - a different `batch_claim_id` while the reservation lives →
     `409 batch_already_claimed`;
   - `reserved_pre_effect` expiry (no `begin` yet) → back to
     `unclaimed`, and that expiry ITSELF invalidates the generation:
     every credential of the expired claim answers `409 claim_expired`
     from then on (atomic revocation — an old widget can never win a
     `begin` after a re-claim). An expired `batch_claim_id` is likewise
     terminal (`409 claim_expired`), never implicitly resurrected — the
     re-claimer must use a fresh id.
   - **First `begin` commits the claim**:
     `reserved_pre_effect → execution_committed` (single CAS; a `begin`
     racing the expiry has exactly one winner). In
     `execution_committed` the owner is irrevocable — the batch can
     never return to `unclaimed` — until terminalization or the
     absolute deadline.
3. **Two deadlines, not three (v8.2)**:
   - **`claim_lease_deadline`** — exists ONLY in `reserved_pre_effect`;
     `?action=renew` + `owner_token` (current `claim_generation`) is an
     idempotent CAS on it, and consent dialogs renew while that state
     lasts. After `execution_committed` the claim lease is ignored:
     `renew` returns an idempotent `200` no-op (deadline unchanged) so
     a client renewing during later consents is harmless.
   - **`absolute_batch_deadline`** — set at T-freeze, NEVER extended,
     active for the whole batch life. Calls not yet begun remain
     executable until it fires. When it fires: never-begun calls →
     `abandoned`; `executing` calls → `indeterminate`; then
     **T-complete**.
   - There is NO per-call execution deadline.
4. **Per-call catalogue identity (checked before begin)**: each receipt
   records, at claim time, the call's catalogue identity — host stable
   id + exact `catalogueVersion` value, or the generic registration
   snapshot reference. Before `begin`:
   - host stable id + matching live `catalogueVersion` → allowed;
   - generic snapshot no longer alive (reload, or any
     `toolchange`/drift since claim) → the call terminalizes as
     `catalogue_unverifiable` / `catalogue_changed` — the NEW
     registration is never executed;
   - a result already obtained before a reload remains depositable
     with its receipt (deposit needs no live catalogue).
5. **State matrix (closed)**: per-call
   `pending/unclaimed → reserved(deadline) → executing →
   result | abandoned | indeterminate`, with deposits constrained by
   state:
   - from `reserved`, only no-effect outcomes are accepted: `denied`,
     `ledger_unavailable`, `cancelled_before_begin`,
     `catalogue_unverifiable`, `catalogue_changed`;
   - `begin` (`?action=begin` + receipt header) is an idempotent CAS
     `reserved → executing`; the widget calls `executeTool` ONLY after
     the begin confirmation arrives;
   - from `executing`: `result`, `null_navigation`, `error`,
     `indeterminate`;
   - duplicate deposit with the identical payload → the recorded answer
     is replayed; a different payload → `409 receipt_conflict`;
   - after `begin`, only the `absolute_batch_deadline` can terminalize
     an `executing` call (→ `indeterminate`; the batch stays
     `execution_committed`) — no other timer applies (item 3);
   - **race rules (v8.1)**: `deposit` racing the deadline → the first
     transaction to commit wins (a deposit committed first stands; a
     deadline committed first makes the late deposit answer with the
     recorded terminal state); a deposit arriving AFTER **T-complete**
     never mutates the consumable batch — the server replays the
     recorded terminal outcome and keeps an audit trace only.
6. When every call is terminal, **T-complete** runs; the follow-up run
   references the server-held batch via `parentRunId`.

**B1-L. Frontend-execution lease** (unchanged): batch not complete →
TTL unarmed, sweep skipped, generation closure waits; bounded by the
absolute batch deadline.

**B1.4-6. TTL** (unchanged): `_meta_expires_at` armed at batch
completion (or run end without batch); scoped opportunistic sweep;
A2A/shared/TTL=0 structurally excluded; `thread_ttl_seconds` in dialog
+ descriptor.

Tests (delta v8): attach/claim by token with mismatched thread/run →
rejected; secrets absent from URLs (recipe + endpoint contract test);
claim idempotence (same `batch_claim_id` → same receipts after lost
response; different id → 409); renew CAS + absolute deadline; deposit
matrix per state (reserved fake-success rejected; executing accepts
result; duplicate replays; conflict 409); begin-confirmation-before-
executeTool ordering; catalogue check before begin
(unverifiable/changed paths; pre-reload result still depositable);
T-freeze and T-complete crash tests (no RUN_FINISHED without claimable
batch; no completed batch with pinned thread); relay restart → fence
resync acknowledged before effects, old-token request refused;
`_inflight` keyed `(run_handle, call_id)`; managed mode not
request-selectable; **v8.1 delta**: token re-derivation after lost
response AND after server restart returns identical bytes; no bearer
bytes anywhere in journal/tables; `begin` vs `reserved_pre_effect`
expiry → single CAS winner; old receipt after re-claim →
`claim_expired`; expired `batch_claim_id` → `claim_expired` (no
resurrection); deposit vs deadline → first commit wins; deposit after
T-complete → terminal outcome replayed, batch unchanged; **v8.2
delta**: handle-lookup resolution for attach/begin/deposit (no scan);
cross-usage token replay rejected; rotation K→K+1→K+2 with
byte-identical re-issuance of a live credential (pinned key);
`batch_token` stable across multiple `claim_generation`s; missing
referenced key at restart → fail closed; `renew` after
`execution_committed` → idempotent no-op while a second call's consent
is open; claim vs absolute deadline and begin vs absolute deadline →
single winner (a begin confirmation received by the widget after the
absolute budget is discarded — `executeTool` is never called); plus all
v7 suites (generation bootstrap,
precedence, watermarks, quotas, terminal snapshot, abandonment,
frontend lease, A2A survival, TTL=0, classic-mode non-regression).

### B2. Client SDK: `pawflow-widget.js`

As v7, with the v8 protocol: persists `batch_claim_id` write-ahead;
holds `owner_token`/receipts (in-memory + `sessionStorage`); renews
during consent; begin-then-execute strictly ordered; deposits with
receipt headers; records catalogue identity per call and terminalizes
`catalogue_unverifiable`/`catalogue_changed` instead of executing a
changed registration; deposits pre-reload results after reload;
`ledger_unavailable` (storage failure) → no-effect deposit, never
execution; grants unchanged (session = live snapshot invalidated on
toolchange; persistent = host stable id + exact `catalogueVersion`);
consent default-confirm, hints decorate only; attach-based resume;
terminal-snapshot rendering; `sessionStorage` thread + generation;
`thread_rotated` handling.

### B3. Declarative API

Unchanged: via `getTools()` on capable browsers; no polyfill parser in
v1.

### B4. Deployment recipe

Unchanged matcher; query strings carry only action discriminators;
all tokens in headers (documented header allowlist for the proxy);
cancel token in `X-PawFlow-Cancel-Token`:

```
@assistant path /api/assistant
handle @assistant {
  rewrite * /agui/<PUBLICATION_ID>
  reverse_proxy 127.0.0.1:<port> {
    header_up Authorization "Bearer {env.PAWFLOW_WIDGET_KEY}"
    flush_interval -1   # keep SSE unbuffered
  }
}
```

Documented: HTTPS/secure context, `Origin-Agent-Cluster` notes,
anonymous rate limiting, diagnostic checklist. Distribution: versioned
immutable URLs, CORS for script loading, SRI hashes, `latest` for dev.

### B5. Optional: migrate the site help bot onto the widget

Unchanged. Decide at phase 4.

### B6. Files touched (Direction B)

- `core/a2a_store.py` — commit domain incl. T-freeze/T-complete,
  thread handles + generations + watermark, journal + quotas +
  replay watermark, outbox claim/ack, run lifecycle + abandonment,
  batch/owner/receipt state machine (v8.2 tokens: handles, pinned key
  versions, credential generations), frontend lease,
  agent lease/fencing/heartbeat, sweep.
- `services/agui_server_endpoint.py` — synchronous acquire,
  `?thread_id=` bootstrap, action endpoints
  (attach/claim_batch/begin/deposit/renew) with header tokens, DELETE
  + header cancel token, subscribers/takeover, sweep trigger,
  descriptor (mode, generation, capabilities, TTL).
- `core/agui_runtime.py` — journaled emission, generation-aware
  `turn_id`, managed batch feeding, classic-mode validation,
  `inputSchema`, untrusted-result wrapping.
- `core/agent_runtime_api.py` + `tasks/ai/agent_streaming.py` —
  idempotent ingress, ack-after-persist, dedupe answers; run-handle
  registries (also `tasks/ai/agent_core.py`, `tasks/ai/agent_loop.py`).
- `tasks/ai/agent_tool_exec.py` — fence guard at `_execute_tool_calls`
  before dispatch; `_inflight` keyed `(run_handle, call_id)`.
- `services/_tool_relay_cache_req.py` — restart-safe high-water fence
  (resync + ack) + handle-scoped cancellation.
- `core/agui_tools.py` — multi-call batching, annotations metadata,
  trust contract.
- Publish dialog (managed-mode + TTL options);
  `pawflow-webmcp-bootstrap.js` + `pawflow-widget.js`;
  `docs/agui_integration.md` + `docs/web_widget.md`.
- Tests: `tests/test_agui_thread_ttl.py`,
  `tests/test_agui_turn_ledger.py`, `tests/test_agui_managed_batch.py`
  (all B1 bullets incl. the v8 delta),
  `tests/js/pawflow_widget_spec.js`, Chromium end-to-end (native +
  polyfill; two-tab claim idempotence; reload deposit).

---

## 4. Direction A — pawflow.allcolor.org as a WebMCP tool provider

Unchanged: `site.js` registers on `document.modelContext`
(feature-detected, no polyfill until B5, rejections handled, DOM
ready): `ask_pawflow` (existing `POST /api/help` contract; annotations
`{readOnlyHint: true, untrustedContentHint: true}`; structured errors)
and `list_site_pages` (nav-derived, tested against the real nav).
Files: `site.js` + `?v=a34` on all 11 pages; `docs/website_webmcp.md`;
`tests/test_website_webmcp.py` + `tests/js/` behavioral spec. Ops:
origin-trial signup + token (owner action).

## 5. Direction C — MCP-client bridge (opt-in, off by default)

Unchanged from v7 (bridge calls follow the SAME managed batch/receipt
discipline and consent gate; generic JSON-RPC layer extracted first;
pairing with hashed secret; sealed resume token / HttpOnly cookie;
timeouts, cancellation, caps; MCP `content` normalization;
`tools/list_changed`; lifecycle; tests incl. published-MCP
non-regression).

## 6. Security & trust model

- Everything client-declared is untrusted data (wrapped, size-capped),
  regardless of hints.
- All credentials (`attach_token`, `batch_token`, `owner_token`,
  receipts, cancel tokens) are opaque, canonical-identity-bearing,
  transported in headers/body only, and **derived, never stored** (B0
  v8.2 token scheme: `v<K>.<handle>.<MAC>`, pinned key version,
  usage + canonical identity + credential generation under the MAC);
  journals/tables hold handles and identities, no bearer bytes. After
  handle lookup the server trusts ONLY the row's pinned key version
  (the token's `K` is untrusted input); unknown handle, bad MAC and
  wrong usage return the same external error. Keys are refcounted:
  "live row" includes any still-replayable tombstone or terminal
  snapshot; a key is deletable only at refcount zero.
- Results deposited exactly-once under a state matrix that forbids
  effect-claiming outcomes from pre-effect states; effects at-most-once
  across all widget instances (`begin` boundary); `indeterminate` only
  after `begin`.
- Catalogue identity re-verified before every `begin`; a changed or
  unverifiable registration is never executed under an old consent.
- Fence enforced at the true effect boundary and at the relay,
  restart-safe (resync + ack).
- Consent default-confirm; auto-execution only via execution-identity-
  bound grants; managed mode is publication-fixed (no request-level
  bypass).
- TTL machinery: only `agui_` contexts, never under an agent or
  frontend-execution lease.

## 7. Phasing

| Phase | Content | Depends on |
| ----- | ------- | ---------- |
| 1 | B1 commit domain + state machine (thread handles, journal, outbox, batches/receipts, leases, restart-safe fence) + idempotent ingress + tests | — |
| 2 | Bootstrap + B2 widget SDK + B4 recipe/distribution + docs + tests | 1 |
| 3 | Direction A: site tools + doc + tests (+ origin-trial signup) | — |
| 4 | B5 help-bot migration (decision point) | 2 |
| 5 | Direction C: JSON-RPC extraction + bridge + opt-in pairing UI + tests | 2 |
| 6 | B3 declarative polyfill (if needed); §8 consumer (separate plan) | 2, spec maturity |

Phase 3 is independent. Every phase ships tests + docs in the same
change (`CLAUDE.md`). No commits without explicit user confirmation.

## 8. Outlook: PawFlow as a WebMCP consumer (separate plan)

Where the relay-driven Chromium exposes `document.modelContext`, call
`getTools()`/`executeTool()` via CDP; where absent, inject the pinned-
profile polyfill (`Page.addScriptToEvaluateOnNewDocument`). New
`browser` actions: `list_page_tools`, `call_page_tool`.

## 9. Open questions (implementation-time, none load-bearing)

1. Tunable defaults: sweep interval, journal retention floor, per-run
   quota + terminal reserve size, batch reservation/absolute deadlines,
   renew interval, tombstone cap (generation-close threshold).
2. Declarative-API handling in the polyfill (wait for spec).
3. Origin-trial token injection: Caddy header vs meta tag (ops).
4. Direction C channel fallback and pairing-store placement.
5. Managed vs classic mode selection UX in the publish dialog.
6. Profile re-validation cadence while upstream IDL and Chrome diverge
   (input type; result nullability).
