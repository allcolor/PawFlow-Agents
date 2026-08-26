# Cognitive Tools

PawFlow provides five persistent cognitive systems plus two scoped work-state
layers. The distinction matters: durable facts, an agent's experience, unfinished
work, and temporary evidence have different owners, lifetimes, and retrieval
rules.

**The five persistent systems and two work-state layers:**

| System | Purpose | Storage |
|---|---|---|
| **Memory** | Persistent facts, preferences, events | `data/memories/{user}.json` |
| **Knowledge Graph** | Entity-relationship triples with temporal validity | `data/knowledge_graphs/{user}.json` |
| **Agent Diary** | Per-agent journal of observations and decisions | `data/memories/{user}/diary_{agent}.jsonl` |
| **Project Graph** | AST-based code structure graph (17 languages) | `data/runtime/graphs/{safe_user}/{safe_relay}/graph.json` |
| **Project Wiki** | LLM-maintained Markdown with source-hash provenance | `data/runtime/project_wikis/{safe_user}/{safe_relay}/` |
| **Todo List** | Authoritative unfinished work for one conversation agent | `data/runtime/todolists/todos.sqlite3` |
| **Scratchpad** | Expiring working evidence and resume notes for one conversation agent | `data/runtime/scratchpads/scratchpads.sqlite3` |
| **ScratchDir** | Relay-backed temporary files scoped to one conversation agent | `data/runtime/scratchdirs/scratchdirs.sqlite3` + relay runtime root |

A procedural layer — the **skill loop** — closes learning into reusable artifacts: agents are instructed (via a `## Skill loop` system-prompt block) to crystallize novel multi-step procedures into skills with `manage_resource` and to fix skills that proved wrong during use; each completed compaction bucket or rollup can produce a structured `skill-draft` memory; recurrence in a different conversation automatically promotes the procedure to a user skill; the Memories UI still exposes pending drafts for reviewed promotion or deletion; `load_skill` tracks usage and suggests scope promotion; and the `skillCurator` flow task produces review-first maintenance reports. See [LEARNING_LOOP_PLAN.md](LEARNING_LOOP_PLAN.md).

They are interconnected:

- **Memory** and **diary** digests are injected dynamically on API turns and
  serialized into cold CLI bootstraps. Scratchpad note bodies are never injected;
  only a count, expiry, and up to five topic labels are shown.
- When a memory is stored, the system cross-checks the **knowledge graph** for contradictions and warns the agent.
- **Auto-extraction** triggers periodically pull facts from conversation text into both memory and KG.
- The **project graph** and **project wiki** are relay-scoped, refreshed asynchronously for the active relay, and shared by every conversation and agent using that project.
- **ScratchDir** is the file counterpart of Scratchpad. Use
  `fs://scratchdir/` (or `/scratch` in shell execution) for temporary files
  that must survive one tool call. Its UI shows bounded file metadata, quotas,
  expiry, renewal, exact clear, and explicit promotion to FileStore; it never
  exposes the relay's physical root. Symbolic links are accepted only when
  their resolved targets remain inside the same scoped root; escaping or cyclic
  links fail closed. Filesystem handlers validate a cached scoped ticket against
  the current store epoch before reuse, so `clear` followed by `ensure`
  immediately rebinds instead of retaining a stale facade.

---

## 1. Memory System

The memory system stores persistent facts per user. Memories survive across conversations and are scoped by visibility, organized by taxonomy, and support temporal validity.

### 1.1 Categories

Memories are classified by category:

| Field | Purpose | Values |
|---|---|---|
| **`category`** | Memory type | `facts`, `events`, `discoveries`, `preferences`, `advice` |

Categories can be filtered directly with `recall(category=...)`.

### 1.2 Scopes

Every memory has a visibility scope determined by the `agent` and `conversation_id` fields:

| Scope | `agent` | `conversation_id` | Visible to |
|---|---|---|---|
| **global** | `""` | `""` | All agents in all conversations |
| **agent** | `"coder"` | `""` | Only this agent, in any conversation |
| **conversation** | `""` | `"abc123"` | All agents, but only in this conversation |
| **private** | `"coder"` | `"abc123"` | Only this agent in this specific conversation |

When recalling, results are sorted by scope priority: private > conversation > agent > global.

