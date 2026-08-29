# Website Creator Scaling and Hardening Plan

Status: implemented
Date: 2026-08-29
Owner: PawFlow core and first-party workflow agents
Baseline: PawFlow 1.0.0-beta.250 (`923dd4b6`)
Validation: 9,830 passed, 1 skipped, 2 xfailed; no failures (Python 3.12)

## 1. Problem statement

The shipped Website Creator (`pawflow.agents.website-creator:1.0.0`) recreates
only the part of the source site the model happens to choose, and it can
preserve only source images. Two user-visible gaps:

1. **Partial builds.** Nothing in the pipeline enumerates the source site, so
   the build phase covers whatever pages the model noticed during visual
   exploration. The explore schema caps `mapping` at 80 entries and the build
   schema accepts any non-empty `files_changed` list, so completeness is never
   structural.
2. **Image-only asset preservation.** `SaveWebsiteAssetHandler` accepts only
   PNG/JPEG/GIF/SVG/WEBP/AVIF responses, so CSS, JavaScript, fonts, media, and
   manifests cannot be preserved even when the user is authorized to reuse
   them. Template CSS, JavaScript, and fonts are re-authored instead of coming
   from a reviewed template artifact.

The documented DOM extraction path also types JavaScript into visible Chromium
DevTools, copies the result through the desktop clipboard, and reads it back
through a 15,000-character tool-output cap. That path is too fragile for a
large inventory.

The solution must not replace one unbounded model call with a larger unbounded
model call. Inventory, mapping, building, correction, and finalization must be
file-backed, bounded, resumable, and machine-verifiable.

## 2. Revised decisions

| # | Decision |
|---|----------|
| D1 | Chromium extraction uses a **dedicated visible workflow Chromium process** owned by the relay daemon and a private CDP pipe. No raw CDP TCP endpoint and no model-provided JavaScript expression are exposed. The tool accepts a server-side `script_id` plus validated options. |
| D2 | Site inventory is a deterministic, resumable **crawl state machine** before visual exploration. One task invocation performs at most one bounded network fetch; a `durableTimer` provides politeness delays without blocking a worker. |
| D3 | The effective crawl budget is shown through the existing `requestUserInput → durableWait → apply` pattern. The gate is skipped only when every safety-relevant bound is supplied explicitly. |
| D4 | Inventory details remain in workspace files. Flow state and model prompts contain only paths, hashes, counts, bounded summaries, and the current page batch. |
| D5 | Mapping, build, and correction operate in deterministic batches of at most 25 pages. A checkpointed coordinator aggregates batch files; increasing one JSON Schema array to 2,000 entries is forbidden. |
| D6 | Template artifacts are pinned by immutable version or commit and SHA-256. Only reviewed distributable roots are extracted; moving `main` archives are forbidden. |
| D7 | URL finalization distinguishes internal pages, first-party subresources, approved third-party subresources, outbound navigation, and active endpoints. Only unresolved required internal resources are blocking. Literal JavaScript strings are never rewritten mechanically. |
| D8 | Asset downloads stream relay-to-workspace with server-enforced per-kind and per-run byte limits. Large response bodies are never materialized in the PawFlow server or model context. |
| D9 | A bounded or error-bearing crawl cannot silently claim completeness. The user must accept the recorded omissions or adjust the limits before mapping begins. Source-asset reuse also requires an explicit rights/provenance declaration. |

## 3. Architecture overview

The workflow becomes:

