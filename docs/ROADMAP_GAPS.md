# PawFlow — Gap Analysis Roadmap

Comprehensive plan covering all identified gaps vs Claude Code and OpenClaw.
Ordered by priority within each category. Dependencies noted.

---

## A. Critical Gaps (functional blockers)

### A1. Hard cost cap per conversation ✅ DONE
**Priority:** P0 — prevents bill shock
**Effort:** Small (1-2h)
**Dependencies:** None

**What:** `max_budget_usd` config on LLM service and/or conversation. Before each LLM call, check cumulative spend. If over budget → error, don't call.

**Plan:**
1. Add `max_budget_usd` field to LLM service config schema
2. Add `max_budget_usd` to conversation extras (overridable per conv)
3. In `agent_core.py` before `_llm_call()`: sum `total_tokens_in * cost_per_1m_input + total_tokens_out * cost_per_1m_output` from token tracker
4. If over budget → `emitter.on_fatal_error("Budget exceeded")`, break
5. `/cost` command shows remaining budget
6. SSE event `budget_warning` at 80% threshold

---

### A2. Voice input (push-to-talk / transcription)
**Priority:** P1 — mobile & accessibility
**Effort:** Medium (1-2 days)
**Dependencies:** A2a depends on `see()` audio transcription (already implemented)

**What:** User can speak instead of type. Audio → transcription → text message.

**Plan:**
- **A2a. Webchat voice input:**
  1. Add microphone button next to send button
  2. `navigator.mediaDevices.getUserMedia({audio: true})` → MediaRecorder
  3. On stop: upload audio blob to FileStore
  4. Call `see(path=file_id, source='filestore')` which transcribes via whisper
  5. Inject transcription as user message
  6. Alternative: browser Web Speech API (`SpeechRecognition`) for real-time — no server dependency

- **A2b. PawCode CLI voice:**
  1. Record via `sounddevice` or `pyaudio`
  2. Send to server for transcription
  3. Inject as user message

- **A2c. Additional STT providers:**
  1. Keep `openaiCompatibleSTT` as the default generic integration path for OpenAI, Groq, and local OpenAI-compatible endpoints.
  2. Add provider-specific services only when their features are needed: Deepgram for realtime/streaming, AssemblyAI for Universal Streaming workflows, Gladia for multilingual/EU live workflows, and Speechmatics for enterprise transcription.

- **A2c. VS Code plugin:**
  1. VS Code has no native mic API — use webview with getUserMedia
  2. Same flow as webchat

---

### A3. Git worktrees isolation for agents
**Priority:** P1 — critical for parallel coding
**Effort:** Medium (2-3 days)
**Dependencies:** Relay bash execution (already works)

**What:** Each sub-agent works in its own git worktree. Changes are isolated. Merge on completion.

**Plan:**
1. New tool `worktree_create(branch, agent_name)`:
   - `bash("git worktree add .worktrees/{agent_name} -b {branch}")`
   - Set the agent's filesystem root to the worktree path
2. New tool `worktree_merge(agent_name)`:
   - `bash("git merge .worktrees/{agent_name}")` from main
   - `bash("git worktree remove .worktrees/{agent_name}")`
3. `delegate` option `isolation: "worktree"`:
   - Auto-create worktree per sub-agent
   - Auto-merge on completion
4. `/batch` command:
   - Takes N tasks, creates N worktrees, spawns N agents
   - Each works in isolation, results merged sequentially

---

### A4. Mobile client (PWA)
**Priority:** P2 — important for non-dev users
**Effort:** Large (1-2 weeks)
**Dependencies:** None (webchat is already responsive-ish)

**What:** Progressive Web App installable on iOS/Android.

**Plan:**
1. Add `manifest.json` with app metadata + icons
2. Add service worker for offline caching of static assets
3. Fix mobile-specific CSS (sidebar, input area, keyboard handling)
4. Add push notifications via service worker (SSE → notification when agent responds)
5. Test on iOS Safari + Android Chrome
6. Publish to app stores via PWA wrapper (TWA for Android)

---

## B. High Priority Gaps

### B1. @file mention with autocomplete ✅ DONE
**Priority:** P1 — CC killer-feature UX
**Effort:** Medium (1-2 days)
**Dependencies:** Relay filesystem (already works)

**What:** Type `@` in the input → autocomplete dropdown with file names from the relay. Selected file content injected into the message.

