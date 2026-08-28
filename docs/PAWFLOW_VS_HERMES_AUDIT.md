# PawFlow vs Hermes Agent: Technical and Product Audit

Audit date: 2026-08-28
Hermes reference: `NousResearch/hermes-agent` commit
[`a24c12d14f5f1d37deee8887c6072a1d579e7e98`](https://github.com/NousResearch/hermes-agent/tree/a24c12d14f5f1d37deee8887c6072a1d579e7e98)
PawFlow reference: the current working tree at the audit date.

Status: review document. Recommendations in this audit are not authorized for
implementation until reviewed. The Desktop Client, Universal Installer, and
WorkflowRun Kanban are now implemented in the working tree and are evidence in
this audit, not pending recommendations.

## 1. Executive conclusion

Hermes is not a lightweight chatbot. At the pinned revision it is a broad,
serious personal-agent platform with a polished terminal experience, a unified
messaging gateway, a large skills surface, scheduled automation, parallel
delegation, several execution backends, a desktop product, and an implemented
multi-agent Kanban subsystem.

PawFlow should not compete by claiming a longer checklist of personal-assistant
features. Hermes currently has the simpler story and faster time to value for
"install one agent and talk to it through many channels."

That statement does **not** mean Hermes automatically resumes one transcript
across those channels. Hermes session keys include profile, platform, chat type,
chat/thread, and sometimes participant. PawFlow has fewer delivered channel
products, but its Telegram and Google Chat agent flows submit to the same
conversation runtime used by webchat, PawCode, VS Code, Android, and PawFlow
Desktop. Cross-client transcript continuity is therefore a PawFlow strength;
connector breadth and connector operations are Hermes strengths.

PawFlow's defensible ground is different:

> PawFlow is the self-hosted control plane for durable, observable, governed
> agents that execute real infrastructure workflows.

The strongest PawFlow assets are the versioned Flow/FlowFile runtime, Workflow
Agents with immutable run identity and recovery, visual composition, explicit
service and capability binding, relay-based infrastructure access, rich
multi-agent conversation state, human interaction as durable runtime state, and
a multi-layer cognitive/project knowledge system.

The strategic goal is therefore not to become "Hermes with a flow editor." It is
to make the workflow-runtime advantage as easy to install, operate, and
understand as Hermes is today.

## 2. Method

The comparison uses code and repository documentation, not homepage slogans.

Hermes evidence was read from the immutable source archive for the commit above,
including:

- `README.md` and `SECURITY.md`;
- `agent/context_engine.py`, `context_compressor.py`,
  `memory_manager.py`, `memory_provider.py`, `skill_commands.py`, and
  `subagent_lifecycle.py`;
- `cron/scheduler.py` and `tools/cronjob_tools.py`;
- `gateway/run.py`, `gateway/session.py`, `channel_directory.py`,
  `pairing.py`, all platform plugin manifests/adapters, their setup functions,
  and connector-focused tests;
- `tools/environments/*`, `delegate_tool.py`, and `kanban_tools.py`;
- `plugins/kanban/dashboard/plugin_api.py`;
- the Hermes Kanban design specification bundled in the repository.

PawFlow evidence includes:

- `core/workflow_agent_*.py`, `workflow_run_store.py`, and
  `workflow_run_inspector.py`;
- `core/continuous_flow_executor.py` and the Flow/Task/Service abstractions;
- `core/agent_*.py`, conversation, interaction, memory, knowledge graph,
  diary, project graph, wiki, todo, and scratch systems;
- relay, authorization, package, and service code;
- the webchat, PawCode, Android, Relay Desktop, installer, and operational docs;
- focused tests that define the shipped contracts.

A PawFlow channel is classified as **delivered product support** only when all
four proofs exist: registered executable task/service code, a flow that binds
the shared agent runtime, focused tests, and a deployment/configuration path.
Having send/receive primitives alone is not product-channel support.

Hermes connector maturity is evaluated symmetrically: executable adapter and
registration, configuration/onboarding path, focused tests, and operational
behavior such as authorization, reconnection, media/commands, health, and
delivery. A manifest is not sufficient evidence of maturity.

Claims are marked **verified**, **corrected**, or **not demonstrated**. Planned
work is never counted as implemented.

Hermes' repository root is MIT-licensed. Reuse is legally possible, but any
substantial copied portion must retain the Nous Research copyright and MIT
permission notice. Every reuse plan below also requires a pinned source path and
commit in `THIRD_PARTY_NOTICES.md`, a record of PawFlow modifications, and a
separate dependency/asset license review. Nested components with their own
licenses are not automatically covered by the root license.

## 3. Product model

| Dimension | PawFlow | Hermes |
|---|---|---|
| Primary model | Agent runtime embedded in a data/workflow engine | Personal agent process with tools, gateway, profiles, and plugins |
| Canonical unit of work | FlowFile, Task, Flow, WorkflowRun, conversation turn | Conversation turn, cron job, delegate, Kanban task |
| Primary UI | Webchat plus visual flow/runtime surfaces | TUI/CLI, messaging channels, Desktop/dashboard |
| Deployment posture | Self-hosted server plus managed/client relays | Single-tenant local/VPS/container/cloud-sandbox agent |
| Multi-user posture | Scoped users, conversations, services, resources | Security policy describes a single-tenant personal agent |
| Transcript continuity | One explicit `conversation_id` across web, CLI, mobile, desktop, VS Code, Telegram, and Google Chat when selected | Sessions are normally scoped by profile + platform + chat/thread; memory/persona may be shared, transcript identity is not automatic across platforms |
| Differentiation | Durable workflow execution and governed infrastructure access | Personal-agent completeness and low-friction ubiquity |

This distinction explains most wins and losses. PawFlow has a stronger control
plane; Hermes has a stronger personal-agent product shell.

## 4. Scorecard

Scores are relative maturity at the pinned snapshot, not theoretical ceiling.
Five is strongest.

| Area | PawFlow | Hermes | Current lead |
|---|---:|---:|---|
| Durable DAG/dataflow runtime | 5 | 2 | PawFlow |
| Workflow-run identity, recovery, audit | 5 | 3 | PawFlow |
| Visual workflow composition | 5 | 1 | PawFlow |
| Personal-agent onboarding | 4 | 5 | Hermes |
| Terminal/TUI experience | 3 | 5 | Hermes |
| Messaging-channel product | 3 | 5 | Hermes |
| Cross-client transcript continuity | 5 | 3 | PawFlow |
| Scheduled automation UX | 4 | 5 | Hermes |
| Multi-agent board collaboration | 4 | 5 | Hermes |
| Cognitive/project knowledge depth | 5 | 4 | PawFlow |
| Skills breadth/discoverability | 4 | 5 | Hermes |
| Package/extensibility governance | 5 | 3 | PawFlow |
| Execution backend choice | 4 | 5 | Hermes |
| Fine-grained runtime governance | 5 | 3 | PawFlow |
| Single-tenant hardening clarity | 4 | 5 | Hermes |
| Multi-user resource scoping | 5 | 2 | PawFlow |
| Rich web operational UI | 5 | 3 | PawFlow |
| Desktop chat client | 4 | 5 | Hermes |
| Installation simplicity | 4 | 5 | Hermes |
| Training/evaluation tooling | 3 | 5 | Hermes |
| Real-infrastructure integration | 5 | 4 | PawFlow |

Every Hermes-led score maps to a detailed plan below:

| Hermes-led area | Gap plan |
|---|---|
| Personal-agent onboarding and installation simplicity | G1 |
| Desktop chat maturity | G2 |
| Terminal/TUI experience | G3 |
| Messaging-channel catalogue/configuration/operations | G4 |
| Multi-agent board collaboration depth | G5 |
| Execution backend choice | G6 |
| Skills breadth/discoverability | G7 |
| Scheduled automation UX | G8 |
| Competitor migration (additional verified advantage) | G9 |
| Training/evaluation tooling | G10 |
| Single-tenant hardening clarity | G11 |

## 5. Where PawFlow is structurally better

### 5.1 A real workflow engine, not orchestration conventions

PawFlow's Flow, Task, Service, FlowFile, batch executor, continuous executor,
queues, backpressure, routing, ports, and nested flow primitives form a general
execution engine. Workflow Agents bind an LLM agent turn to an exact versioned
flow contract.

Hermes can chain tools, delegate work, schedule jobs, and dispatch Kanban tasks,
but these are agent/runtime subsystems rather than one canonical dataflow model.
The Kanban dispatcher is powerful, yet it is a separate board/worker architecture.

Why it matters:

- deterministic non-LLM processing can surround LLM steps;
- branches and joins are executable graph semantics;
- resource/service bindings are explicit;
- the same engine handles agent and non-agent automation;
- operators can inspect and reuse the workflow independently of a prompt.

This is PawFlow's central advantage and should remain the center of positioning.

### 5.2 Immutable WorkflowRun identity and recovery

PawFlow records exact flow references, authorization lineage, run generation,
service snapshots, claimed inputs, events, outbox state, terminal identities,
retry checkpoints, and recovery state in `WorkflowRunStore`.

The state machine explicitly models accepted, running, waiting,
retryable-failed, cancelling, committing, terminal, and recovery outcomes.
Terminal states are immutable. Retry is allowed only from a persisted safe
checkpoint. Final message and event identities are allocated once.

Hermes has durable cron and Kanban state, claims, runs, heartbeats, reclaim, and
review lanes. It is serious operational software. PawFlow still has the stronger
unified transactional identity for an agent turn executed by a versioned
workflow.

### 5.3 Durable human interaction belongs to the runtime

PawFlow confirmations, user input, notifications, waits, and timers are durable
runtime concepts correlated to runs and conversations. A Workflow Agent can stop
at a human boundary and resume the same run.

Hermes Kanban offers comments, block/unblock, review, reassign, attachments, and
human interposition. This is stronger as a collaboration product today.
PawFlow's advantage is that human interaction is inside the same execution
contract as the workflow, not only around a board task.

### 5.4 Resource scoping and multi-user governance

PawFlow models global, user, conversation, and agent-instance scopes across
resources and cognitive state. Conversations have explicit selected agents;
service resolution, authorization snapshots, allowed effects, tool scope, and
relay bindings are part of runtime decisions.

Hermes explicitly describes itself as a single-tenant personal agent. Its
external adapters require allowlists, but within one adapter authorized callers
are equally trusted; its security policy recommends separate instances for
capability separation.

PawFlow is better positioned for a shared self-hosted control plane where
different users, agents, conversations, services, and workspaces must remain
separate.

### 5.5 Cognitive and project knowledge stack

PawFlow ships distinct persistent layers:

- scoped temporal memory with taxonomy and semantic recall;
- temporal entity-relationship knowledge graph;
- per-agent diary;
- AST project graph across 17 languages;
- source-hashed project wiki;
- durable todo list;
- expiring scratchpad and relay-backed ScratchDir;
- a reviewed skill-learning loop.

Hermes has strong file/provider memory, session search, context compression,
learning mutations, skills, and multiple memory provider plugins. It should not
be described as lacking memory or learning. PawFlow's advantage is the breadth
and explicit separation of knowledge types, especially temporal KG, source-aware
project wiki, AST graph, and scoped multi-agent visibility.

### 5.6 Governed extensions

PawFlow's reviewed skills, cross-marketplace import, scoped resources, MCP
servers, custom tools, flow tasks, service providers, and signed `.pfp`
packages provide a broad distribution and provenance model. Imported content is
treated as untrusted, review fails closed without the configured reviewer, and a
package may contain much more than a skill.

Hermes has a much larger immediately usable skills catalog and plugin ecosystem.
Its own security policy states that skills/plugins execute inside the agent
process and require operator code review. PawFlow has the stronger governance
story; Hermes has the stronger catalog story.

### 5.7 Relay-based real infrastructure access

PawFlow separates the server from execution surfaces. Relays expose explicit
workspace, container, host-local, desktop, automation, and tunnel capabilities.
Tools route through the relay and can target remote machines without pretending
that the server filesystem is the user's project.

Hermes terminal backends are broader out of the box, but PawFlow's relay is the
better control-plane primitive for long-lived real infrastructure with
server-mediated identity and workspace scoping.

## 6. Where Hermes is still better today

### 6.0 Corrections produced by the source re-audit

| Previous statement or implication | Status | Correct finding |
|---|---|---|
| PawFlow is only "flows and adapters", not "continue the same agent" | **Corrected** | `telegram_agent` and `google_chat_agent` call `pawflow_agent.agent_runtime_in`. Telegram selects an existing `conversation_id` per user and bridges its live events; Google Chat exposes `direct_conversation_id`. |
| Hermes channels use one shared transcript | **Corrected** | `gateway/session.py::build_session_key` always includes the platform. It preserves continuity inside a platform/chat/thread, not automatic transcript identity between platforms. Pairing is also platform-scoped. |
| Slack and WhatsApp are supported PawFlow agent channels | **Corrected** | They have registered/tested receiver, sender, and service primitives, but no delivered agent flow or channel provisioning path. Discord is in the same state. |
| PawFlow has no native Desktop client | **Corrected** | `pawflow-desktop/` is a separate Electron chat client with PKCE, OS-protected secrets, per-origin permissions, Windows/Linux/macOS build targets, and Node/Python tests. Hermes still leads in operational depth. |
| PawFlow has no Kanban | **Corrected** | `core/workflow_kanban.py` plus the browser board implement a derived, auditable WorkflowRun/task Kanban with commands, assignments, comments, pagination, SSE refresh, desktop/mobile layouts, and tests. Hermes still has a richer independent work-board product. |
| PawFlow has no universal installer | **Corrected** | `pawflow_installer/` implements CLI and GUI frontends, local/SSH transports, reachability/Tailscale guidance, Relay Desktop integration, resumable state, preflight, and focused tests. Distribution and live cross-OS acceptance remain less mature than Hermes. |
| Every Hermes connector is equally mature | **Not demonstrated** | The pinned tree ranges from deeply tested product channels to external-daemon integrations and specialized plugins with one or no connector-named test files. The matrix in 6.4 records the difference. |

The correction changes the product conclusion: connectors are optional front
doors, not an indispensable agent capability. PawFlow should close connector
catalog/configuration gaps without replacing its shared conversation runtime or
making channel count a primary differentiator.

### 6.1 Installation and first value

**Verified, lead narrowed.** Hermes has `scripts/install.sh`,
`scripts/install.ps1`, `hermes_cli/setup.py`, `hermes_cli/doctor.py`, and focused
setup/doctor tests. PawFlow now has a real universal installer, so the remaining
gap is distribution maturity and the number of concepts exposed before first
value, not absence of an installer.

**Gap plan G1 — first-value installation**

- **Target:** one signed Windows/macOS/Linux entry point that can install locally
  or through SSH, optionally configure Tailscale, install Relay Desktop, open the
  browser wizard, and finish at a working conversation.
- **Non-goals:** do not hide security boundaries after advanced mode is selected;
  do not create a second installation engine beside `pawflow_installer`.
- **Architecture:** keep `engine.py` and its event/state model authoritative;
  CLI, GUI, and future package launchers must be thin frontends over the same
  resumable operations.
- **Work packages:** produce signed installers; add release-CI artifacts and
  checksums; add uninstall/repair/upgrade modes; create an explicit "chat now"
  completion path; move relay, summarizer, and advanced network vocabulary behind
  progressive disclosure.
- **Security:** never print secrets; keep SSH host-key verification explicit;
  preserve OS-protected Relay Desktop credentials; show every firewall/Tailscale
  effect before execution.
- **Tests:** live clean-VM smoke tests on Windows, macOS, and Linux; local and SSH
  install/upgrade/repair/uninstall; interrupted-run resume; hostile SSH output;
  unavailable Docker/Tailscale; browser-wizard handoff.
- **Acceptance:** a new user reaches an authenticated chat without reading an
  architecture document; advanced users can export the exact plan and logs.
- **Order:** packaging CI, live VM matrix, recovery/uninstall, then first-run UX.
- **MIT reuse:** reuse Hermes doctor failure categories and installer regression
  ideas; only port code where it is smaller than adapting PawFlow's engine.
  Attribute any copied code to Nous Research at the pinned commit.

### 6.2 Desktop chat product

**Corrected, lead narrowed.** PawFlow now ships the source for a separate
`pawflow-desktop/` chat application. It uses the mobile PKCE endpoints,
`safeStorage`, context isolation, sandboxing, a restricted preload, and explicit
origin/permission policy. Hermes Desktop remains substantially deeper at the
pinned revision: updater and installation lifecycle, remote/SSH lifecycle,
backend ownership and health, crash forensics, native OAuth, multiple window
surfaces, and a much larger Electron/E2E test matrix.

**Gap plan G2 — Desktop production maturity**

- **Target:** production-grade signed PawFlow Desktop packages with secure update,
  repair, diagnostics, and remote-server profiles on all three desktop OSes.
- **Non-goals:** do not merge Relay Desktop into the chat client and do not let the
  client own relay lifecycle.
- **Architecture:** retain the current mobile-auth-compatible client; add a
  platform packaging/update layer and a redacted diagnostic bundle without
  expanding preload privileges.
- **Work packages:** code signing/notarization; auto-update with rollback and
  channel pinning; protocol/deep-link registration; crash recovery; profile
  import/export without secrets; accessibility and keyboard audit; offline/error
  surfaces; release documentation.
- **Security:** keep tokens only in OS protection, bind OAuth state/PKCE to one
  profile, reject navigation and permission requests outside the configured
  origin, and verify update signatures before replacement.
- **Tests:** real Windows/macOS/Linux packaging smokes, update/rollback, locked
  keychain, proxy/custom CA, deep links, multi-profile isolation, crash restart,
  screen-reader and keyboard checks.
- **Acceptance:** installers launch, authenticate, reconnect, update, and recover
  without exposing a gateway key or creating a relay.
- **Order:** packaging/signing, updater/rollback, live OS matrix, diagnostics,
  then accessibility polish.
- **MIT reuse:** copy only well-bounded Hermes Desktop packaging/update test ideas
  or helpers after license review; do not import its backend ownership model,
  because PawFlow Desktop must remain a remote chat client.

### 6.3 Terminal experience

Hermes has a purpose-built TUI with multiline editing, autocomplete, history,
interrupt-and-redirect, streaming tool output, model/tool configuration, and
shared slash-command semantics across CLI and messaging.

PawCode is capable and supports interactive provider sessions, SSE events,
commands, tools, and conversation continuity. It is less legible as a complete
consumer terminal product.

PawFlow should improve PawCode around discoverability, first-run setup, status,
model/tool selection, recovery, and polished streaming. It should not replace
PawCode with another CLI.

**Gap plan G3 — PawCode/TUI product polish**

- **Target:** make current conversation continuity, agents, tools, models,
  approvals, background work, and recovery discoverable without memorizing slash
  commands.
- **Architecture:** keep `pawflow_cli` and the server conversation/event contracts;
  add a capability-driven presentation layer rather than a parallel TUI runtime.
- **Work packages:** first-run connection picker; persistent status header;
  searchable command palette; model/tool/agent chooser; clearer streaming/tool
  phases; interrupt-and-steer feedback; reconnect/replay; structured diagnostics.
- **Security:** render server capability decisions rather than guessing locally;
  redact secrets/tool payloads in diagnostics; preserve explicit approval.
- **Tests:** terminal widths, Unicode, multiline paste, Ctrl-C/redirect races,
  SSE reconnect/replay, approval while streaming, Windows console, SSH terminal,
  and screen-reader-friendly text mode.
- **Acceptance:** every common action is reachable through discovery and has an
  equivalent scriptable command; reconnecting resumes the same `conversation_id`.
- **Order:** status/reconnect, command discovery, model/tool controls, streaming
  polish, then accessibility.
- **MIT reuse:** reuse Hermes TUI interaction/test cases for editing, redirection,
  replay, and model switching; do not copy its gateway/session ownership layer.

### 6.4 Unified messaging gateway

**Verified as a catalogue/operations lead; corrected as a continuity claim.**
Hermes has one gateway framework, common delivery/pairing/allowlist primitives,
platform plugins, config metadata, status surfaces, and a large connector test
tree. It does not automatically share one transcript across platforms:
`gateway/session.py::build_session_key` constructs
`agent:<profile>:<platform>:<chat_type>:...`. Pairing grants are also stored by
platform. Hermes shares agent profile, tools, and memory layers; explicit
delivery/handoff is possible, but Telegram and Slack normally remain different
sessions.

PawFlow's continuity model is the opposite tradeoff. `telegram_agent` and
`google_chat_agent` submit to `pawflow_agent.agent_runtime_in`. Telegram resolves
the authenticated user's active `conversation_id` and its bridge subscribes to
that conversation's event stream; Google Chat can bind
`direct_conversation_id`. Webchat, PawCode, VS Code, Android, and PawFlow Desktop
also address server conversations directly.

#### 6.4.1 PawFlow delivered-channel status

| Surface | Executable primitives | Shared-runtime flow | Tests/config path | Product status |
|---|---|---|---|---|
| Telegram | receiver, send/API, agent client, conversation bridge, bot service | `telegram.telegram_agent:1.0.0` | extensive `test_telegram.py` coverage and repository flow parameters | **Delivered agent channel** |
| Google Chat | signed webhook, agent client, Chat service | `google_chat.google_chat_agent:1.0.0` | `test_google_chat.py` and required owner/audience/service-account parameters | **Delivered agent channel** |
| Discord | receiver, sender, bot service | none | `test_discord.py` | **Transport primitives only** |
| Slack | receiver, sender, Socket Mode service | none | `test_slack_bot.py` | **Transport primitives only** |
| WhatsApp Cloud | receiver, sender, Meta service | none | `test_whatsapp.py` | **Transport primitives only** |
| Email | SMTP/OAuth2 send task | none | send tests and notification flows | **Outbound automation, not an agent channel** |
| Signal | no messaging adapter/service/flow | none | none | **Absent** |

This table deliberately does not promote Slack, Discord, or WhatsApp based on
plans or primitives. A support claim becomes valid only after the four-proof
gate in section 2.

#### 6.4.2 Hermes connector maturity at the pinned commit

Maturity grades are evidence bands, not popularity scores:

- **A:** product-grade onboarding plus deep runtime/operational tests;
- **B:** real executable connector with configuration and focused tests, but
  manual vendor work, an external daemon/bridge, or a narrower operational
  surface remains;
- **C:** code/manifests exist, but product maturity was not demonstrated by the
  bounded source/test audit.

| Hermes connector | Grade | Configuration and operational evidence | Limitation at this snapshot |
|---|---:|---|---|
| Telegram | A | managed QR onboarding or BotFather token; allowlist/home channel; 63 connector-named tests covering polling, fallback networking, media, topics, buttons, auth, reconnect, and health | Bot creation/provider constraints remain external |
| Discord | A | wizard, token/allowlist/home channel; 52 connector-named tests covering threads, slash commands, voice, roles, media, retries, components, and liveness | Discord developer-portal app setup is still manual |
| Slack | A | wizard generates an app manifest and collects bot/app tokens; 38 connector-named tests for Socket Mode, threads, Block Kit, SSRF, retries, auth, and reconnect | App creation/token issuance still requires Slack portal steps |
| Matrix | B+ | wizard supports token/password, optional E2EE, dependency install, allowlist, home room; about 14 directly relevant tests | More credentials/state than core channels; empty allowlist is explicitly open access |
| WhatsApp Web bridge | B | executable adapter, JID/LID normalization, media and bridge lifecycle tests, dedicated enable/allowlist setup | setup tells the user to start a separate Node bridge and treats enablement as connected without live bridge verification |
| WhatsApp Cloud API | B | separate six-field validated wizard, official Meta webhook adapter, allowlist and focused tests | Business account, public webhook, Meta dashboard configuration, and token lifecycle remain manual |
| Signal | B | substantial SSE/JSON-RPC adapter with media, formatting, rate-limit, reconnect/health code and relevant tests | requires an external `signal-cli` HTTP daemon and has no dedicated interactive setup |
| Feishu/Lark | B+ | QR/manual setup, official SDK modes, rich media/comments/meetings, allowlist and roughly 15 focused tests | provider app configuration and regional behavior remain complex |
| Microsoft Teams | B | dedicated wizard, Bot Framework adapter, approvals, runtime/setup tests | Azure app/tenant/secret and public endpoint work remain manual |
| Google Chat | B | dedicated wizard, signed HTTP or Pub/Sub ingress, REST egress, per-user OAuth for files, focused tests | GCP service account, audience, Pub/Sub/webhook setup is comparatively heavy |
| DingTalk | B | QR/manual setup, Stream Mode, rich media and focused auth/runtime tests | vendor application provisioning remains external |
| WeCom | B | QR/manual setup, WebSocket and encrypted callback modes, seven connector-focused tests | two configuration modes and several credentials increase operator burden |
| IRC | B | no third-party dependency, interactive setup, inbound/outbound adapter tests | protocol is simple; media/identity/commands are necessarily narrower |
| Mattermost | B | interactive setup, REST + WebSocket adapter, runtime/setup tests | server URL/token/bot provisioning is manual |
| LINE | B- | HMAC webhook, interactive credential setup, adapter test | public webhook/provider-console work; shallow test breadth |
| SimpleX | B- | interactive setup and adapter test | requires a separately running `simplex-chat` WebSocket daemon |
| Email | B- | IMAP receive + SMTP reply, manifest-driven config, four gateway tests | no dedicated wizard; polling, app passwords, and provider-specific settings |
| SMS/Twilio | B- | bidirectional REST/webhook adapter, manifest config, focused test | paid provider, phone provisioning, and public webhook |
| ntfy | B- | lightweight HTTP stream/publish adapter, explicit topic trust model, focused test | no native user identity; private topic/token is the effective boundary |
| Home Assistant | B- | WebSocket event adapter/tool and focused tests | event integration rather than a general conversational channel; token/entity policy setup |
| QQ Bot | B | onboarding module, credential isolation/scope tests, rich adapter | provider-specific credentials and regional deployment |
| BlueBubbles | B- | implemented iMessage bridge adapter and focused test | external BlueBubbles server/webhook dependency |
| A2A | B (specialized) | inbound/outbound protocol, localhost-safe default, peer tokens, four focused tests | agent protocol, not a human messaging channel |
| Buzz | B- (specialized) | interactive setup, CLI/WebSocket/poll adapter and focused tests | requires Buzz CLI/community relay/Nostr identity |
| Raft | B- (specialized) | interactive setup, loopback wake bridge and focused adapter tests | requires external Raft CLI/workspace; adapter does not own message bodies |
| Photon/iMessage | C | dedicated `hermes photon` setup and supervised Node sidecar are implemented | version 0.3.0 and no connector-named Python test file in the extracted test tree; maturity not demonstrated |
| Weixin and Yuanbao | C | built-in adapters and media/protocol modules exist | the bounded re-audit did not find enough focused setup/test evidence to assign product maturity |

Webhook, generic API server, relay, and Microsoft Graph webhook adapters are
valuable integration surfaces but are not counted as human messaging channels.

#### 6.4.3 Gap plan G4 — optional Channels product

- **Target:** make a supported channel an optional, provisioned front door to an
  existing PawFlow user, conversation, and selected agent, with visible health
  and test delivery.
- **Non-goals:** channel count is not a core product metric; do not introduce a
  gateway-owned transcript, a second identity store, or channel-specific agent
  runtimes.
- **Architecture:** project a Channels view from repository flow templates,
  deployed flow instances, services, runtime links, authenticated identity
  bindings, and health/delivery events. The existing conversation store remains
  authoritative.
- **Work package 1 — support contract:** add machine-readable channel metadata and
  enforce the four-proof delivery gate in docs/catalog tests.
- **Work package 2 — package existing transports:** build reviewed
  `discord_agent`, `slack_agent`, and `whatsapp_agent` flows over the existing
  receiver/service/send primitives and `pawflow_agent.agent_runtime_in`.
- **Work package 3 — identity:** bind provider identity to a PawFlow user and an
  explicit existing/new `conversation_id`; deny by default; expose revoke,
  rebind, and audit history.
- **Work package 4 — operations:** add configure/test/enable/disable, dependency
  preflight, health, last inbound/outbound, last error, reconnect state, and
  redacted diagnostics.
- **Work package 5 — UI and CLI:** one Channels surface plus scriptable commands;
  show "transport available" separately from "agent channel delivered."
- **Work package 6 — selective additions:** implement Signal only after demand is
  confirmed, through a relay-owned service/daemon boundary rather than a server
  subprocess. Treat all other Hermes connectors as demand-led packages.
- **Security:** signed webhook validation, SSRF-safe media download, bounded media,
  secret scopes, deny-by-default identities/chats, replay/idempotency, rate limits,
  and explicit public-listener warnings.
- **Tests:** contract tests for every supported channel; identity isolation;
  same-conversation web/channel round trips; reconnect/replay; duplicate events;
  revoked user; malformed media; provider 429/5xx; secret redaction; deployment
  smoke.
- **Acceptance:** from Channels, an operator can configure, test, bind, observe,
  disable, and audit a channel; a user can select the same conversation in web
  and channel and see one transcript; unsupported primitives are never labeled
  supported.
- **Order:** support contract, identity binding, package Slack/Discord/WhatsApp,
  operations, UI/CLI, then demand-led adapters.
- **MIT reuse:** high-value candidates are Hermes' Slack manifest wizard and
  reconnect/Block Kit/SSRF tests; Discord thread/voice/component/retry tests;
  WhatsApp Cloud field validators, JID/LID normalization and stale-bridge tests;
  Matrix E2EE/recovery tests; and Signal formatting/rate-limit/media logic.
  Port behavior and narrow helpers, not `SessionStore` or the gateway process.
  Preserve the Nous MIT notice for substantial copied portions and re-check every
  external SDK/bridge license.

This plan is a usability/ecosystem improvement, not a prerequisite for PawFlow's
workflow-agent value, and is not authorized for implementation by this audit.

### 6.5 Kanban collaboration

Hermes Kanban is implemented, not merely a PDF proposal. The pinned source
contains:

- SQLite board/task/run state;
- task creation, update, delete, bulk operations;
- comments and attachments;
- parent/child links and dependency enforcement;
- profiles and assignees;
- dispatcher and worker claims;
- active-worker inspection and termination;
- reclaim/reassign;
- review/reopen behavior;
- diagnostics, token/complexity estimates, task specification and decomposition;
- multiple boards/projects;
- home-channel notifications;
- dashboard API and WebSocket updates.

PawFlow now has an implemented WorkflowRun Kanban projection and command surface,
not merely an inspector. It derives run/task lanes from runtime truth, preserves
branches/joins/waits, supports assignment/comments and safe commands, paginates,
updates over SSE, and has browser/mobile/keyboard coverage. Hermes still leads on
general-purpose work-board depth: attachments, multiple projects/boards,
decomposition/specification, worker process/worktree diagnostics, review/reopen,
reclaim, and portfolio-style collaboration.

**Gap plan G5 — Kanban collaboration depth**

- **Target:** extend the existing projection only where a feature maps cleanly to
  FlowDefinition, WorkflowRun, interactions, artifacts, or immutable events.
- **Non-goals:** never copy Hermes' independent board database and never allow a
  drag operation to mutate runtime state directly.
- **Work packages:** artifact-backed attachments; dependency/reopen visibility;
  worker/agent diagnostics; saved board filters/projects as projections; optional
  specification/decomposition that produces a reviewed flow/workflow proposal;
  richer review history.
- **Security:** authenticated actor identity only, command allowlists,
  idempotency keys, redaction, artifact authorization, and audit before/after.
- **Tests:** projection equivalence with graph/timeline, stale generation,
  concurrent commands, attachment authorization, dependency invalidation,
  review/reopen, worker termination, pagination/SSE, mobile/keyboard.
- **Acceptance:** every visible card and transition can be traced to canonical
  runtime state or an immutable event; unsupported transitions are disabled with
  a reason.
- **Order:** artifacts/dependencies, diagnostics, review history, then optional
  proposal/decomposition UX.
- **MIT reuse:** reuse Hermes test scenarios and interaction semantics from
  `tests/**/test_kanban_*.py` and bounded UI ideas; do not reuse its board schema
  or persistence layer.

### 6.6 Execution backend catalog

Hermes advertises and implements local, Docker, SSH, Singularity, Modal, Daytona,
and Vercel Sandbox terminal backends. This gives users direct vocabulary for
local, remote, HPC, and scale-to-zero work.

PawFlow supports remote/container/host work through relays and can model cloud
work through services and flows. It lacks the same turnkey named backend
selection and serverless hibernation story.

PawFlow should add relay deployment profiles for:

- local host;
- Docker;
- SSH-managed host;
- Kubernetes job/pod;
- serverless persistent sandbox providers where credentials and lifecycle can be
  governed.

Do this as relay/service providers, not as special cases in every tool.

**Gap plan G6 — named execution/deployment profiles**

The complete implementation plan is
[`EXECUTION_DEPLOYMENT_PROFILES_PLAN.md`](EXECUTION_DEPLOYMENT_PROFILES_PLAN.md).
It is based on the live PawFlow relay, server-manager, Relay Desktop, installer,
capability, and image-catalog code plus the pinned Hermes environment sources.

The decisive boundary is: PawFlow's relay already makes filesystem/tool execution
provider-agnostic after connection, but a common deployment control plane does not
exist. G6 therefore adds a strict versioned provider contract, transactional
lifecycle and reconciliation, single-use relay bootstrap, workspace/persistence
semantics, capability verification, secret/cost/teardown metadata, leases,
authenticated API/UI, and one-shot migration. It formalizes server/client Docker,
finishes a real local-native launcher, turns SSH into lifecycle-only bootstrap,
adds Kubernetes pod/job and scheduler-aware Apptainer/Slurm, then qualifies one
scale-to-zero pilot against real demand.

The plan contains the exact contracts, state machine, provider-specific work,
security model, test matrix, migration, acceptance checklist, delivery order, and
Hermes MIT reuse ledger. Hermes lifecycle/error/test patterns may be selectively
adapted with the Nous Research MIT notice; its direct terminal-backend coupling is
explicitly excluded.

### 6.7 Skills quantity and discovery

Hermes includes a large bundled and optional skill tree, Skills Hub integration,
skill creation/improvement, and strong user-facing discovery.

PawFlow's learning loop and import governance are sophisticated, but the
out-of-box catalog and curation experience are less immediately visible.

PawFlow should compete on trusted composition:

- curated first-party starter packs by outcome;
- signed PFP bundles;
- compatibility and permission manifests;
- visible usage/success evidence;
- one-click reviewed install.

**Gap plan G7 — skills breadth and discovery**

- **Target:** make trusted outcome-oriented starter packs easier to discover than
  raw marketplace entries while preserving PawFlow review/provenance.
- **Work packages:** define starter-pack criteria; ship a small signed first-party
  set; expose permissions/dependencies/compatibility; add quality/usage evidence;
  improve search/preview/update/uninstall; document failure and support status.
- **Security:** imported scripts remain data until reviewed; no package may grant
  tool approval; enforce size/path/license/dependency policy and fail closed
  without the reviewer.
- **Tests:** malicious paths/scripts, nested binary assets, dependency conflicts,
  signature/update provenance, rollback, scope isolation, and reproducible pack
  install.
- **Acceptance:** users can choose an outcome, inspect effects and provenance,
  install one signed pack, and remove/update it without orphaning resources.
- **Order:** metadata/criteria, three starter packs, discovery UX, evidence, then
  broader curation.
- **MIT reuse:** import individual Hermes skills only after content and nested
  license review. Preserve the root Nous MIT notice or each skill's own license,
  record origin/commit, and never bulk-copy executable catalogs merely because
  the root repository is MIT.

### 6.8 Cron UX

Hermes offers natural-language cron jobs with delivery to any configured
platform, history, incidents, and operator commands.

PawFlow's cron/startup triggers, scheduled flows, wakeups, durable timers, and
continuations are more general. The weakness is product ergonomics.

PawFlow should add a Scheduled Agents view that projects existing flow schedules,
next runs, last outcomes, delivery targets, and explicit run-now/pause actions.
Do not create a second scheduler.

**Gap plan G8 — Scheduled Agents projection**

- **Target:** one view over existing cron triggers, flow schedules, wakeups,
  durable timers, and continuations with next run, last result, incidents,
  delivery target, pause/resume, and run-now.
- **Architecture:** query canonical scheduler/flow/run stores and emit commands to
  them; store no duplicate schedule truth.
- **Work packages:** normalized read projection; incident/history view; run-now
  and pause/resume commands; delivery target picker; natural-language creation as
  a reviewed proposal; notifications.
- **Security:** enforce schedule ownership/scopes, validate delivery identity,
  require confirmation for destructive/high-effect schedules, and redact payloads.
- **Tests:** time zones/DST, misfires, duplicate dispatch, restart, paused jobs,
  revoked target, run-now idempotency, history pagination, and cross-user scope.
- **Acceptance:** every scheduled item is visible once, traceable to its canonical
  source, and opens the exact WorkflowRun/result.
- **Order:** read projection, history/incidents, commands, then proposal-based
  natural language.
- **MIT reuse:** reuse Hermes incident taxonomy, operator wording, and scheduler
  regression scenarios; do not copy its scheduler or job store.

### 6.9 Migration

Hermes explicitly imports OpenClaw personality, memory, skills, allowlists,
messaging settings, API keys, assets, and workspace instructions with preview and
dry-run modes.

PawFlow supports external skills, MCP clients, packages, and rich resources, but
does not present a comparable competitor-migration path.

A migration assistant for OpenClaw and Hermes would reduce switching cost. It
should generate a reviewed import plan and never execute third-party code.

**Gap plan G9 — reviewed competitor migration**

- **Target:** preview and import identity/persona instructions, memories, skills,
  allowlists, channel settings, MCP configuration, and safe assets into explicit
  PawFlow scopes.
- **Architecture:** parsers produce a typed, diffable import plan; existing
  resource/package/channel APIs perform reviewed writes after approval.
- **Work packages:** OpenClaw parser/mapping; Hermes parser/mapping; secret
  references that require re-entry rather than copying plaintext; conflict and
  scope mapping; dry run/export; rollback manifest.
- **Security:** never execute imported scripts; path/symlink/size limits; prompt
  injection review; secret redaction; provenance on every created object.
- **Tests:** malformed/hostile archives, symlinks, huge files, duplicate names,
  partial failure/rollback, unsupported fields, scope conflicts, and dry-run
  determinism.
- **Acceptance:** the preview explains imported, transformed, skipped, and
  secret-reentry items; approval produces only those objects and can be reversed.
- **Order:** common plan/schema, OpenClaw, Hermes, UI, then additional sources.
- **MIT reuse:** Hermes'
  `optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py`
  offers mappings and adversarial cases worth adapting. Port parsers/mappings
  selectively with attribution; replace writes with PawFlow's reviewed APIs.

### 6.10 Training and evaluation story

Hermes includes batch trajectory generation, trajectory compression, evaluation
areas, and training-oriented tooling. This matches Nous Research's model
development background.

PawFlow has logs, usage, workflow inspection, review flows, and testable
automation, but the product does not package trajectories/evals as a coherent
feature.

This is important for research users, but secondary to PawFlow's infrastructure
positioning.

**Gap plan G10 — trajectory and evaluation evidence**

- **Target:** export redacted, versioned trajectories from immutable WorkflowRuns
  and execute repeatable evaluation flows with comparable reports.
- **Architecture:** define a stable trajectory schema over run snapshots, events,
  messages, tool calls, artifacts, cost, and authorization evidence; evaluation
  remains a normal versioned Flow.
- **Work packages:** schema/export; redaction policy; dataset manifests; replay
  harness with side effects disabled or mocked; evaluators; batch runner; report
  UI; optional training-oriented conversion.
- **Security:** default-deny secrets/PII, capability-stripped replay, explicit
  consent for external evaluators, signed dataset provenance, and retention.
- **Tests:** deterministic export, schema migration, redaction, truncated/failed
  runs, branch/join ordering, tool mock/replay, evaluator failure, and aggregate
  reproducibility.
- **Acceptance:** two exact run versions can be compared with reproducible inputs,
  outputs, scores, costs, and redaction evidence.
- **Order:** export/schema, redaction, single-run eval, batch/report, then optional
  training formats.
- **MIT reuse:** selectively adapt Hermes trajectory compressor behavior, eval
  fixtures, and report patterns from `evals/` and
  `tests/test_trajectory_compressor*.py`. Preserve provenance and do not import
  provider-specific training assumptions into the core runtime.

## 7. Security comparison

### 7.1 Hermes strengths

Hermes' `SECURITY.md` is unusually explicit:

- it states that OS-level isolation is the only adversarial-LLM boundary;
- it distinguishes terminal-backend isolation from whole-process wrapping;
- it documents what remains in the host agent process;
- it requires allowlists on network-exposed adapters;
- it treats session identifiers as routing handles, not authorization;
- it describes credentials, plugins, skills, and public exposure honestly.

This clarity is a competitive strength even where the default local backend is
broadly trusted.

### 7.2 PawFlow strengths

PawFlow adds controls Hermes' single-tenant model does not aim to provide:

- scoped users/conversations/resources/services;
- authorization references and lineage;
- exact effect allowlists;
- server/relay process separation;
- explicit relay capabilities;
- immutable workflow snapshots;
- reviewed imports and packages;
- per-conversation/agent tool and service bindings;
- human approval correlated to runtime state.

### 7.3 PawFlow weaknesses

PawFlow's security model is distributed across many documents and runtime
contracts. The operator has to understand which boundary applies to server,
managed relay, client relay, host-local helper, container, MCP, package, and
browser.

Recommendations:

1. publish one concise threat model modeled on Hermes' candor;
2. explicitly identify OS/container/relay boundaries versus heuristics;
3. show capability grants in one operator-visible view;
4. show when `local=true` changes the execution boundary;
5. make keychain migration mandatory for desktop/relay stable releases;
6. ship default deployment profiles for untrusted input.

**Gap plan G11 — one operable threat model**

- **Target:** one concise threat model plus an operator-visible capability map
  covering server, browser, managed/client relay, host helper, desktop, MCP,
  packages, and model/tool boundaries.
- **Work packages:** asset/trust-boundary inventory; abuse cases; secure defaults;
  deployment profiles; UI capability/effect view; incident and diagnostic guide;
  documentation conformance checks.
- **Security requirements:** label heuristic vs OS-enforced isolation, show when
  `local=true` changes the boundary, identify public listeners and secret stores,
  and provide an untrusted-input profile with explicit residual risk.
- **Tests/acceptance:** every externally reachable component maps to an owner,
  authentication mechanism, authorization decision, isolation boundary, logs,
  and revocation path; docs and UI use the same capability identifiers.
- **Order:** threat model, capability inventory, secure profiles, UI, then incident
  exercises.
- **MIT reuse:** Hermes `SECURITY.md` is a strong structural reference. Reuse its
  candid taxonomy or bounded wording only with Nous MIT attribution; PawFlow's
  actual boundaries must be derived from PawFlow code and deployments.

These recommendations require review before implementation.

## 8. Context and learning comparison

Both projects are strong here.

### Hermes

- context engine and compressor;
- session search and summarization;
- persistent memory and user modeling;
- skill creation and self-improvement;
- memory provider plugins;
- learning graph/mutations;
- context files and profile behavior.

### PawFlow

- hot/cold context virtualization and structural compaction;
- automatic memory extraction;
- scoped temporal memory;
- temporal KG and contradictions;
- agent diary;
- project AST graph;
- source-hashed wiki;
- todo/scratch/scratchdir separation;
- cross-conversation skill promotion and curator flow.

Verdict: PawFlow has the deeper control-plane knowledge architecture. Hermes has
the cleaner personal-learning narrative and broader pluggable provider story.

Action: improve the PawFlow UI explanation and observability before adding more
memory systems. Do not chase provider count for its own sake.

## 9. Multi-agent comparison

Hermes supports isolated subagents, profiles, parallel workstreams, persistent
Kanban workers, delegation logs, and profile-aware assignment. Its Kanban makes
named agent collaboration highly visible.

PawFlow supports multi-agent conversations, per-agent contexts, delegate/flash
work, groups, Workflow Agents, explicit resource bindings, task management,
conversation event streams, and shared cognitive/project state.

PawFlow is technically stronger at heterogeneous agent runtimes inside one
governed conversation. Hermes is stronger at making profile-based background
work understandable to a user.

Priority: give PawFlow's existing runtime a board and operational narrative;
avoid inventing another agent hierarchy.

## 10. Operations and reliability

### PawFlow leads on

- server-managed durable runtime;
- continuous queues/backpressure;
- flow-level operational composition;
- scoped service definitions;
- immutable run snapshots;
- restart recovery and outbox;
- rich web inspection;
- remote relays as managed infrastructure.

### Hermes leads on

- local/VPS install and doctor experience;
- named backend inventory;
- gateway/channel health;
- Kanban worker diagnostics;
- profile/task operations;
- clear single-process operator model;
- scale-to-zero backend story.

The difference is not that Hermes lacks durability. It is that PawFlow durability
is centered on flows, while Hermes durability is centered on jobs, sessions, and
board tasks.

## 11. What PawFlow should add or improve

This is a recommendation backlog, not authorization to implement.

### P0: make the existing advantage easy to experience

1. **Implemented:** universal installer core and guided secure reachability.
2. **Implemented:** native Windows/Linux/macOS Desktop client core.
3. **Implemented:** WorkflowRun Kanban projection and command surface.
4. **Remaining:** production packaging/live-OS maturity for installer and Desktop.
5. "First durable workflow agent in ten minutes" starter path.
6. One operational home showing active runs, waits, failures, schedules, relays,
   and costs.
7. Concise trust-boundary documentation.

Implementation of items 1-3 was separately authorized. Remaining audit
recommendations require review.

### P1: close daily-use product gaps

1. Unified Channels surface over existing adapters, with the four-proof support
   gate and shared-conversation binding.
2. Scheduled Agents view over existing flow scheduling.
3. PawCode onboarding/TUI polish.
4. Curated signed starter packs.
5. Relay deployment profiles for Docker, SSH, Kubernetes, and selected
   scale-to-zero providers.
6. Competitor migration preview/import.

### P2: strengthen proof and ecosystem

1. Workflow Agent templates by concrete outcome.
2. Eval/trajectory export from immutable runs.
3. Public compatibility matrix for tools, providers, relays, and packages.
4. Shareable redacted run reports.
5. Cost/usage budgets and evidence inside run inspection, only when explicitly
   configured.
6. Package provenance and permission UX.

### P3: selective research features

1. Batch benchmark/evaluation flows.
2. Dataset/trajectory tooling.
3. Optional memory provider interfaces where a real deployment need exists.
4. Advanced multi-board/project portfolio projection.

## 12. What PawFlow should not copy

1. Do not make an independent Kanban task engine; project WorkflowRuns.
2. Do not move tools back into the server process for convenience.
3. Do not weaken multi-user scoping to mimic a single-tenant local agent.
4. Do not ship a plaintext secret fallback for desktop convenience.
5. Do not add hidden timeouts, iteration caps, or quotas.
6. Do not create channel-specific agent runtimes; channel flows must bind the
   shared PawFlow agent runtime and conversation store.
7. Do not optimize for the largest raw skills count at the expense of provenance.
8. Do not position the product as another generic personal assistant.
9. Do not hide infrastructure concepts after a user chooses advanced mode; make
   them observable and controlled.
10. Do not claim Hermes lacks durability, memory, skills, subagents, cron,
    desktop, or Kanban. Those claims are false at the pinned revision.

## 13. Recommended battleground

### 13.1 Category

**Durable Workflow Agents for real infrastructure.**

Supporting statement:

> Design the workflow, bind the exact services and machines, let agents execute
> inside a recoverable run, and keep a human in control.

### 13.2 Buyer/user

Primary:

- self-hosters with several machines or services;
- engineers and technical operators;
- teams needing traceability and controlled automation;
- users who want local/cloud models without surrendering infrastructure;
- builders of repeatable agent products and internal operations.

Secondary:

- advanced personal users who outgrow a single conversational agent.

Hermes is better positioned for the user who wants one capable personal agent
immediately. PawFlow should welcome that user, but not let that use case define
the architecture.

### 13.3 Proof points

PawFlow should demonstrate:

1. one visual Workflow Agent that survives restart;
2. a human approval pause and exact resume;
3. two relays on different machines;
4. a branch/join with LLM and deterministic tasks;
5. a failure checkpoint and safe retry;
6. immutable run inspection and artifacts;
7. scoped services and secrets;
8. the same agent available through web, desktop/mobile, CLI, and a channel;
9. explicit cost and capability evidence;
10. a reusable signed package.

### 13.4 Message hierarchy

1. Workflow is the operating system; the model is one processor.
2. Your infrastructure remains yours.
3. Runs are durable, inspectable, and recoverable.
4. Humans and multiple agents share one runtime truth.
5. Any model, tool, service, channel, or machine can be bound explicitly.

## 14. Competitive response matrix

| Hermes strength | Bad response | PawFlow response |
|---|---|---|
| One-line install | Hide setup complexity | Universal installer plus progressive disclosure |
| TUI polish | Build another CLI | Polish PawCode around existing conversations/runtime |
| Many channels | Count primitives or fork transcripts | Optional Channels projection over adapters, explicit support gate, same `conversation_id` |
| Kanban | Copy its board database | Project FlowDefinition/WorkflowRun and validated commands |
| Seven backends | Special-case every tool | Relay/service provider profiles |
| Large skill catalog | Import everything blindly | Curated signed packs with review/provenance |
| Personal learning story | Add another memory store | Explain and expose the existing cognitive stack |
| Cron UX | Add another scheduler | Scheduled Agents view over flow scheduling |
| Desktop | Merge chat into Relay Desktop | Harden, sign, and distribute the separate secure PawFlow Desktop client |
| Single-tenant simplicity | Remove scopes | Progressive UX while retaining multi-user governance |

## 15. Decision summary

### PawFlow is better when

- work is a durable multi-step process;
- deterministic and LLM tasks must compose;
- several machines/services are involved;
- scopes, capabilities, and authorization matter;
- operators need a visual definition and exact run evidence;
- human waits and recovery are part of execution;
- knowledge must be shared across agents/projects in structured forms.

### Hermes is better when

- one user wants a personal agent immediately;
- terminal and messaging are the primary interfaces;
- turnkey channel integrations matter more than workflow composition;
- a broad skills catalog is the fastest path;
- named local/cloud execution backends are preferred;
- profile-based background work and Kanban are the core collaboration model.

### Where to fight

Fight on control, durability, observability, real infrastructure, shared
conversation continuity, and reusable workflow products. Mature the implemented
Desktop/installer/Kanban surfaces and close the optional Channels usability gap
so those advantages are visible. Do not fight on "the most personal assistant
features", raw connector count, or "the biggest skill catalog."

## 16. Review questions

Before any audit recommendation is implemented, decide:

1. Is "Durable Workflow Agents for real infrastructure" the explicit category?
2. Is the unified Channels surface a P1 product commitment?
3. Which relay deployment backend should be first after local/Docker/SSH?
4. Should PawCode receive a dedicated product-polish phase?
5. Which three signed starter packs best prove the category?
6. Which parts of the security model should appear in the default UI?
7. Which competitor migrations are worth supporting first?
8. Is trajectory/eval export important to the target buyer this year?

Until those decisions are reviewed, this audit changes no runtime behavior.
