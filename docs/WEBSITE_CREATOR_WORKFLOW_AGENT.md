# Website Creator Workflow Agent

The first-party Website Creator is a durable Workflow Agent that recreates the
content and information architecture of one public source website as a new
static site using the visual language of one public template preview.

It is available without a feature flag. The shipped binding is
`pawflow.agents.website-creator:1.0.0`.

## Requirements

- a visible, enabled `summarizer` or `llmConnection` service bound as
  `creator_llm`;
- one connected relay selected for the Website Creator conversation agent;
- two public HTTP(S) URLs in the request, in this order: source site, then
  template preview;
- user authority for every source, image, font, and template element reused in
  the generated result.

The agent rejects local, private, credential-bearing, unresolvable, or
non-HTTP(S) input URLs. Each run writes only under
`/workspace/pawflow-sites/<run_id>`.

Starting the Workflow Agent grants only the phase capability declared by its
server-side tool scope. Calls that pass that scope's allowlist and argument
guard do not need a second live approval subscriber, so an unattended run can
use its declared `screen`, `fetch`, and workspace-confined file tools. Explicit
per-tool `deny` or `confirm`, read-only mode, policy gates, and catastrophic
command confirmation remain authoritative; the workflow never changes the
conversation permission mode itself.

In the add-agent form, `relay`, `source_url`, and `template_url` may be left
empty. An empty relay uses the concrete default or sole linked relay, while the
two URLs are then read from each request. Clearing `workspace_root` restores
the `/workspace/pawflow-sites` contract default.

## Durable workflow

The functional layout is the executable workflow and contains labelled,
described, colored frames for these stages:

1. validate the request and reserve the stable run workspace;
2. inspect both sites through visible Chromium with `screen` and `see`;
3. present the complete source-to-template mapping and wait durably;
4. stop without writing when the mapping is rejected, or generate the approved
   static HTML/CSS/JavaScript site when it is approved;
5. inspect the rendered result visually and present a final durable decision;
6. finish only when explicitly accepted; otherwise apply the latest feedback,
   review the result again, and repeat the durable decision loop.

Rejecting the mapping produces the typed workflow result `no_change` while the
durable run lifecycle commits as `completed`. Parent flows and terminal events
retain `no_change` for routing; the run store does not treat a user rejection
as an execution failure.

The build and correction phases expose only confined file read/write/edit and
search tools, a public-image downloader, plus desktop inspection. Chromium
DevTools JavaScript can inventory the rendered DOM and asset URLs through the
bounded `clipboard_write` / `clipboard_read` screen actions. Each selected public
image is revalidated, size-limited, written only below the run workspace, and
referenced locally by the generated site. The phases do not expose a shell, test-code
execution, arbitrary patch paths, package installation, Git, deployment, or
headless browser automation. Public fetch is supplementary evidence; desktop
inspection is mandatory during exploration.

The downloader uses the filesystem relay selected for the workflow run for both
the HTTP fetch and the file write. No relay service name is hard-coded. Public-only
URL and redirect validation plus the 12 MiB response limit are enforced by that
selected relay transport.

CLI providers that expose PawFlow through MCP are restricted by an ephemeral
server-side scope keyed to the workflow run and task. Tool discovery, direct
calls, and lazy `use_tool` calls all use the phase allowlist; filesystem
arguments are rewritten to the selected relay and run workspace before
dispatch, while `screen` and `see` are explicitly pinned to that same relay so
the provider's ephemeral conversation identity cannot lose the desktop or file
service selection. Passing this bounded scope satisfies only the generic interactive
approval that would otherwise be requested in default mode. Provider-owned
session suffixes are resolved only when
they match the exact `__ephemeral_<32 hex>` format, so an isolated CLI session
keeps the workflow scope without broadening it to arbitrary conversation IDs.
Workflow sub-conversations inherit the parent conversation's linked relay and a
required tool counts as observed only after it returns a successful result;
authorization attempts, relay errors, blocked calls, and background placeholders
cannot satisfy the visual inspection gate.
The scope is removed when each model turn finishes.

