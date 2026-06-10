# PawFlow Website Media Plan

All website media should live under `pawflow-website/assets/media/`. Keep original generated files here, then optimize final web copies as `.webp` or `.mp4` before publishing.

## Directory Contract

- `hero/`: first viewport visuals and poster images.
- `product/`: product UI screenshots or stylized UI composites.
- `diagrams/`: architecture and flow diagrams.
- `howtos/`: step-by-step visuals for recipe pages.
- `docs/`: documentation hub thumbnails.
- `faq/`: small explanatory graphics for FAQ answers.
- `video/`: short demos, loop videos, and poster frames.

## Required Images

| Priority | Target path | Size | Page/section | Brief |
|---|---|---:|---|---|
| P0 | `hero/pawflow-runtime-hero.webp` | 2400x1350 | Homepage hero | Cinematic product illustration of a self-hosted AI agent runtime: central PawFlow server console, connected relays, code workspace, browser, terminal, and flow graph. Modern infrastructure UI, readable but not text-heavy, graphite background, cyan/green/amber accents. No mascot, no generic robot. |
| P0 | `diagrams/server-relay-architecture.webp` | 1600x1000 | Homepage + docs | Clear architecture diagram: clients -> PawFlow server -> agent runtime + flow engine -> WebSocket relay -> user workspace/tools. Must be inspectable, with simple labels and directional arrows. |
| P0 | `product/webchat-console.webp` | 1600x1000 | Homepage product strip | High-fidelity UI composite showing PawFlow webchat with conversation, resource panel, agent status, and tool result. Use actual product screenshots if possible; otherwise generate a faithful dark console mockup. |
| P0 | `product/quickstart-installer.webp` | 1600x1000 | Quickstart | Browser screenshot/mockup of the installer wizard: provider choice, admin setup, final install confirmation. Should make install feel concrete and approachable. |
| P1 | `diagrams/agent-to-flow-pattern.webp` | 1600x900 | How-to: daily digest | Agent designs a flow once, then deterministic CRON/Flow tasks run later. Show contrast between creative design-time and deterministic runtime. |
| P1 | `product/pawcode-terminal.webp` | 1600x900 | PawCode docs/how-to | Terminal view connected to PawFlow conversation, showing streaming agent response, command palette, and relay-backed diff/run output. |
| P1 | `product/resource-panel.webp` | 1600x1000 | How-to: relay/resources | Resource panel showing relays, agents, services, skills, and package repository. Emphasize connected workspace and permissions. |
| P1 | `product/server-relay-install.webp` | 1600x1000 | How-to: server relay | Screenshot of adding a managed `relay` service in PawFlow resources, with `token` empty and health/connection status visible. |
| P1 | `product/relay-desktop-install.webp` | 1600x1000 | How-to: remote relay | Composite showing release downloads for Relay Desktop/CLI, the Relay Desktop server profile form, and a connected workspace. |
| P1 | `product/webchat-workspace-menu.webp` | 1600x1000 | Homepage: webchat workspace | Real screenshot of the webchat menu showing Desktop Relay, relay terminals, context editor, memory editor, VS Code/code-server, and provider/runtime entries. |
| P1 | `product/desktop-relay-session.webp` | 1600x1000 | Homepage + how-to: desktop relay | Screenshot of a full desktop session opened from PawFlow, with an agent-visible screen and operator controls. Include a local-relay variant if possible. |
| P1 | `product/relay-terminal-menu.webp` | 1600x1000 | How-to: terminals | Webchat terminal selector showing remote Docker relay, local host relay with `allow_local`, and relay server/runtime terminal boundaries. |
| P1 | `product/context-editor.webp` | 1600x1000 | How-to: context editor | Context editor open on a conversation, showing selected agent context, editable snippets, and the next-turn boundary. |
| P1 | `product/memory-editor.webp` | 1600x1000 | How-to: memory editor | Memory editor showing durable memories, source/scope metadata, delete/correct actions, and audit-friendly layout. |
| P1 | `product/vscode-code-server.webp` | 1600x1000 | How-to: VS Code/code-server | Browser VS Code/code-server attached to a linked relay workspace, with files/diffs visible beside the PawFlow conversation. |
| P1 | `product/provider-tmux-session.webp` | 1600x1000 | How-to: interactive providers | Terminal/tmux view for Claude Code interactive or Antigravity/Agy provider runtime, showing observable CLI session state without exposing secrets. |
| P1 | `diagrams/provider-switching.webp` | 1400x900 | Docs: LLM providers | One conversation with multiple agents routed to Codex, Claude Code, Gemini, Anthropic/OpenAI API, and compatible endpoints. |
| P1 | `diagrams/security-boundaries.webp` | 1400x900 | FAQ/security | Trust-boundary illustration: browser, PawFlow server, LLM provider, relay, host/Docker workspace, media providers. Use warning/approval accents. |
| P2 | `docs/docs-hub-map.webp` | 1400x900 | Docs hub | Visual map of docs categories: Start, Agents, Infrastructure, Automation, Media, Build. |
| P2 | `howtos/media-tools-flow.webp` | 1400x900 | How-to: media tools | Flow from prompt -> generate_image/edit/upscale/video/audio/3D -> FileStore outputs -> conversation reuse. |
| P2 | `product/media-service-setup.webp` | 1600x1000 | How-to: media services | Resource panel showing image, video, audio/music, lipsync, upscaling, 3D, and speech-to-video services with secrets referenced, not exposed. |
| P2 | `product/tts-stt-service-setup.webp` | 1600x1000 | How-to: TTS/STT | Service setup screenshot for Supertonic local TTS, Voicebox, and OpenAI-compatible STT, plus webchat speaker/microphone controls. |
| P2 | `product/oauth-provider-setup.webp` | 1600x1000 | How-to: OAuth providers | Auth Gateway provider setup with supported providers visible: Google, GitHub, Microsoft, X, Meta/Facebook, Amazon, Telegram, and generic OAuth/OIDC. Do not show real secrets. |
| P2 | `product/rclone-filesystem-setup.webp` | 1600x1000 | How-to: rclone filesystem | rclone OAuth credential service plus rclone filesystem service, showing Google Drive/OneDrive style remote mounted under `/remote/<service_id>`. |
| P2 | `product/variables-secrets.webp` | 1600x1000 | How-to: variables/secrets | Resource panel showing variables and secrets with scopes, masked values, and package secret bindings. |
| P2 | `product/pawflow-depots.webp` | 1600x1000 | How-to: PawFlow depots | Repository/resources view grouping agents, flows, skills, prompts, tools, MCP servers, services, themes, task definitions, and packages by scope. |
| P2 | `product/skills-marketplace.webp` | 1600x1000 | How-to: skills | Skill create/import/assign flow, including marketplace review and assigned skills for an agent. |
| P2 | `product/pfp-package-install.webp` | 1600x1000 | How-to: PFP packages | Package inspect/install dialog showing objects, capabilities, required secrets, provenance, and selected install plan. |
| P2 | `product/marketplace-search.webp` | 1600x1000 | Marketplace section | Marketplace search results with registry source, package size, SHA-256 pin, developer key metadata, and explicit download confirmation. |
| P2 | `diagrams/flow-engine-explained.webp` | 1600x1000 | Flows explained | Diagram of triggers, FlowFiles, tasks, services, relationships, queues/backpressure, checkpoints, retries, and explicit LLM tasks. |
| P2 | `diagrams/pawflow-agent-flow.webp` | 1600x1000 | Main PawFlow Agent flow | `httpReceiver -> agentLoop -> handleHTTPResponse` plus conversation store, event bus, relays, provider, memory, and FileStore. |
| P2 | `product/tasks-plans.webp` | 1600x1000 | Tasks and plans | Plan/task UI showing create_plan, step status, verification, assigned agent, recurring task, and blocked/done states. |
| P2 | `product/mcp-hooks-tools-prompts.webp` | 1600x1000 | MCP/hooks/tools/prompts | Resource panel showing MCP servers, agent hooks, custom tools, flows, and prompts with explicit activation/scopes. |
| P2 | `product/theme-selector.webp` | 1600x1000 | Themes | Theme selector plus theme resource editor/import dialog, showing global and conversation theme modes. |
| P2 | `product/compact-summarizer.webp` | 1600x1000 | Compact and summarizer | LLM/summarizer settings showing max_context_size, compact_threshold_pct, summarizer service, and compact summary review. |
| P2 | `product/private-gateway-setup.webp` | 1600x1000 | Private Gateway | Gateway setup with key rotation, failure cooldown, ban policy, skin selection, and no exposed secrets. |
| P0 | `howtos/install-script-terminal.webp` | 1600x1000 | How-to: install wizard | Terminal showing release zip download, unzip, and `scripts/install-pawflow.sh --port 19990 --pull-images --version ...`. |
| P0 | `howtos/wizard-gateway.webp` | 1600x1000 | How-to: install wizard | First-run gateway/bootstrap screen, showing local certificate/private install context and gateway key replacement without real secrets. |
| P0 | `howtos/wizard-admin.webp` | 1600x1000 | How-to: install wizard | Admin account creation screen with no real credentials. |
| P0 | `howtos/wizard-llm-provider.webp` | 1600x1000 | How-to: install wizard | LLM provider selection screen showing Codex, Claude Code, Antigravity/Agy, Gemini CLI, Anthropic, OpenAI, and compatible endpoint options. |
| P0 | `howtos/wizard-first-conversation.webp` | 1600x1000 | How-to: install wizard | First conversation after install with `assistant` selected and a small successful response. |
| P1 | `howtos/desktop-novnc-audio.webp` | 1600x1000 | How-to: Desktop Relay | noVNC desktop surface in browser with audio controls and visible relay/session boundary. |
| P1 | `howtos/pawcode-installer.webp` | 1600x1000 | How-to: PawCode installer | Release asset download plus terminal install/login flow. |
| P1 | `howtos/pawcode-server-settings.webp` | 1600x1000 | How-to: PawCode usage | Terminal showing `PAWFLOW_SERVER`, `PAWFLOW_GATEWAY_KEY`, auth login, `/conv`, `/resume`, and linked relay status. |
| P1 | `howtos/vscode-plugin-installer.webp` | 1600x1000 | How-to: VS Code plugin | VS Code command palette installing `pawflow-vscode-<version>.vsix`, settings for server URL/gateway key, and PawFlow activity bar. |
| P1 | `howtos/relay-desktop-installer.webp` | 1600x1000 | How-to: Relay Desktop installer | Desktop installer/profile/workspace registration flow. |
| P1 | `howtos/relay-cli-installer.webp` | 1600x1000 | How-to: Relay CLI installer | Headless CLI install, login/profile, workspace root registration, and connected relay in webchat. |
| P2 | `faq/agent-vs-flow.webp` | 1200x800 | FAQ | Simple split visual: agent explores/decides/builds; flow executes/schedules/retries. |