```
agent_request
  → validate_request
  → prepare_request
  → prepare_crawl_decision
  → request_crawl_limits → wait_crawl_limits → apply_crawl_limits
  → initialize_site_crawl
  → fetch_crawl_entry
  → route_crawl_state
      ├─ queued → wait_crawl_delay ───────────────┐
      │                                           └→ fetch_crawl_entry
      └─ finished/bounded/errors
           → prepare_inventory_decision
           → request_inventory_approval
           → wait_inventory_approval
           → apply_inventory_decision
  → explore_sites
  → prepare_mapping_batches
  → map_page_batch ↔ route_mapping_batches
  → prepare_mapping_decision
  → request_mapping_approval
  → wait_mapping_approval
  → apply_mapping_decision
  → download_template
  → prepare_build_batches
  → build_page_batch ↔ route_build_batches
  → finalize_static_site
  → review_site
  → prepare_review_decision
      ├─ deterministic or visual failure
      │    → prepare_correction_batches
      │    → correct_page_batch ↔ route_correction_batches
      │    → finalize_static_site → review_site
      └─ passed
           → request_final_review
           → wait_final_review
           → apply_review_decision
  → format_result → complete_turn → agent_terminal
```

Human interaction remains a composition of the existing flow tasks. A custom
Python task must not publish a question and park itself.

All new Python tasks declare `AGENT_WORKFLOW_SAFE = True`, exact
`CapabilityEffect` values, an accurate `IdempotencyClass`, and a
`workflow_authorization_target` binding relay and resource paths when they
touch the selected website relay.

## 4. Work packages

### WP0 — Canonical inventory, URL map, and policy contracts

**Goal:** define the machine-owned data model before adding crawl or build code.

The run workspace contains:

```
workspace/
  inventory/
    index.json
    pages.ndjson
    assets.ndjson
    external-links.ndjson
    checkpoint.json
    complete.json
  mapping/
    batches.json
    batch-0001.json
  build/
    batches.json
    batch-0001.json
  reports/
    finalize.json
  template/
  site/
```

- `index.json` contains schema version, source URL, canonical origin, effective
  limits, policy, counts, and relative paths to the record files.
- NDJSON record files avoid rewriting a multi-megabyte JSON array for every
  checkpoint. Each append is followed by an atomic checkpoint replacement.
- `complete.json` is written atomically only after all records and hashes are
  durable. A partial directory is never treated as a reusable cache.
- Flow state contains only the index path, manifest digest, counts, statuses,
  batch cursor, and bounded issue summaries.
- URL canonicalization is specified and tested before crawling:
  - lower-case scheme and host;
  - normalize default ports and dot segments;
  - remove fragments;
  - preserve query strings by default;
  - apply an explicit allow/drop policy for known tracking parameters;
  - preserve trailing-slash semantics;
  - assign collision-safe local paths using a stable hash suffix where needed.
- Every reference is classified as one of:
  `internal_page`, `first_party_asset`, `approved_third_party_asset`,
  `external_navigation`, `active_endpoint`, or `ignored_scheme`.
- External navigation links are retained and checked syntactically, never
  mirrored and never treated as missing files.
- Forms, API endpoints, analytics, trackers, and executable source-site
  integrations are recorded but excluded from the static reproduction.
- The request or crawl-approval form records whether the user owns the source
  material or has permission to reuse selected CSS, JavaScript, fonts, media,
  and images. No asset is marked `keep` without provenance.

Tests cover URL equivalence, query variants, trailing slashes, redirects,
`<base href>`, path collisions, Unicode hosts/paths, and every reference
classification.

### WP1 — Crawl limits contract and durable confirmation

**Goal:** no crawl starts without explicit effective bounds.

`PrepareWebsiteRequestTask` parses structured request parameters first.
Free-text values are accepted only through strict labelled forms; ambiguous
phrasing falls back to confirmation.

Effective limits:

- `max_pages`: default 100, hard ceiling 2,000;
- `max_depth`: default 3, hard ceiling 8;
- `politeness_delay_ms`: default 750, minimum 250, hard ceiling 5,000;
- `request_timeout_seconds`: default 30, hard ceiling 120;
- `max_total_bytes`: default 256 MiB, hard ceiling 2 GiB;
- `max_duration_seconds`: default 1,800, hard ceiling 14,400;
- optional `include_url_patterns` and `exclude_url_patterns`.

Defaults live in the packaged and repository agent resources under `crawl`.

The flow uses four explicit stages:

