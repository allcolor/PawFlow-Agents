# Changelog

All notable changes to PawFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0-beta.60] — 2026-07-30

### Fixed

- Every CLI now answers the same question the same way: no process running ->
  we launch -> cold start -> full context; a process is running -> delta.
  beta.59 gave codex and gemini the refusal that enforces it and left the
  others out, so claude-code interactive and antigravity could still hand a
  bare delta to a process that knew nothing. The rule is asked at each
  provider's own launch site, because that is where "we are about to start a
  process" is known, and the pools take a `before_launch` callback rather
  than deciding themselves: they manage containers, not context policy, and
  they call it only when they are really going to launch, never on a reuse.

- claude-code (`-p`) had a third path, and it was the last provider that did:
  no live process but a persisted session id, so it launched with `--resume`
  and a delta and let CC replay its own jsonl. Whether that file still meant
  anything was decided by re-deriving CC's project-key algorithm and trusting
  a transcript only CC can validate; when CC declined it and opened a fresh
  session instead, the `SESSION MISMATCH` check merely logged it while the
  agent lost its history, and the new empty session id was then persisted as
  though it were sound, so every later turn resumed a session holding
  nothing. There is no resume-from-disk path any more, and the context phase
  asks its live registry instead of the stored id -- the same alignment codex
  and gemini got in beta.58.

- Refusing a launch no longer strands the live session's turn lock. Both
  stream providers hold it when they ask, and their own try/finally starts
  later, so the lock was acquired and never released. It is an RLock and the
  retry runs on the same thread, which hid it: the second acquisition
  succeeded, one finally released one level, and the next turn on that
  session -- on another thread -- waited forever. The refusal now takes a
  `release` callback, and codex asks before minting its MCP token rather than
  after.

- The "this context is a delta" marker no longer lives on the shared service
  client. That client comes from the service registry and the loop only
  clones it later, so a second conversation preparing a cold context on the
  same service cleared the marker before the first one's clone was made --
  and the refusal became a no-op exactly when two conversations shared a
  service. It travels in the turn's context and is stamped on the turn's own
  clone.

- A rebuilt context is now adopted whole. The restart replaced the client,
  the messages and the ctx, but left the loop's tool registry, tool
  definitions, model and cancel registration pointing at the context it had
  just abandoned -- so a turn could execute tools through a registry the new
  context never configured, and force-stop reached the wrong clone. The
  message list is replaced in place, since ctx, the emitter and every closure
  built at setup hold that exact object.

- The cold restart no longer spends an iteration. It was counted before the
  provider was called and never given back, so a turn with
  `max_iterations=1` ended having never called the model -- and CLI providers
  deliberately synthesize no empty answer.

- The context gauge is reset on the pass that launches. The reset sat inside
  the block gated on `not force_cold`, which is exactly the pass that knows
  the old session is gone, so the dead session's percentage survived the
  restart.

- An ephemeral call (compact, memory extraction) is exempt from the refusal
  everywhere. It builds its own full text, but it clones a client that may
  carry the marker, and bouncing it would restart a compaction as if it were
  the agent's own turn.

## [1.0.0-beta.59] — 2026-07-30

### Fixed

- Looking a live CLI session up now restarts its idle clock. The idle TTL
  exists to reap containers nobody asks for, and `last_used` is the only
  evidence the sweeper has — but the lookups that hand a container to a caller
  never refreshed it. A session sitting at the end of its TTL could therefore
  be found alive by the context phase and swept moments later, before the
  provider claimed it. The CCI and Antigravity pools already refreshed on
  lookup; `CodexLiveRegistry`, `GeminiLiveRegistry` and `LiveSessionRegistry`
  now do too. The TTL itself is unchanged: a session nobody asks for is still
  reaped on schedule.

- Launching a CLI process is a cold start, and a cold start now always gets the
  full context. There are two cases and no third one: no process running, so we
  launch and send everything; or a process is running, so we send the delta.
  The context phase decides which applies, but the provider is what actually
  launches, and only it can find the process gone — crashed, or its container
  stopped — after the context was already built as a delta. It used to launch
  anyway, handing a bare question to a process that knew nothing: no
  transcript, no persona, no skills, no tool configuration. It now refuses
  (`ColdStartRequired`), and the turn is rebuilt through the ordinary cold path
  and run again. Nothing has reached the model at that point, so the restart
  costs no tokens.

- The context phase now asks the live-session question with each provider's own
  inputs. The shared helper's pool fallback was unconditional, but the two
  providers disagree on it: codex takes any compatible session, gemini only
  when the stored slot is missing — a concrete index that misses means the slot
  changed on purpose, and the old-slot container would resurrect the previous
  account's session. The policy is now the caller's. Both providers also read
  the stored pool index only while they still hold a session id, so the context
  phase does the same; reading it unconditionally would have passed a concrete
  index where the provider passes -1, and the two would have parted company
  again on the fallback.

### Removed

- The partial cold-context reconstruction (`_arm_cold_context_rebuild`,
  `_cli_cold_context`) and its clone whitelist entry. It recovered the
  transcript only, while its docstring promised persona, skills and tool
  configuration as well. Rebuilding the context through the path that already
  knows how to build it removes both the half-measure and the promise.

## [1.0.0-beta.58] — 2026-07-30

### Fixed

- The context phase and the CLI providers now answer "does this conversation
  still have a live session?" the same way. The providers ask their live
  registry; the context phase asked whether a session id was still persisted.
  Once anything cleared that id — a stale-thread reset, a compaction
  invalidation, a pool index that no longer matched — the two disagreed
  permanently, because nothing on the codex reuse path wrote the id back: every
  turn announced a cold start, loaded and compacted the whole transcript, then
  watched the provider find the very same process alive, resume it and discard
  all of it. Both branches now go through one helper
  (`core.cli_live_sessions.find_live_cli_session`), and codex re-persists a
  thread id recovered from a live session.

- A CLI turn no longer loses its context when the session it was told to reuse
  disappears first. The context phase empties the message list whenever it
  finds a live codex or gemini session, because a resume only needs the delta —
  but that check reserves nothing, and the idle sweeper, a cleanup or a crashed
  process can take the session away before the provider acquires its turn lock.
  The provider then correctly went cold with a list that no longer described
  the conversation: no transcript, no persona, no skills, no tool config. The
  context phase now leaves a one-shot callback the provider invokes on its cold
  path to reload the real context; gemini additionally stops loading its stored
  session in that case, since replaying it on top of a rebuilt context would
  send the transcript twice and leave the gauge on the dead session's numbers.
  Nothing changes on the happy path, where the session really is still there.
  The callback is carried across `clone_for_call`: the agent loop clones the
  client after the context phase arms it, so without that the recovery was a
  no-op in production while every same-instance test still passed. The rebuilt
  context also carries the provider system prompt a resumed turn deliberately
  omits, and is concatenated with the turn's delta instead of replacing it —
  replacing it dropped the user's actual question.

- The Gemini and Antigravity VNC logins start their container again. The
  beta.56 label work added the `pawflow_container_labels` call to that
  background thread but not its import — the identical imports in the Claude
  and Codex branches are local to *their* nested functions — so both logins
  died on a `NameError` reported as a generic "Login failed", before any
  `docker run`. The test that shipped with the label work only checked that the
  name appeared somewhere in the file, which a grep cannot distinguish from a
  name that is actually bound; the new test runs the thread.
- Stopping one PawFlow server no longer removes another one's containers. The
  shutdown reaper's first pass filters on this server's label and is exact, but
  the legacy name pass that follows matches prefixes carrying no server id
  (`pf-cc-pool-`, `pawflow-relay-srv-`, the logins…), which on a shared Docker
  daemon also name a second instance's containers — and those survived pass 1
  precisely because their label was not ours. The label now decides in both
  directions: a container owned by another server is left alone, and only an
  unlabelled one, which can only predate the label, is reaped by name. The
  decision moved into `core.docker_utils.legacy_reap_ids` so it can be tested
  at all. `pawflow-agy-login-` was also missing from the list, so an old
  Antigravity login could survive instead.
- A FileStore write no longer outlives the conversation it belongs to. Moving
  the bytes outside the global lock (beta.55) left a window: `delete_by`
  snapshots the entries it can see, and a store that reserved its path earlier
  is not among them, so it registered afterwards and resurrected a file for a
  deleted conversation — or crashed with `FileNotFoundError` when the wipe took
  the bucket directory with it. A per-conversation wipe counter, read before
  the reservation and rechecked under the lock, now discards such a write and
  its bytes; `store`/`store_file` return `""` in that case.
  The wipe is announced in the same locked block that takes its snapshot and
  stays announced until it finishes, so a write cannot slip between the two —
  reserving after the snapshot and registering before the counter moved — and
  come back with a valid id for a conversation that no longer exists.

- The interactive proxies no longer grow their capture log without bound. The
  shell that starts each one appends its stderr to a file, and for
  claude-code-interactive that file sits on the container's 512 MB `/tmp`
  tmpfs, shared with every tool call — so a long-lived container did not merely
  waste space, it eventually broke the tool calls that needed to write there. A
  daemon thread now checks the file at startup and every 60 s and truncates it
  past 20 MB, which is safe under the live writer because the redirect opened it
  with `O_APPEND`. The Antigravity observer gets the same guard on its stderr
  capture file; its `observer-<id>.jsonl` is deliberately exempt, because the
  pool reads that one back for the `proxy_start` event to decide a session is
  still alive.

- The three webchat invariants that only a browser covered now run without one.
  When beta.57 made the browser tests skip on a runner where headless Chromium
  renders nothing, the turn controller kept its Node twin but the
  `conversations.js` integrations kept nothing: pagination in backend cursor
  units, per-conversation runtime turns across an A/B/A switch, and eviction
  forgetting every id it removed were unguarded wherever the browser was
  unusable — which includes CI. `tests/js/conversations_spec.js` drives the
  real sources against the DOM stub with the same fixtures and assertions, and
  `tests/test_conversations_js.py` puts it on the gate. Each of the three was
  confirmed to fail against a deliberately broken guard. The browser tests stay
  as the real-browser run, and their skip message no longer claims a hole that
  is now closed.

## [1.0.0-beta.57] — 2026-07-30

### Fixed

- A browser that cannot run says so instead of failing the build. Headless
  Chromium never produces a DOM for a local file on the CI runners, and neither
  extra flags nor a longer timeout changed that — it hung at 20 s, then at
  120 s. The line is now drawn at the DOM: no DOM at all is an environment
  verdict and skips, a DOM carrying an error is a behaviour verdict and still
  fails. The verdict is remembered, so the file costs one 25 s wait instead of
  four. The invariants that test asserted also run under Node in
  `tests/js/turn_view_spec.js`, which needs no browser.

## [1.0.0-beta.56] — 2026-07-30

### Security

- An agent turn submitted through the runtime API keeps the principal that API
  stamped: `source_attributes` can no longer overwrite `http.auth.principal`,
  the channel or the request id. A flow forwarding its own visitor payload as
  provenance could otherwise choose the identity every conversation ACL
  downstream authorizes against.

### Changed

- A finished sub-agent gives its CLI container back. A flash agent, delegate,
  task or plan step kept its warm container until the idle sweeper reaped it
  one service timeout later (30 min), while the pool is 1:1 and capped at 50
  and reuse is keyed on identity, never availability — so a fan-out of
  differently named flash agents consumed slots nobody could borrow. Only an
  explicitly persistent delegate keeps its session.
- Everything PawFlow spawns now carries an `org.pawflow.server-id` label and
  the shutdown reap is driven by it instead of a hand-maintained list of name
  prefixes. Interactive Claude Code and the Antigravity observer containers
  were never in that list and survived `docker stop`. The reap also runs before
  the ConversationWriter drain: `docker stop` SIGKILLs after 10 s by default,
  so a reap queued behind a 20 s drain never ran at all.
- Starting a CLI instance is a cold start for codex and gemini too. Codex
  accepted a rollout jsonl on disk as proof of session and gemini checked no
  liveness at all, so a relaunch after the 1800 s idle sweep resumed the
  provider thread instead of reloading `initial_context.md` — for as many
  tokens as our own, possibly compacted, context, and with a gauge left on the
  dead session's numbers. Both now require a live process, like CCI and
  antigravity already did.
- Simplified-view turn boundaries are positional again. beta.55 made a durable
  `turn_id` route rows to the block it names, so answering a first message while
  the reader had already sent a second inserted that answer *above* the newer
  message. An id names a turn; it never selects one. The rule, why correlation
  loses, and the fact that it has now been reverted once are stated in
  `turn_view.js` and `docs/SIMPLIFIED_LIVE_CHAT_VIEW_PLAN.md`. The runtime
  `active_turns` snapshot stays: it reports liveness, never placement.

### Fixed

- The installer's rollback works again: a mangled line continuation made it
  call `docker rename` with a third operand, so a failed replacement removed
  the new container and left the previous server stopped and renamed instead
  of restoring it. The fake docker in the tests now checks argument arity,
  which is what let this ship green.
- Simplified view draws sub-agent boxes again. A classic-mode
  `group_delegate_messages: false` followed the user into simplified, where
  the delegate box is the only sub-agent renderer and its toggle is hidden.
- A turn that was live when the page loaded stops being treated as live once
  it ends: its runtime entry is retired on both terminal paths, so the block
  closes and the reader gets its last message back at the next reconciliation.
- A conversation handed over is no longer blocked by a FileStore row whose
  bytes are already gone; the intact attachments move and the stale row is
  logged. Storing a file no longer holds the global FileStore lock across the
  disk write, while still following an owner handoff that lands mid-write.
- The CLI context gauge passes through zero on every cold start, for every
  provider. The two hand-built `message_meta` payloads dropped
  `cli_context_state`, and the browser discards a zero that does not carry it,
  so a turn could measure an empty window and be unable to say so.
- The between-rounds gauge refresh covers all four CLI providers instead of
  claude-code-interactive alone; codex, gemini and antigravity no longer freeze
  their gauge until the agent stops.
- The browser-level webchat tests run with a private Chromium profile and no
  first-run/network work, so they no longer hang the CI runner.

## [1.0.0-beta.55] — 2026-07-30

### Security

- Conversation-scoped PFP dev load/unload now applies its runtime default scope
  during authorization too, so omitting `scope` cannot turn a conversation
  operation into an ungated user-scoped one.
- Tool process-control actions now fail closed on unknown ids and authorize the
  conversation resolved from the effective approval request or tool call. Direct
  providers reserve ownership before worker submission, closing the interval
  before background registration, and command-bearing tool names are normalized
  case-insensitively before dangerous-command policy is evaluated.
- Conversation FTS now purges derived plaintext when the source listing or a
  transcript is unreadable. Every transcript rewrite bumps its generation under
  the conversation lock, so deleted or redacted text cannot survive behind stable
  ids/timestamps or a transient read failure.

### Fixed

- Simplified webchat pagination now advances a backend transcript cursor instead
  of deriving one from DOM nodes that tabs may reparent. Active-turn snapshots
  hydrate before replay suppression, conversation switches preserve live
  animation/state, durable `turn_id` routes concurrent turns, and legacy
  unstamped transcripts retain positional boundaries.
- Conversation owner handoff now publishes the new owner only after the
  conversation directory, scoped resources, and FileStore bytes/index have all
  moved. Stores serialize with the transfer, failures roll back, and reverse-index
  rebuilds cannot overwrite an invite or role update that lands during a sweep.
- Server updates now reject out-of-root symlink destinations, propagate requested
  git/Compose pull failures, and restore the previous container image when the new
  server fails startup or health checks.
- Managed relay recovery uses the live relay WebSocket as health, replacing a
  container that is running but disconnected instead of waiting forever for a
  wedged client.
- CCI consumer takeover now orders epoch changes, queue delivery, pushback,
  overflow, and wakeups under one condition, preserving the replacement turn's
  first event and stream order.
- CLI context gauges keep their integer bootstrap baseline across `LLMClient`
  clones and preserve `cli_context_state` through both `message_meta` and `done`,
  including authoritative cold zero after compaction.

## [1.0.0-beta.54] — 2026-07-30

### Security

- **A read collaborator could drive the tools of a conversation they could
  only watch.** The SSE stream hands approval requests to anyone with read
  access, and `tools_exec` gated nothing: the dialog could be answered —
  including with `always_allow`, which persists — and the tools that ran next
  killed or detached. The cluster now carries a role table like the three
  already gated, and driving is a write. A tc_id and an approval request_id
  name their target on their own, so the conversation that actually owns them
  is resolved and the check lands there, rather than on the `conversation_id`
  sent alongside.

- **Any authenticated user who knew a conversation id owned its resources.**
  A conversation-scoped agent, skill, prompt, MCP or task is filed under the
  conversation's owner, and `ResourceStore` resolves that owner from the id
  alone — deliberately, so a collaborator's turn finds them. Nothing asked
  whether the requester was entitled to it: the whole `agent_resource` cluster
  took a `conversation_id` straight from the request body and could list, read,
  overwrite or delete them, or point the conversation at another agent. All
  fifty-odd actions are now classified, with a completeness test that fails on
  the next one added without a row.

- **`monitor` bypassed content-sensitive approval.** It builds a shell script
  and hands it to `BashHandler` in-process, so the gate never saw a `bash`
  call: one approval of a harmless monitor was persisted and every later
  command rode in on it — a destructive one, or `local=true` on the host —
  with no second prompt. The gate now judges `monitor` on its command, as it
  does `bash` and `execute_script`.

### Fixed

- **A CCI handoff could swallow the first event of the new turn.** The
  consumer epoch was checked on the way into `wait_event` and never after the
  blocking get, so a coordinator already parked there when a newer consumer
  claimed the stream was still the one the queue woke — and dropped the event
  on its way out, truncating text or severing a tool call from its arguments.
  The event is handed back and the new owner takes it before touching the
  queue.

- **A complete Azure endpoint URL lost its `api-version`.** The request line
  was rebuilt from the path alone, so an operator who pasted the full target
  from the portal had the mandatory version — which lives in the query —
  dropped from every request, streaming and not, and Azure rejected them all.

- **A conversation handed to a collaborator arrived without its resources or
  its files.** When a departed owner's conversation moves, only the
  conversation directory used to move: its own agents, skills, prompts, MCPs
  and tasks are filed under the owner, and so are its attachments, both
  resolved through whoever owns it now. Both follow it now. The move is also a
  compare-and-swap: two collaborators who had each resolved the departed owner
  before either took the lock moved it twice, the second taking it from the
  first, who carried on writing against a directory the conversation had left.

- **A slow passive recall surfaced turns later, on another subject.** A turn
  arriving while one was in flight was skipped — by design — but the slow
  answer still landed and was served to some later turn asking about something
  else. A recall now publishes only if it is still the newest one asked for.

- **A valid tool result containing the words "unknown tool" re-ran the tool.**
  The MCP bridge read the bare substring as a routing miss, threw the result
  away and called the lowercased name — running the side effects a second
  time. It now matches the routing error itself.

- **A failed `docker compose pull` was reported as a successful update.**
  `pull --ignore-buildable || pull || true` turned an unreachable registry, an
  expired login or a rate limit into a success, and `up` then cleanly
  restarted the image already on the host. The flag is probed instead, so
  where compose can tell "nothing to pull" from "pull failed", a failure stops
  the update; the legacy path still tolerates it but says so.

- **A failed artifact refresh destroyed part of the installation it was
  replacing.** Each artifact was deleted just before its replacement was
  copied in, so one failed `docker cp` left that artifact simply gone. Every
  artifact is now copied into a staging directory first and swapped in only
  once they are all there.

## [1.0.0-beta.53] — 2026-07-30

### Fixed

- Loading an older history page in simplified view no longer completes the
  newest live block or leaves a historical partial turn ticking forever.
- Persisted delegate traces now reload in Tool calls, and simplified view always
  renders delegate boxes even when classic delegate grouping was disabled.

## [1.0.0-beta.52] — 2026-07-29

### Fixed

- **The simplified view collapsed to a flat transcript on a long turn.** Top
  level holds a user row, that turn's block, and the block's last message —
  nothing else. The view enforced that as rows arrived, and only then: a block
  was built from a user row and died with it. On a conversation where the agent
  works for hundreds of rows without being spoken to, the 50-row history window
  holds no user row at all, so no turn ever opened and every thought, tool call
  and message rendered inline with no block anywhere — and each reload
  reproduced it exactly. Activity with nothing above it now opens a turn of its
  own, a turn outlives the row it was anchored on, and `turnViewReconcile`
  enforces the rule against the DOM after every history render: a stray row is
  filed into the turn it falls in, and a replayed turn is given its last
  message under its block instead of buried inside it.

- **A delegate left nothing on screen in the simplified view.** Delegate
  grouping had been filed with the classic-only view options and forced off,
  and it is the only thing that draws a sub-agent at all: the box was never
  built, and a stored trace — whose content is empty by construction — rendered
  as a blank row. A sub-agent could run, work and return with nothing visible
  but its result message. The box is activity, so it is drawn again and the
  turn view files it with the tool calls, inside the block.

- **A delegate result was previewed by its opening, not its conclusion.** What
  lands in the caller's context and in the transcript is a preview of the
  sub-agent's answer, cut from the head — so it showed the agent announcing
  what it was about to do and stopped before anything it found. On a provider
  whose turn is several messages long the conclusion was never in it. The
  preview is now the tail, starting on a line boundary when one sits near the
  cut, and the full text stays in its FileStore copy.

- **The codex app-server glued its assistant messages together.** Each
  completed message was one entry of `final_text_parts`, joined with nothing:
  two messages met as `as requested.I'm scoping`, and a turn read as one
  run-on paragraph with no way to tell where its answer started. They are
  joined with a blank line; the delta fallback, whose entries are fragments of
  a single message, still is not.

## [1.0.0-beta.51] — 2026-07-29

### Changed

- **A new conversation opens in the simplified view.** `chat.view_mode` now
  falls back to `simplified` instead of `classic`, so a conversation nobody
  configured gets the live turn view; an explicit choice at any scope of the
  cascade still wins, and the View menu switches back per conversation.
