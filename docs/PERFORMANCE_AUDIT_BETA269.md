# PawFlow beta.269 performance audit

Date: 2026-09-05. Reviewed checkout: `b6229fe687b1507effb2f72b968b31e4682ed624`, plus the existing UI hotpatch working tree.

## Findings and scope

The clearest measured opportunity is repeated Markdown/DOM rendering during answer streaming. Initial page payload, deep-history reads, and replay-buffer maintenance are the next concrete targets. Visibility-aware OpenSpace work and less frequent project-maintenance scans should reduce background load. Tool concurrency, persistence backlog, and retry policy need explicit capacity controls for sustained concurrent use.

This is an analysis deliverable. Application code and the deployed server were not changed by this audit. Pre-existing UI hotpatch changes and `dbforensics/` were preserved.

Evidence combines current source review, two independent read-only audits, existing regression tests, and isolated synthetic benchmarks on the MyWorkspace relay. Python 3.12.3, Linux x86-64, 12 reported CPUs, Chromium 151.0.7922.108. Benchmarks do not establish end-to-end latency, phone frame rate, production capacity, or LLM generation speed. No production stress test was performed.

## Measurements

Times are medians; browser comparisons use three repetitions and history/bus comparisons five. The HTML renderer uses twenty warm repetitions.

| Scenario | Current | Isolated comparison | Interpretation |
| --- | ---: | ---: | --- |
| Stream 65,536 characters in 1,024 events of 64 characters | 2,736.7 ms; 1,024 renders | 168.4 ms; 64 renders after merging groups of 16 | About 16.3 times less accumulated handler/render time; identical final text and HTML digest |
| Stream 32,768 characters | 729.0 ms | 42.4 ms with groups of 16 | Same direction; identical final text and HTML digest |
| Latest 50 history messages, 12,000-row fixture | 1.486 ms; 70 decoded rows | No change needed for this case | Existing tail reader is already effective |
| Cursor page 10,000 messages back | 176.877 ms; 20,070 decoded rows | 82.278 ms; 10,070 decoded rows in a single-pass fixture prototype | Same 50 messages; about 2.15 times faster |
| Publish 200 events with 5,000 buffered conversations and no subscribers | 99.991 ms | 0.538 ms with a global sweep at most once per second | Removes repeated global scans; scale scenario, not observed normal traffic |
| Initial local asset references | 103 JS + 23 CSS; 2,402,735 bytes | 631,128 bytes when individually gzip-compressed | Compression estimate; excludes external vendor files |
| Initial HTML | 379,764 bytes | 106,236 bytes gzip estimate | Includes eager locale catalogs |
| Server HTML generation | First call 46.941 ms; warm median 8.827 ms | Asset-signature portion alone: 4.533 ms, 150 entries | Secondary request cost; not a streaming bottleneck |

The original bus scale sweep measured 0.664 / 2.768 / 21.433 / 105.452 ms per 200 events at 1 / 100 / 1,000 / 5,000 buffered conversations. The separate comparison run above used the same code path.

All 126 asset digests recorded by the original benchmark still matched the reviewed source when checked.

Benchmark limitations:
- The streaming bench executes the real token handler and Markdown/DOM update. Transport, source badges, turn projections and scroll/layout helpers use inert scaffolding. Events are delivered in a synchronous burst. The 16-event grouping demonstrates avoided work; it is not a proposed requirement to wait for 16 fragments, a measured frame rate, or a shipped optimization.
- The history prototype only proves equality for ordinary synthetic assistant rows. Trace updates, grouped technical rows, concurrent appends, cursor response metadata and rewrites require separate production regression coverage.
- The amortized bus prototype retains the subscription-time TTL filter; an expired replay example remained excluded. The 5,000-conversation fixture is deliberately a scaling experiment. Global cleanup is called when buffering undelivered events, not for every successful delivery to a live subscriber.
- Gzip byte counts are local estimates. The actual public proxy's compression, HTTP version and browser cache hit rate were not measured.
- A single server-container process snapshot showed PID 7 at 3,214,432 KiB RSS (about 3.07 GiB), with 178 threads. This is context during normal activity and the audit, not an idle baseline or evidence of a memory leak.

