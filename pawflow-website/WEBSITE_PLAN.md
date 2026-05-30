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
- Use temporary bootstrap key `RoyBetty` only during first install.
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

## Implementation Recommendation

Next implementation step: replace the current `index.html` and `style.css` with a static multi-page website, then add `quickstart.html`, `docs.html`, `howtos.html`, `faq.html`, and `site.js`.

No build system is needed for this phase. Verification can be done with `python -m http.server` from `pawflow-website` and browser screenshots at desktop and mobile widths.