- **A new conversation starts in `auto` permission mode.** The mode is written
  once, at creation, by the shared creation contract — web chat, Telegram and
  the flow API alike — rather than becoming the fallback every reader applies.
  Conversations that already exist keep the mode they have been running under.
  On a deployment where agents touch production systems or untrusted input, set
  the conversation back to `default` or `read_only` before giving it work.

### Documentation

- The webchat view reference described correlated turn grouping and a classic
  default, both of which stopped being true in beta.47. It now describes the
  positional boundaries, the last-message spot, the replayed-history exemption,
  the header timer and the glyph rain, and names simplified as the default.
- The in-app update is documented as it now behaves: `/health` carries the
  running version and a per-process id, the panel waits for a *different*
  process before reloading, and after ten minutes it says which of the two
  failures happened. The `chown` handover and its incomplete-uid/gid fallback
  are documented with it.
- The security model states the `auto` creation default and when to move off it;
  README covers both views, the creation defaults, and the browser update path;
  the website carries an update how-to, a view how-to, and FAQ answers matching
  them.

## [1.0.0-beta.50] — 2026-07-29

### Changed

- **A cold CLI session starts with room to work.** The proactive compact that
  runs before a CLI session is handed its context was evaluated against
  `compact_threshold_pct` — 95% in practice. That threshold is right for a
  live session, where it guards against overflow, but a cold session receives
  its whole context in one go: the stored messages are serialized into
  `initial_context.md` and read back as a single tool result. At 95% a session
  could therefore open at 94% of its window, having done nothing yet, and be
  compacted again within a few turns. A cold start is now held to
  `CLI_COLD_START_TRIGGER_FRACTION` (40% of `max_context_size`) instead —
  whatever `compact_threshold_pct` says, including 0, where the provider's own
  mechanism cannot help because the file is written before it sees anything.
  A stricter configured value is never loosened, and live sessions and direct
  API providers are untouched.

### Added

- **`tools/gauge_probe.py`** — runs the gauge counter and the compaction
  counter over a stored agent context and reports how much of their difference
  the cold-CLI bootstrap boundary accounts for. `UNEXPLAINED` must be 0.
  It also lists every *structural* bootstrap marker, which a grep for the
  marker string cannot do: that also matches messages which merely quote it,
  such as tool output from reading this repository's own source. Read-only, no
  network, works without `tiktoken`, and accepts a bare `.jsonl` so a version
  recovered from conversation git history can be inspected.

### Fixed

- **The context gauge no longer survives the CLI session it describes.** On a
  CLI provider the gauge measures the provider's window since the last
  `initial_context.md` read -- everything before that boundary is deliberately
  counted as zero, because the provider received a file reference, not those
  messages. A server restart kills that session but left the persisted entry
  untouched, and `compute_context_usage` returns it verbatim while no agent is
  running: the next turn redisplayed the dead session's percentage against a
  window nothing had filled. Measured in the field at 35% of 800k on a cold
  start. `_prepare_agent_context` now zeroes and republishes the gauge at the
  one place that knows the session is gone.

  The compaction that followed the restart was not a second bug. While a CLI
  session is live the threshold is evaluated against the gauge itself, so a
  gauge below the threshold means no compaction. The whole stored context is
  measured only on a cold session, where the provider window is empty by
  definition and the size that matters is what is about to be written into
  `initial_context.md`. The stale reading was the only defect: with the gauge
  at 0 the sequence reads as it should -- cold start, oversized store,
  squeeze, then a gauge that climbs as the provider reads the file.

- **A compact notice no longer escapes the simplified view.** The
  `compact_progress` handler creates its system row through `addMsg` rather
  than a message event, which made it the last row-creating path not routed
  through `turnViewIngest`: it landed at top level, outside every block, and
  stayed there. Same for the git-prune notice. Both are now filed in the
  block -- and explicitly barred from the outside spot, which belongs to the
  last message of the turn, not to a status line.

- **`tiktoken` is imported lazily.** `core/token_counter.py` carries a full
  fallback path for when the tokenizer is unavailable -- approximate counting,
  a five-minute retry window -- but imported the module at file scope, so its
  absence raised `ModuleNotFoundError` instead of degrading. Running any tool
  that touches token counting outside the server image failed outright.

## [1.0.0-beta.49] — 2026-07-29

### Changed

- **The simplified view's activity surface is a rain of glyphs, and every cue
  condenses out of it.** The block draws a themed glyph rain behind its cues --
  one shared 15 fps ticker for every running block on the page, stopped the
  moment the last one ends -- and a cue arrives by resolving character by
  character out of that same alphabet, text and copied tool call alike. Cues no
  longer share one spot: they form a column, newest on top at full strength,
  older ones pushed down and fading out. Nothing leaves on a timer any more --
  a cue holds its place until a newer one arrives, so a minute of silence no
  longer blanks the surface. A tool call is shown as the classic view draws it,
  copied in full, instead of the label "Calling tool..." that named neither the
  tool nor its arguments. The surface is taller (190px) and its text is meant
  to be read rather than glimpsed.
- **The block header counts the turn's seconds**, ticking while it runs and
  frozen at what it took once it ends. Through a long silent stretch it is the
  one thing on screen that keeps moving.

### Fixed

- **The last message of a turn is shown outside its block, always.** Which row
  is the answer was decided from the done payload, so a provider that ends a
  turn without naming a final message -- the CLI ones do -- left its answer
  inside a collapsed tab under a header still reading "working". It is
  positional now: the last message row of a turn takes the spot under the
  block and hands it back when a newer one arrives. Replayed history is
  exempt, since an older page arrives after rows that precede it.
- **A done event closes the block even when it names no final message.** The
  close used to require `is_final` *and* `final_msg_id` together; what the
  server names still decides which row is hoisted out, but whether the turn is
  over no longer depends on it.
- **A new user message closes the block above it.** A turn the server never
  closed stayed on "working", with its clock running, above a message the
  reader had already moved past. A turn that ended on an error or a stop keeps
  that status.

- **The update panel reloaded onto the version it started from.** It polled
  `/health` and reloaded on the first answer, and `/health` said only `ok`. An
  updater that failed before stopping anything therefore looked exactly like a
  finished update: the server never went away, the first poll succeeded, the
  page reloaded at once on the old version, and nothing said the update had not
  happened. `/health` now carries the running version and a per-process id, the
  panel waits for a *different* process, and after ten minutes it says which of
  the two failures happened and prints `docker logs pawflow-updater`.
- **The update left its new install directory owned by root, silently.** The
  updater runs as root, so the per-version directory it extracts is root-owned
  until it hands it back. That `chown` required *both* `PAWFLOW_RUN_UID` and
  `PAWFLOW_RUN_GID` in the server container's environment; a container created
  by an older start script carries only the first, so the step was skipped
  altogether -- with no warning, on an update whose log otherwise reads clean.
  The owner is now read off the install directory being replaced when the pair
  is incomplete, and the `chown` runs whatever the refresh did rather than only
  on the success path.
- **A failed host-artifact refresh could leave the deployment unstartable.**
  When the refresh failed on the first artifact -- the start script itself --
  the new directory existed and was empty, and forcing past the failure still
  `cd`-ed into it: the updater died on a script that was not there and the
  server was never touched. The handover now falls back to the directory being
  replaced, which does carry a working script.
- **`install-pawflow.sh` failed on `unlinkat: permission denied`.** That is what
  the leftovers above look like from the command line, and the message named
  neither the cause nor the way out. The extraction now checks the runtime
  directory's ownership up front, before creating anything, and prints the
  `chown` that takes it back.
- The server-update test suite no longer calls the live GitHub API. One test
  resolved the published image without pinning the release lookup, so a
  rate-limited or offline runner got an empty tag and the build failed on
  beta.48 for a reason unrelated to the change under test. The whole suite now
  passes with outbound HTTP refused.

## [1.0.0-beta.48] — 2026-07-29

### Changed

- **The collapsed activity cues stack instead of taking turns.** One cue at a
  time, each waiting 1.5 s for the one before it, hid most of what the agent was
  doing and read as a slideshow. Cues now share one spot and stack in depth: the
  newest zooms in at the front, sharp, while the ones behind it are pushed back
  a step — smaller, dimmer, motion-blurred — and drop off the back of the stack.
  Each leaves at its own moment. A cue's pose is a function of its depth alone,
  so it is recomputed on arrival, and the surface is a fixed-height clipped
  stage: a burst of activity never shifts the layout.

### Fixed

- **Tool results escaped the turn block.** A result whose `tool_call` row is
  not in the DOM is queued for 750 ms and then rendered standalone. That
  fallback in `sse_state.js` was the one row-creating path never wired to the
  turn view, so every unmatched result — backgrounded MCP calls, results
  arriving after a view switch reload — was dropped at top level between the
  block and the next user message. Read off the live DOM: the block held 54
  `tool_call` rows and zero `tool_result` rows while three sat outside it.
- **A user message from another client was filed inside the previous turn.**
  The live `new_message` handler ingested every role, so a user message that
  this tab did not itself submit became turn content instead of a boundary.
  It now opens a turn, exactly like the local submit path.
- Standalone assistant narration — a delegate reply with no delegate frame, an
  agent response — is handed to the block instead of being left at top level.
- **The simplified turn block rendered as a bare bar and swallowed its own
  header.** The message column is a scrolling flex column, so its items shrink
  once the transcript is taller than the viewport. Every other `.msg` is
  protected by the automatic minimum size — which, per the flexbox spec, does
  not apply to a flex item whose overflow is not visible. The activity block is
  the one `.msg` with `overflow: hidden`, so on any conversation long enough to
  scroll it collapsed to the 22 pixels of its own padding: no readable header,
  no animation, and a two pixel sliver as the only clickable target. Measured
  in headless Chromium against the real stylesheet and the real controller:
  22px before, 100px while working and 46px once completed after. The block now
  declares `flex: none`, and drops the `.msg` padding it never wanted — as
  `.msg.simple-turn-block`, because the `.msg` padding is declared further down
  the sheet and wins on source order at equal specificity.
- **The tab icons filled their own tabs.** `_turnSvg` emits a `viewBox` and no
  dimensions, so each inline SVG scaled to the full width of its grid column —
  four ~180px pictograms with their labels pushed out of the block. They are
  sized at 16px and the tab lays its icon and label out on one row.
- A completed turn no longer reserves the ephemeral animation band. It is laid
  out only while the block carries `turn-working`, so a reloaded transcript —
  which is nothing but finished turns — shows headers instead of empty strips.

## [1.0.0-beta.47] — 2026-07-29

### Fixed

- **The simplified chat view did nothing at all in the web chat.** Grouping was
  built on `turn_id` correlation, which the runtime derives from the flowfile
  attribute `agent.request_msg_id`. Only the programmatic runtime API ever set
  it, so every turn submitted from the web chat — the only client the view
  exists for — ran with an empty one. No stored message and no live event
  carried a `turn_id`, so `turnViewIngest` rejected every row: no activity
  block was built, nothing was reparented, and there were no tabs, no ephemeral
  text and no icons. Selecting Simplified persisted the mode and switched the
  menu, then rendered an ordinary classic transcript, with no error anywhere.

  The view no longer groups by correlation. Boundaries are positional, which is
  what they always were on screen: a user message opens a turn and its block,
  everything rendered after it goes into the block, the terminal answer is
  lifted out and placed below it, and the next user message closes the turn and
  opens the next one. A user message that arrives before the answer therefore
  reads user / block / user / block / answer, with the answer under the last
  block — where the reader is looking — rather than lifted back above content
  that arrived after it. `turn_id` names a turn but no longer routes rows, so it
  can no longer be missing: a turn nobody stamped groups identically, including
  one whose user message has no id at all.

  The submitting path also stamps the turn id it was always meant to carry, from
  the user message id the browser already generates, so stored rows are
  self-describing and the terminal-answer marker lands on reload. That stamp is
  no longer load-bearing for the view itself.

## [1.0.0-beta.46] — 2026-07-29

### Added

- **Simplified live chat view.** The View menu now stores `chat.view_mode` at
  conversation scope. `classic` keeps the existing transcript; `simplified`
  presents each user turn as the user message, one expandable activity block
  with live Messages, Thinking, Tool calls and Artifacts tabs, and the same
  canonical final assistant message below it. The block reparents the nodes the
  existing renderers already produce instead of re-rendering them, so tool
  result attachment, diffs, inline media, message actions and `msg_id` dedupe
  keep working unchanged. Only a successful `show_file` result becomes an
  artifact card; approvals, questions, errors and notifications stay top-level.
  Turns carry durable `turn_id`/`turn_final` metadata so reload, pagination and
  reconnect rebuild the same grouping. Classic mode is untouched.

### Security

- **Context and usage actions were not gated by the sharing ACL at all.** Phase
  3 of the sharing work put a role table in front of the conversation
  dispatcher, but `_ctxops_*` and `usage` were never given one, and neither
  called `require_read`/`require_write`. They took whatever `conversation_id`
  they were handed and acted on it. Measured before the fix, against a
  conversation owned by someone else:

  - a user with **no relationship to the conversation at all** got `200` on
    `get_context`, `view_context`, `usage_conversation`, `get_cost` and
    `list_context_usage` — reading another account's agent contexts and what
    their conversation cost;
  - a **read-only collaborator** got `200` on `delete_agent_context` and
    `add_context_message`, and `202` on `git_prune` — destroying an agent's
    whole context and pruning the conversation's git history from a role whose
    entire meaning is that it cannot write.

  Both dispatchers now gate from a role table, like the conversation one, with
  a completeness test per cluster so an action added without a row fails the
  build instead of shipping ungated. All of the above answer `404` — the same
  answer an unknown id gets, so the gate leaks no existence either.

- **The deployment-wide cost total was readable by any authenticated user.**
  `get_cost` with no `conversation_id` answers `UsageLedger.total_cost()` —
  every turn every user of the install has ever run. With an id it is now gated
  on that conversation; without one there is nothing to gate it on but the
  role, so it requires admin. The other usage actions were already scoped:
  `cost` reads `user_usage(user_id)`, and `_usage_query_filters` pins non-admins
  to their own id.

- **Realtime sessions refused write collaborators.** `_livekit_sessions.py`
  still used the owner-equality test inherited from the legacy voice bridge, so
  a collaborator who could drive the agent by typing was denied the microphone.
  Not a leak — the opposite — but the ACL is the single source of truth for
  that question. A non-owner is now allowed exactly when `require_write`
  allows them.

### Fixed

- **The simplified view hid the answer of every turn it could not prove was
  final.** The view lifts the row carrying `turn_final` out of the activity
  block to render it as the standalone answer, so a turn without that marker
  showed a user message, a collapsed block, and no visible reply. The marker is
  written by a patch that legitimately never lands: rows recorded before the
  feature carry none, terminal paths that leave `final_msg_id` empty produce
  none, and a patch matching no row was discarded in silence. Every
  pre-existing conversation reloaded in simplified mode therefore lost its
  answers. Reconstruction no longer depends on the marker: when no row of a
  turn carries `turn_final`, the display classifier marks the last visible
  assistant row and flags it `turn_final_derived`. Error rows, tool traffic and
  display-only rows are never promoted, and a turn that produced no visible
  answer still gets no final row. Classification runs per page, so a derived
  marker never displaces a final already placed, and an authoritative one
  reclaims the row it supersedes. `patch_message` now warns when it matches no
  row instead of returning quietly.

- **Live-window trimming could destroy a turn that was still running.** The
  trim reaches a turn through its user anchor, which is never marked live even
  while the block below it streams, so the existing live guard was bypassed and
  the whole group was evicted mid-flight. The guard now covers every node of
  the group.

- **Streamed text queued one animation cue per token.** The coalescing window
  the simplified view declared was never wired up, and turn identity was
  re-rendered twice per token on top of it. Text now emits one cue per window
  and identity re-renders only when it changes; discrete tool cues stay
  immediate. The transient excerpt also took the last characters of the stream
  rather than the first, and the load-more banner counted nested `msg_id`s in
  classic mode, inflating its total.

- **CLI context gauges counted a session's context before the session had it.**
  The gauge now follows the context actually present in the provider session: a
  compact or context edit invalidates that session and resets it to zero, the
  next cold turn counts only the short injected bootstrap prompt, and the
  serialized PawFlow messages join the gauge when the CLI reads
  `initial_context.md` — counted once, not twice. This lifecycle now covers
  Claude Code, Claude Code interactive, Codex app-server, Antigravity and
  Gemini CLI, replacing the flat invisible-overhead offset that applied to
  Claude Code alone. Direct API providers keep counting their request messages.

- **`conversation_search` kept serving text that had been edited or deleted.**
  The index skips a conversation whose `updated_at` has not moved, and only
  rebuilds one whose row count shrank. Neither sees a redaction: editing a
  message in place leaves `updated_at` untouched — it is the newest message's
  timestamp — at a constant row count, and deleting the newest message moves it
  *backwards*, which reads as "older than what I already hold". A password
  redacted out of a transcript, or a message deleted from it, stayed searchable
  indefinitely. The store now keeps a `transcript_generation` counter, bumped by
  every rewrite and never by a plain append; the index compares it and rebuilds
  the conversation when it moves. Appends stay incremental — a search pays for
  the refresh, so re-reading every transcript would trade a correctness bug for
  a performance one. An index written before the counter existed is rebuilt
  once, since it may hold exactly the text this fixes.

- **Two invitations sent at the same moment could lose one from the "shared
  with me" list.** The reverse index is a whole-file read-modify-write with no
  lock, so two invites to the same person from two conversations both read the
  old list and the second write dropped the first. The ACL is authoritative and
  was never affected — nothing was granted or denied wrongly — but the
  conversation was missing from that user's sidebar until the index was
  rebuilt. Now serialised per user, and the staging file carries a unique name:
  a fixed `.json.tmp` let one writer publish another's bytes.

- **A bad server image destroyed the running server before it was found to be
  bad.** `scripts/run-pawflow-docker.sh` probes the new image twice — does it
  carry the Docker CLI, can it reach the mounted daemon — and both probes ran
  *after* `docker rm -f` on the server container. An image missing the CLI, or
  a socket the container could not reach, left the operator with no server at
  all and a message about how to rebuild one. The probes read and start
  nothing, so they now run first; the destruction is the last step before
  `docker run`. An update that fails must leave the old server running.

- **The update refreshed the server image but never the host files that came
  with it.** `install-pawflow.sh` copies a set of artifacts out of the image
  onto the host — the start script the update itself runs, `doctor-pawflow.sh`,
  the relay image catalog, the AppArmor profiles, `docker/claude-code`,
  `docker/pawflow_sdk`, `tools/mcp_bridge.py`, `core/tool_json.py` and
  `pawflow_relay`. They live outside the container, so pulling a new image left
  them untouched: every update from the UI kept whatever version had last been
  installed from the command line, indefinitely. A fix to any of those files
  could only ever reach a host through a manual reinstall.

  The updater now extracts them from the image it just pulled, into the new
  version's `~/.pawflow/runtime/<tag>` directory, and starts the server from
  there — `PAWFLOW_SOURCE_DIR` moves the `org.pawflow.host-app-dir` label with
  it, so the next update finds what this one wrote, and the previous version's
  directory stays intact to fall back to. An install started with
  `--runtime-dir` is refreshed in place instead, and a git checkout is left
  alone: `git pull` is what moves its files.

  The refresh happens between the pull and the start script, the window where
  failing is still free. **It aborts by default** — the files it could not
  write include the start script about to run — with an opt-in checkbox to
  continue anyway, which warns that the new server image is being started with
  the host-side files of the version it replaces.

## [1.0.0-beta.45] — 2026-07-29

### Fixed

- **A relay container the Docker daemon refused to kill aborted the whole server
  update.** `scripts/run-pawflow-docker.sh` removes the managed relay containers
  before recreating the server, so they restart with current runtime code. It
  did so in a single `docker rm -f` whose failure, under `set -e`, stopped the
  script — *after* the new image had been pulled and *before* the server
  container was recreated. A beta.42 → beta.44 update hit exactly that (`could
  not kill container: tried to kill container, but did not receive an exit
  event`): the server stayed on beta.42 while its relay was left half-killed,
  and the update looked like it had simply reloaded. Each relay is now removed
  on its own and a failure is a loud warning on stderr instead of an abort.

- **A managed relay container that died stayed dead until the server was
  restarted.** The container of a managed server relay is started once, from
  `RelayService.connect()`, and runs with `--rm`. Nothing re-created it, so a
  crash — or an operator running `docker rm -f pawflow-relay-srv-<id>` to
  unblock the update above — left the transport retrying against a container
  that no longer existed, for as long as the server stayed up. The relay now
  respawns itself: when a request fails with a disconnect error and is about to
  be retried, `ensure_managed_relay_alive()` re-creates the container if, and
  only if, it is really gone. A live container is left strictly alone (the
  ordinary disconnect is the relay client reconnecting, and a respawn would
  turn it into a cold start), unmanaged operator-run relays are never touched,
  and a burst of failing calls asks for one container start rather than one per
  call. This is what made the previous entry's "relays are recreated on demand"
  actually true; it was not, while the server kept running.

## [1.0.0-beta.44] — 2026-07-29

### Fixed

- **The CI security gate failed on a false positive.** `bandit` reads
  `GHCR_TOKEN_URL` as a hardcoded password (B105) because the name contains
  `TOKEN` and the value is a string literal. The constant is the public GHCR
  *endpoint* that hands out an anonymous pull token for a public repository —
  there is no credential in the source. Marked `# nosec B105`, as the OAuth
  `token_url` entries already are. The beta.43 image itself published normally:
  `docker-publish.yml` and `ci.yml` are independent workflows.

## [1.0.0-beta.43] — 2026-07-29

### Added