**Example -- storing a global memory:**

```json
{
  "text": "User's timezone is Europe/Paris",
  "tags": ["preference", "timezone"],
  "scope": "global",
  "category": "preferences"
}
```

### 1.3 Temporal Validity

Memories support time-bounded validity:

- **`valid_from`** (epoch float): When the fact became true. `0` means valid since creation.
- **`ended`** (epoch float): When the fact stopped being true. `0` means still active.

The `end_memory` method marks a memory as ended without deleting it. The `as_of` parameter on `recall` filters to memories valid at a specific point in time.

**Example -- ending a memory:**

```
# The user switched from PostgreSQL to SQLite
> end_memory(memory_id="a1b2c3d4e5f6")
# Then store the new fact:
> remember("User switched to SQLite for local storage", tags=["decision"], category="facts")
```

### 1.4 Auto-Extraction Triggers

Memories are extracted automatically in two situations:

1. **Periodic auto-save** -- Every ~15 user messages, the system extracts key facts from recent conversation text using the `summarizer_service` LLM. The counter is tracked per-agent via conversation extras (`_auto_save_count:{agent}`).

2. **Post-compaction extraction** -- When a conversation is compacted, the bucket or rollup summary is fed to `auto_extract_memories()`. The operation resolves the effective `summarizer_service` itself and fails closed when that service or its configured LLM is unavailable; callers cannot inject the active agent client. This path is intentionally conservative: it stores at most two durable memories per extraction, rejects ephemeral/current-task state, and asks the LLM for `importance`, `durability`, `scope`, and `ttl_days` metadata. Extracted memories are tagged `["auto-extracted", "compaction"]`.

Compaction auto-extract does not write global permanent memories by default. Only durable high/critical user preferences or advice may become global. Project/debug facts are stored in conversation scope with a TTL unless explicitly classified as durable. Existing stale auto-extracted entries can be marked ended with `scripts/memory_gc.py`; ended memories remain in the raw JSON audit trail but are ignored by normal recall and the memory panel.

Memory embeddings are optional and use the normal expression cascade. If `embedding_llm_service` is set to an LLM service that exposes an OpenAI-compatible embeddings endpoint, PawFlow uses that service for `remember`, `semantic_recall`, auto-extracted memories, and mirrored Claude Code memories. If the parameter is absent or unusable, PawFlow falls back to the local MiniLM embedder when it is installed; otherwise memories are stored without vectors and remain available through keyword recall.

### 1.5 Memory Digest Injection

At every conversation turn, a compact multi-tier digest is built from the user's memories and injected into the system prompt under `## Persistent memory`. The tiers are:

| Tier | Source | Max items |
|---|---|---|
| **L0** | Identity/profile (tags: `identity`, `profile`) | 3 |
| **L1** | Key facts (category: `facts`) | 5 |
| **L1** | Preferences (category: `preferences`) | 3 |
| **L2** | Recent events (category: `events`, sorted by date) | 3 |
| **L3** | Active decisions (tags: `decision`, category: `facts`) | 3 |
| **L4** | Discoveries (category: `discoveries`) | 3 |
| **L4** | Advice (category: `advice`) | 2 |
| **KG** | God nodes (most connected entities from Knowledge Graph) | 5 |

The digest is capped at 1200 characters by default. If there are no relevant memories, nothing is injected.

---

## 2. Knowledge Graph

The knowledge graph stores facts as temporal (subject, predicate, object) triples per user. It supports contradiction detection, graph traversal, and community analysis.

### 2.1 Triples

A triple represents a single fact:

```json
{
  "id": "a1b2c3d4e5f6",
  "subject": "PawFlow",
  "predicate": "uses",
  "object": "tree-sitter",
  "valid_from": "2025-06",
  "valid_to": "",
  "confidence": "EXTRACTED",
  "confidence_score": 1.0,
  "source": "conversation",
  "extracted_at": 1712345678.0
}
```

### 2.2 Confidence Levels

| Level | Score range | Meaning |
|---|---|---|
| **EXTRACTED** | >= 0.9 | Directly stated by the user or explicitly observed |
| **INFERRED** | 0.5 -- 0.89 | Deduced from context or indirect evidence |
| **AMBIGUOUS** | < 0.5 | Uncertain, possibly contradictory |

