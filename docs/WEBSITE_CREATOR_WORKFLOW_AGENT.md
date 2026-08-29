# Website Creator Workflow Agent

The first-party Website Creator is a durable Workflow Agent that recreates the
accepted content and information architecture of one public source website as a
new static site built from one reviewed immutable template artifact.

The shipped package is `pawflow.website-creator:1.1.0` and the active flow
binding is `pawflow.agents.website-creator:1.1.0`. Version 1.0.0 remains
immutable for existing pinned and in-flight runs.

## Requirements

A run requires:

- a visible, enabled, vision-capable `summarizer` or `llmConnection` service
  bound as `creator_llm`;
- one explicit, default, or sole linked relay providing public HTTP,
  filesystem, and visible desktop access;
- a public HTTP(S) source URL;
- a second public HTTP(S) URL matching the exact immutable `package_url` of one
  entry in the shipped template catalog;
- a crawl contract and a source-rights declaration, either supplied completely
  in the request or approved through the first durable form;
- user authority for every source asset and template element reused in the
  result.

The request parser rejects local, private, credential-bearing, unresolvable, or
non-HTTP(S) URLs. Every reusable artifact is confined to
`/workspace/pawflow-sites/<run_id>` unless an operator configured another
absolute `workspace_root`.

The relay must implement `http_fetch_to_file`, `hash_file`,
`atomic_write_file`, and `extract_zip_subtree` in addition to ordinary
filesystem and `screen`/`see` operations. The preferred extraction path also
requires `website_browser_start`, `browser_console_extract`, and
`website_browser_stop`. The Managed Desktop relay must allow execution and
automation inside its Linux relay container and provide one of `chromium`,
`chromium-browser`, `google-chrome`, or `google-chrome-stable`.

No relay service name is hard-coded. The exact explicit, default, or sole
linked relay is frozen for the run and used for every network, desktop, and
filesystem operation.

Managed Desktop automation is independent from server-local execution and host
screen access. Website Creator never enables those broader surfaces.

## Inputs and default crawl contract

`source_url` and `template_url` may be bound on the agent or supplied, in that
order, in the user message. An empty `relay` selects the concrete default or
sole linked relay. Clearing `workspace_root` restores
`/workspace/pawflow-sites`.

The default crawl contract is:

| Bound | Default |
|---|---:|
| Maximum accepted pages | 100 |
| Maximum link depth | 3 |
| Politeness delay | 750 ms |
| Per-request timeout | 30 s |
| Total response bytes | 256 MiB |
| Total crawl duration | 1,800 s |
| Include/exclude URL patterns | none |

Defaults are not silent consent. If any safety-relevant bound or the rights
declaration is absent, the workflow shows the effective contract and parks
durably before the first network request. A complete explicit contract skips
only that form; it does not weaken URL, origin, robots, sitemap, byte, duration,
or workspace enforcement.

## Durable workflow

The 1.1.0 graph has 42 presented tasks and four possible durable decision
boundaries:

1. validate the server-owned request, public URLs, relay, and stable workspace;
2. freeze the crawl limits and rights declaration, using a durable form when
   the request is incomplete;
3. crawl one public same-origin entry per task invocation, checkpoint every
   record atomically, and use `durableTimer` for politeness delays;
4. when the crawl is bounded or records errors, require the user to accept the
   listed omissions, adjust limits and restart, or stop;
5. partition the accepted inventory into stable mapping batches of at most 25
   pages, run only the current batch, and verify exactly-once coverage;
6. present the complete file-backed mapping summary and park before template or
   site mutation;
7. download, hash-check, and safely extract the approved immutable template;
8. build every approved page in replayable batches of at most 25;
9. deterministically materialize `site/`, rewrite HTML and CSS references,
   preserve external navigation, disable active endpoints, hash outputs, and
   produce the completeness report;
10. route deterministic failures to the affected correction batches before any
    visual reviewer can pass the site;
11. inspect the rendered result through visible Chromium and vision;
12. after visual success, wait durably for Accept or Revise. Revise selects the
    affected pages, reruns correction batches, finalizes again, and repeats
    without an implicit pass limit.

No implicit pass count, timeout, or deadline applies. Only an explicit user or
operator bound, Stop, or Force stop ends an otherwise valid review loop.

