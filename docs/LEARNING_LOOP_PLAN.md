# Learning Loop — Gap Analysis and Plan

*2026-07-16. Reference point: Hermes Agent's "built-in learning loop" (skills created from experience, improved during use, curated in background, persistence nudges, FTS session recall, user modeling). This document maps each component to what PawFlow already has and specifies the missing pieces.*

## 1. Where PawFlow already stands

PawFlow's cognitive stack (see `docs/COGNITIVE_TOOLS.md`) already covers a large part of the loop, in some places more richly than Hermes:

| Loop component | Hermes | PawFlow today |
|---|---|---|
| Persistent memory | MEMORY.md / USER.md + nudges | Memory store with categories, 4 visibility scopes, temporal validity, embeddings (`remember`/`recall`/`semantic_recall`), digest injected every turn |
| Automatic persistence | Periodic nudges to the agent | **Stronger: fully automatic** — auto-extraction every ~15 messages + post-compaction extraction via `summarizer_service` |
| Structured knowledge | — (none) | Knowledge graph: temporal triples, contradiction detection, BFS/DFS, communities — **no Hermes equivalent** |
| Agent self-reflection | — (implicit in memory) | Agent diary (observation/decision/learning/reflection), digest injected — **no Hermes equivalent** |
| Skills as artifacts | SKILL.md files, ~90 bundled, Skills Hub | Skills repository (global/user/conversation scopes), marketplace import (Codex, Anthropic, HermesHub, OpenClaw), signed `.pfp` distribution, RO FUSE mount + CLI bind mounts |
| Agent-created skills | ✅ core feature, nudged | ⚠️ **plumbing exists, loop does not**: `manage_resource` can create/update/assign skills from a conversation, `load_skill` loads them — but nothing tells the agent when to crystallize experience into a skill |
| Skill self-improvement | ✅ agent edits skills that failed | ❌ update path exists (`manage_resource`), no feedback capture, no instruction |
| Curator (usage, staleness, archival, LLM review) | ✅ background process | ❌ nothing — no usage counters, no lifecycle |
| Cross-session recall | FTS5 search over past sessions + LLM summarization | ❌ recall works over *extracted memories* only; raw past conversations are not searchable |
| User modeling | Honcho (external provider) | Memory L0 identity tier + KG about the user — adequate, different approach |

**Net gap = three things**: (a) the *trigger side* of skill creation/improvement, (b) a curator lifecycle, (c) raw conversation search. Everything else is storage/plumbing we already have.

## 2. Plan

### P1 — Close the skill-creation loop (prompt + trigger, no new storage)

1. **System-prompt block** (next to the existing `## Cognitive tools` hint): instruct the agent that when it has just completed a novel multi-step procedure (worked around a quirk, found a working sequence after failures), it should crystallize it as a skill via `manage_resource` (conversation scope by default) and note the trigger conditions in the skill description.
2. **Post-compaction skill proposal**: extend the existing post-compaction extraction prompt with one extra question to `summarizer_service`: "does this summary contain a reusable procedure not covered by an existing skill? If so, output a skill draft (name, description, steps)". Draft is surfaced to the agent (not auto-installed) as a pending suggestion.
3. **Improvement feedback**: append a one-line footer to `load_skill` output: "If these instructions proved wrong or outdated during use, update the skill via manage_resource." This is Hermes' "self-improvement during use" for one sentence of cost.

Deliverable: prompts only + one extraction-prompt change. Highest story value ("agents write and maintain their own skills") for the lowest cost.

### P2 — Usage tracking + promotion

1. Record per-skill stats on `load_skill`: `loads`, `last_used_at`, `created_by` (agent/user/import), stored in a sidecar (`data/repository/skills/.../_stats.json` or a single `data/runtime/skill_stats.json`). Atomic tmp-then-replace like the other stores.
2. **Scope promotion**: when a conversation-scoped, agent-created skill reaches N loads across ≥2 conversations, suggest promotion to user scope (agent asks the user via normal conversation; no silent escalation — consistent with the review-first `.pfp` philosophy).

### P3 — Curator as a bundled flow

Implement the curator with PawFlow's own flow/task engine (dogfooding — also a demo/marketing asset):

- Scheduled flow `skill-curator` (per user): reads skill stats, flags unused-for-90-days and never-loaded skills, runs an LLM review (`summarizer_service`) on flagged skills for staleness/overlap/contradiction with newer skills, and produces a report with proposed actions (archive / merge / keep). Actions are applied only after user confirmation.
- Archive = move to an `archived/` subtree excluded from mounts and `load_skill`, never delete.

### P4 — Conversation search (`conversation_search` tool)

- SQLite FTS5 index per user over the conversation store (message text + agent + conversation id + timestamp), built lazily and updated incrementally on append; index lives in `data/runtime/`.
- Tool: `conversation_search(query, agent?, limit?)` → snippets with conversation/message references; optional `summarize=true` runs `summarizer_service` over the top hits.
- Scoping mirrors memory visibility: a user's index only, agent filter optional. Keep it read-only.
- This is the piece that lets an agent answer "we solved this before, in which conversation?" without having extracted a memory at the time.

### P5 (optional) — Periodic self-reflection

A lightweight periodic trigger (same cadence family as auto-save) prompting the agent to write one diary `reflection` entry synthesizing recent learnings, and to check whether any deserve KG triples or a skill. Cheap; deepens the existing diary rather than adding a system.

## 3. Sequencing and effort

| Phase | Effort | Dependencies | Status |
|---|---|---|---|
| P1 prompts + extraction change | Small (days) | none | **Done 2026-07-16** — `core/skill_loop.py`, hint injected in `tasks/ai/_agentctx_p3.py`, drafts proposed from `core/_bg_bucket_build.py` (bucket + rollup), footer via `load_skill` |
| P2 stats + promotion | Small/medium | P1 | **Done 2026-07-16** — `core/skill_stats.py` (`data/runtime/skill_stats.json`), promotion suggestion in `core/handlers/skills.py` |
| P3 curator flow | Medium | P2 (needs stats) | **Done 2026-07-16** — `skillCurator` task (`tasks/system/skill_curator.py`), report-only; schedule with a cron trigger |
| P4 conversation FTS | Medium | none (parallel) | Pending |
| P5 reflection trigger | Small | none | Pending |

Tests: `tests/test_skill_loop.py` (18 tests: drafts, stats, footer/promotion, curator).

## 4. Positioning note

After P1–P3, the "only agent with a built-in learning loop" claim no longer differentiates Hermes against PawFlow — and PawFlow's version has two angles Hermes cannot match: the loop is **multi-agent** (skills and the knowledge graph are shared across agents with per-agent diaries), and the curator is a **user-visible flow** rather than a hidden background process (review-first, consistent with the security story).