## Prioritized changes

Effort is relative engineering scope, including tests, rather than a delivery-time commitment.

| Priority | Change | Expected benefit | Effort / main constraint |
| --- | --- | --- | --- |
| P1 | Coalesce streaming visual updates | Strong measured reduction in UI main-thread work | Medium; preserve message and turn boundaries |
| P1 | Reduce initial assets and blocking vendor loading | Faster first opening, especially on slower networks/devices | Medium; preserve classic-script dependencies |
| P1 | Read cursor history in one pass | Strong measured improvement on deep history | Medium; preserve trace/group alignment |
| P1 | Pause OpenSpace work when its surface is invisible | Lower CPU/GPU use and unnecessary requests | Small to medium; resume from retained state |
| P1 | Separate wiki source-scan cadence from pending wiki processing | Avoid repeated tree hashing while backlog persists | Medium; retain freshness and retry semantics |
| P2 | Amortize replay-buffer expiry cleanup | Strong measured scaling improvement | Small; preserve replay TTL and synchronization |
| P2 | Bound tool concurrency and apply retry/admission policies | More predictable server load under failures and parallel use | Medium; preserve cancellation and progress |
| P2 | Bound retained history DOM by render cost | Stable memory and navigation after prolonged browsing | Large; scroll anchoring and active groups |
| P2 | Skip unchanged-title FTS updates | Less potential SQLite lock work on incremental search refresh | Small; quantify with query plan and fixtures |
| P2 | Instrument writer backlog and apply upstream pressure | Detect and prevent persistence lag under overload | Medium; preserve durability and nonblocking ingress |
| P3 | Precompute asset versions and refine caching | Reduce warm page-render overhead and update downloads | Small to medium; retain deliberate hotpatch invalidation |

### 1. Streaming: separate event ingestion from visual rendering

`tasks/io/chat_ui/sse_handlers_a.js:254` handles every token event, appends text and replaces `.msg-content.innerHTML` using the complete accumulated text at line 339. The Markdown implementation at `messages_markdown.js:5` makes multiple passes over that text. For fixed-size fragments, repeated full-prefix processing grows approximately quadratically with response length.

Start by accumulating every event immediately and scheduling one visual update per animation frame, with an explicit short maximum delay to validate under realistic traffic. Reuse the existing content element, cache stable badge/identity data, and perform the near-bottom read once per visual update. Flush pending output at message-ID changes and durable/final/tool boundaries; preserve independent conversation sessions, TTS ingestion and cancellation. Do not buffer solely by fragment count.

If profiles still show large per-frame costs, render stable completed blocks once and update only the unfinished tail. A worker parser is a later option: the initial win does not require replacing the rendering architecture.

Server-side delta coalescing is a separate second step. `tasks/ai/agent_emitter.py:543` emits answer previews per callback, whereas thinking previews already aggregate around 250 characters at line 587. Measure event volume first; preserve timestamps/IDs, boundary order, final flush, reconnection and immediate cancellation. Socket chunk flushing exists at `services/_http_request.py:578`; arbitrary streaming compression or proxy buffering could harm first-token latency.

### 2. Startup: load the conversation shell first

The ordered module list is at `tasks/io/serve_chat_ui.py:37`. Resource editors, cognitive panels, OpenSpace application modules and other optional surfaces are loaded before they are needed. Locale embedding loads English, French and Spanish at line 287.

Load the selected locale plus required fallback, then additional locales when requested. Split optional features behind their entry points, retaining explicit dependency order. Serve RxJS/highlight.js locally and defer them safely: `templates/head/vendor.html:1` currently uses two synchronous external scripts that can delay HTML parsing.

The built-in static route already bypasses the flow DAG, caches file bytes in memory and emits immutable browser caching (`services/_http_request.py:117`). It sends raw asset bytes at line 178. Verify actual edge headers, then provide precompressed static variants with content negotiation where compression is missing. The estimated local HTML + asset payload falls from 2,782,499 to 737,364 bytes with gzip, excluding vendor files; this is an opportunity estimate, not observed bandwidth saved.