- **`conversation_search` — an agent can now search what was actually said in
  past conversations, not only what it remembered to store.** `recall` reads
  the memory store, which holds what some agent decided at the time was worth
  keeping; anything nobody extracted was simply gone. The new tool runs a
  SQLite FTS5 index over the raw transcripts (one database per user under
  `data/runtime/conversation_index/`), with an optional `summarize=true` that
  has the summarizer synthesize the hits. Read-only, approval-exempt, and
  allowed in read-only mode. **Encrypted conversations are never indexed** —
  an FTS index is plaintext, so indexing one would put its content back in the
  clear; a conversation encrypted after indexing is purged, and an unreadable
  encryption state counts as encrypted. Closes P4 of
  `docs/LEARNING_LOOP_PLAN.md`.

- **Agents are now asked to reflect, once there is something to reflect on.**
  The diary has always accepted a `reflection` entry type that nothing ever
  requested, so agents accumulated observations and never synthesized them. A
  `Reflection due` block now appears next to the diary digest — but only after
  5 diary entries since the last reflection *and* 6 hours since it, because a
  standing instruction to reflect is one an agent learns to skip and a
  wall-clock trigger nags a diary nothing was added to. The nudge also asks
  whether the synthesis deserves a `kg_add` triple or a skill, which closes the
  loop back into skill creation. Closes P5 of `docs/LEARNING_LOOP_PLAN.md`.

### Fixed

- **A resumed tmux session replayed the whole previous turn into Telegram.**
  After a background result woke the Claude Code session, the user received a
  hundred tool-call messages at once — every tool call of the previous turn,
  again. A live Claude Code session replays its **entire context** on every API
  request, so the MITM proxy observes every prior `tool_use` block each time;
  PawFlow-driven turns dedup against the container's id sets, but
  `_run_manual_capture` built its coordinator without them and fell back to
  fresh per-coordinator sets, re-emitting the lot. Each one was persisted as a
  transcript row and published as a `tool_call` event. The webchat keys tool
  blocks by `tc_id` and absorbed the repeat, which is why it looked like a
  Telegram-only bug — it was not: the duplicate rows were also going into the
  transcript. The capture now shares the pooled container's dedup sets, with a
  per-session fallback so two chained captures still dedup against each other.

- **A message refused by the runtime left a channel client waiting forever.**
  `AgentRuntimeAPI.submit_message` is how every non-HTTP transport submits —
  Telegram today. When the runtime refuses a submission it answers the same
  404 an unknown conversation gets, and that body carries neither `status`
  nor `wait_for_done`, so both defaulted to "accepted" and `True`. The caller
  then waited on the correlated `done` of a turn that was never started, and
  the Telegram bridge waits with no timeout by project rule — the user got
  silence, and the bridge thread stayed parked. Refusals now cancel the
  registered waiter and raise `AgentSubmissionRejected`, which the Telegram
  client already reports to the user. Found while writing the channel-bridge
  test that `docs/CONVERSATION_SHARING_PLAN.md` had been asking for.

## [1.0.0-beta.42] — 2026-07-28

### Fixed

- **The server could not update itself unless it was a Docker Compose stack.**
  Which most servers are not: `install-pawflow.sh` ends on
  `run-pawflow-docker.sh`, a plain `docker run`. Compose stamps its project path
  on every container it creates; a `docker run` stamps nothing, so the update
  refused with *"not started by Docker Compose"* and left the gear menu useless
  for every installer deployment. The updater now recognises that shape and does
  what the installer does: pull the published server image, then re-run
  `run-pawflow-docker.sh`, which already recreates the container in place. The
  environment of the running container is replayed, so the bootstrap key, the
  uid/gid and the relay images survive the update; `PAWFLOW_BOOTSTRAP_RESET` is
  forced empty, since replaying a fresh install's first-run flag would wipe a
  working server's installer state. `run-pawflow-docker.sh` now also stamps
  `org.pawflow.*` labels, and `core/installer_deployment.py` falls back to
  `PAWFLOW_HOST_APP_DIR` and `docker inspect` so an install that is *already*
  running is updatable, not only the next one.

- **The published version of the relay images was always unknown.** Two causes.
  It was never asked of the registry: the shipped catalog's
  `relay_image_version` was reported as "published", which answers what this
  server expects, not what exists. GHCR is now queried for the real tags, with
  the catalog kept as the offline fallback. And that catalog was read from
  `/app/config`, a host bind mount seeded no-clobber by the entrypoint, so an
  install predating the `relay_image_version` key kept a catalog without it
  forever — reporting an empty published version on a perfectly current server.
  Shipped, versioned data is now read from the image's own copy.

## [1.0.0-beta.41] — 2026-07-28

### Added

- **Source-scan tests now fail with a diagnosis instead of a substring error.**
  Structural invariants that cannot be tested by running the code are pinned by
  scanning source text, which couples a test to a marker string in a file that
  does not know it is a marker. `tests/_srcscan.py` replaces bare `str.index()`
  slicing: it refuses a marker that is missing, ambiguous, or matches only as a
  prefix of a longer name, and reports which marker, how often and on which
  lines. Its `region()` searches the end marker after the start marker, so a
  newly added symbol can no longer steal a region boundary and silently empty
  the slice — the failure that prompted this, where `def _append` matched a new
  `def _append_platform_note`. Markers shared by several tests are declared in
  `tests/_anchors.py` with the reason they exist, checked once by
  `tests/test_source_anchors.py`, and carry an `# anchor: <name>` comment at the
  anchored site so a rename is caught where it is written rather than in a
  distant test. See `docs/development.md`.

- **An agent is now told when another agent changes code under it.**
  Several agents in one conversation share the same relay, and therefore the
  same files. Agent B read `service.py`, reasoned about it for a few turns, and
  agent A rewrote it in the meantime — nothing told B. It kept editing against a
  view that no longer existed, and the collision surfaced only as a failed
  `old_string` match, or as a silently clobbered change.
  `core/read_conflict.py` uses the per-agent read hashes the edit guard already
  keeps: when a write lands, every other agent whose last read no longer matches
  what is on disk gets a notice, delivered at its next turn under *Files that
  changed under you*. All mutation tools report it (`write`, `edit`,
  `apply_patch`, `batch_edit`, `find_replace`, `delete`), on both the workdir and
  the relay path.
  It stays quiet: a single-agent conversation has no other readers and pays
  nothing, an identical rewrite notifies nobody, and a re-read clears the notice
  so the agent is told once rather than every turn. The notice is advisory — it
  asks for a re-read, it never refuses an operation.

### Changed

- **A read-conflict notice now reaches the agent during the turn, not after it.**
  The notice was delivered at the next context build, which meant an agent in a
  long tool loop kept working against a stale view for the whole loop — exactly
  the window where the collision does damage. It now rides the last tool result
  of a batch, so the agent sees it between tool calls. It is appended *after*
  the untrusted-content envelope: that envelope tells the agent to treat the
  content as external data, and a PawFlow-generated warning buried inside it
  would teach the agent to distrust our own warnings. The next-turn channel
  stays for turns that ended without tool calls; since taking the block clears
  it, exactly one channel ever delivers and the notice can never arrive twice.

## [1.0.0-beta.40] — 2026-07-28

### Fixed

- **Webchat messages reached nothing while a tmux turn was visibly working.**
  When a PawFlow turn ended with a background tool still running, the tool's
  result landed in the Claude Code container and Claude Code resumed on its
  own. That is a *captured* turn: `_active_turns` is registered so the agent
  shows as busy, but there is no streaming worker, no `_active_contexts` entry
  and no `_active_claude_client`. `agent_streaming` reads that combination as
  "already active but not preemptable" and parks the message in the
  PendingQueue — correct for a real turn, which drains at its end, but a
  captured turn has no owner to drain it. Every message sent from the webchat
  sat in the queue until a force stop discarded it, with the UI showing the
  agent up the whole time. Two gaps closed: the message is now typed straight
  into the live tmux, and the capture hands any queued messages back when it
  releases the turn. Liveness is decided by the MITM proxy's WebSocket — it is
  up exactly while a container lives, so it proves there is a tmux to deliver
  into without consulting the turn bookkeeping that went stale in the first
  place.

- **A captured tmux turn was invisible until it ended.** The same sequence, seen
  from the other direction: the tmux worked for minutes — text, tool calls, tool
  results, every byte observed by the MITM proxy — and the webchat showed
  nothing, because the capture built its turn coordinator with *no callbacks*
  and persisted one lump when the turn finished. A PawFlow-driven turn passes
  four callbacks and streams each block through the agent loop; the capture had
  none, so it was silent by construction, and its tool calls were dropped
  entirely. It now passes the same `callback` and `block_callback`: text deltas
  publish as `token` events while they are written, and each completed block is
  persisted and published as it arrives. This is the rule the Antigravity
  observer already implements — its manual ingest streams out-of-band tmux
  activity by default and suspends only while PawFlow drives a turn — applied
  to Claude Code interactive: everything the proxy intercepts reaches the SSE
  listeners while it happens, whoever started the turn.