Confidence can be provided as a string (`"EXTRACTED"`, `"INFERRED"`, `"AMBIGUOUS"`) or as a numeric score. Numeric scores are automatically mapped to the corresponding label.

### 2.3 Contradiction Detection

When adding a triple, the system checks for active triples with the same subject and predicate but a different object. If found, the response includes a `contradictions` list:

```
> kg_add(subject="Quentin", predicate="prefers_editor", object="Neovim")
added: Quentin -> prefers_editor -> Neovim (id: x1y2z3)
Warning: Contradicts active values: VS Code
```

The old triple is NOT automatically invalidated. The agent must decide whether to call `kg_invalidate` on the old value.

### 2.4 Temporal Validity

Each triple has:
- **`valid_from`**: ISO date string (e.g. `"2026-01"`) -- when the fact became true.
- **`valid_to`**: ISO date string -- when the fact expired. Empty string `""` means still active.

The `query_entity` method supports an `as_of` parameter to retrieve only facts valid at a specific date.

### 2.5 Graph Traversal (BFS / DFS)

The `query_graph` method traverses the graph starting from entities matching a question:

- **BFS** (default): Broad context -- explores all seeds in parallel, returning a wide view of connections up to the specified depth.
- **DFS**: Deep path -- traces a single path from the first matching entity, going deep before wide.

Parameters: `question` (text to match), `mode` ("bfs" or "dfs"), `depth` (default 3), `max_results` (default 50).

Only active triples (with empty `valid_to`) are traversed.

**Example:**

```
> query_graph(question="authentication", mode="bfs", depth=2)
Graph traversal for 'authentication' (7 connections):
  [EXTRACTED] AuthGateway -> supports -> Google
  [EXTRACTED] AuthGateway -> supports -> GitHub
  [EXTRACTED] AuthGateway -> implements -> OAuth2
  [INFERRED] OAuth2 -> used_by -> IdentityService
  ...
```

### 2.6 God Nodes

God nodes are the most connected entities in the graph, ranked by degree (number of active triples referencing them as subject or object). Useful for identifying central concepts.

```
> kg_god_nodes(limit=5)
Most connected entities:
  PawFlow (23 connections)
  Quentin (15 connections)
  AuthGateway (12 connections)
  PostgreSQL (8 connections)
  Docker (7 connections)
```

---

## 3. Agent Diary

The diary is a per-agent journal that persists across conversations. Unlike
memories (facts about the user/project/world), it stores the agent's own
first-person decisions, lessons, recurring failure patterns, and reflections.
Write after a non-obvious choice or a lesson likely to improve future work, not
as a routine turn log. Use the todo list for unfinished work and the scratchpad
for temporary evidence.

### 3.1 Entry Types

| Type | When to use |
|---|---|
| `observation` | A recurring or consequential pattern the agent noticed (default) |
| `decision` | A choice the agent made and why |
| `learning` | A lesson learned from experience |
| `reflection` | Higher-level thinking about patterns |

**Example:**

```
> diary_write(
    entry="User prefers concise error messages over detailed stack traces in production logs. This seems to be a UX-driven decision.",
    type="observation",
    tags=["logging", "ux"]
  )
```

### 3.2 Diary Digest Injection

The 10 most recent diary entries are built into a compact digest (max 600 characters) and injected into the system prompt under `## Your diary (past observations)`. Each entry's text is truncated to 100 chars.

### 3.3 Difference vs Memory, Todo, and Scratchpad

| Layer | Put this here | Scope / lifetime | Context behavior |
|---|---|---|---|
| **Memory** | Durable facts/preferences/events about user, project, or world | Configurable visibility; persistent | Digest plus explicit keyword/semantic recall |
| **Diary** | Agent's durable decisions, lessons, patterns, reflections | User + agent; persistent across conversations | Last 10 entries injected; older/type-filtered entries via `diary_read` |
| **Todo** | Authoritative unfinished work and verification state | User + conversation + agent; durable until completed | Active and recent completed items injected |
| **Scratchpad** | Temporary evidence, hypotheses, local decisions, resume cues | User + conversation + agent; TTL 1-720 hours | Only topics/count/expiry hinted; note bodies require `list` or `get` |

---

## 4. Project Graph