A shared signature covers JS, CSS, templates and locales (`serve_chat_ui.py:205`), so one changed file invalidates every boot asset URL. A build manifest with per-file hashes would retain cache hits for unchanged assets and avoid repeated stat work. Keep explicit development/hotpatch invalidation. Jinja and locale serialization already have caches.

### 3. History: remove repeated scans, then introduce direct positioning

`core/_conversation_store_transcript.py:695` first resolves the cursor using `_offset_after_msg_id`, then starts another reverse scan with `_read_tail`. The latter collects and composes the offset plus page plus alignment margin. It therefore incurs both repeated decoding and temporary allocations at depth.

First consume the cursor and page in one iterator, retaining trace updates and group alignment correctly. Next add reliable cursor-to-segment/byte-position lookup, or exploit segment display-row counts for offset skips. Existing segment counts are available at `core/_segmented_jsonl_io.py:211`; they are not a message-ID index. Invalidate/rebuild derived positions on rewrite, deletion or compaction and respect encryption.

The latest page is already fast. Do not replace the storage engine merely to optimize this path.

Agent tools also have remaining full-scan paths: `load_delegate_state` at `core/_conversation_store_transcript.py:94` rebuilds delegate state from the transcript. Consider an incrementally updated derived index if profiling shows frequent use on very long conversations. It must remain reconstructible after restart.

### 4. UI background work and long-lived DOM

OpenSpace activity currently follows whether its surface is registered (`workspace.js:628`). Its frame loop checks global document visibility, not tile visibility (`openspace_runtime.js:521`). Flow polling runs every 2.5 seconds (`openspace.js:111`) and has an overlap guard but no visibility check (`openspace_flow.js:132`).

Use actual surface visibility to pause GPU rendering and flow polling, retain state, and refresh once on return. Separate visibility from focus: a visible nonfocused tile should continue updating. Apply reduced-motion and rendering-quality options after frame profiling.

The live message cap is conditional (`messages_render.js:22`): roughly 200 eligible top-level rows at the default window, only during autoscroll, with active/selected groups protected. This does not bound all nested DOM or repeatedly loaded history. Introduce a retained-history window with scroll anchoring, preserving selected/live groups, IDs and reload cursors. Measure actual DOM nodes and heap before choosing thresholds.

Do not remove existing idle protections: resource hydration already runs only for the focused session, skips hidden tabs and uses a 120-second interval (`sse.js:380`). Healthy SSE does not generate a polling request on that timer.

### 5. Project maintenance

`core/project_maintenance.py:90` allows pending wiki sources to bypass the normal lazy-refresh interval. Each maintenance run invokes graph refresh and wiki scanning at lines 260 and 268. The wiki scanner reads and hashes eligible files at `core/project_wiki.py:101`, including unchanged source bytes.

Separate tree freshness checks from processing the already known backlog. Honor deferred/retry-ready states, coalesce changed paths, and use metadata/invalidation to limit rehashing while retaining periodic verification. Existing per-relay serialization, write debounce and incremental AST parsing are useful, but do not eliminate repeated full-byte hashing.

Validate with unchanged trees, repeated turns and blocked/deferred backlog: count scan invocations, bytes hashed, duration and pending jobs. This audit confirms the path, not its share of live CPU.

### 6. Server capacity and storage work