1. `PrepareCrawlDecisionTask` builds a bounded typed form containing all
   effective values, source origin, patterns, and rights declaration.
2. Existing `requestUserInput` publishes the form.
3. Existing `durableWait` parks the deployed workflow.
4. `ApplyCrawlDecisionTask` validates the answer and stores the immutable
   effective limits in `state["website"]["crawl"]`.

The gate is skipped only if all six numeric limits and the rights declaration
are explicit. Include/exclude patterns may remain empty. Partial explicit
input is merged with defaults and still shown for confirmation.

The prepare/apply tasks are bounded state transforms. The request task keeps
its existing `RESOURCE_WRITE`, `MESSAGING_SEND`, and `KEYED_EFFECT`
contract; no interaction is declared `PURE`.

### WP2 — Deterministic, resumable crawl state machine

**Goal:** create a complete inventory within the approved budget without a
long-running blocking task.

New tasks:

- `InitializeSiteCrawlTask` (`initializeSiteCrawl`):
  - validates the source origin and effective limits;
  - resolves the exact selected relay from the workflow run context;
  - creates or validates the manifest directory;
  - reuses a completed manifest only when its cache contract matches;
  - seeds the queue and writes the first atomic checkpoint.
- `FetchSiteCrawlEntryTask` (`fetchSiteCrawlEntry`):
  - performs at most one network fetch per execution;
  - writes one page/sitemap/robots record and newly discovered queue entries;
  - updates byte, duration, page, depth, and error counters atomically;
  - sets the next relationship: `queued`, `finished`, `bounded`, or
    `failure`.
- `RouteSiteCrawlTask` performs the explicit route.
- Existing `durableTimer` waits until `next_allowed_at` before the next
  same-origin request. No `time.sleep` is used in workflow task code.

Relay and authorization rules:

- Every fetch uses the selected relay's `http_fetch` with
  `public_only=True`; every redirect is revalidated.
- The tasks resolve the relay using the same conversation binding and service
  registry discipline as the current Website Creator tool phase.
- `workflow_authorization_target` includes `relay_id` and the inventory
  paths under the run workspace.
- Effects are `NETWORK_READ`, `FILESYSTEM_READ`, and
  `FILESYSTEM_WRITE`. File-writing steps are keyed/idempotent by run,
  canonical URL, record kind, and manifest schema version.

Crawl behavior:

- Seed `/`, robots-declared sitemaps, and bounded conventional sitemap
  locations.
- Parse `robots.txt` for `Allow`, `Disallow`, `Sitemap`, and crawl-delay
  rules for the declared PawFlow user agent. A stricter server delay wins.
- Parse sitemap XML with entity expansion disabled and explicit compressed,
  decompressed, URL-count, and nesting limits.
- BFS follows canonical page URLs within the exact source origin only.
  Cross-origin references are classified and recorded, never followed.
- HTML responses have a per-response cap and must have an allowed HTML content
  type. Non-HTML page responses are recorded but not parsed as pages.
- Page records include requested URL, final URL, canonical URL, status, title,
  depth, content type, byte count, content hash, raw HTML path, referrer, and
  error if any.
- Extract HTML references from `href`, `src`, `srcset`, `poster`,
  `<source>`, stylesheet/icon/manifest links, inline style attributes, and
  `<style>` blocks. CSS transitive dependencies are expanded later by the
  asset pipeline.
- Honor all approved page, depth, byte, duration, and response limits.
- On 429/503, honor `Retry-After` when it fits the remaining total duration.
  If it does not, stop that origin as bounded instead of retrying sooner.
- A network or parse failure is recorded. It is not silently removed from
  completeness calculations.

Explicit cache contract:

- `RUN_CACHED` is metadata, not a cache implementation.
- Cache identity includes normalized source URL, effective limits, include/
  exclude policy, crawler schema/code version, and user rights policy.
- Reuse is limited to the current run unless the user explicitly requests a
  fresh crawl or a future shared-cache policy is approved.