Every tool phase publishes its model attempts, required-tool correction, tool
starts/completions, and terminal phase state as redacted workflow progress. If
the model first answers with text instead of calling a tool, the task sends one
explicit correction requiring tool use and `submit_website_phase`. A second
tool-free response fails the run immediately; it cannot start an unbounded
prompt loop or replay the original inbox message. A CLI provider may instead
return the phase result as exact JSON text when the local submission tool is not
exposed. The phase prompt includes that exact closed JSON Schema; PawFlow accepts
the returned object only when it validates against the same schema. Provider
statuses beginning with `blocked`, `denied`, `error`, or `failed` are surfaced as
explicit phase blockers rather than misclassified as malformed submissions.
When a provider returns a complete object missing only final `]` or `}` delimiters,
PawFlow restores those delimiters and still validates the result against the closed
schema. This includes one or more missing nested closers immediately before terminal
outer closers; it does not repair mismatches followed by more content. It never
repairs a response cut inside a string or invents missing data.
The run inspector renders valid structured responses as fields and lists; malformed
structured text is replaced by a readable incomplete-response notice instead of
displaying raw JSON.

Tool turns have no task-local or implicit timeout. They end when the provider
returns, when the user explicitly stops the run, or when the workflow reaches
its declared global duration limit. An explicit Stop or Force stop is propagated
to the provider client through `abort()`.

## Initial template catalogs

Version 1 accepts a public live-preview URL rather than a marketplace account or
an automated download. The initial reviewed catalogs are:

| Catalog | v1 status | License handling |
|---|---|---|
| [HTML5 UP](https://html5up.net/) | Supported for public template previews | Templates use [Creative Commons Attribution 3.0](https://html5up.net/license); preserve the required design credit unless the user supplies separate attribution-free rights. |
| [Start Bootstrap](https://startbootstrap.com/themes) | Supported for public previews of free/open-source themes | Use the corresponding official repository and retain its license; the free themes, such as [Creative](https://github.com/StartBootstrap/startbootstrap-creative), are released under MIT. |
| [ThemeWagon](https://themewagon.com/theme-price/free/) | Preview inspection only; not supported for generation by default | Its [license](https://themewagon.com/license/) varies by author, may require attribution, and restricts generator/derivative-theme use. Proceed only when the user provides a license that explicitly permits this workflow. |

Any other provider is unsupported until its public-preview behavior and license
have been reviewed. The agent never bypasses authentication, paywalls, download
controls, or attribution.

## Version 1 scope

Version 1 generates a self-contained static site. It does not reproduce backend
behavior, authenticated areas, checkout, CMS data, databases, server-side
routing, or third-party API credentials. It does not import or build an existing
Node/React/Vue project. Interactive behavior must be expressible with ordinary
browser-side HTML, CSS, and JavaScript.

The template URL is a design reference. The workflow does not grant permission
to copy proprietary code or assets, and visual similarity does not override
copyright, trademark, privacy, or provider terms.

## Test from chat

1. Add the global `website-creator` agent to a conversation and select the
   connected relay and creator LLM service.
2. Send a request containing a public source URL followed by a supported public
   template preview URL.
3. Confirm that the mapping form appears before any project files are written.
4. Approve the mapping, then inspect the generated run directory and visual
   review report.
5. Choose `accepted` to finish, or `revise` with feedback. Every revision resumes
   the same run and workspace, applies that feedback, performs another visual
   review, and asks again until `accepted` is chosen.
6. Keep the Workflow Run view open and confirm that it refreshes in real time,
   highlights the selected/current block, and shows model attempts, tool
   starts/completions, errors, usage, artifacts, and the single terminal
   response.

When another agent delegates to Website Creator, PawFlow routes the Workflow
Agent through shared context automatically. Durable questions still go to the
user, and the correlated terminal reply wakes the delegating agent once.
