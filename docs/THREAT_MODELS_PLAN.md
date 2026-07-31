# Threat Models — Implementation Plan

Status: **planned, not implemented**. Written 2026-07-31, from the comparison
with `langchain-ai/deepagents`, which carries a `THREAT_MODEL.md` per package
(`libs/deepagents`, `libs/cli`, `libs/code`).

## Problem

We have the defences and not the documents.

`SECURITY.md` (148 lines) is a *policy*: reporting channel, layered defences,
production checklist. `docs/security_model.md` (116 lines) is a *feature map*:
trust boundaries, relay modes, permission modes, secrets, encryption at rest.
Both answer "what did we build". Neither answers the question a reviewer,
an auditor, or a self-hoster actually asks:

> For this surface: who is the attacker, what do they control, what are they
> trying to reach, what stops them, and what does NOT stop them?

The last clause is the one that is missing everywhere. We have no written
statement of residual risk, so every deployment decision — expose the gateway?
allow `local=true`? install a third-party `.pfp`? — is made without a stated
boundary, and every new feature negotiates its own security story from scratch.

This is a documentation gap, not a code gap. It is also the cheapest of the
three plans, and the only one that produces value on day one.

## Non-goals

- **Not a rewrite of `SECURITY.md`.** That file stays: policy, reporting,
  checklist. Threat models link to it and are linked from it.
- **Not a pentest report.** We describe the designed boundary and the known
  residual risk. Findings from actual testing belong in advisories.
- **Not aspirational.** A threat model documents what the code does today. If
  a mitigation is planned and not shipped, it is listed under residual risk,
  named as planned. A document that describes intentions is worse than none.

## Surfaces

One document per surface where an attacker's input meets our execution. Seven,
chosen by asking where untrusted bytes cross a boundary:

| # | Surface | Attacker's foothold | Crown jewels behind it |
|---|---|---|---|
| 1 | **Relay** | a compromised or hostile relay client; a malicious `local=true` request | the user's host filesystem, host shell, clipboard, screen |
| 2 | **MCP bridge / tool relay** | a CLI provider container; anything that can reach the internal HTTP listener | every PawFlow tool, i.e. every other surface |
| 3 | **Agent input** | web pages, files, tool output, other agents' messages | tool invocation on the user's behalf (prompt injection) |
| 4 | **Packages (.pfp)** | a package author, a registry, a compromised signing key | code execution inside the server process |
| 5 | **Auth and credentials** | a co-tenant user; a stolen cookie or API key; a rotated OAuth token | 9 OAuth providers' tokens, credential pool slots, other users' conversations |
| 6 | **Provider containers** | a model that has been steered; a hostile repository under `/workspace` | container escape, credential slot theft, cross-conversation leakage |
| 7 | **Channels** | anyone who can message the bot on Telegram/Discord/Slack/WhatsApp | identity binding, conversation takeover |

Surface 3 deserves emphasis: deepagents states plainly that it follows a
"trust the LLM" model and pushes enforcement to the tool layer. We do not —
we have `tool_approval`, `interrupt_policy`, `sandbox`, AppArmor, and the
`<tool_output>` data-not-instructions block appended in
`tasks/ai/_agentctx_p3.py`. That is a genuine differentiator and it is
currently invisible to anyone evaluating PawFlow.

## Format

One template, identical across the seven, because comparability is the point.
`docs/threat_models/TEMPLATE.md`:

```markdown
# Threat Model — <surface>

Scope: <what is in, what is explicitly out>
Code: <the modules that implement the boundary>

## Assets
What an attacker wants here, ranked.

## Actors and trust
Who touches this surface and what each is trusted with. Anyone not listed is
untrusted by default.

## Entry points
Every way bytes get in. Table: entry, authentication, authorisation, limits.

## Threats
One row per threat: what the attacker does, what breaks, what stops it,
and the test or code that proves the mitigation exists.

## Residual risk
What is NOT stopped, stated plainly, with the deployment choice that changes
it. This section is mandatory and must never be empty.

## Deployment guidance
The settings a self-hoster must get right for this surface.
```

The "proof" column is the rule that keeps these documents honest: a mitigation
with no test and no code reference is a claim, and claims do not ship. This
mirrors the sentence already at the top of `SECURITY.md` — *"Every claim here
is backed by code... If you find a divergence, the code wins."*

## Phasing

**T1 — template plus the two highest-value surfaces.** Relay and MCP bridge.
These are the two where a mistake reaches the user's own machine, and where we
already have the most code to point at (`core/handlers/_fs_base.py`,
`docs/relay_client.md`, `docs/http_listener.md`, the internal MCP token minted
per live session in `core/llm_providers/_cc_stream.py`).

**T2 — agent input and provider containers.** The prompt-injection boundary
and the container boundary. Write these together: they share an attacker.

**T3 — packages, auth, channels.** `PFP_PACKAGES.md` already documents the
signing model, so surface 4 is largely extraction plus a residual-risk section.

**T4 — wiring.** Link every threat model from `SECURITY.md`, from
`docs/README.md`, and from the feature doc of each surface. Add the
residual-risk items that are deployment-dependent to the production checklist
in `SECURITY.md`.

## Keeping them true

A threat model that drifts is worse than none, so:

- `tests/test_threat_models_are_wired.py` — every file under
  `docs/threat_models/` has the template's mandatory sections, a non-empty
  residual-risk section, and is linked from `docs/README.md`. Cheap, and it is
  the check that survives the first six months.
- Every code reference in a threat model names a path that exists. Same test.
- `CLAUDE.md` already requires docs to be updated in the same change as the
  feature; add threat models to that rule for changes touching the seven
  surfaces.

## Cost

This is the one plan with no runtime code and no model spend. Seven documents,
the template, and one test. It is also the plan most likely to surface a real
bug: writing "what does NOT stop this" for the MCP bridge and for `local=true`
is exactly the exercise that finds the gap nobody looked at.