- A completed manifest is reusable only when `complete.json` hashes every
  referenced record file. Partial checkpoints resume; they never masquerade as
  complete output.

After crawling, any `bounded` status or page error enters the inventory
approval gate. The form lists counts and bounded examples, links the complete
report, and offers: accept omissions, adjust limits and restart, or stop.
Only explicit acceptance can exclude a failed page from the final completeness
contract.

Tests use an injected fake relay HTTP service, fake resolver, and fake clock.
They do not use localhost through the public-URL validator. Fixtures cover
robots rules, nested/compressed sitemap limits, redirects, canonicalization,
query explosions, 429/503, byte and duration budgets, crash/resume, atomic
completion, and cache mismatch.

### WP3 — Generalized streaming asset downloader

**Goal:** preserve authorized manifest assets without unbounded memory use.

`SaveWebsiteAssetHandler` becomes a general `save_source_asset` handler:

- `kind` is required. The old image-only caller is migrated in the same
  change; no compatibility default is retained.
- Supported kinds: `image`, `stylesheet`, `script`, `font`, `media`,
  and `manifest`.
- `other` may be inventoried but is never automatically downloaded or counted
  as required.
- The relay gains a bounded `http_fetch_to_file` operation:
  - `public_only=True` and redirect revalidation are mandatory;
  - destination is confined to a temporary file in the run workspace;
  - the relay enforces declared and streamed byte limits before atomic rename;
  - response metadata and SHA-256 are returned without returning the body.
- Per-file caps: images 12 MiB, CSS/JavaScript 5 MiB, fonts 5 MiB, media
  64 MiB, manifests 2 MiB. The approved run also has a total asset-byte budget.
- MIME validation fails on conflicting declared types. Extension fallback is
  allowed only for missing/generic content types and must be confirmed by
  parsing or a recognized signature.
- CSS and manifests are parsed before acceptance. Fonts and media require
  recognized signatures/container headers. HTML error pages with a `.js` or
  `.css` URL are rejected.
- Same-origin assets may be selected under the user's rights declaration.
  Third-party assets require an explicit approved origin, immutable URL when
  available, license/provenance entry, and user approval.
- Analytics, tracking pixels, ads, active endpoints, and source application
  bundles are skipped by default.
- Asset decisions are written in bounded manifest batches. The model never
  emits one unbounded `assets_kept` array.

Tests cover MIME/signature conflicts, redirects, path confinement, streaming
caps, total budgets, partial-file cleanup, atomic rename, origin policy,
provenance, and replay after interruption.

### WP4 — Reproducible template package download

**Goal:** build from a reviewed, immutable template distribution.

`DownloadTemplateTask` (`downloadTemplate`) uses a shipped catalog with:

`{provider, name, version, package_url, sha256, license, attribution,
artifact_root}`.

- Start Bootstrap entries point to immutable release tags or commit archives.
- HTML5 UP entries point to reviewed immutable artifacts mirrored or published
  with a recorded SHA-256. A mutable upstream zip cannot be silently accepted.
- ThemeWagon and unknown providers remain preview-only until equivalent
  provenance is added.
- Fetch uses the streaming bounded relay operation with a 50 MiB archive cap.
- Extraction reuses the package installer's path, entry-size, total-size,
  compression-ratio, file-count, absolute-path, symlink, and traversal guards.
- Only `artifact_root` is copied into `workspace/template/`; repository
  tooling and development-only files are excluded.
- Package hash and catalog version are part of the cache identity.
- License and attribution files are copied into the final site output, not
  merely left in the temporary template directory.
- The final result surfaces template identity, immutable source, hash, license,
  and required attribution.

Tests cover catalog pinning, hash mismatch, mutable URL rejection, extraction
attacks, artifact-root confinement, license propagation, and cache invalidation.

### WP5 — Fixed-script Chromium extraction

**Goal:** extract rendered DOM evidence reliably without exposing arbitrary
browser evaluation.

The relay launches a dedicated Website Creator Chromium process on the visible
desktop:

- It uses a run-scoped profile separate from the user's persistent general
  Chromium profile.
- The relay daemon owns the process and its CDP pipe
  (`--remote-debugging-pipe`); no TCP debug port is opened.
- Lifecycle, target identity, profile directory, and cleanup are bound to the
  workflow run. A crashed relay reports a resumable extraction error.

A new server handler/tool `browser_console_extract` is added end to end:

- server `ToolHandler` and schema;
- relay protocol action and implementation;
- phase registry mapping and confinement hook;
- capability declarations and tests.

Parameters:

- `script_id` required, selected from a server-side catalog such as
  `rendered_inventory_v1`, `dom_outline_v1`, or `computed_assets_v1`;
- `target_id` required and issued by the workflow browser session;
- validated bounded `options`;
- optional workspace-relative `write_to`;
- timeout default 10 seconds, ceiling 30 seconds.

There is no `expression`, `url_prefix`, or model-authored JavaScript escape
hatch. Each script validates that the target's final origin equals the approved
public origin.

Large results:

- maximum serialized result size is 32 MiB and also counts against the run
  inventory budget;
- the fixed script stores serialized output in the target and the relay reads
  fixed-size chunks through CDP;
- chunks are hashed and written atomically to the confined workspace path;
- model output receives only path, bytes, SHA-256, schema version, counts, and a
  bounded preview;
- WebSocket/message limits are configured explicitly and tested.

Effects are `BROWSER_CONTROL` and `FILESYSTEM_WRITE`. The tool is exposed
only in Website Creator explore/build/review/correct phase registries and never
in the default agent registry.

Older relay fallback is the existing visible DevTools/clipboard method using
the same shipped fixed script and chunks no larger than 14,000 characters.
Fallback is recorded as `extraction_mode: "cdp_pipe" | "clipboard"`; it does
not reintroduce arbitrary expressions. A missing extractor capability can also
be configured to stop rather than degrade.

Tests use a fake CDP transport and cover target binding, origin mismatch,
unknown script IDs, timeout, maximum size, chunk ordering, hash verification,
path confinement, profile isolation, relay disconnect, and tool absence
outside the allowed phases.

### WP6 — Batched mapping/build and deterministic completeness gates

**Goal:** scale by partitioning work while keeping completeness machine-owned.

Batch coordinators:

- `PrepareWebsiteMappingBatchesTask` partitions accepted inventory pages into
  stable batches of at most 25 entries and writes `mapping/batches.json`.
- `MapWebsitePageBatchTask` runs the existing visual/model phase for only the
  current batch, writing a schema-validated batch result.
- `MergeWebsiteMappingTask` verifies exactly-once coverage and produces a
  bounded summary for mapping approval. The approval form links the full files.
- Equivalent prepare/run/merge tasks drive build and correction batches.
- Batch identity is a digest of manifest digest, ordered canonical page URLs,
  template digest, phase schema version, and approved mapping revision.
- Completed matching batches replay from their result files; changed inputs
  invalidate only affected batches.
- Flow state carries cursors and digests, never all page records.

Schemas:

- Mapping batch entries:
  `{page_url, local_path, template_component, implementation, notes}`,
  maximum 25.
- Build batch result:
  `{pages_built, skipped_pages, assets_materialized, files_changed,
  validation, remaining_issues}`, with every identifier referencing the
  accepted manifests.
- Skips require a typed reason and are allowed only by the approved inventory/
  mapping policy. A model cannot unilaterally skip an accepted required page.
- The correction coordinator derives affected batches from deterministic and
  visual issues; a global correction remains possible only through an explicit
  user request.

`FinalizeStaticSiteTask` runs after merged build and after each merged
correction:

1. **HTML rewriting**
   - rewrite internal page links and required local subresources through the
     canonical URL map;
   - handle `href`, `src`, `srcset`, `poster`, `<source>`, and
     `<base>`;
   - retain external navigation links;
   - remove or disable active endpoints according to policy.