The project graph builds a structural code graph from a codebase using tree-sitter AST extraction. The relay ID is the project identity, so one cached graph is shared across conversations and agents attached to that relay. Extraction runs on the relay where the source files live.

### 4.1 Build via Relay

Initial context preparation schedules a background build automatically. Successful
relay writes and shell commands schedule a debounced incremental refresh. The
manual `build` action remains available for recovery or an explicit root change.
Automatic maintenance always indexes the relay container, even when a server-local
mutation triggered the refresh; otherwise it would index the deployed runtime
instead of the relay-scoped project. An explicit manual `build(local=true)`
remains available when the caller intentionally targets the local surface.
Each build runs as a single relay exec; the extraction script travels
base64-encoded in the `PAWFLOW_GRAPH_SCRIPT` env var and is executed in memory
by a tiny fixed command, without writing a helper file into the source tree.
Nothing sizeable rides in the command line, keeping it under the Windows
cmd.exe 8191-char cap. The script bootstraps `sys.path` itself before importing
the extractor, trying `PAWFLOW_RELAY_CODE_DIR` then `/opt/pawflow`, since the
relay exec env carries no `PYTHONPATH`. Managed relay runtimes stage the
integrated `graphify` package alongside the relay handlers and include it in
the runtime source hash, so server upgrades cannot reuse a stale runtime that
lacks the extractor. Small deltas retain
Graphify's normal grouped cross-file resolution. Large
deltas are AST-parsed one file at a time in a memory-bounded sequential pass.
Nodes are compressed as they are produced and edges use an anonymous disk spool,
so the relay never retains a large corpus in RAM. The gzip/base64 delta stays
below the relay's bounded text-output transport; the server validates and decodes
that versioned payload before merging it.

1. **Server sends** the cached `{rel_path: mtime}` map to the relay
   via `PAWFLOW_GRAPH_KNOWN` (gzip+base64, so large maps stay under the
   ~32K per-variable Windows cap).
2. **Relay walks** the workspace tree, skipping standard junk dirs
   (venv, node_modules, .git, build, dist, __pycache__, etc.).
3. **Re-parses only files** whose mtime differs from `known` (or are
   new). Unchanged files keep their cached nodes/edges.
4. **Reports**: `parsed_files` (re-parsed), `removed` (in `known` but
   missing now), `mtimes` (new map), `nodes`/`edges` (just for the
   re-parsed slice).
5. **Server merges**: drops nodes/edges sourced from re-parsed or
   removed files, appends the new ones.
6. **No file count cap**. Memory cost grows roughly linearly with codebase size.
   PawFlow consumes Graphify's extracted lists directly instead of materializing
   a duplicate NetworkX graph on the memory-bounded relay.
7. **Cache hit**: if nothing changed and nothing was removed, the
   relay returns `status='unchanged'` and no parsing happens server-
   or relay-side.

**Supported languages (17):** Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, Ruby, C#, Kotlin, Scala, PHP, Swift, Lua, Zig, PowerShell, Elixir.

Supported file extensions: `*.py`, `*.js`, `*.ts`, `*.tsx`, `*.go`, `*.rs`, `*.java`, `*.c`, `*.h`, `*.cpp`, `*.cc`, `*.cxx`, `*.hpp`, `*.rb`, `*.cs`, `*.kt`, `*.kts`, `*.scala`, `*.php`, `*.swift`, `*.lua`, `*.toc`, `*.zig`, `*.ps1`, `*.ex`, `*.exs`.

### 4.2 Query / Report / Node

**query**: BFS traversal starting from nodes matching the question text. Returns edges with source, target, relation, and confidence.

```
> project_graph(action="query", question="AuthGateway", depth=3)
Project graph query 'AuthGateway' (12 edges):
  [EXTRACTED] AuthGatewayService -> inherits -> BaseService
  [EXTRACTED] AuthGatewayService -> calls -> validate_token
  ...
```

**report**: Summary including node/edge counts, confidence breakdown, and god nodes (most connected code entities).

**node**: Details about a specific code entity -- file, location, type, and neighbor edges (up to 20).

### 4.3 Source Parameter

The `source` parameter on the `build` action specifies which relay/filesystem service to use for fetching code. If omitted, the default relay is used. This must be a relay service (not a filestore) since the code lives on the user's machine.

