# Multi-LLM Aggregator

`llmAggregator` lets one agent consult several LLM advisors in parallel before a final LLM synthesizes their reports and answers or completes the requested work.

## Execution Model

For the first LLM call of each user turn, PawFlow:

1. sends the user request to every configured advisor concurrently;
2. lets each advisor inspect the active environment with its permitted tools and produce a detailed internal plan;
3. injects the successful advisor reports into the final LLM's context;
4. streams only the final LLM's answer and tool loop to the user.

Advisor reports are cached for the rest of that user turn, so tool-result iterations do not trigger another fan-out. Advisor traces and sub-conversations are silent, ephemeral, and removed after execution.

## Prerequisites

Create at least two enabled `llmConnection` services:

- one or more advisors, chosen for complementary analysis, review, or domain knowledge;
- one final connection, chosen for synthesis and execution.

The final connection cannot also be an advisor. Aggregators cannot reference another `llmAggregator` or themselves; every reference must point directly to an `llmConnection`.

## Configuration

Create an **LLM Aggregator Service** from the service resource panel and configure it with direct service IDs:

```json
{
  "type": "llmAggregator",
  "aggregator_llm_service": "llm_final",
  "advisor_llm_services": [
    "llm_architect",
    "llm_reviewer"
  ],
  "max_parallel_advisors": 2,
  "advisor_max_iterations": 20,
  "failure_policy": "best_effort",
  "enforce_read_only": true
}
```

| Parameter | Default | Purpose |
|---|---:|---|
| `aggregator_llm_service` | required | Direct `llmConnection` that produces the visible response and runs the final tool loop. |
| `advisor_llm_services` | required | JSON array containing at least one direct `llmConnection` service ID. |
| `max_parallel_advisors` | `4` | Maximum number of advisors running concurrently. |
| `advisor_max_iterations` | `20` | Maximum tool-loop iterations available to each advisor. |
| `failure_policy` | `best_effort` | `best_effort` uses successful reports; `fail_fast` cancels the final call if any advisor fails. |
| `enforce_read_only` | `true` | Restricts advisors to PawFlow's fail-closed read-only tool allowlist. |

Select the new aggregator anywhere an agent or conversation accepts an LLM service. No agent prompt change is required.

## Read-Only and Tool Boundaries

Keep `enforce_read_only` enabled for normal advisor use. PawFlow exposes only tools classified as read-only and applies the same internal permission mode to CLI-backed providers through their ephemeral MCP context. Advisors cannot edit files, mutate external state, ask the user, send notifications, commit, push, or deploy.

Setting `enforce_read_only` to `false` is an explicit trust decision: advisors retain the behavioral instruction not to make changes, but all tools configured for the active conversation become available. The final LLM is unaffected by this setting and continues to use the conversation's normal tool approvals.

## Failure, Cost, and Latency

- Use `best_effort` when partial independent advice is still useful. Advisor failures are included in internal synthesis context and successful reports continue.
- Use `fail_fast` when every review is mandatory. Any advisor failure prevents the final LLM call.
- Parallel fan-out limits wall-clock latency to roughly the slowest advisor rather than the sum of all advisors, subject to `max_parallel_advisors`.
- Every advisor call consumes provider tokens. Advisor usage and cost are tracked separately from the final LLM, and advisor tokens do not inflate the main conversation context gauge.
- Pricing for the visible final turn comes from the configured `aggregator_llm_service`.

## Verification

After selecting the aggregator for a test conversation:

1. send a request that benefits from architecture and review perspectives;
2. confirm only one visible response stream appears;
3. confirm the final answer reflects multiple advisor perspectives;
4. request a tool-backed implementation and verify only the final LLM performs mutations;
5. test an unavailable advisor under `best_effort`, then under `fail_fast`, to confirm the intended policy;
6. review usage accounting to distinguish advisor cost from the final turn.

See also the [service catalog](services.md#llm-aggregator), [technical service reference](02_REFERENCE_TASKS_SERVICES.md#125-llm-aggregator-llmaggregator), and [website how-to](https://pawflow.allcolor.org/howtos.html#multi-llm-aggregator).
