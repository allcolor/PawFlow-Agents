# PawFlow Website Plan

## Current Assessment

The existing `pawflow-website` directory is only a prototype landing page. It is not published, has no routing/build contract to preserve, and its `/docs` links currently point to pages that do not exist inside the website folder. The safest approach is a clean static rebuild rather than incremental edits.

Strengths to keep:

- Clear core product claim from the root README: self-hosted agent runtime for real infrastructure.
- Existing repo docs already cover architecture, providers, Docker install, PawCode, media tools, security, and examples.
- Existing generated assets can be reused temporarily, but new media should be more product-specific and less generic.

Problems to fix:

- Quickstart command is stale: use `python cli.py start --host 0.0.0.0 --port PORT` for source mode and `bash scripts/install-pawflow.sh --port PORT` for Docker mode.
- The current page over-indexes on broad marketing and under-explains the concrete install path.
- Documentation, how-tos, and FAQ are absent as website pages.
- Visual hierarchy is too dark/monochrome and card-heavy for a product docs site.
- No clear 5-minute path from discovery to installing PawFlow.

## Recommended Site Shape

Use a static no-build website unless deployment later requires otherwise:

- `index.html`: product overview, trust model, architecture preview, primary install CTA.
- `quickstart.html`: exact install paths: Docker recommended, source/dev install, first login, first relay, first conversation.
- `docs.html`: curated documentation hub mapped to existing repo docs.
- `howtos.html`: practical recipes with short steps and links to deeper docs.
- `faq.html`: objections and decision answers.
- `style.css`: one shared design system.
- `site.js`: scroll animations, copy buttons, mobile nav, active section state.

This gives the site enough depth without introducing a framework, package manager, or build step.

## Homepage Narrative

The first viewport should answer three questions immediately:

1. What is PawFlow?
   PawFlow is a self-hosted runtime where AI agents work against real infrastructure through relays, shared context, and interchangeable LLM providers.

2. Why should I care?
   Agents can code, operate tools, inspect files, use desktop/browser/media capabilities, and then turn repeatable work into deterministic flows.

3. What should I do now?
   Start the Docker quickstart and open the installer wizard.

Suggested homepage sections:

- Hero: `Self-hosted agent runtime for real infrastructure.` CTA: `Install PawFlow` and `Read the 5-minute guide`.
- Product strip: Web UI, PawCode CLI, VS Code, relays, multi-provider agents, flow engine.
- Why different: relay-local tools, durable context, provider switching, deterministic flows.
- Architecture: server, relay, agents, flow engine, clients.
- Use cases: coding agents, team automations, desktop/browser tasks, media generation, scheduled digests.
- Security posture: self-hosted, explicit relay boundary, permissions, secrets, private gateway.
- Quickstart preview: Docker path first, source path second.
- Docs/How-tos/FAQ teaser cards.

## Quickstart Page

The quickstart must be short and copy-pasteable.

Primary path:

```bash
git clone https://github.com/allcolor/PawFlow-Agents.git
cd PawFlow-Agents
bash scripts/doctor-pawflow.sh --port PORT
bash scripts/install-pawflow.sh --port PORT
```

Then:

- Open `https://localhost:PORT/install`.
- Accept the self-signed bootstrap certificate for local/private installs.
- Use temporary bootstrap key `RoyBatty` only during first install.
- Finalize wizard: admin user, LLM service, summarizer service, PawFlow Agent flow, starter conversation.

Secondary source/dev path:

```bash
git clone https://github.com/allcolor/PawFlow-Agents.git
cd PawFlow-Agents
pip install -r requirements.txt
python cli.py start --host 0.0.0.0 --port PORT
```

## Docs Hub

Make the docs page curated, not exhaustive. Group links by user intent:

- Start: Quickstart, Docker, Deployment, PawCode, VS Code.
- Agents: Agent System, LLM Providers, Tool Catalog, Slash Commands.
- Infrastructure: Relay Client, Filesystem, Security Model, Multi-client Conversations.
- Automation: Task Catalog, Services Catalog, Expression Language, first agent-created flow.
- Media: Media Tools, voice clone, image/video/audio/3D tool docs.
- Build: Development, PFP Packages, PFP Developer Guide, Publisher Guide.

## How-tos

Recommended first how-tos:

- Install PawFlow with Docker.
- Configure your first Codex/Claude/Gemini/OpenAI service.
- Link a relay to a workspace.
- Start a conversation and choose an agent.
- Use PawCode from a terminal.
- Generate a deterministic daily digest flow.
- Give an agent filesystem access safely.
- Use media tools from a conversation.
- Run a private demo behind the Private Gateway.
- Troubleshoot: Docker, port conflicts, relay disconnected, provider auth.

Each how-to should have: objective, prerequisites, steps, expected result, next link.

## FAQ Topics

- Is PawFlow a hosted agent cloud?
- What leaves my machine?
- Why use a relay?
- Which LLM providers work?
- Can I use Codex, Claude Code, and Gemini together?
- What is the difference between an agent and a flow?
- When is execution deterministic?
- Can agents edit my files?
- How do permissions and approvals work?
- Can I use PawFlow for media generation?
- Can I run it on a VPS?
- Is it production-ready?
- How do I update a Docker install?