- **`apply_patch` could rewrite the wrong lines without saying so.** When `git
  apply` refused to parse a diff — which it does on hand-counted `@@` counts,
  even when the context and the edits are correct — its diagnostic was
  discarded and the patch fell through to a fallback parser that applied
  *positionally*. That parser never compared what it removed, so a hunk whose
  context existed nowhere was applied anyway to whatever sat at the stated
  offset; and it indexed the old-side `@@` number into a buffer the preceding
  hunks had already grown, so every hunk after the first landed off by the net
  line delta before it. It also wrote each file as it went, leaving earlier
  files rewritten when a later hunk was nonsense. The fallback now treats the
  `@@` number as a hint and locates each hunk by its context, refuses a hunk it
  cannot place (quoting git's reason when git supplied one), and writes nothing
  until every hunk in the patch has been placed. A patch with wrong `@@` counts
  or a bare `@@` now applies correctly instead of corrupting the target.

  A hunk with no context at all (a pure insertion, as `diff -U0` emits) has
  nothing to match, so it is checked against the header's own redundancy
  instead: the two counts must restate the hunk body, and the new-side start
  must restate the old-side start shifted by every hunk already applied. All
  three agreeing corroborates the position; any disagreement, or a bare `@@`
  carrying no arithmetic, is refused rather than placed on trust. Every hunk is
  now checked — by context where context exists, by arithmetic where it does not.

- **`apply_patch` reported success on a zero-context diff it had not applied.**
  `git apply` requires one line of context and otherwise skips the hunk *and
  still exits 0*, so a `diff -U0` patch came back applied over an untouched
  file. It is now invoked with `--unidiff-zero`, which relaxes only the hunks
  that carry no context — a patch that has context applies identically either
  way.

- **`Monitor` dropped output past `line_limit` without saying so.** A body that
  stops at the cap is indistinguishable from a command that had nothing more to
  say, so a caller reading a truncated result concluded the output simply ended
  there. The watcher script now reports the true line count and which branch it
  took, and a truncated result states the cut twice: `truncated=N` in the
  header, and a closing line giving how many lines were dropped, how many were
  produced, and which end was kept (`first` for raw output, `last` for the
  never-matched fallback). A hit list capped by `limit` is not reported as
  truncation — the header already carries `limit=`, so that cap was never
  silent. `line_limit` still never costs a match: the hit search greps the whole
  capture file, so a marker on line 400 is found under any `line_limit`.

## [1.0.0-beta.39] — 2026-07-28

### Fixed

- **`Monitor` hung instead of returning early, and worst on the patterns it
  advertises.** It piped the command through `grep --line-buffered | head -n N`,
  which cannot stop anything: a downstream stage exiting never ends a pipeline
  because the shell waits for every member, and `grep` only learns `head` is
  gone when it next writes. For a pattern matching once — `FAILED`,
  `listening on port`, its own examples — that second write never comes, so the
  command ran to completion and `Monitor` came back at its timeout. Measured on
  a 60-second command: a pattern matching once took the full timeout, the same
  pipeline with a pattern matching every line returned in two seconds. The more
  selective the pattern, the longer the hang. The command now runs in its own
  session with its output captured to a file, and a watcher kills the whole
  process group — children included — as soon as the pattern is satisfied or
  the deadline passes.
- **A `Monitor` timeout threw away everything the command had produced.** The
  bash layer's own timeout fired first and returned its error banner in place
  of the output, contradicting the documented "returns what was captured so
  far". The watcher now owns the deadline and the capture file survives it; a
  pattern that never matched returns the tail of the raw output rather than a
  blank.
- **`Monitor` never reported the command's exit code, and guessed its own
  reason from the output text** — a build logging the words "timed out" was
  reported as a Monitor timeout. The shell states `reason=` and `rc=` on their
  own line, and the header carries `exit_code=` when the command ended by
  itself. None of this was caught earlier because every existing test mocked
  the bash layer and asserted the command *string*: they passed against a tool
  that hung on every selective pattern. The suite now executes the script.
- **An RTK rewrite would have truncated a generated script to its last line.**
  The rewrite keeps only the final line of `rtk`'s output as the whole command,
  which is harmless for the one-liners it was built for and destructive for a
  multi-line script. Generated scripts opt out (`_skip_rtk`); `Monitor` uses it.

### Changed

- **Tool guidance: match processes by pidfile, not by command line.** `pgrep -f`
  and `pkill -f` also match the shell that runs them, because the pattern is in
  that shell's own command line by construction. Observed twice in one session:
  a wait loop on `pgrep -f 'pytest tests/'` matched itself and spun until
  killed, losing a completed test run, and a `pkill -f` killed its own shell
  mid-command. Stated in `bash`'s description and in the tool-usage block.

## [1.0.0-beta.38] — 2026-07-28

### Fixed

- **The header kept showing "Working: Command" with nothing running.** A
  context operation (`/compact`, `/clear`, `/rewind`) reports its own progress
  through `context_op` events, so its acknowledgement is marked
  `suppress_command_result` to keep the raw JSON from printing as a system
  message. The background dispatcher took that marker as permission to publish
  nothing at all — but the browser clears a pending UI action only when a
  result arrives, so the call stayed open and the server-side registry held it
  pending until its 600s TTL expired. The suppressed path now publishes a
  `command_result` carrying `{"suppressed": true}`: nothing renders, and the
  call completes with the operation.

## [1.0.0-beta.37] — 2026-07-28

### Fixed

- **Two coordinators could read one Claude Code session, splitting the turn
  between them.** `queue.Queue` hands each event to exactly one getter, so a
  capture coordinator (started by a manual tmux prompt, or by Claude Code
  injecting its own background-task notification) and the next webchat turn
  competed for the same stream. Text deltas arrived halved, producing answers
  that begin mid-sentence; `tool_use` blocks were severed from their
  `input_json_delta` chunks, leaving MCP wrappers un-unwrapped and rendered as
  a bare `use_tool`. Only a compact recovered, because killing the session
  mints a new token. Ownership is now arbitrated by epoch: a request claim
  always wins and happens before the drain, a capture claim refuses while a
  request coordinator polls, and an evicted capture discards its partial text
  instead of publishing a truncated message.
- **The webchat showed the agent idle while the tmux was visibly working.** A
  turn PawFlow did not send — a human typing in the tmux, or Claude Code
  resuming on a background-task notification — runs outside the streaming
  worker, so `_active_turns` stayed empty. PawFlow attaches to such a turn
  rather than restarting it (a second prompt would duplicate work already in
  flight) and now publishes the active-agent marker for its duration.
- **Killing a finished tool call cancelled a different, running one.** A
  targeted kill that finds nothing is ambiguous: the call may be running but
  not yet bound to its `tool_call` id, or it may simply be over. Only the
  first justifies widening to a whole-agent cancel. A stale-looking `edit`
  was killed and took the running `bash` with it; the broad fallback now
  requires an unbound in-flight request, and otherwise reports that there was
  nothing to kill.
- **A wrapper call whose streamed input was lost is recovered from the request
  body.** When the chunks carrying `tool_name` go missing the call is dropped
  entirely by the MCP completeness check, so nothing was persisted and the
  observed replay can supersede it without duplicating.
- **PawFlow's own CamelCase tools were unreachable through `use_tool`.** The
  MCP bridge lowercased any CamelCase tool name, to map Claude Code's native
  spellings onto PawFlow tools (`Read` → `read`). PawFlow registers CamelCase
  tools of its own — `Monitor`, `ScheduleWakeup`, `PushNotification`,
  `EnterPlanMode`/`ExitPlanMode` — and those are precisely the ones whose CC
  built-in equivalents are deliberately disallowed, so the intended fallback
  path was broken: `Monitor` answered `unknown tool 'monitor'` with no way
  through. The name is now sent as written and only lowered if the server
  replies that it does not know it, leaving the registry as the single
  authority. Consequence of the defect: agents fell back to polling a log file
  with `sleep N; tail`, which costs a turn per poll and reports on the sleep
  instead of the command.
- **Tool guidance now names the tools that make polling unnecessary.** It said
  "use `run_in_background` for long-running commands" and stopped there —
  never mentioning `Monitor` (blocks until exit or regex match, 10 min cap),
  `run_tests`, or `security_scan`, all of which exist. The `bash` tool now
  points at `Monitor` in its own description too, where an agent reaching for
  a shell actually reads.

### Added

- **Azure OpenAI and GitHub Copilot providers.** Both speak OpenAI
  chat-completions bodies, so they reuse the entire OpenAI path; only the
  envelope differs, and that is the whole of
  `core/llm_providers/openai_dialects.py`. Azure could not be reached through
  `base_url` alone: its key travels in an `api-key` header, it addresses a
  *deployment* rather than a model, and it requires an `api-version`. Copilot
  is a two-token provider — a device flow (no callback URL, no browser on the
  server) yields a GitHub token, saved as the service's `api_key`, which each
  session exchanges for a short-lived Copilot token, cached and renewed before
  expiry rather than at it. Ollama, LM Studio, OpenRouter, DeepSeek and the
  other OpenAI-compatible endpoints already worked through `openai` +
  `base_url` and are unchanged.

## [1.0.0-beta.36] — 2026-07-28

### Added

- **The server can now update itself** from the gear menu, completing the
  Update feature. A container cannot replace itself — `docker restart` comes
  back on the old image and `rm -f <self>` kills the process issuing the
  command — so the work goes to a short-lived detached container that has the
  Docker socket, survives the server's death, and drives
  `docker compose pull` + `up -d --build` from the project directory.
  That directory is **detected, not configured**: compose stamps it on every
  container it creates (`com.docker.compose.project.working_dir`, a host path),
  and `core/compose_deployment.py` finds this container's own id to read it.
  It is mounted into the updater at its own host path, because compose resolves
  `./data` and `build: .` against it and hands the daemon host paths — mounting
  it anywhere else would silently produce wrong bind mounts. A preflight proves
  the updater image runs, carries a working `docker compose`, and that the
  project directory is really there, before anything irreversible happens.
  Restarting kills every running agent turn: the dialog says so, and says how
  many, but does not refuse on the operator's behalf — it is the same cost as
  running `docker compose up -d` by hand.
- **Passive memory recall** (`core/passive_recall.py`). Memories close to what
  the user just said now surface on their own, without the agent deciding to
  call `recall` — which it can only do when it already suspects something
  relevant exists. The embedding and the search run in a daemon thread and the
  result is injected on the *next* turn, so a turn is never delayed and a slow
  or missing embedding provider degrades to "no passive memories" rather than
  to a stall. Hits below 0.4 similarity are dropped and anything already quoted
  in the static digest is skipped. `passive_recall_limit = 0` disables it.
- **Auto-poke** (`core/auto_poke.py`). The plan orchestrator only advances when
  the agent calls `update_plan`; a turn that ended without it left the step
  `in_progress` with nothing to wake the agent again, so the plan stalled until
  somebody noticed. Such a turn now gets handed back with a message naming the
  two acceptable exits — finish the step, or report the blocker. Bounded to two
  consecutive pokes per step, reset by any progress; never after an error, an
  interruption or a force stop; never when messages are already queued.
  `auto_poke_limit = 0` disables it.

### Fixed

- **The cognitive digests were invalidating the prompt cache on every turn
  that wrote to a store.** Memory, diary, knowledge-graph and project-structure
  digests sat in the system prompt, and they are rebuilt from live stores — so
  any `remember`, `diary_write`, `kg_add` or graph rebuild moved the cached
  prefix. Provider caching is prefix-based: that invalidates the system block,
  the tool definitions and every message behind them, i.e. the entire
  conversation is re-read at full price on the next turn. On API providers the
  digests now ride the same channel as the date/time — merged into the last
  user message, after all cache breakpoints. CLI providers keep them in the
  system prompt: their prompt goes through the cold-start bootstrap file and
  would otherwise be echoed back in the prompt handed to the CLI binary.
- Cache-break diagnostics were comparing conversations against each other. One
  `LLMConnection` service owns a single `LLMClient` shared by every
  conversation using it, and the detector kept one slot of state, so every
  switch between conversations looked like a cache break — noise that hid the
  real ones. State is now keyed per conversation and bounded.

### Added

- **Updates panel in the admin gear menu** (`core/update_manager.py`). It
  reports, per component, the installed version against the published one —
  server against the latest GitHub release, agent CLIs against the npm
  registry, relay images against the shipped catalog. The release tag
  (`1.0.0-beta.35`) and the packaged version (`1.0.0b35`) are compared under
  PEP 440, so they read as equal instead of flagging every install as stale
  forever. A component whose version cannot be resolved reports "unknown"
  rather than failing the dialog.
- The same panel rebuilds the **agent CLI tools image**. Antigravity installs
  from an unversioned script, so it has no version signal at all: forcing
  (`--no-cache`) is the only way to pick it up, and the versions actually
  installed are now stamped into the image at build time
  (`/opt/pawflow/cli_versions.json`) instead of being inferred.
- The panel also rebuilds the **relay images** and moves running relays onto
  them. The move goes through a new `ServerRelayManager.recreate()`, which
  replaces the container and nothing else: workspace directory, volumes,
  relay id, registered service and conversation bindings all survive.
  `destroy()` + `spawn()` would have deleted the volume and the workspace,
  i.e. the user's work. A failed respawn restores the previous metadata
  instead of dropping the relay from the store. The sweep is sequential and
  does not stop on a failure. Building alone changes nothing for running
  relays — a container keeps the image it started from until it is recreated.
  All of these actions require the `admin` role, refuse concurrent runs with
  HTTP 409, stream progress over SSE, and log who triggered them: `docker
  build` against the host socket is effectively root on the machine.
- **Conversation sharing is complete and reachable from the UI** (phases 4-7
  of `docs/CONVERSATION_SHARING_PLAN.md`). The sidebar splits into Mine /
  Invitations / Shared with me; an invite has explicit Accept and Decline
  and grants nothing until accepted; the owner gets a share dialog with
  inline role change and removal; a collaborator can leave from the context
  menu; and a user bubble written by somebody else now carries an author
  label — until sharing there was never more than one human in a
  conversation, so every user bubble looked alike. en/fr/es throughout.
- Channel bridges (Telegram et al.) and deployed flows honor collaborators:
  `authorize_conversation_target` resolves through the same
  `core.conversation_access` primitive the webchat actions use instead of
  keeping a second owner-equality check, and takes the access level the call
  site actually needs. The default is `write`, so a call site nobody
  reviewed denies rather than widens.
- A conversation whose owner's account is deleted is handed to the first
  accepted `write` collaborator who writes to it, moving its directory under
  the conversation lock. Without it, deleting an account silently orphaned
  every conversation shared out of it.

### Security

- **A message could be submitted into any conversation by id.** The
  submit path read `conversation_id` from the request body and never
  checked it against the authenticated principal — and nothing below that
  point is partitioned by requester: the agent context, the agent config and
  the CLI session state all load from the owner's directory using the id
  alone. Any logged-in user who knew or guessed an id could post into
  someone else's conversation and have the agent answer with its full
  context. This is the write-side twin of the SSE gap closed in beta.33 and
  predates sharing. A rejection returns the same 404 an unknown id gets.
  The check runs at the streaming ingress, before the user message is
  persisted and published to subscribers and before the preempt logic that
  cancels whatever agent is running on the conversation — a check placed
  after the background-thread boundary would have run after the write it
  exists to prevent.
- **Git history, archives and conversation files were reachable by id.**
  `conv_rollback`, `conv_delete_branch`, `conv_tag`, `conv_git_log`,
  `conv_export_pawflow` (a complete archive of the conversation) and
  `clear_store` (which deletes every FileStore file of a conversation)
  resolved the conversation by id with no access check. They are now gated
  by a single table, `_ACTION_ROLES`, with a test that fails if an action is
  added to those handlers without a row in it. Rollback and branch deletion
  are owner-only: they discard history for every participant.
- `loop_list` returned every user's scheduled loops when called without a
  conversation_id. It now requires one. `loop_stop` is keyed by loop rather
  than conversation, so it resolves the loop's conversation and requires
  write access on it — a loop spends the owner's budget on every tick.
- Server workspace and execution-relay lifecycle actions
  (`create/destroy_server_workspace`, `create/destroy_server_execution_relay`)
  were reachable by id: any logged-in user could destroy another user's
  server workspace. Now owner-only; the two status actions require read.
- **Conversation-scoped services were reachable by conversation id.** The
  service registry keys its conversation scope on the conversation alone —
  `_service_scope_id` drops the requester for `scope="conv"` — so every
  service_flow handler that reads `scope` from the request body acted on
  whichever conversation the request named. `get_service_detail` returned
  another user's service definition **including its config, which is where
  service credentials live**, and `update_service`, `delete_service`,
  `toggle_service`, `move_service_scope`, `service_install` and
  `service_uninstall` mutated it. A request that asks for conversation scope
  now requires write access on the conversation it names; global scope (the
  default) is untouched.

### Fixed

- Conversation-scoped agents, skills and MCPs were filed under whoever asked
  for them rather than under the conversation. Invisible while every
  requester was the owner; on a shared conversation a collaborator's turn
  resolved none of the conversation's own resources, so the agent it runs on
  would simply not be found.
- The `export` and `conv_export_claude_code` actions passed
  `store.load(conversation_id=...)`, a keyword `load()` does not accept —
  both had been raising `TypeError` instead of exporting.

## [1.0.0-beta.35] — 2026-07-27

### Fixed

- **The `edit` tool reported diffs that did not match what it wrote.** The diff
  was assembled from `old_string`/`new_string` *before* the write, so it
  described the intent rather than the result — and it was wrong three ways at
  once. A match starting or ending mid-line marked the WHOLE line removed and
  printed only the replacement fragment, so the untouched remainder of that
  line appeared deleted when it had never moved. Added rows were appended after
  the entire context window instead of sitting at the replacement point, which
  read as code jumping downwards. Added rows were numbered as if the new text
  had the same line count as the old one, and trailing context kept its
  pre-edit numbering. On top of that, `replace_all` announced N replacements
  while showing only the first. The file on disk was always correct — only the
  report lied — but it cost a verification read after edits that were fine.
  The diff is now derived from the before/after texts (`difflib`, grouped
  opcodes, ±3 lines), covers every changed region, and announces truncation
  instead of silently capping.

### Changed

- **Claude Code's native tool calls are no longer hidden from the transcript.**
  The proxy and the turn coordinator both dropped the provider's own
  bootstrap/discovery calls — `GetSchema`, `ToolSearch`, and the `Read` of
  `.pawflow_cci/initial_context.md` — along with their results. It was meant to
  spare the transcript some noise, but it made a turn that opens by reading its
  own context render an empty technical-details block, and left no way to tell
  a deliberately suppressed call from a lost one — precisely the question a
  transcript exists to answer. The `is_hidden_native_tool` predicate and the
  whole `hidden` plumbing behind it (the observer's hidden-id set, the four
  `not block.get("hidden")` guards) are gone rather than neutralized: what the
  agent did is what the transcript shows.

## [1.0.0-beta.34] — 2026-07-27

### Fixed

- **beta.33 broke the chat UI entirely** — the whole page was dead: history
  never rendered, every action hung "in progress" forever, and the browser
  console showed a 404 on `GET /api/agent/events?conversation_id=__ui__:tab-…`.
  The SSE authorization added in beta.33 assumed every `conversation_id` on
  that endpoint is a conversation. It is not: the chat UI opens a second
  stream per browser tab on `__ui__:<tab id>` and routes the `command_result`
  of *every* `action$()` call through it, deliberately decoupled from the
  conversation stream (which `resumeConv` closes and reopens around history
  rendering). That id has no owner and no row on disk, so `require_read` could
  only ever deny it — the gate 404'd the UI's own command bus. The channel is
  now recognized (`is_ui_bus_channel`) and exempted from the per-conversation
  check; it still requires an authenticated requester, and real conversations
  are gated exactly as before.
- A `token` SSE event published without a `msg_id` is now refused
  (`ValueError`) instead of producing an anonymous streaming bubble. Tokens
  accumulate into a bubble that is reconciled against the transcript by
  `msg_id`; with no id, only the turn-ending event could pair it with the
  persisted line, so losing that event made gap reconciliation render the
  stored message beside the bubble already on screen — the same answer twice.
  This closes the residual risk introduced with reconciliation in beta.33: no
  emitter could reach it (the single one stamps a uuid, and Claude Code
  publishes no tokens at all), but nothing enforced it either. A refused event
  costs the live preview only; the message still arrives persisted.

## [1.0.0-beta.33] — 2026-07-27

### Fixed

- Message pipeline audit — three more defects on the live channel, all of
  them invisible in the transcript (the stored conversation was never
  wrong; only the live view was):
  - **Lost messages.** The `done` handler registered every `msg_id` of the
    turn as "already displayed" to guard against replay duplicates. An id
    whose event never arrived — socket down during the gap — was recorded
    as displayed anyway, so `addMsg` refused its later delivery too and the
    message stayed invisible until a manual reload. Only ids that actually
    reached the DOM are marked now.
  - **Unrecoverable gaps.** Nothing healed what the server published while
    a socket was down: events accepted by a half-open writer are never
    buffered (`send()` returned true, so the bus counted them delivered),
    and the watchdog / health-timer reconnects pass `replay=false`, which
    skips the buffer on purpose. A reconnect that follows a real drop now
    re-reads the transcript tail and renders only what is missing —
    idempotent by `msg_id`, and each recovered message is inserted at its
    own server timestamp rather than appended at the end.
  - **Phantom message at the end of a turn.** The token handler appended to
    the stream buffer before creating its element; when `addMsg` refused
    (that `msg_id` was already on screen), the text stayed in a stream with
    no element, and neither reset site clears it — both are guarded on the
    element existing. `done` falls back to that buffer when nothing of the
    turn rendered, resurrecting the text as a duplicate bubble.
- Buffered SSE events were replayed regardless of age: `_BUFFER_TTL` was
  applied only when appending to a buffer, and expired a conversation's
  buffer on its *newest* entry — so a buffer that kept receiving events
  never shed the old ones. A subscriber could be handed an event minutes
  old and render it as if it had just happened. The TTL is now enforced at
  delivery, and stale rows are pruned as they are found.

- The first message of a turn jumped below the final answer once the turn
  ended, and a page reload put it back where it belonged. Nothing was
  re-sent and no event was replayed: the `done` handler looks up the DOM
  element the turn's final metadata belongs to by scanning `all_msg_ids`,
  but it scanned oldest-first and stopped on the first match. On a CLI
  provider a turn persists several messages (a narration, tool calls, then
  the answer), so it matched the turn's OPENING line — stamped the whole
  turn's tokens/cost/duration onto it, then re-sorted it to the `done`
  timestamp, physically moving it to the bottom. The scan now runs
  newest-first, and the re-sort is restricted to the live streaming
  placeholder, the only element positioned with the browser's clock rather
  than a server timestamp of its own.

- Images sent to a vision model while the agent was already running (the
  preempt path) were never downscaled: `_build_user_content` resizes on
  ingestion, but preempt hands the raw `file_id` to the provider, and the
  three sites that materialize a file on disk (Claude Code interactive,
  antigravity, Codex) wrote the original bytes — so the agent opening the
  file itself hit the provider's "exceeds 2000x2000" rejection. New
  `core.image_resize.write_vision_image()` downscales and names the file
  after the encoding actually written (a re-encoded PNG becomes `.jpg`).
- Mobile chat UI: the top header was pushed off-screen and technical text
  was unreadably small. `height: 100vh` on `body`/`.sidebar` is the
  URL-bar-hidden height, i.e. taller than the real viewport, and with
  `overflow: hidden` the overflow was unrecoverable. Switched to `100dvh`
  (with a `100vh` fallback) and added a mobile type scale (+1–2px per
  level, full-width bubbles, `.tc-output` 11 → 12px). The composer is now
  16px, which also stops iOS auto-zoom on focus.
- A tool call interrupted mid-flight stayed "pending" forever (spinner +
  →BG/✘ buttons, no result): `interrupting` was the only turn-ending SSE
  path that did not finalize in-flight tool calls, and since the turn
  keeps running, no other finalizer fired either. It now finalizes them,
  and synthesized results (`[Interrupted]`, `[Stopped]`, `[result not
  delivered]`) are marked as placeholders so a real result arriving late
  replaces them — never the reverse.

### Security

- The live SSE stream (`GET /api/agent/events`) never checked whether the
  requester may see the conversation it streams: `validate_auth` upstream only
  proves *who* is asking, and the task read `conversation_id` straight from the
  query string. Any logged-in user who knew or guessed a `conversation_id`
  could subscribe to someone else's conversation. `agentSSEStream` now
  resolves read access before subscribing and answers a rejection with the same
  404 an unknown `conversation_id` gets, so it cannot be used to probe which
  conversations exist. A request with no trusted principal is rejected too.
- The conversation actions that resolve a conversation by id alone were open
  to any logged-in user who knew that id: `set_conv_title` renamed it,
  `conv_encrypt_*` / `relay_workspace_*` could enable, lock or disable its
  encryption, and `poll` leaked its message count. Access to the owner's
  directory was never the barrier it looked like — only the actions that pass
  `user_id` down to storage (`load_history`, `search_messages`,
  `delete_conversation`) were partitioned by path. Every action in
  `_conv_core.py` that names a conversation now resolves
  `require_read`/`require_write`/`require_owner` first (phase 3 below) and
  answers a rejection with the unknown-conversation 404.

### Added

- Conversation sharing, phase 3 — sharing is now reachable.
  `tasks/ai/actions/_conv_sharing.py` adds `share_conversation`,
  `respond_to_share_invite`, `list_collaborators`,
  `update_collaborator_role`, `kick_collaborator`, `leave_conversation` and
  `list_shared_conversations`. Inviting is owner-only and two-sided (the owner
  can only create a `pending` row; access starts when the invitee accepts),
  kicking keeps the row for the audit trail, and every rejection is the same
  404 an unknown `conversation_id` gets. The `_conv_core.py` handlers now
  address storage with the resolved *owner's* id, so an accepted collaborator
  reads and writes the one shared conversation rather than a copy — encryption
  management and deletion stay owner-only. No frontend yet (phase 7).

- Conversation sharing, phase 1 (`core/conversation_access.py`): the
  authorization primitive every call site will consult instead of its own
  owner-equality check — `resolve_conversation_access` / `require_read` /
  `require_write` / `require_owner`, the collaborators ACL stored via the
  existing `extra` mechanism (no schema change), and a per-user reverse index
  of conversations shared with them. `ConversationStore` gains the read-only
  `resolve_owner()` and `shared_index_path()`. Its only consumer so far is the
  SSE fix above — sharing itself is not reachable yet, there is no action to
  invite anyone; see `docs/CONVERSATION_SHARING_PLAN.md`.

## [1.0.0-beta.32] — 2026-07-27

### Fixed

- Action-menu (`+`) dropdown could be cut off with no way to scroll on
  short/narrow viewports (`position:absolute`, no height limit). Now
  `position:fixed`, clamped to the viewport, scrollable, and positioned/
  flipped in JS relative to the button instead of a static corner.
- Message/thinking-block ordering could land new content above older,
  correctly-timestamped history whenever the browser and server clocks
  disagreed (`_insertMessageChronologically` was comparing a client
  `Date.now()` fallback against real server timestamps). Only genuine
  server timestamps are now compared against each other; content with no
  real timestamp always appends at the true end. A buffered `error_event`
  replayed after a client reconnects now renders at its true original
  time instead of whenever the replay happened to land.
- OAuth refresh race: `_refresh_oauth_token_coordinated` released its
  per-slot lock before the caller persisted the rotated tokens, letting a
  concurrent session read the pool in that gap, miss the rotation, and
  re-POST an already-consumed refresh token — a real `invalid_grant`
  rejection that dropped a perfectly good credential. Persistence now
  happens inside the lock.
- Telegram (and any other `AgentResultWaiter`-based bridge) never
  surfaced fatal LLM errors (credential expiry, session lost, budget
  exceeded): `on_fatal_error` published `error_event` without
  `turn_id`/`request_msg_id`, so the waiter couldn't correlate it back to
  the pending request and callers silently got the turn's empty response
  instead of the real error text.
- A fatal-error's synthetic assistant message was marked `is_error=True`
  but not `display_only=True`, so it could resurface as fake prior
  assistant content in the LLM context on a later turn. Now excluded from
  context (still visible in the transcript) via the same mechanism used
  for `sub_agent_trace`/delegate nudges.
- Mobile-responsive chat UI: `.header`/`.header .actions` now
  `flex-wrap` under 768px instead of squeezing/overflowing; `.sidebar`
  becomes a `position:fixed` overlay under 768px instead of squeezing
  `.main`; ~20 fixed-pixel-width dialog panels across the chat UI now
  clamp to the viewport instead of overflowing horizontally on a phone.

## [1.0.0-beta.31] — 2026-07-27

### Added

- New PFP installable object type `mcp_server`: installs directly as a
  ready-to-use `mcp` resource with no manual reconnection step after
  install. Structural validation (an `http` server needs `url`, a
  `stdio` server needs `command`) and risk classification (high for
  `stdio`, medium for `http`-only), mirroring the existing
  `tool`/`service_provider` and `service_definition` object types.
- Chat UI: a right-side sliding panel (`task_tabs.js`) showing a single
  task's messages, opened from the Active Agents panel or the inline
  task-block header. `addMsg()`/`_getTaskBlock()` tag rendered elements
  with `dataset.taskId`; the panel clones the matching top-level nodes
  and stays live via a `MutationObserver`, with no re-fetch or parallel
  render path.

## [1.0.0-beta.30] — 2026-07-23

### Added

- `manageCalendar` task (`tasks/io/manage_calendar.py`): list/create/update/
  delete calendar events. Two providers: Google Calendar API v3 with the same
  OAuth2 refresh-token flow as `sendEmail` (client_id/client_secret/
  refresh_token), or generic CalDAV (Nextcloud, Radicale, iCloud, most
  self-hosted servers) over HTTP Basic auth using PUT/DELETE of iCalendar
  (.ics) resources and a calendar-query REPORT for listing.
- New PFP installable object type `web_app` (`webapp.v1`): a standalone
  page (html/js/css) served at its own authenticated route
  `/apps/<package>/<name>/`, separate from the chat page's `ui_extension`
  slot/hook contract — `ui_extension` never ships `.html` and never gets
  its own URL, `web_app` does both. New task `servePfpWebAppAssets`
  (`tasks/io/serve_pfp_webapp_assets.py`), wired into the default
  `pawflow_agent` flow behind the same session auth gate as `/chat`. The
  Packages section of the Resources sidebar shows a ↗ link to any
  installed package's web_app pages. See `docs/PFP_DEVELOPER_GUIDE.md`
  ("Standalone Pages") for the manifest shape and trust model.

## [1.0.0-beta.29] — 2026-07-20

### Added

- Spend budgets (`core/budget_store.py`): cumulative daily/monthly caps
  scoped to a user, conversation, agent, LLM service, or globally, with a
  `warn` (notify only) or `block` (refuse the next turn on that scope)
  policy. Enforcement checks period-to-date REAL spend from the usage
  ledger once per external agent turn (subscription/virtual cost never
  counts); notifications fire at 50/80/100% of the limit into the
  triggering conversation, deduplicated per period. Managed via a new
  "Budgets" section on the dashboard (admin-only create/delete, progress
  bars for everyone budgets apply to) or `budget_list`/`budget_create`/
  `budget_update`/`budget_delete` actions. Distinct from the existing
  per-turn `max_budget_usd` LLM-service safety cap.
- Global "Usage & Costs" dashboard (header action menu): KPI cards
  (today/7d/30d, tokens, cache-hit rate, 30-day projection), a stacked
  daily bar chart (canvas, no external charting dependency) stackable by
  LLM service / agent / model / channel, and top-10 conversations/agents
  by cost. Bars fall back to tokens when the window has no priced usage.
  Admins get an "All users" toggle. Backed by a new bundled
  `usage_dashboard` action on the usage ledger.
- `subscription` flag on `llmConnection`: usage from a flat-rate service
  (Claude Code / Codex / Gemini subscription login) is recorded as
  `virtual_cost_usd` in the usage ledger instead of `cost_usd` — real spend
  stays $0 and budgets never count it, while `cost_per_1m_*` rates still
  drive a "what this would have cost via API" figure surfaced everywhere
  the ledger is read (badge, panel, summary/timeseries/top/export).
- Persistent usage/cost ledger (`core/usage_ledger.py`, SQLite at
  `data/system/usage.db`): every LLM call is recorded as ONE event with
  full dimensions (user, conversation, agent, llm_service, model,
  provider, channel) and the cost FROZEN at the service rates in effect
  at call time. Replaces TokenTracker (JSON aggregates, no conversation
  dimension) and CostTracker (in-memory, lost on restart); the legacy
  `token_usage.json` is imported once as synthetic `migrated` events.
  Newly attributed traffic that was previously invisible: sub-agent runs
  (`delegate`/`flash_delegate`, channel `subagent`, priced from the
  sub-agent's own service), realtime LiveKit sessions (structured token
  metrics from the worker, channel `realtime`), aggregator advisors, and
  internal calls such as title generation (channel `system`).
- Live conversation cost gauge in the webchat header: a `usage.updated`
  SSE event is published after every turn (task sub-conversations roll up
  to the parent), the badge shows the conversation's cumulative cost and
  opens a breakdown panel — totals, by agent / channel / model with bars,
  and the most recent turns. Backed by a new `usage_conversation` action
  and conversation-prefix queries on the ledger.
- Usage query actions on the agent-loop API (`usage_summary`,
  `usage_timeseries` with hour/day/month buckets and one group-by
  dimension, `usage_top`, `usage_export` JSON/CSV) with period and
  dimension filters — non-admins scoped to their own user, admins can
  query any/all users. Foundation for the upcoming usage dashboards.
  See `docs/usage_tracking.md`.

## [1.0.0-beta.28] — 2026-07-19

### Fixed

- Corrected the spelling of the default first-run Private Gateway bootstrap key
  (`roy betty` → `roy batty`) everywhere it appears: code default
  (`DEFAULT_BOOTSTRAP_GATEWAY_KEY`), install scripts, installer flow, docs,
  README, website, and tests.

## [1.0.0-beta.27] — 2026-07-19

### Added

- Native Claude Code plugin support (`claude-code` / `claude-code-interactive`):
  new `claude_plugins` (`plugin@marketplace` ids) and `claude_marketplaces`
  (`name=owner/repo` or `name=<git-url>`) parameters on `llmConnection` —
  merged as `enabledPlugins` / `extraKnownMarketplaces` into the session's
  `.claude/settings.json` (all other keys preserved, CCI hook settings
  included); Claude Code auto-installs them on session start, and removed
  entries are cleared on the next session.
- Native Codex plugin support (`codex-app-server`): a new `codex_plugins`
  parameter on `llmConnection` (comma-separated names, optional
  `name@marketplace`, default marketplace `openai-curated`) emits
  `[plugins."<name>@<marketplace>"]` entries in the session's generated
  `~/.codex/config.toml`, enabling OpenAI's curated plugins (Linear,
  GitHub, Gmail, Calendar, ...) in OAuth-mode Codex sessions. The
  generated config.toml is now a managed section between markers —
  content codex or the user writes outside it (e.g. `codex plugin
  install` state in the persistent session slot) survives regeneration.