---

## 5. Project Wiki

The project wiki is a persistent set of generated Markdown pages for one
`(user, relay)` project. Source files remain on the relay. PawFlow stores only
SHA-256 source metadata, generated pages, an index, an append-only activity log,
and exact source provenance for every factual page.

Automatic wiki maintenance resolves the conversation's effective
`summarizer_service` for every job, then uses only the LLM service configured by
that summarizer. It never reuses the active agent's LLM client and does not fall
back to another LLM when the summarizer binding is unavailable.

Context preparation and successful relay mutations schedule the same coalesced
background worker as the Project Graph. The worker scans source hashes, selects
one bounded batch of changed high-signal files, and makes one ephemeral LLM call.
The source scanner is encoded into the relay command and executed in memory; it
does not create a helper file in the project or on the server-local root.
Wiki scans and updates run **only** on the relay container surface:
`local=true` is rejected with a `ValueError`, because the server/host working
tree is the deployed runtime (`app/data/runtime/...`), not the project — one
local scan would poison the manifest with thousands of phantom sources that the
next relay scan reports as removed, leading the maintainer LLM to write bogus
"removals" pages. The maintenance worker and the panel refresh action pin
`local=False` regardless of the surface used for the graph build.
If a manifest was poisoned before this guard existed, `acknowledge` accepts
glob patterns (`app/*`, `usr/*`) that expand against the pending set, so the
phantom backlog can be cleared with a handful of patterns instead of an
exhaustive path list.
The first scan seeds root configuration, architecture documentation, and central
graph files instead of enqueueing an entire large repository. Later additions,
changes, and removals become pending automatically.

Changing the selected project root performs a full derived-state reset: the AST
graph is rebuilt, old generated wiki pages and pending entries are removed, and
the new root is seeded again from high-signal sources.

The LLM receives untrusted source text and affected existing pages, returns
validated JSON, and may update up to twelve pages. A page is written with the
current SHA-256 digest of every cited source. If any batched source changes while
the LLM call is running, the response is marked `superseded`, no page is written,
and the newer source remains pending for the next worker run.
An empty, malformed, or structurally invalid LLM response is also fail-closed:
no page or source marker changes, the batch remains pending, and the graph/source
scan portion of project maintenance still completes.
If an otherwise structured LLM response omits only a page's `sources` field,
the embedded maintainer conservatively fills it from the non-removed
`processed_sources` in the exact selected snapshot. When that list is itself
absent or empty, it uses all non-removed sources from the exact selected batch
and records them as processed. It then runs the same strict patch and citation
validation, and never repairs malformed, removed, or out-of-snapshot citations.
For a removed-only batch there is no live citation to infer: uncited page
proposals are discarded, while the declared removals remain processed. PawFlow
never invents a factual page citation from a deleted source.
The Auto Wiki prompt separately budgets the final JSON document. It does not use
that response budget as the provider generation ceiling, because some providers
include internal reasoning in their output-token limit. The provider transport
limit therefore remains controlled by the selected LLM service configuration.

The `project_wiki` tool provides manual inspection and recovery:

| Action | Purpose |
|---|---|
| `status` | Show source/page counts, pending changes, scan limits, and stale pages. |
| `query` | Rank generated pages by full-text relevance. |
| `page` | Read one page by slug. |
| `lint` | Report stale pages, missing links/files, orphans, and uncited pages. |
| `refresh` | Rescan source hashes manually. |
| `upsert` | Create or replace a page with current source citations. |
| `acknowledge` | Clear processed sources; stale cited sources are refused. |

Agents receive a compact wiki status digest on each prepared turn. They should
query the wiki before broad architectural exploration and validate stale claims
against live source files.

### 5.1 Webchat Panels and Scratchpad

The webchat **Agent tools** menu exposes the cognitive panels. Project Graph,
Project Wiki, and Scratchpad can also be opened with `/graph`, `/wiki`, and
`/scratchpad`; Diary and Memory are available from the same menu and their slash
commands.