- **Event bus:** `core/conversation_event_bus.py:271` calls the global expiry sweep under the bus lock when buffering. Amortize that sweep; `_fresh_buffered` at line 424 must continue enforcing TTL at delivery. Measure lock hold time and resident buffer size, not just publication throughput.
- **Tool concurrency:** `tasks/ai/agent_tool_exec.py:501` sizes a fresh pool to the complete tool-call batch. Add a configurable per-agent/per-batch concurrency ceiling and fair admission for expensive tools; assess shared limits from measured peaks. Backgrounding does not end the underlying work. Preserve cancellation, independent call parallelism and result ordering.
- **Retries:** `engine/continuous_executor.py:93` defaults to unlimited retries; `engine/_continuous_exec_run.py:363` retains the executing worker across attempts, with retry waits capped at three seconds. Classify permanent errors and use explicit finite attempt budgets or delayed rescheduling where appropriate. Do not blindly alter durable flow semantics; exhaustion can roll a FlowFile back to its queue, so validate behavior across later scheduling cycles too.
- **Writer backlog:** `core/conversation_writer.py:118` has an unbounded queue and a thread per active writer. Track queued bytes, oldest-item age and enqueue-to-persist latency. Apply pressure upstream before accepting unbounded work; do not drop durable messages or block HTTP workers with a naive queue cap. FIFO batching and idle retirement already mitigate overhead.
- **Search index:** `core/conversation_index.py:372` updates indexed titles whenever a refreshed conversation has a title. Skip the statement if the stored title is unchanged. The predicate targets FTS metadata; use EXPLAIN and a synthetic populated index to quantify scan/lock cost before making stronger claims.
- **HTTP:** bounded short/long dispatch capacity already exists (`services/_http_server.py:51`), and waiting flow responses leave the short pool (`services/_http_request.py:519`). The flow engine also reserves interactive capacity (`engine/continuous_executor.py:464`). A wholesale async-server rewrite or larger thread limits is not the first intervention supported by this evidence.

## Delivery and verification sequence

1. Add bounded measurements at existing observability boundaries, then implement streaming coalescing, OpenSpace visibility handling and amortized expiry cleanup as separate reviewable changes.
2. Implement one-pass history with cursor/trace/rewrite regression coverage. Address repeated maintenance scans and unchanged-title updates.
3. Split startup assets and locales, verify compression at the real serving edge, and refine asset hashes.
4. Use sustained isolated load to set tool concurrency, retry and writer-pressure policies. Add history virtualization only with scroll/selection tests.

Acceptance measurements:
- Browser: accumulated rendering time, per-update p50/p95, long tasks, frame gaps, input latency, heap and DOM nodes; single and six visible conversations, short/long Markdown, mobile CPU throttling, history navigation, offscreen OpenSpace and hidden browser tabs.
- Server: route p50/p95/p99, short/long admission rejections, active threads, writer backlog bytes/age, persistence latency, SSE queue depth/overflow/reconnects, bus lock time, index lock time, maintenance scans/bytes, and relay/LLM/tool boundary timings.
- Load matrix: idle; one stream; six streams; deep-history navigation during streaming; many lightweight disconnected conversations; slow storage; failing dependencies; reconnect storms. Run against synthetic data and an isolated instance.
- Correctness: byte-identical completed output, no duplicate or missing messages, UUID/timestamp preservation, durable-before-final-event ordering, trace alignment, stable scroll, no stale cross-conversation updates, prompt cancellation, and state recovery after reconnect/restart.
- Treat performance targets as relative improvements from the same device and workload until an authenticated deployment baseline exists. Preserve the user's Chromium profile.

## Verification and reproduction

Existing tests: `tests/test_sse_streaming.py` and `tests/test_conversation_history.py`: **148 passed in 16.84 seconds**. These verify the current baseline, not unimplemented optimizations. Comparison scripts also assert identical fixture messages, final streaming text/HTML evidence and expired replay exclusion.

Evidence files are in the agent ScratchDir under `performance269/`: `benchmarks.py`, `benchmarks.json`, `comparisons.py`, `comparisons.json`. A FileStore archive accompanies this report so evidence survives ScratchDir expiry.

To reproduce through PawFlow, run with `bash(path='fs://scratchdir/performance269', ...)`. The scripts assume the source checkout at `/workspace` and the project's Python/Playwright environment. Compile the scripts first. The full original benchmark creates synthetic segmented transcripts if absent and should use background execution with durable output and a passive continuation if it will exceed one minute. Run `comparisons.py` after `benchmarks.py`; no real conversation payloads are required.