## [1.0.0-beta.26] — 2026-07-17

### Added

- Background delegate results are now spoken into live voice sessions:
  when a `flash_delegate`/`delegate` sub-agent finishes while its caller
  has an active realtime LiveKit session, the result is injected into
  the session as an out-of-band context message (voice-friendly wording)
  on top of the normal text-channel delivery.
- `consult_agent` tool (voice-front delegation): one-shot call to the
  conversation agent's own model — resolved system prompt + configured
  `llm_service`, bounded conversation context, answer returned as the
  tool result. Approval-exempt (the delegate gets no tools). With
  `tool_profile=consult_agent` on a realtime service, the realtime model
  becomes a thin spoken interface that routes substantial work to the
  agent's brain; long answers are spoken when they land via the detached
  tool path.

- Managed realtime stack (zero-config LiveKit): leaving `livekit_url`
  empty on a `realtimeVoiceConnection` service now makes PawFlow
  provision and supervise the LiveKit stack itself through the Docker
  socket — `pawflow-livekit` (generated API credentials, never
  devkey/secret) and `pawflow-livekit-worker` (dependency-only image
  built locally once, worker code bind-mounted from the server install).
  The worker deployment secret is generated and persisted encrypted;
  browsers connect same-origin through the new `/livekit` signal
  WebSocket proxy (works on HTTPS pages, no manual docker-compose or env
  variables). External LiveKit servers remain supported by setting
  `livekit_url` + API key/secret.

- Realtime LiveKit Gemini video bench (`spikes/livekit/bench/driver3.py`):
  synthetic camera track (red square) plus a spoken color question against a
  real `gemini` Live session — user/agent transcripts, correct color answer,
  and non-silent agent voice all asserted. Validates the native video-input
  path end to end.

## [1.0.0-beta.25] — 2026-07-17

### Added

- Realtime LiveKit local-pipeline bench (`spikes/livekit/bench/`): minimal
  OpenAI-compatible local STT (faster-whisper) and TTS (piper) servers plus a
  `BENCH_PROVIDER=local_pipeline` mode of the fake control plane — the full
  zero-cloud-audio loop (Silero VAD + turn-detector + local STT/TTS + text
  LLM + tool round-trip) validated end-to-end with real speech.

### Fixed

- Realtime LiveKit worker: `local_pipeline` TTS model default changed from
  `kokoro` to `tts-1` — the OpenAI TTS plugin picks its wire format from the
  model name, and any non-`tts-1` name selects SSE streaming, which
  kokoro-fastapi/speaches-style local servers do not implement (the session
  then produces no agent audio at all).

## [1.0.0-beta.24] — 2026-07-17

### Added

- Realtime LiveKit P0 spike (`docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`):
  new optional dependency group `pawflow[realtime-livekit]` guarded by
  `services/livekit_deps.py` (clear setup error when absent), docker-compose
  `realtime` profile (self-hosted LiveKit dev server + `livekit-worker`
  sidecar built from `docker/livekit-worker/Dockerfile`), and spike scripts
  under `spikes/livekit/`: OpenAI Realtime voice hello-world, Gemini Live
  video hello-world with a synthetic-frame publisher, and the worker-control
  WebSocket prototype (`control_protocol.py`) with a fake tool-call
  round-trip — protocol covered by CI tests
  (`tests/test_livekit_spike_control.py`), no live provider calls. Also:
  local pipeline spike (`spike_local_pipeline.py`, the OpenLive-shaped
  zero-cloud-audio cascade — Silero VAD + turn-detector + local
  OpenAI-compatible STT/TTS + any text LLM) and `SPIKE_VIDEO=1` on the
  OpenAI spike (gpt-realtime image-input frame path).
- Realtime LiveKit P1 (service + session API): `realtimeVoiceConnection`
  gains `engine: livekit` with full config validation and a compatibility
  loader mapping legacy configs (`protocol`→`provider`,
  `vad`→`turn_detection`); scoped JWT tokens (browser room token with
  minimum grants and 15-min-capped TTL, agent room token, PawFlow-signed
  worker-control token); session registry with one active session per
  conversation and force-stop integration; `POST
  /api/realtime/livekit/start`/`stop` endpoints and the
  `/ws/realtime-worker/{session_id}` control WebSocket speaking the
  promoted `services/_realtime_worker_protocol.py` contract; `realtime.*`
  events on the conversation event bus. 40 new unit tests
  (`tests/test_livekit_engine.py`); docs in `services.md` and
  `security_model.md`.
- Realtime LiveKit P2 (worker MVP + tools + transcripts): new sidecar
  worker package `pawflow_livekit_worker/` (automatic LiveKit dispatch,
  PawFlow bootstrap fetch, provider `AgentSession` for
  openai/gemini/local_pipeline, proxied function tools, session cap);
  `POST /api/realtime/livekit/worker/bootstrap` endpoint guarded by the
  `PAWFLOW_REALTIME_WORKER_SECRET` deployment secret, resolving provider
  credentials server-side (never to the browser); worker tool calls now
  run through the existing `RealtimeToolBridge` (silent approval, long
  tools detach to `context` injection); final transcripts persist as
  normal conversation messages. 13 new tests
  (`tests/test_livekit_worker_p2.py`).
- Realtime LiveKit P3 (webchat live panel): the conversation mic button now
  routes `engine: livekit` services through WebRTC — vendored
  `livekit-client` 2.20.1 served at `/api/realtime/livekit/sdk.js`
  (lazy-loaded), new `conversation_livekit.js` reusing the voice overlay
  with camera/screen-share controls gated by `video_input`, live
  captions/state/tool activity from `realtime.*` SSE events, and a
  LiveKit/provider badge in the voice settings panel. Legacy-engine
  services keep the PCM bridge until the P5 retirement window. 8 new tests
  (`tests/test_livekit_ui.py`).
- Realtime LiveKit P4/P6/P7 config: `video_fps_active`/`video_fps_idle`
  frame-sampling service keys (worker applies them via
  `VoiceActivityVideoSampler`), worker provider mapping for `azure_openai`
  (OpenAI plugin Azure mode), `xai` (api.x.ai OpenAI-realtime endpoint) and
  `aws_nova` (guarded `livekit-plugins-aws` import with a clear install
  error), and `local_stt_url`/`local_stt_model`/`local_tts_url`/
  `local_tts_model`/`local_tts_voice` service keys so the zero-cloud-audio
  local pipeline is configured per service instead of per worker env.
- CUA screen mode phase 2 (AX-first addressing, `docs/CUA_MODE_PLAN.md`):
  new `screen` tool actions `windows` (window list), `window_state`
  (accessibility-element tree + grounding screenshot) and `status` (backend
  health), and `click`/`double_click`/`type` now accept `element_index` with
  `pid`/`window_id` to act on an element by AX identity — works on
  backgrounded/minimized windows, no coordinates or screen revision needed
  (a stale element yields a structured driver error instead of a blind
  click). The relay container path (`tools/fs_screen.py`) now routes through
  cua-driver too when `PAWFLOW_SCREEN_MODE=cua` — AT-SPI element trees on
  the Xvfb desktop with zero pointer contention with VNC users; the default
  xdotool backend is unchanged. Documented in `docs/desktop_vnc.md`.
- Desktop-capable relay images now bundle `cua-driver`: install step in the
  `desktop.runtime` feature of `config/relay_image_catalog.json` (relay image
  version `2026.07.16`) and in `docker/relay-dev/Dockerfile`, ready for CUA
  screen mode inside relay desktops (container dispatch lands with phase 2).
- Realtime multimodal plan: added a local pipeline profile to cascade mode
  (`docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`) — LiveKit local VAD/STT/TTS
  plugins + turn detector for full-duplex, zero-cloud-audio voice with any
  text `llmConnection`, composing with the vision fallback for video frames.
- CUA screen mode (phase 1 of `docs/CUA_MODE_PLAN.md`): with
  `PAWFLOW_SCREEN_MODE=cua`, relay-host `screen_*` actions route through
  [cua-driver](https://github.com/trycua/cua) desktop-scope tools (CLI form,
  per-action subprocess) for background computer use — the real cursor never
  moves and focus is not stolen. Per-agent overlay-cursor sessions
  (`PAWFLOW_CUA_SESSION`), pre-click screen guard unchanged, structured
  refusals surfaced verbatim (no silent foreground fallback), and a new
  `screen_status` action exposing the driver health report. Default backend
  is unchanged.

- Skill learning loop (P1–P3 of `docs/LEARNING_LOOP_PLAN.md`):
  - Agents now receive a `## Skill loop` system-prompt block instructing them
    to crystallize novel multi-step procedures into skills via
    `manage_resource` and to update skills that proved wrong during use.
  - Post-compaction extraction also asks the summarizer whether the summary
    contains a reusable procedure not covered by an existing skill; approved
    drafts are stored as conversation-scoped memories tagged `skill-draft`
    (never auto-installed).
  - `load_skill` records per-skill usage statistics
    (`data/runtime/skill_stats.json`), appends a self-improvement footer, and
    suggests promoting a conversation-scoped skill to user scope after
    repeated loads.
  - New `skillCurator` flow task: flags never-loaded and stale skills, runs an
    optional LLM review (keep/archive/merge), and emits a JSON report.
    Report-only — actions are applied by the user after review.

### Fixed

- Secrets added mid-conversation now reach tool executions immediately: the
  tool-relay secrets env/redaction caches stored a config fingerprint but
  never compared it on cache hits, so a newly added secret was neither
  injected into `$VAR` env nor redacted from output until a server restart.
  The fingerprint is now checked once per execution (shared between the env
  and redaction caches — hot path unchanged). Regression tests in
  `tests/test_tool_relay_secret_cache.py`.

## [1.0.0-beta.23] — 2026-07-13

### Fixed

- Webchat history pagination now uses the oldest rendered message as a cursor,
  so **Load more** returns the immediately adjacent older messages even when
  live-render trimming or technical message groups made the numeric offset drift.
- `LLMConnectionService` now reads its service id from the config injected by
  the registry instead of a never-set instance attribute, restoring per-service
  API-key-pool stickiness (`llm_api_key_idx:<id>` no longer collides across
  services) and the vision-fallback self-reference guard.
- The LLM aggregator injects advisor reports into the last user message instead
  of appending a trailing system message, which Anthropic-API connections
  treated as a replacement for the agent's system prompt and CLI session
  serialization dropped entirely. The Anthropic message builder now also
  concatenates multiple system messages instead of keeping only the last one.
- Read-only advisor conversations register their permission mode in an
  in-process `ToolApprovalGate` registry: `set_extra` silently no-ops for
  ephemeral (never-persisted) conversations, so CLI-provider advisors reaching
  tools through the MCP relay were not actually restricted to the fail-closed
  read-only allowlist. The registration is removed when the run finishes.
- CLI tool-result truncation now exempts results carrying inline
  `__image_data__:` payloads (same rule as the ToolRegistry cap), so oversized
  screenshots are no longer cut mid-base64.

## [1.0.0-beta.22] — 2026-07-13

### Added

- Added the `llmAggregator` service. It consults multiple direct
  `llmConnection` advisors in parallel, injects their internal plans into a
  separate final LLM, and exposes the composite anywhere an agent accepts an
  LLM-capable service.
- Added `best_effort` and `fail_fast` advisor failure policies, configurable
  concurrency and iteration limits, per-turn report reuse, coordinated aborts,
  and separate advisor usage/cost accounting that does not inflate the main
  context gauge.
- Added a complete multi-LLM setup guide, README example, website how-to, and
  documentation-hub links.

### Security

- Advisor sub-contexts are silent, isolated, and ephemeral. Read-only
  enforcement is enabled by default with a fail-closed tool allowlist, including
  CLI-backed providers through their scoped MCP context; interactive and
  state-mutating tools remain available only to the final LLM under the normal
  conversation approval policy.

## [1.0.0-beta.21] — 2026-07-13

### Fixed

- Telegram slash commands no longer expose raw command-result JSON or internal
  `client_only` envelopes. Supported local commands execute in the Telegram
  client, unavailable UI operations return actionable guidance, and unknown or
  failed commands return an explicit human-readable error.
- `/new` now opens Telegram's native conversation wizard, and
  `/conversations` is wired as a functional alias of `/conv` in Telegram,
  PawCode, webchat, and the VS Code extension.
- PawCode now treats `/conv list` as a conversation listing instead of trying
  to select a conversation whose ID is `list`.

## [1.0.0-beta.20] — 2026-07-13

### Added

- Added end-to-end delegated-vision documentation, including a GLM 5.2 +
  Gemma 4 Cloud configuration guide, provider requirements, caching behavior,
  cost and security boundaries, and website discovery links.

### Changed

- `screen(screenshot)` and `see(screen)` now return an opaque screen revision.
  `click` and `double_click` require that revision and may accept a target
  bounding box so actions are tied to the exact image used for coordinate
  selection.

### Security

- Desktop clicks now compare a bounded reference crop with a fresh local relay
  capture immediately before any mouse input. A changed target returns
  `STALE_SCREEN` without moving or clicking; an unchanged target proceeds
  without another LLM or vision request. Revisions are scoped to the user,
  conversation, relay, and local or Docker display route.

## [1.0.0-beta.19] — 2026-07-13

### Fixed

- Vision fallback now works end-to-end in the agent loop. The
  `LLMConnectionService._maybe_apply_vision_fallback` method referenced
  `self._service_id`, which does not exist on `BaseService` instances — the
  attribute lives on `ServiceDefinition` (the registry wrapper). The resulting
  `AttributeError` was silently caught at DEBUG level, making the fallback
  appear to run but always return messages unchanged. Both call sites now use
  `getattr(self, "_service_id", "")` so the fallback proceeds when
  `supports_vision=false` and `vision_llm_service` is configured. Image
  attachments and `see`/`read` tool results are now described by the vision
  service before reaching a non-vision LLM.
- Escalated vision fallback exception logging from DEBUG to WARNING so silent
  failures are immediately visible in server logs.
- Added early-return diagnostic logging in `apply_vision_fallback` to identify
  which guard (recursion, no images, self-reference, unresolved service) skips
  the description pass.

## [1.0.0-beta.18] — 2026-07-13

### Fixed

- Corrected cold-session context accounting for Codex app-server, Gemini ACP,
  Claude Code, Claude Code interactive, and Antigravity interactive. The
  serialized PawFlow context is replaced at the first native bootstrap read
  boundary, so only content actually loaded into the provider context is
  counted and chunked reads remain additive.
- Invalidated stale context-usage snapshots created by the previous accounting
  formula, preventing an incorrect near-full gauge from surviving a restart.
- Suppressed Bandit's B105 false positive for the
  `conv_encrypt_passwd` action identifier without changing the public command
  contract.

## [1.0.0-beta.17] — 2026-07-13

### Changed

- Unified domain slash-command parsing and human-readable result rendering on
  the server for webchat, Telegram, PawCode, and the VS Code extension. Client
  implementations now retain only transport-specific UI operations.
- Reserved `/audio` for server-side audio generation across clients and
  renamed the webchat relay-stream control to `/relay-audio`.

### Fixed

- Restored broken or mismatched routing for conversation, agent, task, flow,
  memory, tool, media, debug, loop, hook, and resource commands.
- Preserved complete multi-word text in `/memory add` and `/memory search`,
  and aligned PawCode `/msg` and `/btw` target parsing with the shared
  syntax, accepting both `agent` and `@agent`.
- Added consistent `display` output without suppressing structured client
  state updates such as conversation switching after `/fork`.
- Extended vision-fallback diagnostics to report early-return reasons.

## [1.0.0-beta.16] — 2026-07-13

### Fixed

- Pre-cache tiktoken `cl100k_base` BPE file at Docker build time in a persistent
  path (`/app/data/tiktoken_cache`) so token counting never needs network at
  runtime. The entrypoint seeds the cache into the `/app/data` bind mount on
  first boot, preventing a `/tmp` cache wipe + network failure from permanently
  degrading the context gauge.
- Added diagnostic logging to the vision fallback path in the agent loop.
  Both `_alc_apply_vision_fallback` and `LLMConnectionService._maybe_apply_vision_fallback`
  now log the `supports_vision`, `vision_llm_service`, and image detection
  results so a misconfigured fallback is immediately visible in server logs.

## [1.0.0-beta.15] — 2026-07-13

### Fixed

- Context gauge no longer inflates artificially for CLI providers (Codex,
  Claude Code, Gemini, CCI, Antigravity). Tool results arriving through the
  live `block_callback` and `turn_callback` paths were persisted without the
  `tool_result_max_chars` cap (default 50K chars) that `ToolRegistry.execute`
  applies to PawFlow MCP tools. Native Codex tool outputs (e.g. `cat
  initial_context.md`) were stored at full size, duplicating the serialized
  context and causing the gauge to jump from 19K to 80K+ tokens after a single
  cold-start file read. Both callback paths now truncate to the configured
  limit before persistence.
- `tiktoken` encoding failures are no longer permanent. A transient network or
  cache issue at startup previously set `_encoding_failed = True` forever,
  making every subsequent token count use the approximate fallback
  `(bytes + 3) // 4`, which overestimates by 1.1–2x depending on content and
  inflates the context gauge. The flag is now a monotonic timestamp with a
  5-minute retry window, so tiktoken is re-attempted and the precise
  `cl100k_base` tokenizer is used as soon as it becomes available.

## [1.0.0-beta.14] — 2026-07-12

### Fixed

- Multimodal prompt-token fallbacks now use the shared message counter instead
  of measuring Python's serialized image blocks. Image transport bytes no
  longer inflate context usage when a provider omits usage metadata; only text
  content and message overhead are estimated across API and CLI providers.
- Delegated result-shape resolution now supports minimal tool registries that
  expose `list_tools()` without `get()`, restoring targeted cancellation for
  direct API-provider tool execution.
- The Claude Code interactive no-proxy timeout regression test now patches the
  coordinator's owning module, preventing the test from waiting for the
  production 300-second timeout.

## [1.0.0-beta.13] — 2026-07-12

### Fixed

- Image-producing tools invoked through `use_tool` now preserve oversized
  image payloads across the registry safety cap, agent multimodal conversion,
  and MCP relay serialization. The effective delegated handler is resolved
  through aliases and nested wrappers while retaining the trusted
  `_returns_images` gate, so ordinary text containing an image marker remains
  capped.

## [1.0.0-beta.12] — 2026-07-11

### Added

- Pocket TTS (`pocketTTS`) local text-to-speech service for on-device voice
  generation without external API dependencies.

### Fixed

- Vision fallback now triggers in the agent loop when the active LLM lacks
  native vision support but has a `vision_llm_service` configured. Previously
  the fallback only ran through `LLMConnectionService.complete[_stream]`,
  which the agent loop bypasses by calling `LLMClient` directly — so image
  attachments and `see`/`read` tool results were never described for
  non-vision models. The fix delegates to the resolved service's existing
  `_maybe_apply_vision_fallback` from both the main LLM call and the
  interrupt-handling path.
- Tool-result image materialization (`_materialize_tool_result_images`) now
  logs the exception when FileStore storage fails instead of silently
  returning `[image omitted: failed to store image result]`.

## [1.0.0-beta.11] — 2026-07-10

### Fixed

- Claude Code interactive and Anthropic streaming now buffer indexed Anthropic
  content blocks independently, so interleaved `thinking` and `text` blocks no
  longer flush out of order or split visible words across Telegram messages.
- Telegram conversation forwarding now treats `thinking_delta` as a transient
  preview until the durable `thinking_content` arrives, preventing broken
  fragments such as partial reasoning sentences from being posted before tool
  calls.

## [1.0.0-beta.10] — 2026-07-10

### Added

- Native Tripo3D (`tripo3DGeneration`) and Meshy AI (`meshy3DGeneration`)
  services against the vendor APIs: text-to-3D (Meshy preview + refine
  workflow), image-to-3D, rigging, animation (Meshy action ids, Tripo
  presets incl. quadruped/hexapod/serpentine variants), retexture, and
  Tripo convert/stylize. New agent tools `rig_3d_model`,
  `animate_3d_model` and `retexture_3d_model`; `generate_3d` now surfaces
  the vendor `task_id` for chaining. See `docs/tripo_meshy.md`.
- Vision fallback for non-vision LLMs: an `llmConnection` with
  `supports_vision: false` can name a `vision_llm_service` that describes
  incoming images (OCR, UI elements with coordinates) before the messages
  reach the non-vision model, with memory + disk caching by image hash.
  `supports_vision` is now configurable for all providers, including the
  CLI ones whose `base_url` may point at a non-vision model.
- Ollama cloud free-tier presets: live model listing from
  `https://ollama.com/v1/models` in the service panel and install wizard,
  plus documentation for the free out-of-the-box setup path.

## [1.0.0-beta.9] — 2026-07-06

### Added

- Documented the LiveKit-first realtime migration plan. Future realtime work now
  targets `realtimeVoiceConnection` with a LiveKit engine, sidecar worker,
  worker-control WebSocket, feature-by-feature migration matrix, scoped LiveKit
  tokens, ConversationEventBus/SSE event reuse, and explicit retirement of the
  custom provider protocol bridge after parity.

### Fixed

- OpenAI vision-rejection retry tests now match the current `_http_post`
  signature that receives a per-call `base_url`, keeping relay-aware URL
  handling covered in CI.

## [1.0.0-beta.8] — 2026-07-06

### Fixed

- Relay-aware provider URLs now mint `/relay-proxy/...` links against the
  listener's private address and keep the route `private_only`, so leaked proxy
  URLs cannot be used from the internet. HTTPS listeners are supported by
  skipping certificate hostname verification only for that internal private
  `/relay-proxy/` hop.
- Token counting no longer fails at import time when `tiktoken` cannot download
  its `cl100k_base` BPE file in CI or offline environments; PawFlow falls back
  to a deterministic local estimate.

## [1.0.0-beta.7] — 2026-07-06

### Added