| Panel | Available actions |
|---|---|
| **Project Graph** | Select an explicitly linked relay, automatically load its existing report, build or refresh the derived AST index, search nodes and edges, and inspect a node's source location and neighbors. **View** opens an interactive force-directed canvas in a tab: it starts from an overview of the most-connected nodes and navigates by capped ego subgraphs (`project_graph_ego`, ≤300 nodes, 1–2 hops) — click selects a node, double-click/double-tap re-centers on it, edges are colored by confidence, and pan/zoom/pinch work with mouse and touch. The full graph is never rendered at once; the view page requests subgraphs from the panel over `postMessage`. Stored reports remain readable while that relay is disconnected. The graph is read-only because source code is its source of truth. |
| **Project Wiki** | Select an explicitly linked relay, list and search pages, render Markdown, create or edit a page, delete a page, refresh source metadata, and run lint checks. |
| **Scratchpad** | Select a conversation agent, list and search working notes, create or edit a note with tags and a required TTL, delete one note, or clear that agent's notes. |
| **Diary** | Select a conversation agent, read structured diary entries, filter them by type, and add an entry. |
| **Memory** | Select all agents, global memory, or a specific conversation agent for filtering and explicit add/edit targeting. |

Project Graph and Project Wiki always send the relay selected in the panel.
Scratchpad notes are isolated by user, conversation, and the agent selected in
the panel, and expired notes are removed from normal reads automatically.

---

## 6. Work-State Routing

Use `todolist` before meaningful multi-step or long-running work and keep its
status authoritative. Use `scratchpad` only when the work needs transient
evidence or a resume note that should expire.

| Situation | Tool |
|---|---|
| Work must resume after compaction/restart and completion matters | `todolist` |
| Evidence or a hypothesis is useful only inside the current conversation agent | `scratchpad` |
| A lesson should improve this agent's work in future conversations | `diary_write` |
| A fact should be available as durable user/project/world knowledge | `remember` or `kg_add` |

Scratchpad bodies are deliberately pull-only. When the injected Scratchpad Hint
names a relevant topic, call `scratchpad(action="list", query="...")` or
`scratchpad(action="get", note_id="...")`. Update existing notes rather than
duplicating them, and delete obsolete notes before their TTL when possible.

---

## 7. Auto-Triggers

### 7.1 Periodic Auto-Save (Every ~15 Messages)

The `_maybe_auto_save_memories` method runs after each agent response. It checks if 15 new user messages have accumulated since the last save. When triggered:

1. Loads the last 15 messages from the conversation store.
2. Concatenates user and assistant message text (first 200 chars each).
3. Uses the `summarizer_service` LLM to extract structured facts.
4. Stores extracted facts via `auto_extract_memories()` with tag `auto-extracted`.

### 7.2 Post-Compaction Extraction

When a conversation is compacted (context window overflow), `_auto_extract_memories` is called with the compaction summary. The extraction uses an LLM prompt asking for 3-5 key facts as JSON:

```json
[
  {"text": "User prefers JSON over SQLite for storage", "category": "preferences"},
  {"text": "Auth middleware rewrite driven by compliance", "category": "facts"}
]
```

If no LLM is available, a heuristic fallback scans for decision/preference indicator words.

### 7.3 Summarizer Service

Both auto-triggers use the `summarizer_service` -- a lightweight LLM configured for extraction tasks. It is resolved via `_get_summarizer_client()` using the user's service configuration.

### 7.4 Skill Draft Proposals

Every completed compaction bucket and rollup summary is also inspected for one
repeatable operational procedure. Coverage requires an existing skill to target
the same outcome; a broad skill about the same product or domain does not suppress
a release, deployment, migration, validation, or recovery procedure.

The proposer records an INFO outcome for every attempt (`created`, `promoted`,
`rejected`, `invalid`, `duplicate`, or `skipped`) and a WARNING with traceback for `error`.
Created proposals are conversation-scoped memories tagged `skill-draft` with a
bounded structured payload. In the Memories panel, **Skill drafts** filters these
entries. **Promote** submits the generated instructions through the canonical
skill security review and creates a conversation-scoped skill; PawFlow deletes
the draft only after creation succeeds. The normal delete action rejects a draft.
A first occurrence stays a draft. If the same normalized procedure is extracted
from another conversation, PawFlow creates a user-scoped skill through the
validated `ResourceStore` path and removes the draft only after creation succeeds.
One conversation cannot confirm its own draft, and validation, name collisions,
or storage failures leave the draft intact.