**Plan:**
1. Webchat: detect `@` in input → debounced API call `list_dir` or `search`
2. Show autocomplete dropdown with matching files
3. On select: read file content, inject as attachment (like Ctrl+V paste)
4. PawCode CLI: use readline completer with `@` trigger
5. VS Code: use webview autocomplete in chat input

---

### B2. More LLM providers (Ollama, Mistral, Llama, vLLM)
**Priority:** P2 — self-hosted LLM support
**Effort:** Medium (2-3 days)
**Dependencies:** LLM service config schema

**What:** Connect to local/self-hosted LLMs via OpenAI-compatible API.

**Plan:**
1. Most self-hosted LLMs expose OpenAI-compatible endpoints
2. The existing OpenAI provider should work with base_url override
3. Add `base_url` field to LLM service config
4. Add Ollama auto-discovery: `GET http://localhost:11434/api/tags` → list models
5. Add model catalog UI in admin panel
6. Test with: Ollama, vLLM, LM Studio, Together.ai

---

### B3. MCP Elicitation (tools ask user for input)
**Priority:** P2 — needed for MCP ecosystem compatibility
**Effort:** Medium (1-2 days)
**Dependencies:** MCP bridge, ask_user tool

**What:** MCP servers can request user input during tool execution.

**Plan:**
1. MCP bridge already proxies tool calls
2. When MCP server sends elicitation request → bridge forwards to PawFlow
3. PawFlow publishes SSE event `mcp_elicitation` with question + options
4. UI shows dialog (like tool approval)
5. User responds → bridge sends response to MCP server
6. Tool continues with user's answer

---

### B4. PawFlow as MCP server (mcp serve)
**Priority:** P2 — composability with other agents
**Effort:** Medium (1-2 days)
**Dependencies:** MCP protocol implementation

**What:** PawFlow exposes its tools as an MCP server. Other agents (CC, other PawFlow instances) can use PawFlow tools.

**Plan:**
1. New CLI command: `pawflow mcp serve --port 8765`
2. Expose all registered tools via MCP stdio or HTTP transport
3. Authentication via API key
4. Other Claude Code instances can add PawFlow as MCP server
5. Enables "PawFlow as tool backend" for any MCP-compatible agent

---

### B5. {agent_name}.md auto-reinject post-compact ✅ DONE
**Priority:** P1 — context persistence
**Effort:** Small (2-4h)
**Dependencies:** Compaction system (already exists)

**What:** A project instructions file that survives compaction. Always re-injected after summary.

**Plan:**
1. Convention: `{agent_name}.md` in the relay root (case-insensitive match).
   Agent "toto" matches `toto.md`, `Toto.md`, `TOTO.MD`, etc.
   Agent "claude" matches `CLAUDE.md` — natural alignment with Claude Code.
2. At context build time (`_prepare_agent_context`): scan relay root for
   matching file via case-insensitive glob. Cache content per conversation.
3. After compaction, insert its content as a user message after the summary:
   `[System: Project instructions from {filename}]\n{content}`
4. Also re-inject at every context load (not just post-compact).
5. For Claude Code agents: CLAUDE.md in workdir is already read natively
   by CC. The injection ensures LLM API agents get the same instructions.
6. If no match: no injection, no error. Silent.

---

### B6. --output-format json headless mode
**Priority:** P2 — automation pipelines
**Effort:** Medium (1 day)
**Dependencies:** API endpoint

**What:** Single-shot API call that returns structured JSON response.

**Plan:**
1. New API action `headless_run`:
   ```json
   {"action": "headless_run", "agent": "claude", "message": "...",
    "json_schema": {...}, "max_turns": 10}
   ```
2. Runs agent loop, returns final response as JSON
3. Optional `json_schema` for structured output (passed to LLM)
4. No SSE, no streaming — synchronous HTTP response
5. Timeout configurable
6. Useful for CI/CD, scripts, webhooks

---

## C. Medium Priority Gaps

### C1. Agent YAML frontmatter in repo
**Priority:** P2
**Effort:** Small (4h)
**Dependencies:** Agent ResourceStore

**What:** Define agents as `.yaml` files in the repo (like CC agent frontmatter).

**Plan:**
1. Convention: `.pawflow/agents/agent_name.yaml`
2. Fields: `model`, `llm_service`, `tools`, `system_prompt`, `effort`, `max_iterations`
3. On relay connect: scan `.pawflow/agents/` → auto-register in ResourceStore
4. Changes detected on reconnect → update agents
5. Versioned in git like CC agents

---

### C2. /diff interactive viewer
**Priority:** P3
**Effort:** Medium (1 day)
**Dependencies:** Webchat UI

