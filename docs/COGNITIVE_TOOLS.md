# Cognitive Tools

PawFlow provides four persistent cognitive systems that give agents long-term memory, structured knowledge, personal reflection, and codebase understanding. These systems work together to make agents context-aware across conversations, capable of tracking evolving facts, and able to reason about code structure.

**The four systems:**

| System | Purpose | Storage |
|---|---|---|
| **Memory** | Persistent facts, preferences, events | `data/memories/{user}.json` |
| **Knowledge Graph** | Entity-relationship triples with temporal validity | `data/knowledge_graphs/{user}.json` |
| **Agent Diary** | Per-agent journal of observations and decisions | `data/memories/{user}/diary_{agent}.jsonl` |
| **Project Graph** | AST-based code structure graph (17 languages) | `data/graphs/{user}/{conv_id}/graph.json` |

They are interconnected:

- **Memory digest** and **diary digest** are injected into the agent's system prompt at the start of every conversation turn.
- When a memory is stored, the system cross-checks the **knowledge graph** for contradictions and warns the agent.
- **Auto-extraction** triggers periodically pull facts from conversation text into both memory and KG.
- The **project graph** is built via the relay (user's machine) and queried server-side for code navigation.

---

## 1. Memory System

The memory system stores persistent facts per user. Memories survive across conversations and are scoped by visibility, organized by taxonomy, and support temporal validity.

### 1.1 Categories

Memories are classified by category:

| Field | Purpose | Values |
|---|---|---|
| **`category`** | Memory type | `facts`, `events`, `discoveries`, `preferences`, `advice` |

Categories are browsable via the `memory_navigate` tool.

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

2. **Post-compaction extraction** -- When a conversation is compacted (context window overflow), the compaction summary is fed to `auto_extract_memories()` which uses the LLM (or a heuristic fallback) to extract 3-5 key facts. Extracted memories are tagged `["auto-extracted", "compaction"]`.

The heuristic fallback scans for sentences containing indicators like "prefer", "decided", "chose", "using", "switched", "deadline", "must", "should", "always", "never", "important", "key", "role".

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

### 2.7 Hyperedges

Hyperedges detect group relationships where one entity has the same predicate pointing to 3 or more objects. For example:

```
> kg_hyperedges()
Group relationships:
  PawFlow -> depends_on -> [tree-sitter, FastAPI, SQLAlchemy, Pydantic] (4 objects)
  AuthGateway -> supports_provider -> [Google, GitHub, Microsoft, X, Facebook] (5 objects)
```

### 2.8 Surprises

The surprises algorithm scores triples for unexpectedness based on:
- **Cross-entity-type bonus** (+2): Subject and object have different entity types.
- **Low confidence bonus** (+2-3): INFERRED gets +2, AMBIGUOUS gets +3.
- **Peripheral-to-hub bonus** (+2): One end has <= 2 connections and the other has >= 5.

Higher scores indicate more surprising connections.

### 2.9 Community Detection

Uses label propagation (no external dependencies) to cluster strongly connected entities. Returns `{community_id: [entity_names]}` ordered by size. The text report includes cohesion scores (ratio of intra-community edges to total edges of community members).

---

## 3. Agent Diary

The diary is a per-agent personal journal that persists across conversations. Unlike memories (which store facts about the user/project), the diary stores the agent's own observations, decisions, and learnings.

### 3.1 Entry Types

| Type | When to use |
|---|---|
| `observation` | Something the agent noticed (default) |
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

### 3.3 Difference vs Memory

| Aspect | Memory | Diary |
|---|---|---|
| **About** | Facts about the user, project, world | Agent's own reflections |
| **Scope** | global / agent / conversation / private | Always per-agent |
| **Taxonomy** | Category | Entry type + tags |
| **Recall** | Searchable by query, tags, category | Read chronologically |
| **Storage** | JSON (one file per user) | JSONL (one file per agent per user) |
| **Digest** | Multi-tier (L0-L4) | Last 10 entries |

---

## 4. Project Graph

The project graph builds a structural code graph from a codebase using tree-sitter AST extraction. Files are fetched via the relay (running on the user's machine), and AST parsing runs server-side.

### 4.1 Build via Relay

The `build` action:

1. **Discovers code files** on the user's machine via `fs_service.search()` for each supported extension.
2. **Fetches file contents** via `fs_service.read_file()` (capped at 500 files).
3. **Writes to a server-side temp directory** recreating the directory structure.
4. **Runs tree-sitter AST extraction** via `core/graphify/extract.py`.
5. **Builds the graph** via `core/graphify/build.py` (nodes = code entities, edges = relationships).
6. **Cleans up** the temp directory.

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

## 5. Memory Navigate

The `memory_navigate` tool provides 3 actions to browse the memory taxonomy:

### 5.1 Actions

| Action | Description | Parameters |
|---|---|---|
| **`list_categories`** | List memory type categories with counts | -- |
| **`get_taxonomy`** | Full `{category: count}` overview | -- |
| **`graph_stats`** | Overall statistics: totals, category counts, ended count, category distribution | -- |

### 5.2 Examples

**Browsing categories:**

```
> memory_navigate(action="list_categories")
Categories (5):
- advice (4 memories)
- discoveries (7 memories)
- events (12 memories)
- facts (42 memories)
- preferences (15 memories)
```

**Getting stats:**

```
> memory_navigate(action="graph_stats")
Total memories: 80
Categories: 5
Ended (obsolete): 3
Active: 77
Category distribution:
  advice: 4
  discoveries: 7
  events: 12
  facts: 42
  preferences: 15
```

---

## 6. Auto-Triggers

### 6.1 Periodic Auto-Save (Every ~15 Messages)

The `_maybe_auto_save_memories` method runs after each agent response. It checks if 15 new user messages have accumulated since the last save. When triggered:

1. Loads the last 15 messages from the conversation store.
2. Concatenates user and assistant message text (first 200 chars each).
3. Uses the `summarizer_service` LLM to extract structured facts.
4. Stores extracted facts via `auto_extract_memories()` with tag `auto-extracted`.

### 6.2 Post-Compaction Extraction

When a conversation is compacted (context window overflow), `_auto_extract_memories` is called with the compaction summary. The extraction uses an LLM prompt asking for 3-5 key facts as JSON:

```json
[
  {"text": "User prefers JSON over SQLite for storage", "category": "preferences"},
  {"text": "Auth middleware rewrite driven by compliance", "category": "facts"}
]
```

If no LLM is available, a heuristic fallback scans for decision/preference indicator words.

### 6.3 Summarizer Service

Both auto-triggers use the `summarizer_service` -- a lightweight LLM configured for extraction tasks. It is resolved via `_get_summarizer_client()` using the user's service configuration.

---

## 7. System Prompt Injection

At every conversation turn, three cognitive blocks are appended to the agent's system prompt:

### 7.1 Persistent Memory Digest

Injected under `## Persistent memory`. Contains the multi-tier digest (L0-L4 + KG god nodes). Max 1200 characters. Only present if the user has stored memories.

### 7.2 Diary Digest

Injected under `## Your diary (past observations)`. Contains the last 10 diary entries (truncated). Max 600 characters. Only present if the agent has diary entries.

### 7.3 Cognitive Tools Hint

Always injected under `## Cognitive tools`. Tells the agent what tools are available:

```
You have persistent memory, knowledge graph, diary, and code analysis tools:
- Memory: remember to store facts (with category), recall to search, forget to delete,
  memory_navigate to browse categories
- Knowledge Graph: kg_add to store relationships (subject->predicate->object),
  kg_query to find facts about an entity, query_graph for BFS/DFS traversal,
  kg_god_nodes/kg_surprises/kg_communities for analysis
- Diary: diary_write for personal observations/decisions/learnings,
  diary_read to review past entries
- Project Graph: project_graph with action=build to index a codebase (AST,
  17 languages), then action=query/report/node to explore code structure
```

---

## 8. Storage Paths

All paths are relative to the PawFlow data directory:

| System | Path | Format |
|---|---|---|
| Memory store | `data/memories/{user_id}.json` | JSON array of MemoryEntry objects |
| Knowledge graph | `data/knowledge_graphs/{user_id}.json` | JSON with `entities` and `triples` |
| Agent diary | `data/memories/{user_id}/diary_{agent_name}.jsonl` | JSONL, one record per line |
| Project graph | `data/graphs/{user_id}/{conv_id}/graph.json` | JSON with `nodes`, `edges`, `metadata` |

All writes use the atomic tmp-then-replace pattern: write to `.tmp` file first, then `replace()` to the final path.

---

## 9. Tool Reference

Complete table of all 21 cognitive tools with their parameters:

### Memory Tools (6)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 1 | **`remember`** | `text` (string, required), `tags` (string[]), `scope` (enum: conversation/agent/global/private), `category` (enum: facts/events/discoveries/preferences/advice), `valid_from` (number) | Store a fact in persistent memory |
| 2 | **`recall`** | `query` (string), `tags` (string[]), `category` (enum: facts/events/discoveries/preferences/advice), `as_of` (number) | Search memories by text, tags, and category |
| 3 | **`semantic_recall`** | `query` (string, required), `limit` (integer), `category` (enum) | Search memories by meaning via vector embeddings |
| 4 | **`forget`** | `memory_id` (string, required) | Delete a specific memory by ID |
| 5 | **`check_duplicate`** | `text` (string, required), `category` (string) | Check if a similar memory already exists |
| 6 | **`memory_navigate`** | `action` (enum: list_categories/get_taxonomy/graph_stats, required) | Browse memory taxonomy structure |

### Knowledge Graph Tools (8)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 7 | **`kg_add`** | `subject` (string, required), `predicate` (string, required), `object` (string, required), `valid_from` (string), `confidence` (enum: EXTRACTED/INFERRED/AMBIGUOUS), `source` (string) | Add a fact triple with contradiction detection |
| 8 | **`kg_query`** | `entity` (string, required), `as_of` (string), `direction` (enum: outgoing/incoming/both) | Query all facts about an entity |
| 9 | **`kg_invalidate`** | `subject` (string, required), `predicate` (string, required), `object` (string, required), `ended` (string) | Mark a fact as no longer valid |
| 10 | **`kg_timeline`** | `entity` (string), `limit` (integer) | Chronological history of facts |
| 11 | **`kg_stats`** | _(none)_ | Summary statistics: entity count, triple count, relationship types |
| 12 | **`query_graph`** | `question` (string, required), `mode` (enum: bfs/dfs), `depth` (integer), `max_results` (integer) | BFS/DFS traversal from matching entities |
| 13 | **`kg_god_nodes`** | `limit` (integer) | Most connected entities ranked by degree |
| 14 | **`kg_surprises`** | `limit` (integer) | Surprising cross-domain connections ranked by score |
| 15 | **`kg_hyperedges`** | _(none)_ | Group relationships (3+ objects for same subject+predicate) |
| 16 | **`kg_communities`** | _(none)_ | Detect entity clusters via label propagation |

### Diary Tools (2)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 17 | **`diary_write`** | `entry` (string, required), `type` (enum: observation/decision/learning/reflection), `tags` (string[]) | Write a diary entry |
| 18 | **`diary_read`** | `limit` (integer), `type` (enum: observation/decision/learning/reflection) | Read recent diary entries (newest first) |

### Project Graph Tools (1, with 4 actions)

| # | Tool | Parameters | Description |
|---|---|---|---|
| 19 | **`project_graph`** | `action` (enum: build/query/report/node, required), `path` (string), `question` (string), `depth` (integer), `source` (string) | Build, query, or report on codebase structure |

**Action breakdown:**

| Action | Required params | What it does |
|---|---|---|
| `build` | `path` (default "."), `source` (optional relay name) | Fetch code via relay, run AST extraction, build graph |
| `query` | `question`, `depth` (default 3) | BFS traversal on the graph |
| `report` | _(none)_ | Summary with god nodes, stats, confidence breakdown |
| `node` | `question` (node label) | Details about a specific code entity |

### Summary: 21 Tools Total

- 6 memory tools (`remember`, `recall`, `semantic_recall`, `forget`, `check_duplicate`, `memory_navigate`)
- 10 knowledge graph tools (`kg_add`, `kg_query`, `kg_invalidate`, `kg_timeline`, `kg_stats`, `query_graph`, `kg_god_nodes`, `kg_surprises`, `kg_hyperedges`, `kg_communities`)
- 2 diary tools (`diary_write`, `diary_read`)
- 1 project graph tool with 4 actions (`project_graph`)

**Note on `end_memory`**: Ending a memory (marking it as no longer valid without deleting it) is done via the `MemoryStore.end_memory()` API method. There is no dedicated tool exposed to agents for this -- agents should use `forget` to remove obsolete memories or manage temporal validity through the knowledge graph's `kg_invalidate` instead.