2. **CSS rewriting**
   - parse stylesheets and rewrite `url(...)` and `@import` through the
     manifest;
   - resolve transitive dependencies;
   - never use regex-only rewriting.
3. **JavaScript**
   - do not rewrite literal strings;
   - copied source application bundles are prohibited by default;
   - authored template scripts are checked only for explicit static references.
4. **Link and policy check**
   - blocking: unresolved required internal page or `keep` subresource,
     path escape, missing attribution, prohibited active endpoint, or manifest
     digest mismatch;
   - non-blocking report: external navigation and approved third-party assets.
5. **Completeness**
   - every accepted inventory page has exactly one local output;
   - every required asset has a matching hash-verified local file;
   - every accepted omission has a durable user decision.

The task writes `reports/finalize.json` atomically. Its replay key includes
hashes of every generated HTML/CSS file, the URL map, manifests, template, and
policy. `RUN_CACHED` alone is not treated as implementation.

Deterministic blocking issues route directly to correction batches before the
visual reviewer. The reviewer receives only report path/hash/counts and a
bounded issue summary. It cannot override a deterministic failure.

Tests cover batch partition/replay/invalidation, exactly-once coverage,
HTML/CSS fixtures, external-link classification, collision-safe paths,
transitive CSS assets, no JavaScript mutation, deterministic routing,
attribution, and file-hash cache invalidation.

### WP7 — Registration, versioning, documentation, and rollout

Code registration:

- register every new task with `TaskFactory`;
- export it from `tasks.ai.workflow` and the central task registration path;
- add the new server handler and relay action to their registries;
- update tool schemas, task definitions, capability metadata, and reference
  documentation.

Capability ceilings:

- add `network.read` for crawl/template/asset reads;
- add `browser.control` for fixed-script CDP extraction;
- retain the existing filesystem/resource/messaging effects;
- update both packaged and repository agent resources;
- validate that each task's effects are within the flow ceiling.

Versioned resources:

- create
  `data/repository/flows/global/pawflow/agents/website-creator/versions/1.1.0.json`;
- update the corresponding `latest.json`;
- update `data/repository/agents/global/website-creator.md`;
- update
  `packages/pawflow.website-creator.pfpdir/content/flows/website-creator.json`;
- update the packaged agent's `flow_fqn`, parameters, effects, and prompt;
- bump `packages/pawflow.website-creator.pfpdir/pfp.json` and all object FQNs
  to 1.1.0;
- build, inspect, sign, install-smoke, and uninstall-smoke the package;
- keep repository and packaged flow definitions byte-for-byte equivalent where
  the existing tests require it.

Documentation:

- update `docs/WEBSITE_CREATOR_WORKFLOW_AGENT.md`;
- update `docs/WORKFLOW_AGENT_OPERATIONS.md`;
- update `docs/02_REFERENCE_TASKS_SERVICES.md`;
- document relay image/capability requirements and the fixed-script fallback;
- change this plan to `implemented` only after all gates pass.

Test matrix:

- task unit tests for every parser, bound, transition, and idempotency key;
- fake-relay integration tests for crawl and streaming download;
- fake-CDP tests for the fixed-script transport;
- full workflow tests with multiple mapping/build batches, interruption,
  resume, bounded crawl approval, deterministic correction, and acceptance;
- package/resource/version synchronization tests;
- authorization tests proving relay/path/origin confinement and tool absence
  outside Website Creator phases;
- existing Website Creator, workflow-agent, PFP, relay, and full-suite tests
  remain green.

Rollout:

1. Ship the relay extractor capability first or retain the tested clipboard
   fallback.
2. Publish flow/package 1.1.0 alongside 1.0.0; do not mutate an installed
   1.0.0 definition in place.
3. Point the packaged and repository agent to 1.1.0 only after capability
   negotiation and package smoke tests pass.
4. Existing in-flight 1.0.0 runs finish on their pinned definition.
5. New 1.1.0 runs record extractor mode, manifest schema, template digest, and
   all accepted omissions in the final result.