---

### 7.5 Reflection nudge

The diary accepts a `reflection` entry type that nothing ever asked for: agents
write observations and decisions as they work, and the synthesis across them
never happens. `core/reflection_trigger.py` injects a `Reflection due` block
next to the diary digest asking for exactly one — and for a check of whether
the synthesis deserves a `kg_add` triple or a skill.

It is deliberately silent most of the time. Both conditions must hold:

| Condition | Default | Why |
|---|---|---|
| Diary entries since the last reflection | ≥ 5 (`MIN_ENTRIES_SINCE`) | Nothing to synthesize otherwise |
| Time since the last reflection | ≥ 6h (`MIN_INTERVAL_S`) | A busy afternoon should not produce three |

A standing "remember to reflect" instruction is one an agent learns to skip;
this one appears only when it is actually due, and disappears once the
reflection is written.

---

## 8. System Prompt Injection

The static system prompt contains one canonical routing block,
`## Tool selection`. Mutable data stays out of the cached
prefix: API providers append it to the latest user turn, while a cold CLI session
serializes it into `initial_context.md`.

### 8.1 Persistent Memory Digest

Injected under `## Persistent memory`. Contains the multi-tier digest (L0-L4 + KG god nodes). Max 1200 characters. Only present if the user has stored memories.

### 8.2 Diary Digest

Injected under `## Your diary (past observations)`. Contains the last 10 diary entries (truncated). Max 600 characters. Only present if the agent has diary entries.

### 8.3 Availability-Aware Tool-Selection Hint

Generated from `core/tool_selection.py` using the active agent's filtered tool
registry. It names the selection boundary and positive trigger for every
cognitive/work-state route that is actually available, alongside the ambiguous
delegation, orchestration, and waiting families. Families with fewer than two
available routes are omitted from the permanent hint. Use
`get_tool_schema(family="cognition")` for the complete available comparison;
the individual tool schema remains the source of truth for parameters.

### 8.4 Todo and Scratchpad Context

Active todo state is injected automatically. Scratchpad note content is never
injected; a non-empty scratchpad contributes only a compact hint with note count,
earliest expiry, and up to five topic labels.

---

## 9. Storage Paths

All paths are relative to the PawFlow data directory:

| System | Path | Format |
|---|---|---|
| Memory store | `data/memories/{user_id}.json` | JSON array of MemoryEntry objects |
| Knowledge graph | `data/knowledge_graphs/{user_id}.json` | JSON with `entities` and `triples` |
| Agent diary | `data/memories/{user_id}/diary_{agent_name}.jsonl` | JSONL, one record per line |
| Project graph | `data/runtime/graphs/{safe_user}/{safe_relay}/graph.json` | JSON with `nodes`, `edges`, `metadata` |
| Project wiki | `data/runtime/project_wikis/{safe_user}/{safe_relay}/` | Markdown pages plus JSON manifest |
| Conversation index | `data/runtime/conversation_index/{user_id}.db` | SQLite FTS5, derived from transcripts |
| Todo list | `data/runtime/todolists/todos.sqlite3` | SQLite, scoped by user/conversation/agent |
| Scratchpad | `data/runtime/scratchpads/scratchpads.sqlite3` | SQLite with TTL, scoped by user/conversation/agent |

All writes use the atomic tmp-then-replace pattern: write to `.tmp` file first, then `replace()` to the final path. The conversation index is the exception and deliberately so: it is SQLite (WAL), and it is *derived* data — deleting the file costs the next search one rebuild and loses nothing.

---

## 10. Tool Reference

The runtime registry currently exposes 20 cognitive and work-state tools.