**What:** Visual diff viewer in webchat for file changes.

**Plan:**
1. Side-by-side or unified diff view in a modal
2. Triggered by clicking on an edit tool_result
3. Use `diff2html` library (10KB, MIT license)
4. Show file path, line numbers, +/- colored
5. Also accessible via `/diff` command

---

### C3. Web search native (Brave, Perplexity)
**Priority:** P3
**Effort:** Small (4h per provider)
**Dependencies:** API keys

**What:** Direct integration with search APIs beyond DuckDuckGo.

**Plan:**
1. `web_search` handler already exists (DuckDuckGo)
2. Add `search_provider` parameter: `duckduckgo` (default), `brave`, `perplexity`, `google`
3. Brave: `GET https://api.search.brave.com/res/v1/web/search` with API key
4. Perplexity: OpenAI-compatible API with search context
5. Google: Custom Search JSON API
6. API key stored in secrets: `${secrets.brave_api_key}`

---

### C4. Ctrl+R history search in PawCode ✅ DONE (already built-in)
**Priority:** P3
**Effort:** Small (2h)
**Dependencies:** PawCode CLI readline

**What:** Readline-style reverse search through command/message history.

**Plan:**
1. PawCode already has `messageHistory` in localStorage
2. Add readline `set_completer` with history search
3. Ctrl+R triggers reverse-i-search through `messageHistory`
4. Uses Python's `readline` module (already available)

---

### C5. sparsePaths git checkout
**Priority:** P3
**Effort:** Small (2h)
**Dependencies:** Relay bash

**What:** For large repos, checkout only specific paths.

**Plan:**
1. New option on relay connect: `--sparse-paths src/,tests/`
2. Relay runs `git sparse-checkout set src/ tests/` on connect
3. Reduces disk usage and context noise for monorepos
4. Agent sees only the relevant subset of the repo

---

### C6. CwdChanged / FileChanged hooks
**Priority:** P3
**Effort:** Small (4h)
**Dependencies:** Relay filesystem watcher

**What:** React to filesystem changes (auto-run tests, lint, etc.).

**Plan:**
1. Add `watchdog` or `inotify` to relay (optional)
2. When a watched file changes → trigger configured hook
3. Hooks defined in `.pawflow/hooks.yaml`:
   ```yaml
   on_file_changed:
     - pattern: "*.py"
       run: "ruff check {path}"
   ```
4. Hook results injected as system messages in the conversation
5. Similar to CC's PreToolUse/PostToolUse but file-system triggered

---

### C7. Permission modes with quick toggle ✅ DONE
**Priority:** P3
**Effort:** Small (2h)
**Dependencies:** Tool approval gate

**What:** Quick switch between read-only / approve-all / auto-approve modes.

**Plan:**
1. Add conversation-level `permission_mode`: `default`, `approve_edits`, `read_only`, `auto`
2. Webchat: toggle button in toolbar
3. `default`: approve write tools (current behavior)
4. `approve_edits`: only approve edit/write/delete
5. `read_only`: block all write tools
6. `auto`: approve everything (dangerous, like CC --dangerously-skip-permissions)
7. PawCode: `/permission auto|readonly|default`

---

## D. Low Priority / Nice-to-haves

### D1. Skill marketplace
**Priority:** P4
**Effort:** Large (1-2 weeks)
**Dependencies:** Plugin system, public API