- Relay-aware provider URLs now support native `relay://<relay>/<host>:<port>/path`
  and `relays://<relay>/<host>:<port>/path` forms. Legacy
  `http(s)://<relay>/<host>:<port>/path` URLs remain supported.

### Fixed

- OpenAI-compatible relay streaming now resolves the relay base URL once per
  request, preventing repeated proxy token minting and inconsistent stream
  routing. Broken relay streams fall back to non-streaming completion with
  redacted diagnostic logs.
- MCP HTTP relay URLs now preserve conversation scope when minted at discovery
  time and re-minted at execution time.

## [1.0.0-beta.6] — 2026-07-06

### Fixed

- Relay-routed `llmConnection` base URLs now expose an explicit `relay_local`
  mode. Docker relays can route OpenAI-compatible endpoints such as Ollama at
  `http://<relay>/localhost:11434/v1` through the host helper, while container
  namespace targets remain available with `relay_local=false`.

## [1.0.0-beta.5] — 2026-07-06

### Fixed

- OpenAI-compatible `llmConnection` services can now reliably use relay-routed
  local endpoints such as `http://MyWorkspace/localhost:11434/v1`; per-call
  user/conversation identity is applied to isolated LLM clients before resolving
  the relay proxy URL.
- `/relay-proxy/...` now strips backend hop-by-hop streaming headers such as
  `Content-Length`, `Transfer-Encoding`, and `Connection`, preventing broken
  SSE/chunked responses from local OpenAI-compatible servers.
- `claude-code-interactive` custom `base_url` handling now supports custom
  HTTPS hosts/ports and local clear-HTTP upstreams behind the local TLS MITM.
- LLM client clone isolation is preserved while still allowing relay-aware URL
  resolution during `complete()` and `complete_stream()` calls.

## [1.0.0-beta.4] — 2026-07-06

### Added

- Flow lifecycle controls: `shutdownTrigger`, cron backpressure controls
  (`skip_if_pending`, `max_queue`), `manage_flow logs`, and
  `update_definition` hot-swap support make long-running flows easier to
  debug and stop cleanly.
- Relay-backed port forwarding is now surfaced through the PawFlow HTTP
  listener with absolute `/fwd/...` URLs in chat, plus slash-command support
  for listing, opening, and removing forwards by relay and visible port.

### Fixed

- Flow-deployed relay/script execution now preserves destination casing,
  injects user/conversation context into sandboxed tasks, and resolves relay
  filesystem services consistently.
- `httpListener` and `relay` help sheets now expose static parameter metadata,
  and the intentional `0.0.0.0` listener default is annotated for Bandit.
- chat-ui: inline audio players inserted in tool results never loaded (stuck
  at `0:00 / --:--`) in bearer-only sessions, while the same file played fine
  from the Files panel — the same 401 the inline video black-box bug had. The
  June revert of the authed-blob video fix also removed the audio half and it
  was never restored: the player used a raw `Audio(url)` src that sends
  neither the `pawflow_token` cookie nor the bearer header. Inline audio now
  fetches the bytes with the bearer header and plays from a same-origin blob
  URL, like the file viewer and inline images (video stays lazy-native, blobs
  would break range streaming there).
- `web_search` no longer returns a `ModuleNotFoundError` traceback when the
  connected relay's workspace is not the PawFlow repo: the relay payload
  imports PawFlow's `core` package, which only exists on the dev relay. A
  failed relay run (exception, non-zero exit, or empty output) now falls back
  to the server-side provider chain instead of surfacing the error as the
  search result.

## [1.0.0-beta.3] — 2026-07-04

### Added

- Realtime voice context (P3): voice sessions now start knowing what was
  discussed before — the `realtimeVoiceConnection` service's new
  `context_mode` (default `summary:2000`; `isolated` disables, `last:N` /
  `full` supported) appends conversation context to the session
  instructions, reusing the same context system as sub-agents
  (`resolve_context_messages`, extracted from the spawn handler). Applies
  to webchat sessions and Telegram voice-note turns.
- Gemini Live adapter (P3): `protocol: gemini_live` on
  `realtimeVoiceConnection` runs voice sessions through Google's Live API
  (`BidiGenerateContent`), with credentials from a `gemini` llmConnection.
  The adapter resamples PawFlow's 24 kHz uplink to Gemini's 16 kHz input
  (pure Python, no new dependency), maps `toolCall`/`toolResponse` onto
  the same PawFlow tool bridge, and handles server-side barge-in.
- Realtime session resumption (P3): when a provider drops a session whose
  adapter carries a resumption handle (Gemini Live
  `sessionResumptionUpdate`), the bridge reconnects transparently (max 2
  attempts) — the browser session, captions, and tool state survive the
  disconnect instead of ending with `provider_closed`.
- Voice settings panel (P3): right-click on the webchat mic button now
  opens a settings panel listing every realtime voice service with its
  model, voice, VAD mode, and context setting; one click selects, and the
  choice is remembered per conversation.

### Fixed

- The Gemini ACP warm-container fallback now only fires when the stored pool
  slot is missing (restart/compact), so an intentional slot change (rotation,
  slot removal) can no longer resurrect the previous account's live session.
  Codex and Gemini credential setup also no longer rewrite the pool when every
  refresh failure was transient (matching Claude Code), so a stale local copy
  cannot clobber a concurrent login.
- Gemini ACP live sessions now key warm containers by OAuth credential pool
  slot and recover compatible sessions when the stored slot is missing, so
  token recovery persists refresh-token changes back to the correct provider
  account. Conversation-scoped helpers also recognize `::flash::` sub-convs
  wherever normal delegate/task sub-convs were already supported.
- CLI provider credential setup now preserves freshly refreshed OAuth tokens
  when compacting dead pool entries, and reindexes the selected pool slot
  after purge so Claude Code, Codex, and Gemini teardown recovery write back
  to the correct account.
- Codex and Gemini OAuth refresh now distinguish rejected credentials from
  transient network/server failures, matching Claude Code behavior: temporary
  refresh failures no longer remove saved login slots from the provider pool.
- Delegate and `flash_delegate` sub-agents using CLI-backed providers now keep
  the parent conversation/service scope when resolving LLM clients, so one
  OAuth login shared through an `llmCredentialOAuthProvider` is reused instead
  of falling back to an empty credential pool.
- `flash_delegate` now receives the caller's source agent and `llm_service`
  context through the relay, matching normal `delegate` behavior. The Active
  Agents poll is also conversation-bound and rejects stale responses, so a
  delayed `list_active` response from another conversation cannot repaint the
  current conversation's active-agent panel or context gauge.

## [1.0.0-beta.2] — 2026-07-03

### Added

- Realtime voice conversation (P1): new `realtimeVoiceConnection` LLM-family
  service type — speech-to-speech sessions with a PawFlow agent through
  provider realtime APIs. Multi-provider by protocol adapter
  (`openai_realtime` covers OpenAI, Azure OpenAI, and compatible endpoints;
  credentials/base URL come from an existing `llmConnection`). The webchat
  gains a voice-mode button: continuous mic streaming and agent audio over an
  authenticated `/ws/realtime/{conversation_id}` WebSocket, live captions,
  barge-in, session/duration caps, and conversation-ownership enforcement.
  Final transcripts persist as normal messages so all attached clients see
  the exchange and the text agent resumes seamlessly. Design and phasing in
  `docs/REALTIME_VOICE_PLAN.md`.
- Realtime voice tools (P2a): the service's `tool_profile` exposes PawFlow
  tools to the voice model through a silent approval gate (new
  `ToolApprovalGate.check(allow_prompt=False)` probe — exempt/pre-approved
  tools run, anything needing a dialog is refused with a spoken
  explanation; `permission_mode` auto/read_only honored). Long tools detach
  to the background with an immediate interim result; the real result is
  injected back into the session, or persisted as a system message if the
  session already ended.
- Voice-native agents (P2b): agents can pin a `realtime_voice_service` in
  their conversation config (webchat agent editor). The webchat voice mode
  is now a full-screen overlay — state-reactive orb, live captions, tool
  activity, mute and hang-up — and a linked agent skips the service picker.
- Telegram speech-to-speech voice notes (P2c): a voice note sent to a
  voice-native agent is answered by a one-shot realtime turn (ffmpeg
  OGG/Opus ⇄ PCM16) — the reply is a Telegram voice note in the model's own
  voice, the transcript arrives as text through the live bridge, and the
  same tool bridge applies. Falls back to the STT pipeline on any failure;
  the bridge no longer synthesizes TTS on top of voice-channel transcripts.
- Voice mode UI: push-to-talk "Send" button for `vad=manual` sessions (the
  bridge announces the VAD mode in the `ready` frame), and the voice-service
  picker (right-click on the mic button) is now a clickable list instead of
  a `prompt()` dialog.

### Fixed

- Realtime voice stack hardened across ten review passes (26 findings):
  RFC 6455 fragmentation/reassembly and frame-size caps on both WS legs,
  provider-stream desync under mid-frame timeouts, session-cap starvation
  with a muted mic, force-stop wiring, `response.create` serialization
  against the active response (silent agent after fast tool calls),
  cross-session credential-scope race on shared service instances,
  question→answer transcript ordering in both VAD modes, Telegram
  double-processing of voice notes, ffmpeg timeouts, and socket/registry
  leaks on failed session opens.
- Audio WS proxy (`services/audio_proxy.py`, pre-existing): client frames
  are now reassembled per RFC 6455 fragmentation rules and capped at 16 MiB
  — a fragmented or hostile-length frame no longer corrupts the stream or
  buffers unbounded.

## [1.0.0-beta.1] — 2026-07-02

### Security

- chat-ui: the flow-instance editor error message is now HTML-escaped before
  being injected into the panel (last unescaped `innerHTML` error sink).

### Fixed

- FileStore: "Share public link" (`gateway_key` access) locked the owner out
  of their own authenticated access — the files panel "View" returned 403
  until the file was made private again. The gateway-key check now falls back
  to the owner check when no `?k=` is presented; `check_access` is covered by
  tests across all five access levels.

### Changed

- Project status: alpha → beta (README badge, PyPI classifier, ROADMAP,
  PROJECT_SUMMARY, website fallback version metadata).
- `/rewind`: removed the dead, never-wired `summarize` mode stub that
  answered "Not implemented yet"; summarize-from-checkpoint remains covered
  by `/compact`.
- docs: `media_tools.md` documents all `openaiCompatibleVideoGeneration`
  video modes, the configurable source-media body field names, and the
  config-only AtlasCloud Wan 2.7 recipe.

## [1.0.0-alpha.61] — 2026-06-30

### Added

- attachments: document payloads are converted to bounded Markdown context with
  MarkItDown when available, including PDF/DOCX/XLSX/PPTX-style inputs, and the
  dependency is declared in both `requirements.txt` and `pyproject.toml`.

### Fixed

- Telegram: document attachments and vision descriptions are preserved through
  the agent handoff instead of losing context before the model sees them.
- web_search: Claude-style `q` and `maxResults` arguments are accepted, Google
  search falls back to Chromium when static Google HTML is no longer parseable,
  and the server image installs the Patchright browser runtime needed by that
  fallback.

## [1.0.0-alpha.60] — 2026-06-28

### Fixed

- chat-ui: `show_file` video previews still rendered as a permanent black box
  while the same video from `generate_video` played fine. The `.58` lazy-load
  fix deferred wiring via a captured element id (`getElementById('vid_...')`),
  but inline tool-result media is reparented by technical grouping and can be
  re-rendered/replaced before the deferred pass runs — the captured id then
  pointed at an orphaned node while the *visible* `<video>` was never observed.
  Wiring is now a DOM sweep (`hydrateLazyVideos`) that observes whatever lazy
  `<video[data-lazy-src]:not([src])>` is actually present, re-run after every
  regrouping, so it survives reparenting and re-render.

## [1.0.0-alpha.59] — 2026-06-27

### Added

- openaiCompatibleVideoGeneration: full support for every video mode, not just
  text-to-video. The service now exposes `image_to_video`, `frame_to_video`
  (first + last frame), `reference_to_video`, `video_edit`, `video_extend`, and
  `speech_to_video`, so `generate_video` calls carrying an image/video/reference
  are dispatched to the provider instead of being rejected by the handler's
  capability gate. Source-media body field names are configurable
  (`image_field`, `end_image_field`, `video_field`, `audio_field`,
  `reference_field`) — defaults keep the generic OpenAI convention
  (`image_url`/`end_image_url`); set them to `image`/`last_image`/`video`/`audio`
  for AtlasCloud Wan 2.7.

## [1.0.0-alpha.58] — 2026-06-27

### Fixed

- chat-ui: inline `show_file` video previews intermittently rendered as a black
  box, while the full-screen file viewer always worked. The `<video>` carried
  its `src` up-front, but inline tool-result media is regrouped (technical/task
  grouping runs right after each render) and can sit in a collapsed panel — the
  native loader would skip an element that was hidden or mid-reparent, so it
  never painted a frame. The src is now deferred to an `IntersectionObserver`
  that loads the video once it is visible and its DOM position has settled,
  keeping native streaming (HTTP range requests, no in-memory blob).

## [1.0.0-alpha.57] — 2026-06-26

### Added

- openaiCompatibleVideoGeneration: `minimal_submit_body` option — when enabled,
  the async submit sends only `{model, prompt}` (plus `image_url`/`end_image_url`,
  `extra_body`, `callback_url`), omitting `duration`/`aspect_ratio`/`resolution`/etc.
  for providers (e.g. AtlasCloud) that reject unknown body fields. Combined with
  the configurable `submit_path` / `status_path_template`, AtlasCloud's Wan/Kling
  Predictions API (`POST /model/generateVideo` → poll `GET /model/prediction/{id}`)
  now works as a pure-config integration over an `openai` `llmConnection`.

### Changed

- openai-compatible media services: the image/video URL extractors now also
  resolve a result URL from `outputs`/`output` arrays even when the (often
  signed) URL carries no recognizable file extension — matching the response
  shape of Predictions-style providers like AtlasCloud.

## [1.0.0-alpha.56] — 2026-06-26

### Security

- chat-ui: file URLs and ids are no longer interpolated into inline `onclick`
  JS strings (`openFileViewer('…')`, download/share/delete in the file context
  menu, inline image/video/markdown-file links). The browser HTML-decodes an
  attribute before parsing its JS, so an escaped `'` (`&#39;`) decoded back and
  could break out of the string — a DOM-XSS vector for attacker-influenced file
  names/URLs. Values now reach the handlers via HTML-escaped `data-*`
  attributes read from `dataset`, matching the existing inline-audio pattern.

### Fixed

- openai provider: `base_url` paths whose version segment carries a suffix
  (e.g. `/v1beta`, `/v2alpha` on Gemini-compatible gateways) no longer get a
  spurious `/v1` re-appended; a fully-qualified `…/chat/completions` base is
  still used verbatim.

### Changed

- chat-ui: `escapeHtml` is now a single canonical definition in `state.js`
  (loaded early) instead of duplicated in `conversations.js` and
  `messages_tools.js`, so the escaper can't be silently shadowed by a stale
  copy.

## [1.0.0-alpha.55] — 2026-06-26

### Fixed

