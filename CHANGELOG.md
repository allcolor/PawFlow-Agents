# Changelog

All notable changes to PawFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0-alpha.1] — 2026-04-08

First public release.

### Added

**AI Agents**
- Multi-agent conversations with tool-use loop (LLM → tool → LLM → ...)
- 4 LLM providers: Claude Code (subprocess + MCP), Anthropic API, OpenAI API, Gemini CLI
- Streaming SSE output to web chat and CLI
- Plan system: structured plan creation, approval, assignment, verification
- Context compaction with `{agent_name}.md` re-injection
- Configurable permission modes: auto, approve-edits, read-only
- Cost tracking with per-conversation budget caps (`max_budget_usd`)
- Force stop: Escape 1x = graceful, 2x = immediate kill

**Tools (80+)**
- Filesystem: read, write, edit, glob, grep, list_dir, move, delete
- Execution: bash, execute_script, run_in_background, screen
- Web: web_fetch, web_search, web_screenshot
- Media: generate_image, generate_video, generate_audio, see (vision)
- Git: git_log, git_diff, git_commit, git_branch
- Multi-agent: delegate, manage_resource, ask_user
- Security: security_scan, validate_http_auth
- MCP: connect to any MCP server, tools auto-discovered
- All tools route through relay for sandboxed execution

**Cognitive Systems**
- Memory: categorized facts with scopes and temporal validity
- Knowledge Graph: entity-relationship triples with BFS/DFS, community detection
- Agent Diary: per-agent personal journal
- Project Graph: AST-based code structure analysis (17 languages via tree-sitter)
- Memory digests auto-injected into system prompt

**Pipeline Engine**
- 101 NiFi-style tasks across 5 categories (System, IO, Data, Control, AI)
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
- @file autocomplete from relay filesystem
- 60+ slash commands
- Drag & drop file attachments
- Multi-agent support with agent switching

**Infrastructure**
- 9 OAuth2 providers (Google, GitHub, Microsoft, X, Facebook, Amazon, Telegram, Generic)
- Expression language: 40+ chainable operations with scope cascade
- Docker relay for sandboxed tool execution
- Plugin system with semver versioning, .pfp export/import
- Cluster mode with leader election
- Audit logging, rate limiting, Prometheus metrics
- HTTP listener service with SSL/TLS
- PawCode CLI (Claude Code drop-in replacement)
- 2589 tests

### Security
- Secrets encrypted at rest (XOR + PBKDF2 + HMAC)
- PBKDF2 password hashing (600K iterations)
- `config/secret.key` excluded from version control
- Configurable CORS, rate limiting, request size limits
- Sandboxed script execution with restricted imports