### Memory Tools (5)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 1 | **`remember`** | `text` (string, required), `tags` (string[]), `scope` (enum: conversation/agent/global/private), `category` (enum: facts/events/discoveries/preferences/advice), `valid_from` (number) | Store a fact in persistent memory |
| 2 | **`recall`** | `query` (string), `tags` (string[]), `category` (enum: facts/events/discoveries/preferences/advice), `as_of` (number) | Search memories by text, tags, and category |
| 3 | **`semantic_recall`** | `query` (string, required), `limit` (integer), `category` (enum) | Search memories by meaning via vector embeddings |
| 4 | **`forget`** | `memory_id` (string, required) | Delete a specific memory by ID |
| 5 | **`check_duplicate`** | `text` (string, required), `category` (string) | Check if a similar memory already exists |
### Knowledge Graph Tools (7)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 6 | **`kg_add`** | `subject`, `predicate`, `object` (required), `valid_from`, `confidence`, `source` | Add a fact triple with contradiction detection |
| 7 | **`kg_query`** | `entity` (required), `as_of`, `direction` | Query all facts about an entity |
| 8 | **`kg_invalidate`** | `subject`, `predicate`, `object` (required), `ended` | Mark a fact as no longer valid |
| 9 | **`kg_timeline`** | `entity`, `limit` | Chronological history of facts |
| 10 | **`kg_stats`** | _(none)_ | Summary statistics |
| 11 | **`query_graph`** | `question` (required), `mode`, `depth`, `max_results` | BFS/DFS traversal from matching entities |
| 12 | **`kg_god_nodes`** | `limit` | Most connected entities |

### Diary Tools (2)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 13 | **`diary_write`** | `entry` (required), `type`, `tags` | Write a durable agent-experience entry |
| 14 | **`diary_read`** | `limit`, `type`, `agents` | Read recent own or explicitly selected same-user agent entries |

### Project Graph Tools (1, with 4 actions)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 15 | **`project_graph`** | `action` (build/query/report/node), `path`, `question`, `depth`, `source` | Inspect relay-scoped code structure |

**Action breakdown:**

| Action | Required params | What it does |
|---|---|---|
| `build` | `path` (default "."), `source` (optional relay name) | Fetch code via relay, run AST extraction, build graph |
| `query` | `question`, `depth` (default 3) | BFS traversal on the graph |
| `report` | _(none)_ | Summary with god nodes, stats, confidence breakdown |
| `node` | `question` (node label) | Details about a specific code entity |

### Project Wiki Tools (1, with 10 actions)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 16 | **`project_wiki`** | `action` (status/pages/query/page/page_data/lint/refresh/upsert/delete/acknowledge), plus action-specific fields | Query, inspect, refresh, or repair the relay-scoped project wiki |

### Work-State Tools (2)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 17 | **`todolist`** | `action` (create/update/list/get), plus action-specific fields | Authoritative durable work state for one conversation agent |
| 18 | **`scratchpad`** | `action` (create/update/get/list/delete/clear), note fields, TTL, pagination | Expiring pull-only working notes for one conversation agent |

### Learning and Conversation Search (2)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 19 | **`learn`** | `limit` | Extract user preferences and communication patterns from raw messages |
| 20 | **`conversation_search`** | `query` (required), `agent`, `limit`, `include_current`, `summarize` | Search raw text of past conversations |

This is the counterpart of `recall`, not a duplicate of it. `recall` searches
memories — what an agent decided at the time was worth keeping. This searches
what was actually said, which is the only way to answer "we solved this
before, where?" when nobody extracted a memory back then. `read_history`
remains the tool for the *current* conversation.

Encrypted conversations are never indexed (the index is plaintext, so
indexing one would undo the encryption), and only the searching user's own
conversations are in their index. See
[tool_catalog.md](tool_catalog.md#searching-past-conversations) for the full
behaviour.

### Summary: 20 Exposed Tools

- 5 memory tools (`remember`, `recall`, `semantic_recall`, `forget`, `check_duplicate`)
- 7 knowledge graph tools (`kg_add`, `kg_query`, `kg_invalidate`, `kg_timeline`, `kg_stats`, `query_graph`, `kg_god_nodes`)
- 2 diary tools (`diary_write`, `diary_read`)
- 1 project graph tool with 4 actions (`project_graph`)
- 1 project wiki tool with 10 actions (`project_wiki`)
- 2 work-state tools (`todolist`, `scratchpad`)
- 1 learning tool (`learn`)
- 1 conversation search tool (`conversation_search`)

**Note on `end_memory`**: Ending a memory (marking it as no longer valid without deleting it) is done via the `MemoryStore.end_memory()` API method. There is no dedicated tool exposed to agents for this -- agents should use `forget` to remove obsolete memories or manage temporal validity through the knowledge graph's `kg_invalidate` instead.