Rejecting or stopping at a decision boundary returns the typed workflow result
`no_change` while the durable run itself commits as completed. It is not an
execution error.

Flow state contains only paths, hashes, cursors, counts, and bounded summaries.
Complete crawl records, mappings, batch inputs/results, asset ledgers, and
finalizer evidence stay in run-workspace files. Matching completed batches
replay from disk; a changed inventory, template digest, mapping revision, or
affected-page set invalidates only the dependent work.

## Crawl and completeness

The crawler canonicalizes public same-origin URLs, honors robots and bounded
sitemaps, rejects query explosions and private redirects, and checkpoints
queued, in-flight, fetched, skipped, and failed entries. A completed inventory
is reusable only when its completion manifest hashes every referenced record
file.

A `bounded` or `errors` inventory cannot reach mapping automatically. The
inventory decision records the original status, reasons, bounded issue
examples, feedback, and timestamp. Final completeness is defined only against
the accepted manifest plus these explicit omissions; Website Creator never
claims completeness beyond that contract.

## Immutable template catalog

Catalog version 1 currently contains:

| Template | Immutable input URL | License |
|---|---|---|
| Start Bootstrap Creative 7.0.7 | `https://codeload.github.com/StartBootstrap/startbootstrap-creative/zip/b1762d8c690a2379c078c776dc0830bdd81c6f55` | MIT |
| HTML5 UP Identity `be7721e3` | `https://codeload.github.com/html5up/identity/zip/be7721e3a3c17ba44da0f63df57617fdaf7ee491` | CC BY 3.0 |

The executable 1.1 request contract requires a public URL, so use the exact
catalog URL above rather than the internal shorthand
`provider:name:version`. The relay streams at most 50 MiB, verifies the
catalogued SHA-256, rejects redirects away from the immutable URL, and extracts
only the reviewed `artifact_root` with traversal, symlink, entry-count,
expanded-size, and compression-ratio guards. License and attribution are
written into `site/THIRD_PARTY_NOTICES.txt` and surfaced in the final result.

ThemeWagon and unknown providers remain preview-only. Mutable branches,
`latest` archives, authenticated downloads, paywalls, and unreviewed template
packages fail closed.

## Fixed-script Chromium extraction

The preferred mode is `cdp_pipe`. The relay launches a dedicated visible
Chromium process with `--remote-debugging-pipe` and no debug TCP port. Its
target, approved public origin, session ID, and profile are bound to the
workflow run. The profile is isolated below the run workspace and deleted when
the session stops; the user's persistent Chromium profile is never opened or
cleaned.

The model cannot provide JavaScript. `browser_console_extract` accepts only one
reviewed `script_id`:

- `rendered_inventory_v1`;
- `dom_outline_v1`;
- `computed_assets_v1`.

Options are closed and bounded, the target and final origin are revalidated,
one CDP message is capped at 2 MiB, and one serialized extraction is capped at
32 MiB and charged to the run inventory budget. Large results are read in fixed
chunks, hashed, and atomically stored in the workspace; the model receives only
the path, byte count, hash, schema, counts, and bounded preview.

If an older relay cannot start the CDP-pipe session, the default task records
`extraction_mode: "clipboard"`. The visible desktop path may then execute only
the same shipped fixed script through `screen` clipboard actions in chunks of
at most 14,000 characters. It does not restore arbitrary expressions. A custom
flow may set `browser_extractor_required: true` on Website Creator tool or page
batch tasks to fail instead of using this fallback.

## Asset policy

`save_source_asset` supports `image`, `stylesheet`, `script`, `font`,
`media`, and `manifest`. Per-file limits are:

| Kind | Maximum |
|---|---:|
| Image | 12 MiB |
| Stylesheet or JavaScript | 5 MiB |
| Font | 5 MiB |
| Media | 64 MiB |
| Manifest | 2 MiB |

Downloads stream relay-to-workspace and enforce the approved total asset-byte
budget. Declared MIME, extension fallback, parser results, and recognized
signatures must agree before atomic publication. HTML error pages disguised as
CSS or JavaScript, partial files, unknown formats, and over-budget responses
are rejected.

