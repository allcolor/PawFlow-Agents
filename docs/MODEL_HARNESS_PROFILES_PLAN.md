# Model Harness Profiles — Implementation Plan

Status: **planned, not implemented**. Written 2026-07-31, from the comparison
with `langchain-ai/deepagents`, which ships one profile module per model
(`profiles/harness/_anthropic_opus_4_7.py`, `_anthropic_haiku_4_5.py`,
`_openai_codex.py`, `_nvidia_nemotron_3_ultra.py`) plus per-provider profiles.

## Problem

Every agent in PawFlow receives the same system prompt, the same tool surface
and the same loop limits regardless of which model is behind it.

`tasks/ai/_agentctx_p3.py:135` composes the prompt as:

```python
st.system_prompt = self._build_identity_block(...) + inject_common_agent_system_prompt(st.system_prompt)
```

and `core/agent_prompt_policy.py` holds exactly two constants:
`COMMON_AGENT_SYSTEM_PROMPT` and `CLI_MCP_SYSTEM_PROMPT`. `config/default_models.json`
maps a provider to a default model name — nothing more.

So:

- A small fast model gets the same 5-section "Agent Operating Principles" as a
  frontier model, competing for the same attention budget it does not have.
- A model that is bad at parallel tool calls is still told to batch
  independent calls.
- A model with a small context window gets the same compaction thresholds as
  one with ten times the room.
- A model whose tool-calling breaks past N tools still sees the full catalog.
- Every provider quirk we discover ends up as a global `if provider ==` branch
  or, worse, as another paragraph in the shared prompt that every other model
  now pays for.

The prompt is a shared resource with no owner. Each addition is free for the
author and taxed to every model.

## Non-goals

- **Not per-model forks of the agent loop.** A profile tunes inputs to the
  loop. If a model needs different control flow, that is a provider, not a
  profile.
- **Not a place for persona.** Persona is the user's (`config/agents.json`,
  ResourceStore, `{agent_name}.md`). A profile is about the *model's*
  handling characteristics, and must read identically for every persona.
- **Not automatic.** A profile is written by a human after observing a
  failure, ideally with an eval case attached (see `EVAL_HARNESS_PLAN.md`).

## Architecture

One new module, one resolution point, one merge rule.

```
core/model_profiles/
  __init__.py            # resolve_profile(provider, model) -> HarnessProfile
  _base.py               # the HarnessProfile dataclass + merge()
  provider/              # coarse: one per provider family
    _anthropic.py
    _openai.py
    _google.py
    _cli.py              # shared by claude-code / codex / gemini / antigravity / cci
  harness/               # fine: one per model, overrides its provider profile
    _claude_opus_5.py
    _claude_haiku_4_5.py
    _gpt_5_5.py
    _gemini_3_1_pro.py
```

### The profile object

```python
@dataclass(frozen=True)
class HarnessProfile:
    name: str                          # "anthropic/claude-haiku-4-5"
    prompt_prepend: str = ""           # before the common principles
    prompt_append: str = ""            # after them, before the security block
    drop_prompt_sections: tuple = ()   # named sections of COMMON_AGENT_SYSTEM_PROMPT
    excluded_tools: tuple = ()         # removed from the request, not from the registry
    max_tools: int | None = None       # catalog cap; over it, tools go behind get_tool_schema
    parallel_tool_calls: bool = True
    compact_at_ratio: float | None = None   # override the auto-compact trigger
    max_consecutive_tool_calls: int | None = None
    reasoning_effort: str | None = None
```

Every field is `None`/empty by default, and a `None` field means "inherit".
That is the whole merge rule: `provider_profile.merge(model_profile)`, model
wins field by field. No inheritance chains, no config DSL.

### Named prompt sections