**What:** Community skills repository (like OpenClaw's ClawHub).

**Plan:**
1. Define skill package format (.pfp with metadata)
2. Public registry API (GitHub-based or custom)
3. `/install skill_name` fetches from registry
4. Rating, versioning, search
5. Requires public launch first

---

### D2. Additional messaging channels
**Priority:** P4
**Effort:** Small-Medium per channel (4h-1d each)
**Dependencies:** Channel framework (already exists for Telegram/Discord/Slack/WhatsApp)

**What:** Matrix, Teams, LINE, iMessage, Signal, IRC, Nostr.

**Plan:**
1. Each channel = 1 service class implementing send/receive
2. Priority order: Teams (enterprise demand) → Matrix (open standard) → Signal → IRC
3. iMessage: macOS only, requires AppleScript bridge — low priority
4. Most channels have Python SDKs

---

### D3. Themes/colors configurable
**Priority:** P4
**Effort:** Small (4h)
**Dependencies:** Webchat CSS

**What:** User-selectable color themes.

**Plan:**
1. CSS variables already used for most colors
2. Add theme selector in settings
3. 3-4 built-in themes: dark (current), light, solarized, dracula
4. Custom theme via CSS override in conversation settings

---

### D4. !cmd inline bash in prompt ✅ DONE
**Priority:** P4
**Effort:** Small (2h)
**Dependencies:** PawCode CLI

**What:** Prefix message with `!` to run a shell command inline.

**Plan:**
1. In PawCode input handler: if text starts with `!`, run `subprocess.run(text[1:])`
2. Display output in the terminal
3. Optionally inject output into conversation context

---

### D5. /remote-control bridge
**Priority:** P4
**Effort:** Medium (1-2 days)
**Dependencies:** WebSocket bridge

**What:** Control PawFlow from another frontend (like CC's /remote-control to claude.ai).

**Plan:**
1. WebSocket server that accepts remote connections
2. Remote client sends user messages, receives SSE events
3. Enables: webchat controlling PawCode session, or external dashboard
4. Authentication via token

---

### D6. Group chat with @mention in external channels
**Priority:** P4
**Effort:** Medium (1 day)
**Dependencies:** Channel services

**What:** In Telegram/Discord groups, agents respond only when @mentioned.

**Plan:**
1. Channel service filters incoming messages
2. Only process messages containing `@agent_name` or `@bot`
3. Strip the mention prefix before sending to agent
4. Configurable: always respond in DM, mention-only in groups

---

### D7. TTS in webchat
**Priority:** P4
**Effort:** Small (4h)
**Dependencies:** Audio generation service

**What:** Play agent responses as audio in the webchat.

**Plan:**
1. Add speaker button on assistant messages
2. On click: send text to TTS service (already exists: `generate_audio`)
3. Play the audio in the browser via `<audio>` element
4. Option: auto-play for accessibility

---

## E. Dependency Graph

```
A1 (cost cap) ← standalone
A2 (voice) ← see() audio [done] + UI changes
A3 (worktrees) ← bash relay [done] + delegate orchestration
A4 (mobile PWA) ← webchat responsive CSS
B1 (@file) ← relay list_dir [done] + UI autocomplete
B2 (providers) ← OpenAI provider base_url
B3 (MCP elicit) ← MCP bridge + ask_user [done]
B4 (mcp serve) ← MCP protocol + tool registry [done]
B5 (AGENT.md) ← compaction [done] + relay read [done]
B6 (headless) ← agent loop [done] + API endpoint
C1 (agent yaml) ← ResourceStore [done] + relay scan
C2 (/diff viewer) ← diff2html library
C3 (web search) ← API keys + web_search handler [done]
C4 (Ctrl+R) ← readline [done]
C5 (sparse) ← relay bash [done]
C6 (file hooks) ← watchdog library
C7 (permissions) ← tool approval gate [done]
```

## F. Suggested Sprint Order

| Sprint | Items | Effort |
|--------|-------|--------|
| **Sprint 1** | A1 (cost cap), B5 (AGENT.md post-compact), C7 (permission modes) | 1 day |
| **Sprint 2** | B1 (@file autocomplete), C4 (Ctrl+R), D4 (!cmd) | 1 day |
| **Sprint 3** | A3 (git worktrees + /batch) | 2-3 days |
| **Sprint 4** | B6 (headless JSON), B4 (mcp serve) | 2 days |
| **Sprint 5** | A2 (voice input), D7 (TTS playback) | 2 days |
| **Sprint 6** | B2 (more providers), C3 (web search APIs) | 2 days |
| **Sprint 7** | B3 (MCP elicitation), C1 (agent YAML) | 2 days |
| **Sprint 8** | A4 (PWA mobile), C2 (/diff viewer) | 1 week |
| **Sprint 9** | C5 (sparse), C6 (file hooks), D3 (themes) | 1 day |
| **Sprint 10** | D1 (marketplace), D2 (channels), D5 (remote), D6 (@mention) | ongoing |

---

## G. Review Follow-ups (weekly review, 2026-06-10)

Findings from the read-only review of the 2026-06-03 → 2026-06-10 commit range
(159 commits, full suite green at HEAD `84fa2e08`). None are regressions; all
three are hardening/robustness improvements to schedule later.

### G1. Narrow container security-opt (drop blanket `unconfined`)
**Priority:** P2 — security hardening
**Effort:** Medium (0.5–1 day, includes empirical verification)
**Dependencies:** None

**Context:** `a59f3333` added `--security-opt apparmor:unconfined` and
`--security-opt seccomp=unconfined` to the CC interactive and Antigravity
observer containers (`core/claude_code_interactive_pool.py` and
`core/antigravity_observer_pool.py`, docker run builders) so the in-container
`unshare -m` + `mount --bind` tmux isolation works. Combined with
`--cap-add SYS_ADMIN` and `--user root`, isolation of containers that run
agent CLIs with `--dangerously-skip-permissions` is much weaker than needed.

**Plan:**
1. Empirically determine which opt is actually required. Docker's *default*
   seccomp profile conditionally allows `mount`/`umount2`/`unshare` when the
   container has `CAP_SYS_ADMIN` — the blocker was most likely only the
   `docker-default` AppArmor profile (denies `mount` regardless of caps).
   Test matrix on an Ubuntu host: SYS_ADMIN + default seccomp +
   `apparmor:unconfined` → run the bind-mount/tmux sequence.
2. If default seccomp passes: remove `seccomp=unconfined` from both pools.
3. Replace `apparmor:unconfined` with a custom profile (new file
   `docker/apparmor/pawflow-mount`): clone of `docker-default` plus
   `mount options=(rw, bind) -> /cc_sessions/**` and matching `umount` rules.
   Loaded via `apparmor_parser -r` at install time (installer step + docs).
4. Runtime wiring: probe whether the `pawflow-mount` profile is loaded
   (`/sys/kernel/security/apparmor/profiles`); if yes use
   `--security-opt apparmor=pawflow-mount`, else fall back to today's
   `apparmor:unconfined` with a WARNING bulletin so hosts without AppArmor
   (non-Ubuntu) keep working.
5. Tests: unit-test the docker-args builder for both branches (profile
   present / absent) in `tests/test_cc_interactive_provider.py` and
   `tests/test_antigravity_observer.py`; manual smoke on a real host.
6. Docs: security section of `docs/docker.md` + `docs/CLAUDE_CODE_INTERACTIVE.md`.

### G2. Rotate interactive proxy logs inside containers
**Priority:** P3 — robustness
**Effort:** Small (1–2h)
**Dependencies:** None

**Context:** the CCI proxy is launched with
`exec python3 /opt/pawflow/cc_interactive_proxy.py >> /tmp/cci_proxy.log 2>&1`
(`core/claude_code_interactive_pool.py`) and the AG observer proxy with the
same pattern (`core/antigravity_observer_pool.py`). `/tmp` is a 512 MB tmpfs;
a long-lived interactive container can slowly fill it (proxy log grows
unbounded), which would break tool calls writing to `/tmp`.

**Plan:**
1. Keep the shell `>>` redirect (it captures interpreter-level tracebacks
   before logging is configured) and add a size guard inside each proxy:
   at startup and then periodically (existing event/poll loop), if the log
   file exceeds ~20 MB, `os.truncate(path, 0)` — safe with `O_APPEND`
   writers, no fd dance needed.
2. Factor the guard as a small helper shared by both proxies (each file is
   copied standalone into the container, so duplicate the ~10-line helper
   rather than adding an import dependency).
3. Tests: unit test the truncate guard (size below/above threshold) for both
   proxy modules.
4. Docs: log location + rotation note in `docs/CLAUDE_CODE_INTERACTIVE.md`
   and `docs/ANTIGRAVITY_OBSERVER.md`.

### G3. Branch discipline for risky relay/WebSocket work
**Priority:** Process
**Effort:** Trivial
**Dependencies:** None

**Context:** 7 revert commits landed on `main` in one week (code-server WS
routing ×2, desktop relay fallback ×2, gateway bypass, private-only routes,
Codex event unwrap). Net state is coherent and tested, but `main` carried
transient broken states for transport-layer changes that are hard to unit
test.

**Plan:**
1. Adopt `feat/<topic>` branches for relay/WebSocket/proxy transport work;
   merge to `main` only after CI + a manual smoke of the affected surface
   (code-server iframe, VNC, desktop relay). CI already runs on
   `pull_request` (`.github/workflows/ci.yml`), so no pipeline change needed.
2. Add a short note to `CONTRIBUTING.md`: “`main` must stay releasable;
   transport-layer changes (relay tunnels, WebSocket proxying, capability
   routes) go through a feature branch with a manual smoke checklist.”
3. Optional: a `docs/smoke_checklist.md` with the 5-minute manual pass
   (code-server loads, VNC connects, desktop relay tunnel, Telegram bridge
   round-trip) to make the pre-merge smoke repeatable.