Same-origin reuse follows the accepted rights declaration. Third-party assets
require an approved origin, provenance and license, an immutable URL when
available, and explicit approval. Analytics, trackers, pixels, ads, active
endpoints, and source application bundles are skipped by default. Asset
decisions are checkpointed in manifest files of at most 25 entries.

## Deterministic finalization

The finalizer parses HTML and CSS instead of applying global text replacement.
It resolves collision-safe local paths, rewrites internal page and approved
asset references, preserves outbound links, leaves literal JavaScript strings
untouched, and disables form or endpoint behavior that a static site cannot
implement.

It validates page coverage, unresolved required internal resources, transitive
CSS assets, asset hashes, template attribution, and accepted omissions. The
atomic report includes its replay key, input digests, counts, output hashes,
and bounded blocking issues. A failed report always returns to correction; an
LLM reviewer cannot override it.

## Tool and authorization boundary

Website Creator phases expose only their closed tool lists:

- exploration and review: visible desktop inspection, fixed extraction,
  supplementary public fetch, and confined reads/search;
- build and correction: the same inspection surface plus confined
  read/write/edit and approved asset download.

The phases do not expose a shell, arbitrary browser evaluation, test-code execution,
arbitrary patch paths, package installation, Git, deployment, private URLs, or
paths outside the run workspace. `network.read` and `browser.control` are part
of the exact 1.1 flow ceiling; they do not broaden the default agent registry.

Tool calls from CLI providers use an ephemeral server-owned scope keyed to the
workflow run and task. Relay and path arguments are rewritten and checked
before dispatch. Authorization attempts, blocked calls, relay errors, or
background placeholders do not satisfy required visual observations.

## Recovery and stopping

Crawl records, batch inputs/results, manifests, reports, durable interactions,
and run checkpoints survive server restart. A retry resumes the same run and
reuses matching keyed effects; it does not submit a replacement generation.
Changing an input digest causes deterministic selective replay.

Tool turns have no task-local implicit timeout. They end when the provider
returns, the user explicitly stops the run, or the workflow reaches an explicit
global duration limit. Stop and Force stop propagate through `abort()`. Relay
disconnect during fixed extraction is a resumable run error; cleanup also
terminates relay-owned sessions and removes only the isolated run profile.

## Version 1.1 scope

Website Creator produces a self-contained static HTML/CSS/JavaScript site. It
does not reproduce backend behavior, authenticated areas, checkout, CMS or
database state, server-side routing, form processing, private APIs, or
third-party credentials. It does not import or build an arbitrary Node, React,
or Vue project and does not automatically clone an application bundle.

The source and template remain untrusted data. Visual similarity never grants
copyright, trademark, privacy, or provider-term rights.

## Test from chat

1. Install `pawflow.website-creator:1.1.0` and add the global
   `website-creator` agent to a conversation.
2. Bind a vision-capable `creator_llm` and a Managed Desktop relay meeting the
   capability requirements above.
3. Send the public source URL followed by one exact immutable catalog URL.
4. Confirm the crawl contract and rights form appears before network access
   when the request omitted any required field.
5. Exercise a clean inventory and a bounded/error inventory. Verify that only
   explicit Accept can continue with omissions and Adjust starts a fresh
   bounded crawl.
6. Approve the mapping and verify that the template manifest, attribution,
   page batches, asset manifests, final site, and deterministic report are
   confined to the stable run workspace.
7. Inspect the run and record `cdp_pipe` or `clipboard`. In strict-mode canaries,
   prove an unavailable extractor fails before model-authored browser code can
   run.
8. Confirm a deterministic failure returns to correction before visual review.
9. Confirm visual `passed=false` returns to correction without a user decision.
   After `passed=true`, choose Revise at least once and then Accept.
10. Restart the server while parked at a durable gate and confirm the same
    request, run ID, workspace, cursors, and completed batches resume without
    duplicate interaction or provider submission.
11. Inspect the terminal result for the workspace artifact, accepted omissions,
    inventory/mapping/template/finalizer digests, extractor mode, counts,
    attribution, tool calls, and correction passes.

The Workflow Run view should refresh in real time, highlight the current
presented task, and show bounded progress, tool starts/completions, errors,
usage, artifacts, and exactly one terminal response.