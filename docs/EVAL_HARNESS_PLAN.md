# Eval Harness — Implementation Plan

Status: **planned, not implemented**. Written 2026-07-31 after comparing PawFlow
to `langchain-ai/deepagents`, whose `libs/evals` package is the one axis where
they are not ahead of us but in a different category. Revised 2026-08-18 after
reviewing [`llm-as-a-verifier`](https://github.com/llm-as-a-verifier/llm-as-a-verifier)
and its [paper](https://arxiv.org/abs/2607.05391). The revision retains
mechanical assertions as the source of truth and adds an optional probabilistic
verification layer, isolated Best-of-N experiments, and progress scoring.

## Problem

We have 6847 unit tests. They prove the code does not regress. They prove
nothing about whether the agent is *good*.

Every prompt change, every context-policy change, every model default is
currently an opinion. When `COMMON_AGENT_SYSTEM_PROMPT` gains a rule, nobody
can say whether task success went up or down. When a provider's cold-start
serialization changes — the exact work of beta.59 to beta.61 — we know the
process receives the transcript; we do not know the agent completes more tasks
because of it. When someone asks "is Haiku good enough for the skillCurator
role?", the answer is a guess.

This is the gap that matters: without a score, prompt and policy work is
unfalsifiable.

A binary rubric judge alone is not enough. It discards uncertainty, is sensitive
to prompt-slot ordering, and cannot tell whether a small score change reflects a
real harness improvement or sampling noise. The harness therefore needs both an
objective outcome layer and, where the selected provider exposes token-level
log probabilities, a fine-grained probabilistic layer.

## Non-goals

- **Not a benchmark suite for models.** We are not ranking Opus against GPT.
  We measure *PawFlow's harness* on a fixed model, and a fixed model across
  harness changes. Model comparison is a side effect, not the purpose.
- **Not SWE-bench.** Big public benchmarks measure a coding agent's ceiling.
  We need regression signal on the behaviours we actually ship: tool routing,
  context survival, multi-agent handoff, memory recall, cold-start integrity.
- **Not in the CI gate initially.** Evals cost money and are non-deterministic.
  They run on demand and on a schedule, and they report; they do not block a
  merge until the variance is known.
- **Not a new dependency in PawFlow core.** `llm-verifier` is a research-quality
  reference implementation, not a runtime dependency. PawFlow will implement a
  small provider-neutral scorer behind the eval boundary. Any copied code must
  retain its MIT attribution.
- **Not an LLM-only completion oracle.** A verifier score never overrides a
  failed mechanical assertion and never auto-approves `verify_task`.
- **Not automatic early stopping.** Online progress scores run in shadow mode
  until their false-stop rate is measured on PawFlow cases.
- **Not shared-state Best-of-N.** Candidate agents must never race against the
  same mutable relay workspace. Each candidate gets an isolated fixture.

## Architecture

The harness is a **flow**, not a new runtime. PawFlow already knows how to run
a task list against agents — that is the product. An eval is:

```
  eval case (yaml) ─→ isolated run(s) ─→ AgentLoopTask turn(s) ─→ mechanical oracle
                       (N=1 or N>1)      (real provider)          │
                                                                  ├─→ rubric baseline
                                                                  ├─→ probabilistic verifier
                                                                  └─→ result row (jsonl)
```

`AgentLoopTask` is directly instantiable (`tasks/ai/agent_loop.py`, already
constructed bare in `tests/test_goal_task_assignment.py:34` and
`tests/test_agent_loop.py:583`), so a case runs in-process with no server.

### Layout

```
evals/
  cases/                     # one yaml per case, grouped by suite
    tool_routing/*.yaml
    context/*.yaml
    multi_agent/*.yaml
    memory/*.yaml
    cold_start/*.yaml
  scorers/                   # mechanical, rubric and probabilistic scorers
    mechanical.py
    rubric.py                 # discrete structured-output baseline
    probabilistic.py          # optional token-logprob expectation
    tournament.py             # isolated Best-of-N ranking
  criteria/                  # versioned, reusable criterion definitions
  backends/                  # verifier capability adapters, not provider SDKs
  runner.py                  # case -> run -> score -> row
  report.py                  # rows -> scorecard (markdown + svg)
  cache/                     # content-addressed verifier results, gitignored
  results/                   # jsonl, one file per run, gitignored
```

Top-level `evals/`, not `tests/evals/`: pytest must never collect these, and
the directory is a deliverable of its own (a user can add cases for their own
agents).

### Case format

```yaml
id: tool_routing/reads_through_relay
suite: tool_routing
agent:
  persona: default          # or a named agent from config/agents.json
  service: ${EVAL_SERVICE}  # resolved at run time, never hardcoded
fixture:
  relay: tmp_workspace      # seeded directory copied into a scratch relay
  files:
    src/app.py: |
      def add(a, b): return a - b
turns:
  - user: "Fix the bug in src/app.py."
assert:
  - kind: file_contains
    path: src/app.py
    value: "a + b"
  - kind: tool_used
    any_of: [edit, apply_patch, batch_edit]
  - kind: tool_not_used
    none_of: [bash]              # a write must not go through a shell heredoc
  - kind: turns_max
    value: 6
score:
  - kind: probabilistic_rubric
    criteria:
      - id: task_correctness
        description: "Does the final workspace satisfy the requested behavior?"
      - id: empirical_verification
        description: "Did observed commands verify the requested behavior?"
```

Rules for a case: **deterministic assertions first**. A rubric judge is
allowed (`kind: rubric`) but every case must carry at least one mechanical
assertion, so a run that scores 0 tells you *what* broke without reading a
transcript.

The case owns the criterion identifiers and descriptions, but not a verifier
model or credentials. The run profile selects the generator service, verifier
service, model, repetitions, tournament size, and budget. This keeps the same
case comparable across providers and prevents secrets or deployment details
from entering committed fixtures.

### Scorers

| kind | mechanism | cost |
|---|---|---|
| `file_contains` / `file_absent` | read the scratch relay | free |
| `tool_used` / `tool_not_used` | tool-call ledger from the turn | free |
| `turns_max` / `tokens_max` | loop counters, usage ledger | free |
| `answer_contains` / `answer_matches` | final assistant text | free |
| `context_survives` | assert a fact planted in turn 1 is recalled in turn N | free |
| `rubric` | a judge model emits one structured discrete verdict | one call |
| `probabilistic_rubric` | expected value over ordered score-token logprobs | C × K calls per comparison |
| `best_of_n` | pairwise probabilistic tournament over isolated candidates | O(Nk) comparisons |
| `progress` | offline or shadow-online scores over trajectory prefixes | K calls offline; K per update online |

The usage ledger already exists (`docs/usage_tracking.md`), so cost per case
comes for free and belongs in the scorecard: a harness change that raises
success by 2 points and cost by 60% is a regression.

### Scoring precedence

Every run produces separate fields; it never collapses all evidence into one
opaque score:

1. **Mechanical outcome** — pass/fail per assertion and overall case pass.
   This is the oracle wherever the case can express one.
2. **Discrete rubric baseline** — the existing one-call judge, retained so the
   probabilistic method must prove that its extra cost buys a better signal.
3. **Probabilistic criterion scores** — continuous values in `[0, 1]`, one per
   criterion plus an explicitly defined aggregate.
4. **Selection outcome** — for Best-of-N only: selected candidate, complete
   ranking, comparison graph, and whether the mechanical oracle says the
   selected candidate passed.

A failed mechanical assertion always remains visible even if an LLM assigns a
high score. A missing or invalid verifier result is `invalid`, never `0.5`,
`pass`, or an implicit tie.

### Probabilistic verifier

The initial method follows the useful ideas from LLM-as-a-Verifier without
copying its provider/client layer:

- use an ordered 20-token scale with single-token values for the selected
  tokenizer;
- compute the expectation over the returned score-token distribution instead
  of reducing it to the sampled token;
- evaluate one narrow criterion at a time;
- repeat each criterion `K` times and retain every raw repetition;
- alternate candidate A/B prompt slots for pairwise comparisons;
- report the mean, standard deviation, sample count, and raw valid mass rather
  than only the mean;
- version the prompt template, score scale, and aggregation formula.

The scorer must distinguish the probability mass visible in the provider's
top-K response from the full model distribution. If required score tokens are
missing, it reports `invalid_distribution`; it must not renormalize an
arbitrarily tiny visible subset and present it as full confidence.

### Provider capability boundary

The scorer requests a normalized capability rather than importing provider
SDKs directly:

```python
class VerifierBackend(Protocol):
    async def score_tokens(
        self,
        *,
        prompt: str,
        allowed_tokens: list[str],
        images: list[ResolvedMedia],
        request_context: EvalRequestContext,
    ) -> TokenDistribution: ...
```

An LLM service advertises `token_logprobs` only when it can return the ordered
token alternatives and log probabilities needed by the scorer. Subscription
CLI providers and APIs without this capability are explicitly unsupported for
`probabilistic_rubric`; users may select the separate `rubric` baseline instead.
There is no silent fallback that pretends a sampled letter is a probability
distribution.

Verifier requests remain async, pass through PawFlow's normal service
resolution, rate limits, budgets, cancellation, usage ledger, and user scope,
and use bounded concurrency. The reference implementation's process-global
usage counter, environment-driven client selection, synchronous thread pool,
and provider-specific defaults are not adopted.

### Criteria decomposition

Criteria are small, orthogonal, stable, and versioned. A code-edit case should
normally separate at least:

- task correctness;
- root-cause alignment;
- contract and regression safety;
- empirical verification;
- PawFlow policy compliance when relevant.

Criteria must not restate the expected answer or expose hidden mechanical
assertions. Development cases may inform criterion authoring, but final
calibration and selection accuracy are reported on held-out cases. Changing a
criterion description changes its content hash and invalidates its cache.

### Cache and reproducibility

Verifier cache keys are content-addressed. The canonical digest includes:

- scorer and prompt-template versions;
- verifier service, provider, model, and all sampling/logprob parameters;
- task text and ground-truth note;
- ordered criterion definition;
- complete candidate trajectory content and direction;
- resolved image content hashes;
- repetition number, tournament seed, and score-scale definition.

Cache records carry a UUID and creation timestamp. Writes are atomic. Errors,
timeouts, truncated distributions, and synthetic ties are never cached as valid
scores. Results record whether each value was fresh or cached. This avoids the
stale-cache collisions possible when a cache key contains only candidate
indices and criterion names.

### Isolated Best-of-N selection

Best-of-N is an eval mode, not an `AgentLoopTask` default. For each case the
runner creates `N` equivalent but isolated conversations and fixtures, records
their complete trajectories, then scores only after every candidate has
finished or reached a declared limit.

For small `N`, the reference implementation uses a seeded Hamiltonian ring,
selects `k` pivots, compares every non-pivot with the pivots, and aggregates
soft Bradley-Terry wins. PawFlow will implement both full round-robin and pivot
tournament strategies so the approximation can be measured against the exact
ranking on small experiments. Every result stores the seed and directed
comparison graph.

The decisive metric is not agreement with another LLM. It is **selection
accuracy against the mechanical oracle**: how often the strategy chooses a
candidate that actually passes. Public benchmark uplift is context, not an
acceptance criterion for PawFlow.

### Progress scoring

Offline progress scoring evaluates selected trajectory prefixes after the run.
It must not expose later steps to earlier checkpoints. Online scoring feeds
only the prefix observed so far and initially records its curve in shadow mode.

The runner records monotonicity, regressions, plateau length, false-low scores
on eventual successes, and false-high scores on eventual failures. No score may
stop, resample, approve, reject, or mutate a production agent until a separate
policy is proposed from measured PawFlow data. Any later stopping policy must
require a minimum number of steps, consecutive low-confidence observations,
and compatible mechanical evidence.

## Suites — what we actually need to measure

These are chosen from the classes of bug this repository has actually shipped,
not from a generic list.

1. **`cold_start`** — the beta.59-to-beta.61 class. Kill the CLI process
   mid-conversation, force a relaunch, and assert the agent still knows a fact
   stated three turns earlier. This is the end-to-end coverage that the
   AGENT_SYSTEM "two cases, no third one" rule currently lacks, and it is the
   one manual test still open at the end of beta.61.
2. **`tool_routing`** — does the agent use the right tool? `read` not `cat`,
   `apply_patch` over three `edit` calls, `run_tests` not `bash pytest`,
   MCP-only when the CLI prompt says MCP-only. Every one of these is a written
   rule in `agent_prompt_policy.py` with no evidence it is obeyed.
3. **`context`** — compaction and gauge. Plant facts, drive the conversation
   past the auto-compact threshold, assert survival. Also asserts the gauge
   reported afterwards is not lying.
4. **`multi_agent`** — delegate a subtask, assert the result comes back and
   the delegating agent uses it; assert `flash_delegate` is used for a
   parallelisable subtask and not for a coupled edit.
5. **`memory`** — store a preference in conversation A, assert recall in
   conversation B. The cognitive layer is our differentiator and is entirely
   unmeasured.

Start with 4 cases per suite. Twenty cases that run in ten minutes beat two
hundred that nobody runs.

## Isolation

Each case gets:

- a fresh conversation id and a temp `ConversationStore` root,
- a scratch relay directory seeded from `fixture.files`,
- a fresh live-session registry (the `_clear_cc_live_registry` autouse fixture
  in `conftest.py:55` already does this for tests; the runner does it
  explicitly),
- its own credential slot when the provider is a CLI pool.

Each Best-of-N candidate additionally gets its own relay fixture or filesystem
snapshot. Candidate runs cannot share writable state, browser profiles,
memory/KG scopes, pending tools, or provider sessions. Equivalent fixture hashes
are recorded before execution.

No case may see another case's store, memory, or KG. A run is reproducible in
shape even when the model is not reproducible in output.

Verifier inputs are treated as a new data egress surface. Before a trajectory
is sent to a verifier service, the runner applies PawFlow's normal secret
redaction and records the destination service. Images are resolved only through
the relay/FileStore permission boundary, with byte, count, and MIME limits; the
scorer does not fetch arbitrary URLs itself. Raw reasoning and trajectories are
stored only in the scoped eval result directory and never copied into the
global usage ledger.

## Reporting

`report.py` turns `results/*.jsonl` into:

- `evals/SCORECARD.md` — suites x models, pass rate, mean turns, mean cost,
  committed on every scheduled run so the diff *is* the history;
- a per-run failure list with the transcript path for each failed case.
- probabilistic calibration: Brier score, expected calibration error, valid
  distribution rate, mean/std per criterion, and confidence intervals;
- Best-of-N: Pass@1, selected-pass rate, oracle upper bound, selection regret,
  rank correlation against mechanical outcomes, calls, tokens, latency, and
  cost;
- progress: false-low/false-high rates and score curves split by final outcome;
- operational quality: verifier error/timeout rate, cache hit rate, and
  unsupported-capability count.

No dashboards, no external service. The scorecard is a file in git.

Scores are comparable only within the same scorer version, criterion version,
verifier model, and run profile. The report refuses to merge incompatible rows
into one trend line. Repeated trials report bootstrap confidence intervals;
single-run point estimates are labelled exploratory.

## Phasing

**E1 — runner and one suite (smallest useful thing).**
`evals/runner.py`, the yaml loader, the free scorers, and the four
`tool_routing` cases. Run manually: `python -m evals.runner --suite
tool_routing --service <id>`. Deliverable: a pass rate that a human can
disbelieve and check by hand.

**E2 — the remaining four suites.** Includes the `cold_start` suite, which
needs a way to kill a provider container mid-run; that helper is worth having
on its own and closes the last manual test from beta.61.

**E3 — scorecard and scheduling.** `report.py`, `evals/SCORECARD.md`, and a
nightly GitHub Actions workflow (separate file, never in `ci.yml`) with the
model matrix behind a repository secret. Reports as a job summary; does not
fail the build.

**E4 — discrete rubric baseline.** Only after the mechanical suites are stable,
because a judge model adds variance to a measurement whose variance we do not
yet know. Establish one-call structured verdict accuracy and cost before adding
the more expensive probabilistic scorer.

**E5 — probabilistic verifier pilot.** Add the `VerifierBackend` contract, one
logprob-capable backend adapter, decomposed criteria, content-addressed cache,
and calibration report. Run it beside the discrete baseline; it does not affect
case pass/fail. Exit only when invalid-distribution/error behavior is explicit
and the scorer is better calibrated or selects successful runs more reliably
than the baseline at a reported cost.

**E6 — isolated Best-of-N laboratory.** Generate three candidates for a small
held-out subset, compare full round-robin with the seeded pivot tournament, and
measure selected-pass uplift over Pass@1. Do not expose this as a production
agent-loop option until isolation, cancellation, budgets, and side-effect
boundaries have their own tests.

**E7 — progress shadow mode.** Add offline curves first, then optional online
prefix-only scoring that records but never controls the agent. Collect enough
successful and failed trajectories to quantify false-stop risk before proposing
any policy.

**E8 — gate.** Once at least three weeks of nightly runs establish the noise
floor, promote a deterministic subset to a required check on PRs that touch
`core/agent_prompt_policy.py`, `tasks/ai/_agentctx_*`, `tasks/ai/_alc_*`, or
`core/llm_providers/`. Probabilistic metrics remain advisory until their model
version is pinned and their confidence interval shows a regression beyond the
measured noise floor.

## Tests for the harness itself

The harness is code, so it follows the same rule as everything else here:

- `tests/test_eval_runner.py` — a case runs end to end against a stub provider
  that replays a canned tool sequence; every scorer kind has a passing and a
  failing fixture.
- `tests/test_eval_cases_valid.py` — every yaml under `evals/cases/` parses,
  has a unique id, names a real suite, and carries at least one mechanical
  assertion. This is the test that stops the suite from rotting.
- `tests/test_eval_probabilistic_scorer.py` — exact expectations from synthetic
  token distributions; missing token mass, fused tokens, malformed tags,
  unsupported providers, cancellation, and timeouts are invalid rather than
  neutral successes.
- `tests/test_eval_cache.py` — every input dimension changes the digest; writes
  are atomic; errors are not persisted; identical requests reuse the cache.
- `tests/test_eval_tournament.py` — slot balance, deterministic seeds,
  comparison counts, tie-breaking, round-robin equivalence fixtures, and no
  cross-candidate state sharing.
- `tests/test_eval_progress.py` — prefix-only visibility, regressions, missing
  checkpoints, multimodal limits, and shadow mode's no-control invariant.
- `tests/test_eval_security.py` — secret redaction, relay/FileStore-only media,
  scoped artifacts, permission failures, and verifier egress attribution.

## Open questions

- Which service generates candidates, and which distinct service verifies
  them? Self-verification and cross-model verification must be separate report
  dimensions rather than silently mixed.
- Which available PawFlow providers can expose trustworthy token-level
  logprobs and constrained single-token output? This is discovered through
  capabilities, not a hardcoded provider list.
- How many repetitions and pivots maximize selected-pass rate per dollar on
  PawFlow cases? Answer in E5/E6 with cost curves, not defaults copied from a
  public benchmark.
- Which score scale is single-token across each supported tokenizer? Validate
  the tokenization at backend startup and fail closed when it changes.
- Variance per case: how many trials before a pass rate means anything? To be
  answered empirically in E3 and revisited for probabilistic scoring in E5.
- When does a verifier see source files or only the recorded trajectory? The
  initial pilot uses the trajectory plus mechanically collected artifacts;
  broader tool access would create a second agent run and is out of scope.

## Adoption decision

PawFlow adopts the method, not the package architecture:

- **adopt:** expected score-token distributions, repeated narrow criteria,
  pairwise slot balancing, pivot tournaments, and prefix-only progress curves;
- **adapt:** provider access, async concurrency, cost accounting, isolation,
  media routing, result persistence, cache identity, and error semantics to
  PawFlow invariants;
- **reject:** a core dependency on `llm-verifier`, process-global counters,
  environment-selected clients, unbounded/synchronous fan-out, arbitrary URL
  image loading, stale index-based cache keys, neutral scores on errors, and
  automatic completion or stopping based only on an LLM score.

Implementation begins with E1. The probabilistic work must not delay the small,
mechanical twenty-case harness that first makes PawFlow behavior measurable.