## 5. Sequencing and dependencies

```
WP0 canonical contracts
  ├→ WP1 limits + durable confirmation
  ├→ WP2 resumable crawl
  ├→ WP3 streaming assets
  └→ WP6 batching/finalizer foundations

WP4 pinned templates ───────────────┐
WP5 fixed-script Chromium ─────────┼→ WP6 complete integration
WP2 inventory + WP3 assets ────────┘
WP6 → WP7 registration/version/docs/rollout
```

Implementation order:

1. WP0 data contracts and adversarial URL/reference tests.
2. WP1 durable gate and flow wiring.
3. WP2 crawl state machine and inventory approval.
4. WP3 streaming transport and asset policy.
5. WP4 immutable template catalog.
6. WP5 fixed-script visible-browser extraction.
7. WP6 batched model work and deterministic finalizer.
8. WP7 versioned resources and rollout.

Line-count estimates are planning hints, not acceptance criteria. Each work
package lands with its tests and documentation; unrelated packages are not
bundled into one implementation commit.

## 6. Acceptance gates

The feature is ready only when all of the following are proven:

- A multi-page fixture produces one canonical record and one intended output
  for every accepted page.
- A 2,000-page synthetic manifest is partitioned without placing the full
  inventory or mapping into one model prompt or response.
- Crawl interruption and relay restart resume from the last atomic checkpoint
  without duplicate records or repeated user interactions.
- Page, depth, byte, duration, response, sitemap, and delay bounds all stop at
  deterministic edges.
- A bounded/error crawl cannot reach mapping without an explicit durable user
  decision.
- External navigation does not fail finalization; an unresolved required
  internal asset does.
- A 64 MiB allowed media fixture streams to disk under bounded server memory,
  while a one-byte-over-limit fixture fails and leaves no partial file.
- Unknown MIME, hash mismatch, archive traversal, zip bomb, and mutable template
  artifacts fail closed.
- No Website Creator tool accepts arbitrary JavaScript for CDP evaluation.
- The extractor cannot target another origin, browser session, relay, or path.
- Deterministic failures cannot be overridden by the visual reviewer.
- Package 1.1.0, repository resources, capability ceilings, layouts, and
  documentation are synchronized.
- Targeted security tests and the complete PawFlow test suite pass.

## 7. Security and policy invariants

- Every public network request uses relay validation with
  `public_only=True`, including every redirect.
- Every file write is confined to the authorized run workspace and uses atomic
  completion for reusable artifacts.
- No shell, package installation, headless automation, git operation, or
  deployment is exposed to Website Creator phases.
- Browser extraction uses fixed reviewed scripts over a relay-owned private
  transport. There is no arbitrary `Runtime.evaluate` tool surface.
- General persistent Chromium profile state is never exposed to the workflow
  extractor.
- Crawl and download enforce per-request, per-file, total-byte, total-duration,
  and workspace quotas before data crosses a trust boundary.
- Crawled content, manifests, template files, DOM results, and tool output are
  untrusted data, never instructions.
- Source and template provenance are recorded. Third-party reuse requires an
  approved origin and license/provenance entry.
- External links remain links; they are not silently mirrored or converted to
  failures.
- Deterministic policy/completeness gates cannot be bypassed by an LLM verdict.

## 8. Explicit non-goals

- No backend behavior, authenticated-area reproduction, form processing, API
  emulation, or arbitrary single-page application cloning.
- No claim of completeness beyond the accepted crawl manifest and recorded
  omissions.
- No automatic continuation after a crawl reaches a bound or records page
  errors.
- No mirroring of third-party assets without explicit origin, provenance, and
  user approval.
- No automatic copying or execution of source-site JavaScript application
  bundles.
- No literal-string rewriting of JavaScript.
- No model-authored CDP expression or unrestricted browser console.
- No reuse of mutable template branch archives.
- No automatic crawl above the confirmed limits.
