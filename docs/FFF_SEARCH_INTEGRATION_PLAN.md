# FFF Search Integration Plan

## Status

Proposed. PawFlow now has a separate `ripgrep` fast path for relay-backed
content search; this plan covers the additional indexed and agent-oriented
capabilities from [dmtrKovalenko/fff](https://github.com/dmtrKovalenko/fff).

## Decision

FFF should be an optional relay-side search backend, not a replacement MCP
server exposed directly to agents and not a server-side filesystem index.
PawFlow must continue to own relay selection, `local` routing, path
authorization, output limits, and response formatting.

The integration is worthwhile for long-lived coding sessions and large
repositories because it adds persistent indexing, typo-tolerant path and
content search, multi-pattern search, definition ranking, Git-state boosts,
and frecency. It is not required merely to make regex search fast: the native
`ripgrep` path handles that lower-risk case.

## Baseline

A local benchmark on the PawFlow repository used five representative queries
over 1,849 indexed files:

| Backend | Median latency |
|---|---:|
| Existing Python relay scanner | 2,332 ms |
| Stateless `ripgrep` | 31 ms |
| FFF first query after indexing | 5 ms |
| FFF warm query | 1.07 ms |

These figures establish an order of magnitude, not a release gate. Production
benchmarks must include larger repositories, concurrent calls, file churn, and
both container and host relay surfaces.

## Goals

- Add typo-tolerant, frecency-ranked file discovery for agents.
- Add one-call OR search across naming variants.
- Accelerate compatible repeated content searches without changing PawFlow's
  public filesystem contracts.
- Keep indexes inside the relay process or a relay-owned child process.
- Bound memory, startup work, result size, and inactive index lifetime.
- Fail open to the existing `ripgrep` and Python implementations.

## Non-goals

- Do not let the PawFlow server read or index relay files.
- Do not bypass filesystem permissions or `local` authorization.
- Do not replace arbitrary regex or multiline behavior until parity is proven.
- Do not make FFF mandatory for standalone or host-helper relays.
- Do not expose FFF's native output format through existing PawFlow tools.

## Architecture

Each relay surface owns an index manager keyed by canonical project root:

```text
PawFlow tool handler
  -> relay and path authorization
  -> search backend router
       -> FFF index for supported operations and a ready index
       -> ripgrep for general regex and cold/unavailable indexes
       -> Python scanner as the final compatibility fallback
  -> PawFlow response normalization
```

Container and `local=true` host surfaces must never share an index. The cache
key is `(surface, canonical_root)`. Each entry records last access, scan state,
watcher state, memory budget, and failure cooldown. An LRU policy closes
inactive indexes before creating new ones.

## Public Tool Shape

1. Add `find_files` for fuzzy path discovery. It must remain distinct from
   `glob`, whose contract is deterministic glob matching.
2. Add `patterns` OR support to `search`, or a narrowly scoped `multi_grep`
   tool if provider compatibility requires a separate schema.
3. Route literal, single-line `grep` and `search` requests through FFF only
   after response-parity tests pass.
4. Keep regex, multiline, multiple-root, unsupported encoding, and out-of-root
   requests on `ripgrep` or the Python fallback initially.

All results are normalized to PawFlow paths, line numbers, context blocks,
pagination, and global limits. FFF cursors or scores remain internal unless a
new public field has a demonstrated agent benefit.

## Packaging

Evaluate both integration forms before implementation:

- `fff-search` Python bindings provide the cleanest in-process API, but the
  published Linux wheels currently target `manylinux_2_38` and do not cover
  every PawFlow host architecture.
- The standalone `fff-mcp` releases provide broader static binary coverage,
  including musl Linux builds, but require a supervised stdio child and an
  internal protocol adapter.

Whichever form is selected must be version-pinned, checksum-verified, covered
by the relay release matrix, and absent from server-only images. Missing or
incompatible artifacts must leave native search fully functional.

## Delivery Phases

### Phase 1: Compatibility spike

- Build adapters for FFF file search, literal grep, and multi-grep.
- Compare paths, exclusions, case rules, Unicode, binary detection, symlinks,
  Git-ignored files, contexts, and limits against PawFlow behavior.
- Measure index time, warm/cold latency, resident memory, watcher churn, and
  shutdown time on small, medium, and large repositories.
- Decide between Python bindings and a supervised static binary.

Exit gate: no permission-boundary change, documented semantic differences,
and a packaging route for every supported relay platform.

### Phase 2: Relay index manager

- Implement lazy per-surface, per-root index creation.
- Add bounded readiness waits and immediate native fallback while warming.
- Add memory/file-count budgets, LRU eviction, failure cooldown, health state,
  and clean watcher shutdown.
- Ensure reconnects, child relays, workspace changes, and force stops cannot
  leak watchers or child processes.

Exit gate: lifecycle and concurrency tests pass with no retained process,
thread, descriptor, or index after relay shutdown.

### Phase 3: Agent-facing capabilities

- Ship `find_files`.
- Ship OR-pattern content search.
- Add definition and Git-state annotations only where they reduce follow-up
  reads without destabilizing output size.
- Feed successful file selections or reads into frecency through an explicit,
  privacy-scoped store.

Exit gate: agent evaluations show fewer search calls or lower search-token
usage without reducing task success.

### Phase 4: Configuration and rollout

- Add `search_backend = native | fff | auto`, defaulting to `native` during
  canary rollout.
- Expose bounded cache settings and health diagnostics through the relay
  configuration surface.
- Enable `auto` only for persistent project roots after the compatibility,
  memory, and agent-evaluation gates pass.
- Retain an immediate configuration rollback to native search.

## Required Tests

- Exact response parity for supported grep/search requests.
- Fallback on missing binary/module, invalid regex, scan timeout, watcher
  failure, index corruption, and memory eviction.
- Permission and path-escape tests before index access.
- Separate container and host-surface index tests.
- Concurrent query, mutation-during-query, reconnect, shutdown, and force-stop
  tests.
- Cross-platform packaging smoke tests for Linux x86_64/arm64, Windows x86_64,
  and macOS x86_64/arm64.
- Benchmarks for cold start, warm queries, memory, file churn, and repositories
  above the cache budget.

## Acceptance Criteria

FFF can become the `auto` backend only when:

- compatible warm searches are materially faster than stateless `ripgrep`;
- memory stays within configured limits under multiple roots;
- fallback adds no user-visible failure when FFF is unavailable;
- all PawFlow permission and relay-routing tests remain unchanged;
- agent evaluations demonstrate a measurable reduction in search round trips
  or consumed context.
