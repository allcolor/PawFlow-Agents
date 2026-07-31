# Eval Harness — Implementation Plan

Status: **planned, not implemented**. Written 2026-07-31 after comparing PawFlow
to `langchain-ai/deepagents`, whose `libs/evals` package is the one axis where
they are not ahead of us but in a different category.

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

## Architecture

The harness is a **flow**, not a new runtime. PawFlow already knows how to run
a task list against agents — that is the product. An eval is:

```
  eval case (yaml)  ─→  fresh conversation  ─→  AgentLoopTask turn(s)  ─→  scorer  ─→  result row
                        (isolated store)        (real provider)          (rubric)     (jsonl)
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
  scorers/                   # python, one function per scorer kind
  runner.py                  # case -> run -> score -> row
  report.py                  # rows -> scorecard (markdown + svg)
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
```

Rules for a case: **deterministic assertions first**. A rubric judge is
allowed (`kind: rubric`) but every case must carry at least one mechanical
assertion, so a run that scores 0 tells you *what* broke without reading a
transcript.

### Scorers

| kind | mechanism | cost |
|---|---|---|
| `file_contains` / `file_absent` | read the scratch relay | free |
| `tool_used` / `tool_not_used` | tool-call ledger from the turn | free |
| `turns_max` / `tokens_max` | loop counters, usage ledger | free |
| `answer_contains` / `answer_matches` | final assistant text | free |
| `context_survives` | assert a fact planted in turn 1 is recalled in turn N | free |
| `rubric` | a judge model scores against written criteria | one call |

The usage ledger already exists (`docs/usage_tracking.md`), so cost per case
comes for free and belongs in the scorecard: a harness change that raises
success by 2 points and cost by 60% is a regression.

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

No case may see another case's store, memory, or KG. A run is reproducible in
shape even when the model is not reproducible in output.

## Reporting

`report.py` turns `results/*.jsonl` into:

- `evals/SCORECARD.md` — suites x models, pass rate, mean turns, mean cost,
  committed on every scheduled run so the diff *is* the history;
- a per-run failure list with the transcript path for each failed case.

No dashboards, no external service. The scorecard is a file in git.

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

**E4 — rubric scorer.** Only after the mechanical suites are stable, because
a judge model adds variance to a measurement whose variance we do not yet
know.

**E5 — gate.** Once three weeks of nightly runs establish the noise floor,
promote a subset to a required check on PRs that touch
`core/agent_prompt_policy.py`, `tasks/ai/_agentctx_*`, `tasks/ai/_alc_*`, or
`core/llm_providers/`.

## Tests for the harness itself

The harness is code, so it follows the same rule as everything else here:

- `tests/test_eval_runner.py` — a case runs end to end against a stub provider
  that replays a canned tool sequence; every scorer kind has a passing and a
  failing fixture.
- `tests/test_eval_cases_valid.py` — every yaml under `evals/cases/` parses,
  has a unique id, names a real suite, and carries at least one mechanical
  assertion. This is the test that stops the suite from rotting.

## Open questions

- Which service do scheduled runs use? A subscription CLI provider is cheap
  but rate-limited and interactive; an API key is metered but predictable.
  Probably: API provider for the schedule, CLI providers for on-demand runs.
- Variance per case: how many trials before a pass rate means anything? To be
  answered empirically in E3, not guessed now.