- openai provider: a `base_url` whose version segment is not `/v1` (e.g.
  z.ai's `/api/paas/v4`) was rewritten down to `/v1`, breaking every request
  to such gateways. The existing version segment is now preserved.

### Changed

- chat-ui / vscode webview: oversized JavaScript modules were split so every
  file is ≤800 lines, with no behavior change (per-file `node --check` and the
  full test suite stay green):
  - `sse.js` (2034 lines) → `sse_state.js` + `sse_handlers_a.js` +
    `sse_handlers_b.js` + a slim `sse.js` shell. `connectSSE`'s per-connection
    state is hoisted to module globals and reset on each connect; the event
    handlers are registered via `_sseWireA()` / `_sseWireB()` on the shared
    `eventSource`.
  - `messages.js` → core + `_render` + `_tools` + `_markdown`.
  - `conversations.js` → core + `_io` + `_menu`.
  - `terminal.js` → engine + `terminal_commands.js`.
  - vscode webview `chat.js` → `chat.js` + `chat_handlers.js`.
  - `HELP_DATA` extracted into `commands_help.js`.

## [1.0.0-alpha.54] — 2026-06-25

### Fixed

- Telegram: agent reasoning was duplicated — the live streamed preview
  (`thinking_delta` fragments) appeared, then the same reasoning again as
  the durable `thinking_content` block. The Telegram bridge merged the two
  with `\n\n` separators between every delta, so the fragmented preview no
  longer substring-matched the clean block and dedup failed. The bridge now
  keeps the delta preview separate; the durable block supersedes it (a
  leftover preview with no block — e.g. a cancelled turn — is still
  flushed). Webchat was unaffected and keeps streaming thinking live.
- claude-code (`-p`) streaming: the assistant's explanatory text ("here's
  what I'll do") arrived in the transcript *after* the tool calls it
  preceded. tool_use/tool_result were persisted live via `block_callback`,
  but text was only emitted at the end-of-turn flush, so it surfaced last.
  Text (and any pending thinking) is now persisted live in emission order —
  `thinking → text → tool_use` — mirroring the interactive provider, with no
  double-persist at the flush.
- claude-code-interactive / claude-code: native file tools
  (Read/Edit/Write/Glob/Grep/NotebookEdit) are no longer disallowed — the agent
  can read its local PawFlow bootstrap and session files even with no relay
  connected (mirrors the codex provider). Bash/WebFetch/WebSearch and the
  MCP-shadowed tools stay blocked.
- claude-code-interactive: each live container now claims an exclusive OAuth
  credential slot (1 login = 1 concurrent container). Anthropic refresh tokens
  are single-use, so two concurrent containers sharing one slot raced and
  invalidated the loser's session; pool exhaustion now raises instead of
  sharing, and teardown recovers any CLI-rotated token back to its slot.
- claude-code (`-p`): the mid-turn preempt intermittently failed — it targeted
  the subprocess via singleton state (`_claude_proc` / `_result_emitted`)
  clobbered by concurrent background streams. It now resolves the target from
  the live session registry, like claude-code-interactive's `find_session`.

## [1.0.0-alpha.53] — 2026-06-20

### Changed

- Refactored the relay worker for maintainability. `pawflow_relay/worker.py`
  shrank from 2390 to 776 lines (−68 %) by extracting focused modules, all
  ≤ 800 lines: `_relay_desktop` (VNC lifecycle + WS tunnel), `_relay_dispatch`
  (the `execute_command` router), `_relay_codeserver` (code-server process +
  WS tunnel), `_relay_terminal` (`TerminalManager` PTY), `_relay_actions`
  (http_proxy + script sync), `_relay_fs_setup` (combined FUSE mount), and
  `_relay_conn` (WS connect + handshake). Per-call state moved from function
  attributes to a `RelayWorkerState` dataclass. Behavior is preserved; the
  public `_ws_connect` entry point is unchanged. Adds ~890 lines of tests,
  including first execution coverage of the PTY, WS/VNC, and HTTP-proxy paths.

### Removed

- Dead HTTP `FSRelayHandler` path in the relay worker (never invoked —
  `worker_main` only calls `_ws_connect`).
- Dormant HTTP remote-worker stack in the engine.

## [1.0.0-alpha.52] — 2026-06-19

### Fixed

- Installed wheel could not start (`pawflow`/`pawcode` crashed with
  `ModuleNotFoundError: No module named 'cli_commands'`). `cli.py` imports
  `cli_commands` at module load, but only `cli` was listed in
  `[tool.setuptools] py-modules`, so `cli_commands.py` was never packaged.
  Both top-level modules are now declared.
- CI bandit run failed on the deliberate `subprocess` re-export in
  `core/install_bootstrap.py` (kept so tests can patch `ib.subprocess`);
  the import is now annotated `# nosec B404`.
- Pixazo describe/remix now upload the image bytes instead of handing
  Pixazo a URL it must fetch.
- Media error messages distinguish an unsupported operation from a
  failed-to-connect condition.
- claude-code (`-p`) tool calls now stream live again instead of arriving
  bundled with their results at the end of the turn. agent_core wired
  `block_callback` for every CLI provider except `claude-code`, so its
  tool_use/tool_result blocks were held until the end-of-turn flush — the
  UI showed the tool_call and its result together, late, with no BG/Kill
  window (worsened by newer Claude Code CLIs emitting the whole response
  under a single turn). `claude-code` is now in the `block_callback` gate,
  and the claude-code stream loop marks block-persisted tool_use ids so the
  turn flush no longer re-persists them (no double tool_call in the
  transcript).

## [1.0.0-alpha.50] — 2026-06-18

### Fixed

- Telegram inbound delivery latency/loss. Media callbacks download via
  `getFile` synchronously; running them on the single poll loop stalled
  `getUpdates` for every bot and message (text included) for minutes, then
  flushed in a burst, and could drop messages. Inbound updates now dispatch
  on a bounded thread pool so downloads run concurrently and the poll loop
  never blocks — messages (and images) arrive immediately again.
- Expired session now surfaces instead of a silent blank chat. An expired
  session makes `/api/agent/events` answer 401, but `EventSource` only exposes
  an opaque error; the stream is now probed on error and a confirmed 401/403
  shows a "session expired" message and redirects to login.
- Admin `view=all` no longer returns a sparse `list_resources`. The alpha.49
  branch dropped every non-catalog section (deployed flows, relays, remote FS,
  summarizer, tasks, flow templates), blanking the panel — notably "Dépôt
  Flows". The full self-view is now built first, then the repo-backed catalogs
  (incl. cross-user flow templates) are overlaid owner-labelled. Secrets and
  variables are never enumerated cross-user.

### Added

- Loading spinner in the chat while a conversation history loads (was a silent
  blank between clearing the view and the history arriving).
- Startup/post-login + relay-close diagnostics (`[ui-action]`, `[svc-load]`,
  `[sse-events]`, per-connection relay ids) to pin remaining startup latency.

## [1.0.0-alpha.49] — 2026-06-17

### Added

- Admin cross-user UI for the resource sidebar. Admins get a "view all" toggle
  on the Services / Flows / Dépôt listings (sends `view=all`) with an owner
  badge on every row, target-owner pickers in the install-service and
  create-resource dialogs, and a "which user?" prompt when demoting a global
  resource down to a user. Non-admins see none of this and behave as before.
- Admin owner override for the resource scope-move path
  (`copy_resource_scope`): demote a global resource to a specific user, or
  promote another user's resource to global, via
  `target_user_id` / `target_conversation_id`. Default = caller.

### Changed

- `tasks/io/chat_ui/resources.js` (5092 lines) was split into 10 semantic
  modules of <=800 lines each (core, pfp, flow_templates, render, menus,
  flow_dialogs, resource_dialogs, create_dialogs, service_dialogs,
  service_login). Cuts fall only on whole-function boundaries; load order is
  preserved in `_JS_MODULES` (core first). No behaviour change.

## [1.0.0-alpha.48] — 2026-06-17

### Added

- Admin cross-user scopes. An admin can switch the Services, Flow repository,
  and resource depot listings to a view-all mode (`view="all"`) that returns
  every user's and conversation's definitions, each labelled with its owner
  (user id / display name, and conversation when conv-scoped). The same admin
  may create, and promote/demote, on behalf of another owner via
  `target_user_id` / `target_conversation_id` — including "demote a global
  definition down to user X". All of this is strictly additive: a non-admin, or
  any request without the new fields, behaves exactly as before. New
  `core/admin_scope.py` centralises the admin gate, owner resolution (validates
  the target user exists and that a target conversation belongs to it), and
  owner display lookup. Enumeration primitives: `ScopedRepository`
  `list_all_owners`, `ResourceStore.list_all_global`,
  `ServiceRegistry.iter_all_scopes`.

### Fixed

- Telegram messages no longer arrive minutes late in bursts. Since the
  off-thread listener dispatch landed, the Telegram bridge ran on a serial
  per-conversation lane with no backpressure while each send opened a fresh TLS
  connection, so under load it fell behind the (SSE-delivered) webchat. Sends
  now reuse a persistent per-bot keep-alive connection — kept separate from the
  long-poll `getUpdates` connection so a 30s poll never blocks a send —
  reconnect on a broken socket, and honour Telegram `429 retry_after` with
  bounded backoff.
- The package version now lives in exactly one place. `core.__version__` had
  drifted to a hardcoded `1.0.0a10`; it is now derived from `pyproject.toml`
  (source checkouts) or the installed package metadata (wheels/docker), so it
  can never go stale again — only `pyproject.toml` needs bumping per release.

## [1.0.0-alpha.46] — 2026-06-16

### Fixed

- The display/persistence side of tool-call decoding is now unified on
  `core.tool_json.parse_tool_arguments`, matching the execution path. The
  unwrap family (`unwrap_mcp_tool`, the Claude Code `_pub` event relay, the
  interactive `_loads_tolerant`/`cc_interactive_filters` helpers, and the
  nested-unwrap loop in `agent_tool_exec`) each carried its own inline
  `json.loads`/autoclose mini-decoder; they now route through a shared
  `_decode_str_arg` helper, so a truncated or escape-mangled arguments
  envelope recovers identically on every provider and in the UI.
- Mid-string truncations are now recovered everywhere. The canonical
  truncation guard treats an "Unterminated string" decode error as an EOF
  truncation (CPython reports its position at the string's opening quote,
  which can be far from EOF), so autoclose repair fires on the execution path
  too — not just on the display helpers that previously autoclosed
  unconditionally.

## [1.0.0-alpha.45] — 2026-06-16

### Added

- Containerized `executeScript` now has full parity with the in-process path:
  `get_service(id)`, `pawflow`, and `flowfile` work identically, proxied to the
  host over the pfp host-call protocol (the service stays on the host; the
  container holds no secrets). Bytes round-trip losslessly; an explicit
  `docker_timeout` cancels a blocking `pawflow.run_agent`.
- `dbConnectionPool` is now a real connection pool: up to `max_connections`
  live connections, one per concurrent caller, with rollback-on-error and
  eviction of broken connections (SQLite `:memory:` pinned to one connection).

### Fixed

- Tool-call argument decoding is unified on
  `core.tool_json.parse_tool_arguments`. `tools/mcp_bridge.py` and
  `services/tool_relay_service.py` no longer carry divergent inline copies, so
  an arguments envelope decodes identically on every route — fixing intermittent
  "failed to decode arguments" and leaked `arguments_json` errors. The canonical
  module is vendored next to the bridge (`/opt/pawflow/tool_json.py`) in every
  provider container.
- The `telegram/pink_skin` moderation flow could not start: the script sandbox
  blocked `from core.embeddings import ...`. The embedding helper
  (`build_memory_embed_fn`) is now injected into `executeScript` in-process, and
  the blacklist regex is bounded to mitigate owner-supplied ReDoS.

## [1.0.0-alpha.44] — 2026-06-16

### Added

- Generic multi-tenant infrastructure for Telegram moderation bots (core), and
  the `telegram/pink_skin` 1.0.0 moderation bot flow built on top of it.

### Fixed

- Bandit B110 finding on the best-effort SQL rollback in
  `tasks/data/execute_sql.py`. The intentional `try/except/pass` cleanup is now
  annotated with `# nosec B110` and a rationale comment, so the security scan
  passes (exit 0) again.

### Changed

- Relay test suite: retry-sleep capture is now scoped to the test thread to
  avoid cross-test interference.

## [1.0.0-alpha.43] — 2026-06-16

### Fixed

- `pyproject.toml` version now tracks the release tag again. It had drifted to
  `1.0.0a33` while tags advanced; release commits now bump it alongside the
  CHANGELOG.

## [1.0.0-alpha.42] — 2026-06-16

### Fixed

- Claude Code interactive provider: live preempts — and the `POST /api/agent`
  request that triggers them — no longer block ~8.5s on tmux submit
  verification. `send_interrupt` ran the best-effort `_verify_submitted` pane
  poll (up to `PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS`, default 6s, plus ~20
  docker-exec round-trips) inline on the request thread. It now runs in a
  daemon thread, so the ack returns immediately and queued Telegram/webchat
  messages no longer back up behind slow injections.
- Secret/variable right-click menu in the chat file viewer rendered literal
  `\u{1F5D1}` escape text instead of the 👁 / ✏ / 🗑 glyphs (doubled backslash
  in `tasks/io/chat_ui/file_viewer.js`).

### Added

- Website hero and README now note that the **Ask PawFlow** help bot is itself
  powered by a PawFlow agent flow.

## [1.0.0-alpha.41] — 2026-06-16

### Added

- Opt-in per-conversation encryption for the public web and Telegram help bots.
  A new `encrypt_conversations` flow parameter (default `false`) makes each
  visitor conversation encrypted at rest with a key derived from the visitor's
  own secret (the web session cookie / Telegram `user_id`); the lookup key is
  `sha256(session)` so the raw session is never stored, and the instance owner
  reading the conversation files on disk sees only ciphertext. Backed by new
  scope-bounded `enable_conv_encryption` / `unlock_conv_encryption` /
  `lock_conv_encryption` flow API methods that wrap the existing DEK/passphrase
  vault. A regression test proves the owner cannot read a conversation without
  the visitor's secret.
- Floating help-chat window on the website: on wider viewports the help panel
  can be dragged by its header and resized via a bottom-right grip; on phones
  it stays full-screen.

### Fixed

- ToolSearch agents no longer have every tool call denied. The permission gate
  ran against the literal `use_tool` dispatch wrapper instead of the inner tool
  it invokes, so a non-interactive conversation got an un-approvable denial and
  content-aware checks (dangerous `bash`, protected paths, read-only writes)
  inspected the wrapper's empty arguments and missed the real command. The gate
  now unwraps `use_tool` → the real tool (with its real arguments) and decides
  on that; `get_tool_schema` / `use_tool` schema plumbing is treated as
  transparent and always allowed. This also closes a latent hole where a
  dangerous `bash` invoked via `use_tool` bypassed the content checks.

### Security

- Flow-level prompt-injection defense for both help bots: every visitor message
  is wrapped before `run_agent` in a `_guard()` envelope delimited by a
  per-message random nonce, instructing the agent to treat the contents as
  untrusted data and ignore any embedded role/prompt/secret/tool-redirect
  attempts. This treats the visitor themselves as a potential attacker,
  independent of the agent's own system prompt.

## [1.0.0-alpha.40] — 2026-06-16

### Fixed

- Agent `max_depth` no longer throttles the tool-use loop. The per-conversation
  `max_depth` setting is the **sub-agent (delegation) recursion depth** only —
  enforced in the executor via `min(max_depth, MAX_GLOBAL_DEPTH)`. A stray
  override in agent context resolution also assigned it to `max_iterations` (the
  tool-use loop cap, a per-LLM-service setting), so any agent whose `max_depth`
  was lowered to forbid delegation was silently capped to that many tool
  iterations. With `max_depth=1` (e.g. the web/Telegram help bots: "no
  sub-agents") the agent got a single iteration, spent it on the first
  `get_tool_schema` call, then hit forced synthesis with no gathered data and
  hallucinated an answer instead of fetching the docs. The two notions are now
  fully decoupled: `max_iterations` is resolved from the LLM service/config
  (default 1000) and is never derived from `max_depth`.

## [1.0.0-alpha.39] — 2026-06-15

### Fixed

- SSE event delivery no longer stalls when a downstream sink is slow. The
  conversation event bus ran in-process listeners — notably the Telegram
  bridge, which POSTs to the Telegram API with a 60s socket timeout — inline on
  the conversation-writer thread, so a single slow push froze live SSE updates
  for every webchat client: the activity panel went blank and messages arrived
  up to ~40s late in bursts. Listeners now run on a bounded, dynamically sized
  thread pool with per-conversation ordering, so one slow sink can no longer
  delay the SSE stream, the server, or any other conversation. Pool size is
  tunable via `PAWFLOW_EVENT_LISTENER_THREADS`.
- Telegram bridge: long HTML messages (e.g. consolidated thinking blocks) that
  exceed Telegram's 4096-char limit no longer break markup. The message
  splitter is now tag-aware — it never cuts inside a tag and closes/reopens any
  open tags at chunk boundaries — fixing the `400 ... Can't find end tag
  corresponding to start tag "blockquote"` rejections that dropped every long
  mirrored message.

## [1.0.0-alpha.38] — 2026-06-15

### Changed

- Host networking is now the **default** container network mode on Linux
  (`--network-host` no longer needed; `--network bridge` opts back to `-p`
  publishing). macOS/Windows keep `bridge` by default because Docker Desktop's
  host networking binds the Docker VM, not the host, leaving ports unreachable.
- The in-container bind defaults to `0.0.0.0` (env `PAWFLOW_CONTAINER_HOST`)
  instead of `127.0.0.1` under host networking. A loopback-only bind made the
  main listener unreachable from sibling **bridge** containers — the managed
  server relays connect back via the host-gateway IP, which only resolves to a
  `0.0.0.0` bind, so a relay-less server could not start workspaces. Keeping
  those ports off the public internet is the host firewall's job in this mode;
  pass `PAWFLOW_CONTAINER_HOST=127.0.0.1` when a front proxy is the only ingress.

### Fixed

- `web_help_bot`: the `POST /api/help` route is now registered `public`, so
  unauthenticated visitors reach the help agent instead of getting a `401
  Unauthorized` from the session-auth gate. The endpoint's security boundary is
  its Origin allowlist, shared LLM budget, and per-session TTL — not login auth
  (mirrors `telegram_help_bot`). Redeploy the flow to pick up the fix.

## [1.0.0-alpha.37] — 2026-06-15

### Added

- Installer `--network-host` (`--network host|bridge`, env `PAWFLOW_NETWORK_MODE`):
  run the server container with host networking so every port it opens —
  including the dynamically-chosen ports of deployed `httpListener` flows, which
  are not known in advance — is reachable on the host without explicit `-p`
  publishing. The in-container bind defaults to `127.0.0.1` in this mode, so
  those ports stay loopback-only (private) and are meant to be fronted by a
  host-side reverse proxy (e.g. Caddy). The `web_help_bot` flow's `http_host`
  now defaults to `127.0.0.1` to match.

### Security

- Resource panel (`list_resources`) no longer leaks other users' deployed
  flows to an admin. The Flows section gated its owner/conversation check on
  `not _is_admin`, so any admin saw every user- and conversation-scoped
  deployment of every account (e.g. a technical user's user-scope bot).
  Ownership is now strict and owner-only — the admin role grants no cross-user
  visibility in this per-user panel; cross-user management stays on the
  dedicated admin endpoints. Other resource listings (agents, skills, MCP,
  tasks, prompts, hooks, services, variables, secrets, packages, voices) were
  audited and already scope strictly to the viewing user + global + current
  conversation.

### Fixed

- Resource panel stayed entirely invisible for a user with no conversation
  (e.g. a freshly-created/technical user). `_loadResourcesNow` hid the panel
  and returned early when no conversation was selected, so the no-conversation
  rendering path (added previously) was never reached, and the boot path with
  no conversations never called `loadResources()`. The panel now renders the
  scope-independent sections (Flows, Services, Packages, Variables, Secrets,
  Agent/Flows repositories) immediately on login, and refreshes into that view
  after the last conversation is deleted.

## [1.0.0-alpha.36] — 2026-06-15

### Fixed

- Resource panel: the **Variables** and **Secrets** sections disappeared
  entirely when empty — the section header (and its `+` create button) was
  gated on a non-empty list, unlike every other section. A user with no
  variables yet could never see the section or add a first one. Both headers
  now render unconditionally with a "no variables"/"no secrets" placeholder,
  matching Services/Flows/etc.
- Resource panel with no conversation selected (e.g. a freshly-created user
  before any conv exists) now shows only the scope-independent sections the
  user can act on: Flows, Services, Packages, Variables, Secrets, Agent
  Repository, Flows Repository. The conversation-scoped sections (Agents,
  Tasks, Relays, Filesystem, Summarizer, Linked Accounts) and the
  conv-irrelevant repos (Skills/Prompts/Themes/Voices/Tasks/MCP/AgentHooks/
  Tools) are hidden until a conversation is selected, instead of rendering a
  confusing mixed set.
- The `default.telegram_help_bot` flow (public Telegram help bot) was invisible
  in the Flow repository browser: it shipped without a `latest.json`, and the
  repo enumeration globs `**/latest.json`, so a flow lacking that file is never
  listed even though its `versions/1.0.0.json` is seeded to disk on restart.
  Added the missing `latest.json` (`{"version": "1.0.0"}`), matching every other
  default flow.
- Interactive-provider interrupt landing on a compact boundary no longer crashes
  the agent loop. When the provider compact already invalidated (killed) the
  Claude Code / Antigravity interactive session before a queued interrupt ran,
  `interrupt_claude_code_interactive` / `interrupt_antigravity_interactive` now
  treat the missing session as a completed no-op (force stop is never an error)
  instead of raising `No active … session for interrupt`.

### Added

- `pawflow` flow facade: user-scope variable access for deployed flows —
  `get_variable`/`set_variable` and an atomic `increment_variable` (file-locked
  read-modify-write via `ConfigStore.atomic_increment_param`, safe under
  parallel `executeScript` instances). Lets a public bot keep a durable,
  panel-visible/resettable counter (e.g. a shared LLM budget across all its
  conversations), since public-channel visitors have no per-user store.
- `pawflow.run_agent` now returns the completed turn's `cost_usd`, `tokens_in`
  and `tokens_out` (surfaced from the existing `done` event — the same figures
  `/cost` reports) so a flow can charge a budget per turn.
- Deterministic, timing-free regression test for the empty `Bash()` tool-call
  race (`test_turn_coordinator_observed_full_args_supersede_empty_stream_emit`),
  marked `xfail(strict=True)`: it drives an empty STREAM emit that claims the
  `tc_id` followed by a full OBSERVED emit for the same id, asserting the
  complete args must win. Documents the two-emitter race at the code level and
  becomes the executable spec for the single-source fix (remove the xfail when
  the fix lands).
- `http_bots.web_help_bot` flow: a public web help bot exposed as an HTTP
  endpoint (`POST /api/help`), mirroring `telegram_help_bot` with HTTP
  ingress/egress — per-session conversation (cookie-keyed), sliding TTL,
  response timeout, Origin allowlist, and a shared daily LLM budget.

### Changed

- Conversations carrying a non-zero TTL are now treated as **temporary**
  (`ConversationStore.is_temporary`): the throwaway per-session conversations
  bots create are deliberately excluded from durable side effects — never
  git-historized (`git_snapshot` is a no-op) and never fed to auto-memory
  (`auto_extract_memories` returns early). Normal compaction still applies. The
  `.git` is left in place, so toggling a conversation unlimited↔temporary just
  stops/resumes committing.
- Builtin flow repository reorganized into groups: `cryptos/`, `github/`,
  `http_bots/`, and `telegram/` (out of the flat `default/` group, which now
  holds only `pawflow_agent` and `pawflow_installer`). The new groups are
  registered as image-managed roots so runtime-installed packages are never
  clobbered by image defaults.
- Crypto report flows (`daily_crypto_email_oauth2`, `manual_crypto_email_oauth2`)
  downgraded from v2.0.0 to v1.0.0 (old v1.0.0 dropped, v2.0.0 renumbered;
  fqn/subflow references updated).

### Removed

- Builtin `discord_agent`, `slack_agent`, and `whatsapp_agent` flow definitions,
  plus the demo/example flows (`demo_pipeline`, `example_pipeline`,
  `exemple_flux`, `http_hello_world`, `http-hello-world`, `sub_upper`). The
  Discord/Slack/WhatsApp task and service code is unchanged — only the shipped
  flow templates were removed.

## [1.0.0-alpha.35] — 2026-06-15

### Fixed

- Agent response waits no longer carry an illegal implicit timeout: the shared
  agent runtime wait (`AgentRuntimeAPI.wait_for_done` / `AgentResultWaiter`),
  the Telegram agent client, and the `pawflow` flow facade `run_agent` now wait
  unbounded by default (project rule: no timeout unless explicitly configured).
  A long turn that exceeded the old 600s cap could detach its coordinator and
  drop the final `done`, so the answer only surfaced on the next message.

### Added

- Diagnostic logging (`[cci-args-debug]`) at the two CCI tool-call emit points:
  warns, only when an MCP tool is about to be emitted with empty arguments,
  with the raw observed input and emit path (stream vs observed). Temporary
  instrumentation to pin a non-deterministic case where a `bash` tool call
  renders with empty arguments in the chat.

## [1.0.0-alpha.34] — 2026-06-15

### Added

- Generic, scope-bounded **`pawflow` API facade** injected into `executeScript`
  (alongside `content`/`attributes`/`flowfile`/`fs`). It lets a flow script
  drive PawFlow — `create_conversation`, `run_agent`/`submit_agent`,
  `cancel_agent`, `set_tool_filters`, conversation extras/TTL,
  `list`/`find`/`delete_conversation` — with every operation authorized against
  the flow's deployment scope via `core.flow_runtime_access` (the same boundary
  `createConversation`/`publishMessage`/`spawnAgent` use). `run_agent` enforces a
  hard message→response timeout and force-cancels a stuck turn, for unattended
  flows where no human can cancel.
- **Public Telegram help bot** flow (`default.telegram_help_bot`) built entirely
  from generic tasks (`telegramReceiver` + `executeScript` + `telegramSend` +
  `cronTrigger` sweep): one conversation per origin user, optional
  `allowed_chat_ids` source gate (restrict to a specific group, exclude DMs),
  no relay, web-only tool allowlist (`web_search,fetch,read`), sliding
  conversation TTL with proactive purge, and a configurable response timeout.
- PawFlow help-agent system prompt (`docs/prompts/pawflow_help_agent.md`) and
  documentation (`docs/telegram_help_bot.md`, plus the `pawflow` facade in
  `docs/multi_client_conversations.md`).

## [1.0.0-alpha.33] — 2026-06-14

### Fixed

- HTTP MCP servers were effectively unusable: the client spoke a non-standard
  "sessionless JSON-RPC over a single POST" dialect (one `POST`, `Accept:
  application/json` only, no `initialize` handshake, no `Mcp-Session-Id`, no
  SSE), which virtually no real MCP server (FastMCP, the official SDK servers)
  accepts — they answer 400/406 or reply over `text/event-stream`. Only stdio
  MCP servers (proxied through the relay) actually worked. The HTTP client now
  implements the conformant **Streamable HTTP** transport: lazy `initialize` +
  `notifications/initialized` handshake, `Mcp-Session-Id` capture and replay,
  `Accept: application/json, text/event-stream` negotiation, incremental SSE
  response parsing, and one transparent re-initialize-and-retry on an expired
  session (HTTP 404). Both tool discovery (`tools/list`) and invocation
  (`tools/call`) go through the new `core.mcp_http_client` module.

### Changed

- HTTP MCP tools routed through a relay-proxy URL now re-mint the ephemeral
  proxy token at call-time (from the stored URL template + user id) instead of
  reusing the token captured at discovery, which could expire on long-lived
  conversations. The relay HTTP proxy already streams SSE end-to-end and
  forwards the `Mcp-Session-Id` header in both directions, so no relay change
  was required.

## [1.0.0-alpha.32] — 2026-06-14

### Fixed

- Large `edit` tool calls rendered as a bare `Update()` with no arguments in
  the chat UI, while smaller edits rendered correctly as `Edit(<path>)`. The
  Claude Code interactive provider rebuilds a tool call's display arguments
  from the streamed `input_json_delta` chunks; when a large input was
  truncated at EOF the strict `json.loads` failed and the arguments were
  dropped to `{}`, so the client fell back to the bare tool-name summary. The
  provider now closes EOF-truncated tool JSON via `autoclose_truncated_json`
  before giving up — valid and genuinely-unrecoverable inputs behave exactly
  as before — and the chat UI recovers the file path from the edit result
  line as a fallback so the header reads `Update(<path>)`. Display-only: the
  edit itself always executed correctly.

## [1.0.0-alpha.31] — 2026-06-14

### Fixed

- Tool calls rendered as the raw `use_tool` wrapper in the chat UI
  (`Read(tool_name=read, arguments_json={...})`) instead of the real tool and
  its arguments. The client unwrap only peeled the wrapper when the tool *name*
  was still a `use_tool` wrapper; when a call arrived half-wrapped — name
  already unwrapped but the arguments still `{tool_name, arguments_json}`, the
  shape the server emits and persists — it passed the wrapper straight through.
  The client now also unwraps when the *arguments* are a `use_tool` wrapper,
  mirroring the server-side `unwrap_mcp_tool` behaviour.

### Changed

- Vision downscale ceiling is now configurable and defaults to 1568px on the
  longest edge (up from 720p/1280px). 1568px is the largest size the Anthropic
  API actually uses — it internally downscales anything larger for
  tokenisation — so this recovers detail for screenshots and fine text without
  spending tokens on pixels the model discards. Override with the
  PAWFLOW_VISION_MAX_DIM env var (clamped just below the 2000px provider
  reject); the re-encode byte budget is likewise overridable with
  PAWFLOW_VISION_MAX_BYTES.

## [1.0.0-alpha.30] — 2026-06-13

### Fixed

- OAuth credential loss on live-session idle teardown. The idle sweeper,
  shutdown, and evict paths killed a warm CLI container without copying back
  the OAuth token the in-container CLI had rotated into its workdir. Anthropic
  rotates the refresh_token (single-use), so the dropped rotation left a dead
  token in the pool and logged Claude Code users out (the next refresh failed
  with invalid_grant). Teardown now recovers the rotated token to the correct
  pool slot first. codex/gemini wired identically as defense-in-depth (OpenAI/
  Google do not invalidate the old refresh_token, so the same hole was benign
  there).
- Oversized images failed to render in vision instead of being downscaled.
  The read/filestore/workdir image paths emitted raw base64 without the shared
  downscaler, so images above the provider pixel limit errored. All image
  emitters now route through resize_image_for_vision, and the vision ceiling
  is lowered to 720p (1280px longest edge) so every payload stays small.
- MCP tool-argument decoding is now tolerant of near-valid JSON. A last-resort
  repair fixes invalid backslash escapes and raw control characters inside
  string literals — but only after strict parsing has already failed, and it
  never alters an already-valid payload. Decode-error messages no longer
  misreport invalid JSON as a wrapping problem.

## [1.0.0-alpha.29] — 2026-06-13

### Added

- Opt-in encryption at rest for conversations and conv-scoped relay workspaces.
  Strictly opt-in and transparent: conversations without it enabled are
  byte-for-byte unchanged on disk and through the API. Threat model is T1 (disk
  at rest) — with the server stopped, encrypted data is ciphertext on disk and
  no key is in memory.
  - Conversation encryption (`/encrypt on`): a per-conversation DEK encrypts
    content fields (message text, thinking, tool arguments and results) with
    AES-GCM; metadata (ids, timestamps, ordering, roles) stays clear so the
    store, restart-from, and git history keep working without the key. Content
    is migrated to ciphertext on enable and back on disable.
  - Key custody: the DEK is wrapped by a passphrase (scrypt + AES-GCM) in a
    RAM-only, session-bound vault — zeroised on lock, purged on logout,
    idle-locked after 15 minutes, and gone on server restart. Commands:
    `/encrypt status|on|off|unlock|lock|passwd`.
  - Optional recovery (escrow) passphrase: `/encrypt escrow on|off` +
    `/encrypt recover` to unlock when the primary passphrase is lost.
  - Trusted key-relay (optional, no prompts): bind a relay's X25519 public key
    (`/encrypt relay <pubkey>`) so a connected relay auto-unlocks bound
    conversations; the server seals the DEK to the relay pubkey and never holds
    a key that opens that wrap, and DEKs are purged when the relay disconnects
    (relay-gone = re-locked). Relay key provisioning via `pawflow-relay key`
    (init/status/export-pubkey/rotate, passphrase-locked at rest) and the
    Relay Desktop "Relay Encryption Key" panel; `pawflow-relay start --unlock-key`.
  - Workspace encryption for conv-scoped server relays (`/relay encrypt <id>
    on|off`, `/relay unlock <id>`): the workspace is stored as a CryFS
    cipher-store and mounted with a DEK delivered over the relay control channel.
  - Relay images bumped to `2026.06.13` (now include `cryfs`).
  - Docs: Security Model "Encryption at Rest" section, design RFC, `/encrypt`
    slash-command reference, and website (features, FAQ, how-to).

## [1.0.0-alpha.28] — 2026-06-13

### Fixed

- Web chat (SSE): the agent event stream checked its lifetime cap at the top of
  the loop, after `writer.iterate()` had already dequeued an event, and broke
  without yielding that chunk. When a message landed on the same iteration the
  cap expired it was dropped — and since `send()` had returned True the bus
  never buffered it for replay, so the reconnecting client could not recover it
  (the message reached side channels like Telegram via the flow sink but never
  the web chat transcript, intermittently). The stream now yields the dequeued
  chunk before the lifetime check and drains any already-queued events before
  closing; adds `SSEWriter.drain_nowait()`.
- Claude Code interactive (tool badges): CCI never set `tool_origin` on tool
  calls, so they rendered with no native/mcp badge (unlike Codex). Tool calls
  are now tagged — PawFlow MCP-bridge tools (`use_tool`/`get_tool_schema`) get
  the MCP badge, the allowed native Claude Code tools get the Native badge —
  threaded through the MITM observer and both provider emit paths.
- Web chat (tool-call display): the client-side `use_tool` unwrap read only the
  legacy `arguments`/`parameters` object, never the advertised `arguments_json`
  string, so a raw wrapper reaching the client rendered as empty parens. The
  client now decodes `arguments_json` first, mirroring the alpha.27 server fix.

## [1.0.0-alpha.27] — 2026-06-13

### Fixed

- Claude Code interactive (transcript display): after alpha.26 switched the
  `use_tool` payload to a string `arguments_json`, the CCI transcript observer
  still read the inner arguments from the legacy `arguments` object, so tool
  calls rendered with empty parentheses (`Bash()`, `Read()`) in the technical
  details panel. The observer now decodes `arguments_json` first (falling back
  to a legacy `arguments`/`parameters` object), so arguments render again.
  Display-only — tool execution was unaffected. Codex/other providers use a
  separate path and were never impacted.

## [1.0.0-alpha.26] — 2026-06-13

### Fixed

- Claude Code interactive (MCP bridge): `use_tool` advertised its payload as a
  free-form `arguments` object, which Anthropic's constrained tool decoding
  intermittently collapsed to an empty `{}` input (`tool_name` and arguments
  both dropped) — producing random "missing required parameter 'tool_name'"
  failures. The bridge now advertises a string `arguments_json` field (mirroring
  the in-process meta-tool); the reader still accepts `arguments_json`, a legacy
  `arguments` object, or flat keys, so other MCP clients (Codex, Gemini) are
  unaffected.
- Telegram bridge: the pre-answer reasoning of a turn was dropped. Thinking was
  buffered under the agent's `agent_name`, but the closing `new_message` event
  carries only `source.name`, so no-tool-call turns never flushed their
  reasoning to Telegram (webchat showed it). The buffer key is now derived from
  `agent_name` or `source.name`, and turn end (`done`/`error_event`) flushes any
  remaining burst.

### Added

- Tool name aliases `image`, `image_view`, `view_image` route to the `see`
  (vision) tool — for `use_tool`, direct MCP calls (rerouted through use_tool,
  no new tools exposed), and HTTP providers. `view` still maps to `read`.
- Design RFC `docs/design/encryption-at-rest.md`: opt-in, per-conversation
  at-rest encryption and encrypted server relay workspaces (threat model,
  KEK/DEK with passphrase/relay/escrow wraps, RAM-only custody, UX/commands).

## [1.0.0-alpha.25] — 2026-06-13

### Fixed

- Relay/services connection dot: the Services list reported a relay as
  "started" as soon as it was enabled, while the Relays panel reported it via
  the live connection state — so the same relay could show green in one panel
  and red in the other during the connect window. Both panels now compute a
  relay's state from the same `is_connected()` call.

### Changed

- Relays panel connection dot is now tri-state, matching the Services list: 🟢
  connected, 🟡 connecting (enabled but the relay pool has no connection yet —
  managed container dialing back or lazy connect in flight), 🔴 down/disabled.
  The relay info dialog shows the same "starting" state.

## [1.0.0-alpha.24] — 2026-06-13

### Fixed

- Sub-conversation runtime scope (HIGH): the tool relay only rooted `::task::`
  sub-conversations to their parent, so `::task_verify::` and `::delegate::`
  sub-conversations resolved hooks, tool permissions and secret injection
  against their own (empty) conversation id. A `bash`/`execute_script` run from
  a verify or delegate step did not enforce the parent's tool permissions or
  receive its secrets. `_root_conversation_id` now strips all three markers.
- Vision: a pre-uploaded oversized image (e.g. a full-resolution JPEG whose
  mime type is unchanged by the resize) was downscaled in memory but the
  oversized original was kept in storage, so downstream reads still hit the
  provider pixel limit. The attachment is now re-stored whenever the resize
  actually changed the bytes.
- Catch-up context: the Claude Code provider stripped `::delegate::` and
  `::task::` but not `::task_verify::`, so a verify sub-agent received no
  catch-up from the parent conversation. Aligned on the canonical marker
  triple.

## [1.0.0-alpha.23] — 2026-06-13

### Fixed

- Claude Code interactive and Antigravity interactive: a live preempt that
  extended a turn past a Stop hook left the stop/done latch set, so a later
  idle gap (the model churning on a large tool result) ended the turn
  coordinator mid-answer. The coordinator returned the already-delivered
  previous answer while the real final answer was generated with no listener —
  reaching only the tmux session, never the webchat/Telegram channels. A fresh
  `/v1/messages` request after a Stop now clears the stale latch so the turn
  runs to its real end and the final answer is delivered.
- Vision: oversized images are now downscaled to the 2000px ceiling
  proactively at ingestion, provider-agnostically. User attachments,
  tool-result images and `see`/`screen` captures share one resize helper
  (`core/image_resize.py`), so a full-resolution screenshot no longer exceeds
  the provider pixel limit and gets rejected at read time — the stored copy
  every downstream path uses is already within limits.

## [1.0.0-alpha.22] — 2026-06-12

### Fixed

- Full scope-resolution audit (11 passes) across the four scoped chains —
  ServiceRegistry, ResourceStore/repository, the secrets/params expression
  cascade, and relay bindings. ~80 call sites that resolved only user/global
  now walk the canonical conv > user > global chain, so conversation-scoped
  services, agents, skills, prompts, secrets and relays (e.g. installed by
  packages into a conversation) are visible everywhere they are used:
  agent system prompts and Connected Relays, relay listing/connect/disconnect,
  relay-proxy routes (tokens now carry the conversation), LLM service and
  cost lookups, fs-service auto-detection, tool argument expression
  resolution, and more.
- Relay bindings: `/relay status` and the cognitive-ui build fallback now
  read the per-agent bindings format correctly; whitelists, scans and
  fs-manifest notifications cover agent-specific links via the new
  `get_linked_all`.
- Sub-conversations (`::task::`, `::task_verify::`, `::delegate::`) inherit
  the parent conversation's agent roster, and all SSE/event routing and
  task/config lookups recognize every sub-conversation marker instead of
  only `::task::` — delegate events no longer vanish onto an unwatched bus.
- Checkpoint rewind and cleanup actually work again: checkpoint files are
  saved with an owner, but all reads passed no user_id and were silently
  denied, so rewind restored nothing and expired checkpoints were never
  deleted. Sandbox `filestore://` reads and the write handler no longer
  wrongly deny the caller's own private files; filestore deletes now enforce
  the owner check.
- delete_agent routes to the scope the definition actually lives in
  (conversation/user/global with admin gate), matching delete_skill.

## [1.0.0-alpha.10] — 2026-06-10

### Fixed

- Telegram now shows agent thinking as a single consolidated block per
  reasoning burst instead of flooding the chat with every streamed fragment
  ("bouts") followed by a duplicate of the whole thing. The conversation
  bridge accumulates `thinking`/`thinking_delta`/`thinking_content` events and
  flushes one merged message when the burst ends (next tool call, tool result,
  or message), de-duplicating cumulative snapshots. This also removes the
  message-flood that could rate-limit the bot and stall inbound Telegram
  messages. Most visible with the Claude Code interactive provider, whose CLI
  now emits thinking in many small blocks.
- Claude Code interactive terminal viewer ("open in tmux") no longer reports
  "no sessions". The webchat viewer attached/resized the tmux session as a
  hardcoded uid 1000, but alpha.9 moved the in-container CLI (and its tmux
  server) to `PAWFLOW_RUN_UID`; the viewer now derives the same uid from the
  pool, so it looks in the correct `/tmp/tmux-<uid>/` socket dir.

## [1.0.0-alpha.9] — 2026-06-10

### Fixed

- Media reference sharing now actually reaches the provider. The temporary
  public `?k=` (gateway_key) URL minted for image/video/audio reference
  inputs was rejected with `401 Unauthorized` by the HTTP listener's inline
  session-auth gate, which had no notion of public/gateway_key file access
  (the private gateway and the flow auth task already did). `/files/<id>`
  downloads that authenticate via a public access level or a valid `?k=`
  now bypass the session gate; `_handle_filestore_download` still enforces
  `check_access`. This unblocks image-to-video and other media-ref flows.
- Claude Code interactive containers now run the in-container CLI as
  `PAWFLOW_RUN_UID`/`PAWFLOW_RUN_GID` (the host user that launched the
  PawFlow Docker server) instead of a hardcoded uid 1000 — matching the
  batch claude-code pool. The session `projects/` and `memory/` trees are
  created and chowned to that uid, so server-side tools (e.g. the memory
  skill's `write` via the combined-fs) and the CLI share one uid and no
  longer hit `Permission denied` across the uid boundary. Existing
  on-disk sessions created before this fix stay owned by the old uid and
  may need a one-time `chown` of their `projects/` trees.

## [1.0.0-alpha.8] — 2026-06-10

### Added

- Share FileStore files publicly from the chat: the file context menu now
  offers "Share public link" (mints an unguessable gateway-key URL that
  needs no login and bypasses the private gateway) and "Make private" to
  revoke it, backed by a new owner-only `set_file_access` action.
- Media webhook mode now polls the provider status URL in lockstep with
  the callback (Pixazo): a callback that never arrives falls back to
  polling instead of hanging until the timeout.

### Fixed

- Media reference inputs no longer leak the dead `localhost:9090` handler
  default to external providers. The temporary public share resolves the
  reachable base from the media service `public_callback_base_url` (the
  value already used for webhooks), so image-to-video and other reference
  flows work without a separate relay `file_base_url`; a clear warning is
  logged when no public base can be resolved.
- Claude Code interactive: the first message after a cold container/tmux
  start is no longer dropped. The sender now waits for the TUI input
  prompt to be on screen before pasting, fixing the race that required a
  manual Enter.

## [1.0.0-alpha.7] — 2026-06-10

### Added

- Media reference inputs (image/video/audio) are shared as public,
  gateway-key URLs only for the duration of a single generation call and
  revoked afterwards, letting external providers fetch FileStore assets
  without leaving them publicly reachable. Wired into `generate_video`,
  `edit_image`, and every capability handler.
- Website: Telegram surfaced as a first-class agent client — homepage
  showcase section and a Channels how-to recipe with a real chat
  screenshot.

### Fixed

- Media provider webhooks: callback routes now bypass the private gateway
  challenge (`gateway_exempt`) while still accepting public IPs, so a
  provider's internet callback reaches PawFlow instead of the challenge
  page — previously the job was never notified and silently timed out.
- Webhook mode now surfaces a synchronous-ack error (invalid input URL,
  unsupported format, ...) immediately instead of blocking on a callback
  that will never arrive.
- CC interactive: double-Enter submit so a message is not dropped when it
  is sent right after a restart.

## [1.0.0-alpha.6] — 2026-06-10

### Added

- `github.ci_autofix` flow package: auto-fix CI failures via webhooks.
- Per-instance webhook routes minted through the reserved
  `${_instance_id}` parameter.
- Website: hero install command, SEO metadata, release links resolved
  live from the GitHub API, and generated hero/diagram/docs-map/FAQ
  visuals.

### Fixed

- CI tests no longer download models from HuggingFace, and the CI job is
  capped at 30 minutes — a stalled download could otherwise hang the job
  until the 6h Actions limit.
- OpenAI image generation filesystem handling and request timeout.
- The interactive final response is now emitted as the last message
  only; CLI task store writes fixed.
- tmux submit tests record only the test thread's sleeps, removing a CI
  flake.

## [1.0.0-alpha.5] — 2026-06-10

### Added

- Expression language: documented `${...}` escaping via opaque tokens
  that survive recursive resolution passes.
- claude-code image: resolve and pin the latest published npm version of
  each agent CLI so a rebuild reinstalls only on an upstream change.

### Fixed

- Expression resolver no longer mangles unresolved `${...}` expressions
  (pipeline ops in content, e.g. shell parameter expansions, were
  truncated).

## [1.0.0-alpha.4] — 2026-06-09

### Added

- Surface the effective CCI model from `message_start`.
- Documentation: A2A multi-hop async confirmation saga and A2A
  multi-client isolated context patterns.

### Fixed

- Normalize suffixed Telegram bot commands (e.g. `/cmd@botname`).
- Telegram command mirroring and CCI final-response relay.

## [1.0.0-alpha.3] — 2026-06-09

### Added

- Manual tmux messages in Claude Code Interactive (CCI) are now
  published live.

### Fixed

- Avoid side effects when mirroring Telegram commands into conversations.

## [1.0.0-alpha.2] — 2026-06-09

### Added

- Telegram commands are mirrored into active conversations.

### Fixed

- Interactive tmux runtime isolation.
- Preserve tmux mouse scroll in interactive terminals.

## [1.0.0-alpha.1] — 2026-05-19

First public release.

### Added

**AI Agents**
- Multi-agent conversations with tool-use loop (LLM → tool → LLM → ...)
- 5+ LLM backends: Claude Code, Codex CLI, Gemini CLI, Anthropic API, OpenAI API, and OpenAI-compatible endpoints
- Streaming SSE output to web chat and CLI
- Plan system: structured plan creation, approval, assignment, verification
- Context compaction with `{agent_name}.md` re-injection
- Configurable permission modes: auto, approve-edits, read-only
- Cost tracking with per-conversation budget caps (`max_budget_usd`)
- Force stop: Escape 1x = graceful, 2x = immediate kill

**Tools (90+)**
- Filesystem: read, write, edit, glob, grep, list_dir, move, delete
- Execution and desktop: bash, execute_script, run_in_background, screen, browser, desktop/VNC-backed interaction
- Web: web_fetch, web_search, web_screenshot
- Media: generate_image, generate_video, generate_audio, generate_3d, upscale_image, try_on, lipsync, clone_voice, speak, see (vision)
- Git: git_log, git_diff, git_commit, git_branch
- Multi-agent, plans, and resources: delegate, ask_user, create_plan, manage_plan, manage_resource, link_resource
- Security: security_scan, validate_http_auth
- MCP: connect to any MCP server, tools auto-discovered
- All relay-backed tools route through the connected runtime for local or containerized execution

**Cognitive Systems**
- Memory: categorized facts with scopes and temporal validity
- Knowledge Graph: entity-relationship triples with BFS/DFS, community detection
- Agent Diary: per-agent personal journal
- Project Graph: AST-based code structure analysis (17 languages via tree-sitter)
- Memory digests auto-injected into system prompt

**Pipeline Engine**
- 100+ NiFi-style tasks across 5 categories (System, IO, Data, Control, AI)
- Batch, continuous, and CRON execution modes
- Backpressure, checkpointing, crash recovery
- Flow versioning with rollback
- Graphical debugger with breakpoints and step-through
- Data preview and flow diff
- NiFi flow import (XML/JSON) with Groovy-to-Python script conversion
- 15 flow templates (ETL, Monitoring, Communication, Data Processing, Integration)
- Event triggers: file watcher, webhook, event-driven, polling

**Web Chat UI**
- Real-time SSE streaming
- File explorer with relay filesystem access
- Context editor (view/edit agent context)
- Conversation management with auto-titles
- Shared conversation state across web, PawCode CLI, VS Code, APIs/channels, and flows
- @file autocomplete from relay filesystem
- 60+ slash commands
- Drag & drop file attachments
- Multi-agent support with agent switching
- Desktop access via `/desktop`, screen interaction, and VNC-style sessions when configured

**Infrastructure**
- 9 OAuth2 providers (Google, GitHub, Microsoft, X, Facebook, Amazon, Telegram, Generic)
- Expression language: 40+ chainable operations with scope cascade
- Docker relay for sandboxed tool execution
- Plugin system with semver versioning, .pfp export/import
- Cluster mode with leader election
- Audit logging, rate limiting, Prometheus metrics
- HTTP listener service with SSL/TLS
- PawCode CLI (Claude Code-compatible terminal client)
- VS Code extension connected to the same relay/runtime model
- 4105 tests

**Skills**
- Agent Skills system: per-skill `SKILL.md` manifests with bind-mounted
  asset directories and allowed-tools enforcement.
- Skills repository FUSE mount (`skfs.*`): relay containers mount the
  Agent Skills repository read-only at `/skills`, so non-CLI providers
  can reach a skill's asset files referenced from its instructions.

### Fixed

- `SKILL.md` frontmatter no longer accumulates the read-derived
  `declared_allowed_tools` alias on update.
- `/skill update` is routed to the server from the chat UI, and
  `/add-skill` derives a short manifest description instead of copying
  the full instructions body.

### Security
- Secrets encrypted at rest with AEAD v2
- PBKDF2 password hashing (600K iterations)
- `config/secret.key` excluded from version control
- Configurable CORS, rate limiting, request size limits
- Sandboxed script execution with restricted imports