`COMMON_AGENT_SYSTEM_PROMPT` becomes a dict of named sections
(`think_before_coding`, `simplicity_first`, `surgical_changes`,
`goal_driven`, `parallel_flash_agents`) rendered in a fixed order.
`drop_prompt_sections` then means something precise, and a profile that drops
`parallel_flash_agents` for a model without parallel tool calls stops lying to
it. The rendered default output is byte-identical to today's constant — that
is a test.

### Resolution point

One call site, where provider and model are already resolved —
`tasks/ai/_agentctx_p3.py:135`, immediately after `st._client_model_name` and
`st._client_provider_name` are read from the service:

```python
profile = resolve_profile(st._client_provider_name, st._client_model_name)
st.system_prompt = self._build_identity_block(...) + render_common_prompt(profile) + ...
st._profile = profile
```

The profile then travels on the turn state, the same way
`_context_is_delta` does since beta.60 — **never on the shared client**, which
is the singleton the whole beta.59 marker bug came from. Downstream consumers:

| field | consumed by |
|---|---|
| `excluded_tools`, `max_tools` | tool-definition build in `_alc_setup` |
| `parallel_tool_calls`, `reasoning_effort` | provider request build |
| `compact_at_ratio` | auto-compact trigger (`docs/AGENT_SYSTEM.md` §4) |
| `max_consecutive_tool_calls` | loop limit in `_alc_iteration` |

### Matching

`resolve_profile` matches on `(provider, model)` with, in order: exact model
id, longest registered prefix (`claude-haiku-4-5-20251001` -> `claude-haiku-4-5`),
provider profile, empty profile. Never a fuzzy or regex match: a wrong profile
is worse than none, and "no profile" must stay the ordinary, correct case.

## Phasing

**P1 — the seam, no behaviour change.** Dataclass, `resolve_profile` returning
an empty profile for everything, sections dict, and the call site in
`_agentctx_p3`. Test: rendered prompt is byte-identical to beta.61 for every
provider. This lands alone and is boring on purpose.

**P2 — provider profiles.** Move the two existing global branches into
profiles: `CLI_MCP_SYSTEM_PROMPT` becomes the `_cli` provider profile's
`prompt_append` (it is already applied per-provider by hand in
`append_cli_mcp_system_prompt`), and provider-specific text currently inlined
in providers moves with it. Still no per-model behaviour.

**P3 — the first real model profile.** One model where we have an observed
failure and, ideally, a failing eval case. Haiku-class is the likely first:
shorter prompt, `parallel_flash_agents` dropped, tighter
`max_consecutive_tool_calls`. Merge only if the eval suite moves in the right
direction — otherwise the profile is an opinion in a new place.

**P4 — tool surface.** `excluded_tools` and `max_tools`. This is the field
with real blast radius (a tool that vanishes is a capability that vanishes),
so it comes last and is logged loudly at request build: a missing tool must be
explainable from a log line, not from reading profile source.

**P5 — user override.** Expose profile fields on the LLM service config so a
self-hosted user running a local model can write a profile without forking.
Same schema, loaded after the built-ins, model-wins merge.

## Tests

- `tests/test_model_profiles.py` — merge semantics (`None` inherits, empty
  tuple is not `None`), resolution order, prefix matching, unknown model
  yields the empty profile.
- `tests/test_prompt_is_stable_without_a_profile.py` — the rendered common
  prompt for a model with no profile is byte-identical to the checked-in
  golden. This is the regression that protects every existing agent.
- `tests/test_profile_travels_on_the_turn.py` — the profile is on the turn
  state and never assigned to a shared client attribute. Enforced by source
  inspection, same technique as `test_cold_start_means_full_context.py`.
- One eval case per shipped model profile, named in the profile module
  docstring. A profile without evidence does not merge.

## Risk

The honest risk is that profiles become the place where per-model hacks
accumulate unmeasured — the same failure as the shared prompt, now sharded and
harder to see. Two rules hold it back: a profile module must cite the observed
failure in its docstring, and P3 onwards requires an eval case. This plan is
worth doing **after** `EVAL_HARNESS_PLAN.md` E1, not before.