## Design Direction

Avoid a generic dark SaaS landing page. Use a precise infrastructure-console feel: clean, dense enough for engineers, but not visually flat.

Recommended palette:

- Background: near-black graphite, not pure blue/slate.
- Accent 1: electric cyan for active routes and relays.
- Accent 2: lime/green for running flows and success states.
- Accent 3: amber for approvals/security warnings.
- Neutral panels: dark graphite with subtle borders.

Motion:

- Subtle animated flow lines in the hero.
- Cards reveal on scroll.
- Copy buttons with quick feedback.
- Architecture diagram with moving relay packets.
- Respect `prefers-reduced-motion`.

## Implemented Information Architecture

The public website now separates product orientation from technical depth:

- `index.html`: a concise product story built around Relays, the shared durable runtime, and agents plus deterministic Flows.
- `product.html`: the product definition, runtime architecture, shared state, agent/Flow division of labor, and interoperability.
- `features.html`: the complete capability catalog grouped by product concern instead of presented as equal homepage sections.
- `flows.html`: Flow Editor, Runtime Viewer, queues, backpressure, checkpoints, retries, subflows, and agent-generated workflows.
- `relays.html`: the controlled boundary to real filesystems, shells, browsers, desktops, services, and GPU hosts.
- `integrations.html`: models, clients, MCP, A2A, AG-UI, and infrastructure compatibility.
- `use-cases.html`: coding, multi-machine operations, self-hosted workspaces, automation, UI control, media, and private AI operations.
- `quickstart.html`, `docs.html`, `howtos.html`, and `faq.html`: installation and implementation depth.

The homepage deliberately keeps only seven sections: Hero, Why PawFlow, Architecture, Demos, Stack, Comparison, and Install. Detailed capabilities belong on a specialized page and receive at most one short homepage mention.

## Visual Contract

The visual system keeps PawFlow's graphite and cyan infrastructure identity. The homepage uses a single technical stage, grid depth, runtime routes, restrained motion, and strong type hierarchy. This cadence is inspired by contemporary technical launch pages such as Forge, but the component shapes, copy, diagrams, and product identity remain PawFlow-specific.

Motion must remain functional and lightweight:

- route packets can move through the runtime diagram;
- content may reveal on scroll;
- cards may use small depth changes;
- all non-essential animation must stop under `prefers-reduced-motion`.

On wide desktop viewports with a precise pointer, the homepage becomes a
zoomable narrative canvas. It is deliberately hybrid rather than a sequence of
identical zooms:

- Hero to About, Architecture to Videos, and Comparison to Install use nested
  inception zooms;
- About to Architecture and Videos through Comparison use vertical canvas
  pans;
- transitions use a short blur crossfade, then settle to a completely sharp
  scene;
- one complete wheel/trackpad gesture advances exactly one scene; inertial
  events stay locked until the scene is sharp and the gesture has been idle
  for one second;
- every scene is top-aligned just below the fixed header instead of vertically
  centered, avoiding decorative empty space above short content;
- the final Install scene zooms into a visual Hero copy, then resets to the
  original Hero without a visible seam.

Every scene must fit inside the available viewport as one complete artboard.
The runtime measures the natural scene content and applies a fit scale that
accounts for the persistent section navigator. A scene may never require an
unreachable inner scroll area. Only the current and next scenes remain
rendered; all deeper scenes use `content-visibility: hidden` so the nested
camera does not compound browser load.

The fixed scene navigator keeps direct anchors for Home, About, Architecture,
Videos, Stack, Comparison, and Install, plus a direct How-tos link. Keyboard
Page Up/Page Down, arrows, Home, End, and Space mirror wheel navigation.

On mobile, the first viewport prioritizes the H1, one sentence, primary and secondary actions, then the single runtime visual. It must not reintroduce a logo animation, sound control, help banner, install command, or capability badge wall ahead of the product story.

Mobile uses a full-screen chapter canvas rather than ordinary page scrolling.
Each short chapter fits as one stable scene; a chapter that genuinely needs
more room scrolls vertically inside that scene. A swipe can trigger the next
zoom or pan only when it starts at the current chapter's bottom, so reaching the
last line and changing chapters are always two distinct gestures. Reverse
navigation follows the same rule at the top, and the final chapter loops to the
first. Reduced-motion visitors receive the same complete content as a normal
document without this canvas.

The How-tos page reuses this visual language without sacrificing complete
documentation. Its animated canvas is a nine-scene category map; every card
opens the canonical recipe in a normal vertical reader. Existing direct recipe
anchors remain valid, while category anchors navigate the canvas. Mobile and
reduced-motion users receive the same map as a normal scrolling document.

The site includes a looping ambient soundtrack at low volume. Audible autoplay
is requested by default; when a browser blocks it before user activation, the
same playback request is retried on the first pointer, keyboard, or wheel
gesture. A persistent, accessible sound toggle lets the visitor opt out, and
playback pauses whenever the page is hidden. The current position is carried
across internal full-page navigations within the same tab, including the short
page-load transit time, so the static multi-page site resumes almost seamlessly.

No build system is needed for this phase. Verification uses structural tests plus a local static server and browser screenshots at desktop and mobile widths.