## Required Videos

| Priority | Target path | Duration | Page/section | Brief |
|---|---|---:|---|---|
| P0 | `video/pawflow-logo-intro.mp4` | 8s | Homepage hero | Animated PawFlow logo intro: cyan/white particle swarm converges into the hexagon badge (paw + three agent nodes + wordmark), radial light bloom, settle with idle shimmer. Synchronized sound (whoosh + impact + chime) generated with the video. Plays muted-autoplay with a click-to-unmute control. Poster is `assets/logo.png`. |
| P0 | `video/install-to-first-chat.mp4` | 45-75s | Quickstart | From clone/install command to browser installer to first conversation with `assistant` selected. Include a poster at `video/install-to-first-chat-poster.webp`. |
| P1 | `video/relay-tool-run.mp4` | 30-45s | How-to: relay | Link a relay, run a safe filesystem/search command, show result streaming back into the chat. Include approvals if relevant. |
| P1 | `video/server-relay-install.mp4` | 30-45s | How-to: server relay | Add a managed `relay` service, save it, and show healthy status in the resource panel. Include a poster at `video/server-relay-install-poster.webp`. |
| P1 | `video/remote-relay-desktop-cli.mp4` | 45-60s | How-to: remote relay | Install Relay Desktop from release downloads, connect to PawFlow, then show the equivalent Relay CLI profile/login flow. Include a poster at `video/remote-relay-desktop-cli-poster.webp`. |
| P1 | `video/desktop-relay-local.mp4` | 30-45s | Homepage + how-to: desktop relay | Open Desktop Relay from webchat, switch between remote relay desktop and local desktop when `allow_local` is available, then show the agent using a safe screen inspection. Include a poster at `video/desktop-relay-local-poster.webp`. |
| P1 | `video/webchat-terminals.mp4` | 30-45s | How-to: terminals | Open three terminal surfaces from webchat: remote Docker relay, local host relay, and relay server/runtime diagnostics. Make the execution boundary visually clear. Include a poster at `video/webchat-terminals-poster.webp`. |
| P1 | `video/context-memory-editors.mp4` | 30-45s | How-to: context/memory | Edit short-term context, correct a durable memory, then run the next agent turn with the corrected state. Include a poster at `video/context-memory-editors-poster.webp`. |
| P1 | `video/media-service-setup.mp4` | 45-60s | How-to: media services | Add one image service, one video service, and one audio/TTS service, store secrets, then run a tiny generation test returning FileStore URLs. Include a poster at `video/media-service-setup-poster.webp`. |
| P1 | `video/tts-stt-webchat.mp4` | 30-45s | How-to: TTS/STT | Configure Supertonic or Voicebox, use the webchat read-aloud button, then use microphone STT to fill the prompt box. Include a poster at `video/tts-stt-webchat-poster.webp`. |
| P2 | `video/oauth-provider-setup.mp4` | 45-60s | How-to: OAuth providers | Configure one OAuth app at a provider, paste client id/secret into Auth Gateway, set callback URL, and test login. Include a poster at `video/oauth-provider-setup-poster.webp`. |
| P2 | `video/rclone-filesystem.mp4` | 45-60s | How-to: rclone filesystem | Create rclone OAuth credentials, add rclone filesystem, link it, and access `/remote/<service_id>` from a relay terminal/tool. Include a poster at `video/rclone-filesystem-poster.webp`. |
| P2 | `video/resources-skills-pfp.mp4` | 60-90s | Resources/skills/packages | Browse depots, import a skill, assign it to an agent, inspect a PFP package, bind secrets, and install selected objects. Include a poster at `video/resources-skills-pfp-poster.webp`. |
| P2 | `video/flow-agent-plan.mp4` | 60-90s | Flows/tasks/plans | Show the main agent flow, create a plan, convert a useful recurring task into a flow, and verify plan step completion. Include a poster at `video/flow-agent-plan-poster.webp`. |
| P2 | `video/private-gateway-compact-theme.mp4` | 45-60s | Security/context/theme | Configure Private Gateway, summarizer/compact settings, and select/import a theme. Include a poster at `video/private-gateway-compact-theme-poster.webp`. |
| P1 | `video/provider-tmux-debug.mp4` | 30-45s | Providers | Show a tmux-backed interactive provider session for Claude Code interactive or Antigravity/Agy, including login/approval visibility without secrets. Include a poster at `video/provider-tmux-debug-poster.webp`. |
| P1 | `video/agent-builds-flow.mp4` | 45-60s | How-to: daily digest | Prompt an agent to create a daily digest flow, then show the flow shape and deterministic execution result. |
| P2 | `video/pawcode-shared-conversation.mp4` | 30-45s | PawCode docs | Continue the same conversation from web UI to terminal PawCode and back. |
| P0 | `video/install-script-to-conversation.mp4` | 60-90s | How-to: install wizard | Full install script to browser wizard, every first-run screen, then first conversation with `assistant` selected. Include `video/install-script-to-conversation-poster.webp`. |
| P1 | `video/desktop-screen-see.mp4` | 45-60s | How-to: Desktop Relay | Open Desktop Relay/noVNC, enable/verify audio controls, use `screen` screenshot and `see` analysis before an approved UI action. Include `video/desktop-screen-see-poster.webp`. |
| P1 | `video/pawcode-installer.mp4` | 30-45s | How-to: PawCode installer | Download PawCode package, install, login, and stream a response. Include `video/pawcode-installer-poster.webp`. |
| P1 | `video/pawcode-usage.mp4` | 45-60s | How-to: PawCode usage | Set `PAWFLOW_SERVER` and `PAWFLOW_GATEWAY_KEY`, login, list/resume conversations, create a relay-linked conversation, and run a safe relay command. Include `video/pawcode-usage-poster.webp`. |
| P1 | `video/vscode-plugin-installer.mp4` | 45-60s | How-to: VS Code plugin | Install the release VSIX, set `pawflow.serverUrl` and `pawflow.gatewayKey`, login, open chat sidebar, and run selection actions. Include `video/vscode-plugin-installer-poster.webp`. |
| P1 | `video/relay-desktop-installer.mp4` | 45-60s | How-to: Relay Desktop installer | Install Relay Desktop, create server profile, register workspace, and confirm connected relay in webchat. Include `video/relay-desktop-installer-poster.webp`. |
| P1 | `video/relay-cli-installer.mp4` | 45-60s | How-to: Relay CLI installer | Unpack CLI, login/profile, connect workspace root, and show relay-linked terminal/filesystem tool in webchat. Include `video/relay-cli-installer-poster.webp`. |

## Prompt Template For Generated Illustrations

Use `codex_image_service` for generated illustrations. Recommended base style:

```text
Modern technical product illustration for PawFlow, a self-hosted AI agent runtime. Realistic dark infrastructure UI, graphite background, cyan relay lines, green running states, amber approval/security accents. Precise panels, readable shapes, no fake paragraphs, no generic robot mascot, no playful cartoon style, no purple-blue gradient hero, no beige palette.
```

Add the specific brief from the table above, exact target size, and write directly to the target path when possible.

## Screenshot Preference

For product UI sections, prefer real screenshots from a local PawFlow instance over fully generated UI. Generated UI is acceptable for hero and abstract diagrams, but actual screenshots will be more credible for quickstart/how-to pages.

## Optimization Notes

- Convert large final images to `.webp` at quality 82-88.
- Keep hero image below 500 KB if possible.
- Keep section images below 250 KB where possible.
- Use `.mp4` H.264 for demos and a `.webp` poster beside each video.
- Avoid embedding base64 media in HTML or CSS.
