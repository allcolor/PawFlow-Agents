# Adaptive LLM Router and OmniRoute Provider Implementation Plan

Status: **in implementation**. WP0 characterization and the WP1 OmniRoute
provider are implemented; WP2-WP8 remain in progress. WP9 still requires
production telemetry from deterministic policies before implementation.

Validated against the PawFlow source tree and OmniRoute
<code>release/v3.8.50</code>. WP1 pins the verified upstream wire contract to
commit <code>c6c134300bd9d1c7a54448de1e5d5009b7143f3f</code>.

## 1. Outcome

PawFlow should implement two independent capabilities:

1. an <code>omniroute</code> provider profile inside
   <code>llmConnection</code>, using OmniRoute's OpenAI-compatible endpoint;
2. a native <code>llmRouter</code> composite service that replaces
   <code>llmFailover</code> and selects among PawFlow
   <code>llmConnection</code> services.

The two capabilities solve different problems:

- the OmniRoute provider delegates provider/model selection to an external
  gateway;
- the native router keeps provider/model selection inside PawFlow and preserves
  PawFlow's provider-native sessions, credential scopes, cold handoff, usage
  accounting, and operational controls.

Neither capability creates a new LLM transport runtime. Physical calls remain
owned by <code>LLMConnectionService</code> and <code>LLMClient</code>. The
router is a composite policy and continuity layer.

The first deliverable should be the OmniRoute provider because it is small,
useful on its own, and validates gateway metadata handling. The native router
can then be implemented without coupling its state model to OmniRoute.

## 2. Decision summary

The target service split is:

~~~text
llmConnection
  owns: protocol, authentication, credentials, physical client, provider session

llmRouter
  owns: candidate selection, ordered fallback, turn affinity, health policy

llmAggregator
  owns: parallel advisors and final synthesis

omniroute provider
  is: one llmConnection dialect backed by an external OmniRoute gateway
~~~

The following decisions are binding for the first implementation:

1. Rename and replace <code>llmFailover</code> with
   <code>llmRouter</code> through a one-shot migration.
2. Preserve the current cold-start handoff instead of replaying an agent turn.
3. Select one initial candidate at the beginning of a logical agent turn.
4. Keep that candidate for every LLM/tool iteration in the turn while it works.
5. Reorder or rotate only between turns or after a classified provider failure.
6. Never treat a credential as busy merely because another call uses it.
7. Never reject a request with an all-credentials-busy condition.
8. A pool containing one credential remains valid for unlimited concurrent
   logical sessions; provider-side limits are represented as observed cooldowns,
   not local ownership locks.
9. Keep OmniRoute's internal routing opaque to PawFlow. PawFlow records the
   gateway and its reported metadata but does not mirror OmniRoute's internal
   candidate state.
10. Do not enable PawFlow adaptive routing over an OmniRoute adaptive route by
    default. One layer must own primary routing.
11. Keep the first native policy set small and deterministic.
12. Do not copy OmniRoute's large fallback implementation wholesale.

## 3. Source basis

### 3.1 PawFlow implementation inspected

The current design is spread across these live files:

- <code>services/llm_connection.py</code>;
- <code>services/llm_failover.py</code>;
- <code>services/llm_credential_oauth.py</code>;
- <code>core/llm_client.py</code>;
- <code>core/_llm_client_driver.py</code>;
- <code>core/llm_providers/openai.py</code>;
- <code>tasks/ai/agent_context.py</code>;
- <code>tasks/ai/_agentctx_p1.py</code>;
- <code>tasks/ai/_alc_llm_turn.py</code>;
- <code>tasks/ai/agent_core.py</code>;
- <code>core/usage_ledger.py</code>;
- <code>core/conv_agent_config.py</code>;
- <code>tasks/ai/actions/_sf_k1.py</code>;
- <code>tasks/io/chat_ui/resources_service_dialogs.js</code>;
- <code>tests/test_llm_failover.py</code>.

### 3.2 OmniRoute implementation inspected

The useful design references are:

- <code>open-sse/services/autoCombo/scoring.ts</code>;
- <code>open-sse/services/autoCombo/selfHealing.ts</code>;
- <code>open-sse/services/combo/rrState.ts</code>;
- <code>open-sse/services/combo/failureTracker.ts</code>;
- <code>open-sse/services/combo/sessionStickiness.ts</code>;
- <code>open-sse/services/accountFallback.ts</code>;
- <code>open-sse/services/accountFallback/exactModelLock.ts</code>;
- <code>open-sse/handlers/chatCore/keyHealth.ts</code>;
- <code>open-sse/services/combo/responseValidation.ts</code>;
- <code>open-sse/services/autoCombo/requestControls.ts</code>;
- <code>src/domain/omnirouteResponseMeta.ts</code>;
- <code>src/shared/constants/headers.ts</code>;
- <code>docs/routing/AUTO-COMBO.md</code>;
- <code>docs/routing/QUOTA_SHARE.md</code>.

OmniRoute is MIT licensed. If implementation code is copied rather than
independently reimplemented from the concepts, PawFlow must retain the required
copyright and MIT notice for the copied portion.

## 4. Current PawFlow behavior

### 4.1 Physical LLM connections

<code>LLMConnectionService</code> owns one provider configuration and creates an
isolated <code>LLMClient</code> clone per logical call. It already supports:

- direct OpenAI-compatible Chat Completions;
- OpenAI Responses;
- Anthropic;
- Azure OpenAI and Copilot dialects;
- Claude Code, Codex, Gemini, and Antigravity CLI-backed providers;
- per-call identity propagation;
- API-key pools;
- OAuth credential-provider references;
- per-service retries, timeouts, model defaults, pricing, and subscriptions.

The API-key pool already uses round-robin selection for calls without an
explicit affinity index. This is credential selection inside one physical
service. It is not multi-service routing.

Runtime service-level capacity gating is intentionally disabled. A shared LLM
service must not make foreground agents, compaction, memory extraction, or
sub-agents wait behind another caller.

### 4.2 OAuth credential pools

<code>llmCredentialOAuthProvider</code> stores encrypted pools for Claude Code,
Codex, and Gemini families. Interactive aliases reuse the canonical provider
pool.

The pool entries are currently addressed operationally by array index. They do
not have a stable public credential UUID. The native router must therefore not
persist health keyed by pool index: reordering or removing an entry would attach
old health state to a different credential.

Credential-granular health is deferred until credential entries have stable
opaque IDs. Service/model health is sufficient for the router V1.

### 4.3 Ordered failover

<code>LLMFailoverService</code> currently:

- starts each turn at the configured main connection;
- resolves ordered fallbacks lazily;
- stays on the selected fallback for later LLM calls in the same turn;
- turns a provider failure into <code>LLMFailoverRequired</code>;
- asks AgentLoop to flush persisted work and cold-start the next connection;
- preserves accumulated sanitized failure records;
- excludes cancellation and other control-flow exceptions;
- returns one sanitized exhaustion error.

This continuity mechanism is the foundation of <code>llmRouter</code>. It must
not be replaced by a request replay loop.

### 4.4 Usage ledger

<code>UsageLedger</code> records one event per successful LLM call with:

- user;
- conversation;
- agent;
- logical LLM service;
- model;
- provider when supplied;
- tokens and cache usage;
- duration;
- frozen real or virtual cost.

An AgentLoop turn may therefore produce several usage events as it alternates
model calls and tool execution. The ledger does not record failed attempts,
route decisions, cooldowns, or the physical child service selected by a
composite service. Those are operational routing events, not cost events.

### 4.5 Present gaps

PawFlow does not currently have:

- initial candidate selection other than first configured;
- shared service/model health across turns;
- typed failure categories;
- retry-after-aware cooldowns at the router layer;
- per-model lockouts;
- round-robin across LLM services;
- turn-count stickiness;
- selection explanations;
- failed-attempt telemetry;
- gateway-specific response metadata;
- a distinction in the ledger between logical router and physical child.

## 5. Goals

The implementation must:

1. keep an agent productive when a provider, model, account, or endpoint is
   temporarily unavailable;
2. distribute new turns across configured services without changing provider
   during a healthy turn;
3. preserve provider-native context and prompt-cache affinity;
4. distinguish routing failures from cancellation, compaction, tool errors, and
   malformed caller requests;
5. avoid repeatedly selecting a known-degraded target;
6. recover targets automatically after bounded cooldowns;
7. explain every selection and exclusion;
8. persist operational state across server restarts;
9. remain safe under concurrent turns;
10. work with global, user, and conversation service scopes;
11. preserve exact cost accounting for the physical child;
12. support an OmniRoute gateway without requiring the native router;
13. keep all UI and HTTP operations asynchronous;
14. provide complete unit and integration coverage.

## 6. Non-goals for the first release

The first release does not provide:

- twenty routing strategies;
- task-content classification by another LLM;
- Arena ELO or external benchmark synchronization;
- automatic discovery of free web-cookie providers;
- cross-deployment distributed routing state;
- Redis;
- credential-level health for pools without stable credential IDs;
- live migration of an active turn between providers without a cold handoff;
- routing between arbitrary composite services;
- recursive router or aggregator graphs;
- dynamic model override inside one candidate;
- silent fallback after a caller validation error;
- local fair-share quotas that block a shared credential;
- provider scraping or undocumented OmniRoute dashboard APIs;
- automatic configuration of OmniRoute itself;
- mirroring OmniRoute's internal provider graph into PawFlow.

## 7. Non-negotiable invariants

1. A logical turn has one immutable route-plan ID and creation timestamp.
2. The candidate order for a turn is snapshotted once.
3. Health changes may affect the next turn but never reorder an existing plan.
4. Successful LLM/tool iterations stay on the current physical child.
5. A handoff always rebuilds canonical persisted context before continuing.
6. Persisted text, tool calls, and tool results are not emitted twice.
7. An unresolved tool call remains an unknown outcome after handoff.
8. Cancellation, force stop, supersession, provider compaction, and local
   shutdown never penalize candidate health.
9. A timeout created by PawFlow's own deadline is not automatically a provider
   rejection.
10. Caller errors and malformed requests do not open a provider circuit.
11. Context overflow is not provider health.
12. A model-specific failure does not disable unrelated models or the whole
    provider.
13. No health key contains an access token, refresh token, API key, cookie,
    authorization header, or reversible secret fingerprint.
14. Service resolution always respects conversation, parent, user, and global
    scope rules.
15. A router references only visible enabled <code>llmConnection</code>
    definitions.
16. A router cannot reference itself, another router, or an aggregator.
17. Candidate IDs are unique within one router.
18. A single credential may serve unlimited concurrent logical sessions.
19. The router never reserves or exclusively acquires a credential.
20. A missing required configuration field raises a validation error.
21. No anonymous/default service target is selected.
22. Every route plan, decision, and outcome has a UUID and timestamp.
23. Routing telemetry never changes frozen historical cost.
24. Routing-state write failures do not silently corrupt affinity.
25. A strict budget failure is never converted into a cheaper-but-over-budget
    call.
26. Exactly one layer owns primary adaptive routing.

## 8. Routing ownership modes

### 8.1 Native PawFlow mode

~~~text
AgentLoop
  -> llmRouter
       -> direct llmConnection A
       -> direct llmConnection B
       -> direct llmConnection C
~~~

PawFlow owns selection, health, fallback, and explanations. This is the preferred
mode for provider-native CLI sessions and installations that want full local
control.

### 8.2 OmniRoute gateway mode

~~~text
AgentLoop
  -> llmConnection(provider=omniroute, model=auto)
       -> OmniRoute
            -> provider/model selected by OmniRoute
~~~

OmniRoute owns selection and internal fallback. PawFlow treats it as one physical
gateway connection and records the metadata OmniRoute reports.

This is the preferred mode for rapidly accessing OmniRoute's catalog and routing
without implementing native PawFlow policies first.

### 8.3 Hybrid fallback mode

~~~text
AgentLoop
  -> llmRouter(strategy=ordered)
       -> preferred direct llmConnection
       -> secondary direct llmConnection
       -> llmConnection(provider=omniroute, model=auto)
~~~

This is permitted when OmniRoute is an opaque last-resort target. PawFlow must
not score OmniRoute's internal candidates against direct PawFlow candidates.

### 8.4 Unsupported default

The following should produce a configuration warning:

~~~text
llmRouter(strategy=adaptive)
  -> omniroute(auto)
  -> omniroute(auto/fast)
  -> direct providers
~~~

Two adaptive engines would make affinity, quota, cost, and failure explanations
ambiguous. It may be explicitly allowed for expert use later, but it is not a
supported V1 topology.

## 9. Target architecture

~~~text
                         +----------------------+
                         | AgentLoop turn       |
                         | turn_id + identity   |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | LLMRouterService     |
                         | config validation    |
                         +----------+-----------+
                                    |
                                    v
 +------------------+    +----------------------+    +----------------------+
 | LLMRoutingStore  |<-->| Candidate selector   |<-->| Failure classifier   |
 | health           |    | eligibility          |    | typed outcomes       |
 | affinity         |    | policy ordering      |    | cooldown scope       |
 | route events     |    | plan snapshot        |    | retryability         |
 +------------------+    +----------+-----------+    +----------------------+
                                    |
                                    v
                         +----------------------+
                         | RouterLLMClient      |
                         | one immutable plan   |
                         | current attempt      |
                         +----------+-----------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
       +----------------------+              +----------------------+
       | LLMConnectionService |              | LLMConnectionService |
       | physical child A     |              | physical child B     |
       +----------------------+              +----------------------+
~~~

The modules should be small and directional:

~~~text
services/llm_router.py
  -> core/llm_routing_policy.py
  -> core/llm_routing_store.py
  -> core/llm_routing_types.py
  -> core/llm_failure_classifier.py

core modules do not import AgentLoop or chat UI modules.
~~~

## 10. Domain model

### 10.1 CandidateDefinition

~~~python
@dataclass(frozen=True)
class CandidateDefinition:
    service_id: str
    priority: int
    weight: float
    enabled: bool
~~~

Rules:

- <code>service_id</code> is required and unique;
- the referenced type must be <code>llmConnection</code>;
- <code>priority</code> is an explicit integer;
- <code>weight</code> is finite and greater than zero;
- disabled candidates remain visible in configuration but are ineligible;
- the router never overrides the child's model or credentials.

### 10.2 RouteIdentity

~~~python
@dataclass(frozen=True)
class RouteIdentity:
    user_id: str
    conversation_id: str
    agent_name: str
    turn_id: str
    event_cid: str
~~~

All fields except <code>event_cid</code> are required for AgentLoop routing.
Standalone service calls create an explicit ephemeral turn ID.

The affinity key is:

~~~text
(router_scope, router_scope_id, router_service_id,
 user_id, conversation_id, agent_name)
~~~

It is not global by router ID. Service IDs are unique only inside one PawFlow
scope, so the owning <code>ServiceDef.scope</code> and
<code>ServiceDef.scope_id</code> are part of every persisted router key. Two
users or conversations must never affect one another's sticky target.

### 10.3 ResolvedServiceRef

Configuration stores a child service ID, but a route plan stores the exact
definition resolved through PawFlow's conversation, user, then global cascade:

~~~python
@dataclass(frozen=True)
class ResolvedServiceRef:
    scope: str
    scope_id: str
    service_id: str
    definition_revision: str
~~~

The tuple <code>(scope, scope_id, service_id)</code> is the natural identity of a
live <code>ServiceDef</code>. A handoff opens that exact definition; it never
resolves <code>service_id</code> through the cascade again. If the snapshotted
definition was deleted, disabled, changed type, or materially revised, that
candidate becomes safely unavailable. A newly created higher-scope definition
with the same ID cannot capture an in-progress plan.

<code>definition_revision</code> is a derived value; it is not an existing
<code>ServiceDef</code> field. WP3 must add one shared
<code>compute_service_definition_revision()</code> helper with this contract:

1. build a canonical payload from <code>service_type</code>,
   <code>created_at</code>, <code>enabled</code>, and the persisted service config;
2. remove runtime-injected fields and normalize dictionaries with sorted keys
   while preserving list order;
3. reuse the schema-based sensitive-key classification currently owned by
   <code>ServiceRegistry</code> persistence; WP3 extracts a shared public helper
   from that existing logic rather than maintaining a second key list, and the
   revision canonicalizer replaces every classified value with a fixed marker
   before hashing;
4. serialize with deterministic JSON separators and reject non-finite or
   unsupported values;
5. return a lowercase SHA-256 digest.

Including <code>created_at</code> distinguishes a deleted-and-recreated
definition even when its visible config is identical. A material non-secret
config change produces a new digest. Secret rotation is deliberately not a
secret fingerprint and does not alter the digest; the successful service-update
path explicitly clears applicable authentication health instead. The router
recomputes the revision immediately before opening a snapshotted candidate. A
mismatch yields a safe <code>definition_changed</code> exclusion and never falls
back to name resolution. Although current <code>ServiceDef</code> persistence
already writes <code>created_at</code>, WP3 must detect any legacy record where it
is absent or invalid, assign it once under the normal registry write lock, and
persist it before routing. It must never synthesize a new timestamp on every
load.

### 10.4 CandidateKey

~~~python
@dataclass(frozen=True)
class CandidateKey:
    router_scope: str
    router_scope_id: str
    router_service_id: str
    child_scope: str
    child_scope_id: str
    child_service_id: str
    model: str
    credential_id: str = ""
~~~

V1 leaves <code>credential_id</code> empty. A future child service may expose a
stable opaque credential ID. Array positions are forbidden.

### 10.5 RoutePlan

~~~python
@dataclass(frozen=True)
class RoutePlan:
    plan_id: str
    created_at: float
    router: ResolvedServiceRef
    strategy: str
    identity: RouteIdentity
    ordered_candidates: tuple[ResolvedServiceRef, ...]
    excluded: tuple[CandidateExclusion, ...]
~~~

The plan is immutable. A handoff carries this exact snapshot into the rebuilt
AgentLoop context.

### 10.6 RouteAttempt

~~~python
@dataclass
class RouteAttempt:
    attempt_id: str
    plan_id: str
    attempt_index: int
    child: ResolvedServiceRef
    started_at: float
    completed_at: float = 0
    outcome: str = "running"
    failure_kind: str = ""
~~~

### 10.7 HealthRecord

~~~python
@dataclass
class HealthRecord:
    key: CandidateKey
    state: str
    consecutive_failures: int
    last_success_at: float
    last_failure_at: float
    cooldown_until: float
    last_failure_kind: str
    last_status: int
    updated_at: float
    revision: int
~~~

Allowed states are:

- <code>healthy</code>;
- <code>degraded</code>;
- <code>cooldown</code>;
- <code>locked</code>;
- <code>unknown</code>.

<code>locked</code> is reserved for conditions requiring operator action or a
known reset event. It must not be created from an unclassified string match.

### 10.8 FailureObservation

~~~python
@dataclass(frozen=True)
class FailureObservation:
    observation_id: str
    timestamp: float
    category: str
    origin: str
    scope: str
    retryable: bool
    provider_status: int
    retry_after_seconds: float
    service_id: str
    provider: str
    model: str
    safe_message: str
~~~

Raw provider bodies, secrets, stack traces, and headers are never stored in the
routing database.

### 10.9 RouteDecision

~~~python
@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    plan_id: str
    timestamp: float
    selected: ResolvedServiceRef
    strategy: str
    reason_code: str
    candidate_summaries: tuple[CandidateSummary, ...]
~~~

A decision records normalized factors and reason codes, not secrets or entire
service configurations.

## 11. Service configuration

The new service type is <code>llmRouter</code>.

Recommended V1 schema:

~~~json
{
  "candidates": [
    {
      "service_id": "codex_subscription",
      "priority": 10,
      "weight": 1.0,
      "enabled": true
    },
    {
      "service_id": "claude_subscription",
      "priority": 20,
      "weight": 1.0,
      "enabled": true
    },
    {
      "service_id": "api_fallback",
      "priority": 30,
      "weight": 1.0,
      "enabled": true
    }
  ],
  "strategy": "sticky_round_robin",
  "sticky_successful_turns": 3,
  "affinity_ttl_seconds": 86400,
  "transient_failure_threshold": 3,
  "base_cooldown_seconds": 30,
  "max_cooldown_seconds": 1800,
  "response_validation": {
    "reject_empty_assistant": true
  }
}
~~~

Parameters:

| Name | Type | Required | Default | Meaning |
|---|---|---:|---:|---|
| <code>candidates</code> | JSON candidate array | yes | none | Physical child services |
| <code>strategy</code> | select | yes | <code>ordered</code> | Initial ordering policy |
| <code>sticky_successful_turns</code> | integer | conditional | 1 | Full successful turns before rotation |
| <code>affinity_ttl_seconds</code> | integer | no | 86400 | Idle affinity expiry |
| <code>transient_failure_threshold</code> | integer | no | 3 | Failures before transient cooldown |
| <code>base_cooldown_seconds</code> | integer | no | 30 | Initial cooldown |
| <code>max_cooldown_seconds</code> | integer | no | 1800 | Cooldown cap |
| <code>response_validation</code> | JSON | no | empty | Safe validation rules |

Validation must reject:

- fewer than two enabled candidates;
- duplicate service IDs;
- empty service IDs;
- non-<code>llmConnection</code> references;
- self-reference or composite reference;
- unknown strategies;
- non-finite or non-positive weights;
- negative TTLs or cooldowns;
- a base cooldown larger than its cap;
- sticky strategy without a positive turn limit;
- strict response validation with no declared rule.

The two-enabled-candidate rule is an install/update invariant. The backend
rejects a save that would reduce a router below two enabled configured
candidates, and the previously valid definition remains active. The candidate
editor disables that save action and explains why; it does not let a toggle
silently break the service.

Runtime availability is different from configuration validity. A child may be
deleted, disabled in its own service definition, moved out of scope, locked, or
put into cooldown after the router was saved. The router excludes that child and
emits a bounded <code>candidate_set_degraded</code> event. It may continue a turn
with one eligible snapshotted candidate; with none it follows normal exhaustion.
A stored router config that itself contains fewer than two enabled candidates is
still invalid and fails connection rather than being accepted as runtime drift.

## 12. Route-plan lifecycle

### 12.1 Initial selection

At the first LLM call of a logical turn:

1. resolve the router in the caller's service scope;
2. validate and resolve child definitions to immutable
   <code>ResolvedServiceRef</code> values;
3. read health and affinity in one store snapshot;
4. classify each candidate as eligible, cooldown, locked, disabled, missing, or
   invalid;
5. apply the configured strategy to eligible candidates;
6. append controlled recovery probes when applicable;
7. create a UUID route plan with the full ordered snapshot;
8. persist a <code>plan_created</code> routing event;
9. instantiate the first physical child lazily from its exact scope tuple.

A health update after step 7 does not mutate this plan.

### 12.2 Internal LLM/tool iterations

Every later LLM call within the same turn reuses:

- plan ID;
- ordered resolved candidate references;
- current attempt index;
- accumulated sanitized failures.

The router does not call the selection policy again.

### 12.3 Handoff

When a classified child failure is fallback-eligible:

1. record the attempt outcome;
2. update health atomically;
3. select the next resolved reference from the existing plan;
4. abort and clean up the abandoned child;
5. raise <code>LLMRouteHandoffRequired</code>;
6. flush queued conversation writes;
7. rebuild canonical context with <code>force_cold=True</code>;
8. restore the same plan snapshot and next attempt;
9. continue through the next physical child.

The handoff signal contains no raw provider exception text in its public message.

### 12.4 Successful completion

A successful physical response records latency and resets the applicable
transient failure streak.

Only terminal completion of the entire AgentLoop turn increments
<code>sticky_successful_turns</code>. A successful first model response followed
by a later failed tool-result response is not a successful sticky turn.

### 12.5 Exhaustion

When no candidate remains:

- persist one <code>plan_exhausted</code> event;
- return a sanitized error containing candidate count and a correlation ID;
- retain already-persisted work;
- never expose API keys, provider bodies, URLs containing tokens, or stack traces;
- do not mutate next-turn affinity toward a failed candidate.

## 13. Selection policies

### 13.1 Ordered

Sort by:

~~~text
priority ascending, configured position ascending
~~~

The first eligible candidate is selected. This reproduces current
<code>llmFailover</code> semantics.

### 13.2 Round robin

Round robin advances once per new logical turn, not once per LLM call.

The store atomically increments a counter scoped by router service and service
definition scope. Selection is:

~~~text
eligible[counter modulo eligible_count]
~~~

Cooldown candidates do not consume a slot. Re-admission does not rewrite old
plans.

### 13.3 Sticky round robin

Sticky round robin uses the affinity key and a configured number of successful
full turns.

Rules:

1. reuse the affinity target while it is eligible and below its success limit;
2. on reaching the limit, advance round robin for the next turn;
3. on target failure, clear affinity immediately and continue the current plan;
4. on TTL expiry, select afresh;
5. no successful-turn increment occurs for cancelled, failed, or superseded
   turns.

### 13.4 Least recently used

Choose the eligible candidate with the oldest completed selection timestamp.
Ties resolve by priority and configured position. This policy is deterministic
and does not require latency or quota telemetry.

### 13.5 Health weighted

Defer this strategy until the deterministic policies and telemetry are stable.

The first version of health weighting should use only four normalized factors:

~~~text
score =
    health_weight * health
  + latency_weight * latency_inverse
  + cost_weight * cost_inverse
  + availability_weight * availability
~~~

Requirements:

- weights are non-negative and normalized;
- missing telemetry is neutral, never zero;
- every factor is clamped to the range 0 through 1;
- non-finite values invalidate that factor;
- hard exclusions run before scoring;
- tie-breaking is deterministic;
- the decision stores every factor used;
- quota is not a factor until a reliable provider signal exists.

Do not add task fitness, benchmark rank, tier affinity, specificity, connection
density, or exploration in V1.

## 14. Typed failure classification

### 14.1 Required error contract

The current router sees generic exceptions and sanitized strings. That is not
enough to distinguish quota, model, auth, and caller failures reliably.

Introduce a structured error carried from provider adapters:

~~~python
class LLMCallError(Exception):
    category: str
    origin: str
    provider_status: int
    retryable: bool
    retry_after_seconds: float
    provider: str
    model: str
    safe_message: str
    caused_by_local_timeout: bool
~~~

Provider adapters classify at the point where status, headers, and body are
available. The router consumes structured fields. A conservative text classifier
is permitted only as a final fallback and may create a short degraded state, not
a terminal lock.

### 14.2 Classification matrix

| Category | Router action | Health scope |
|---|---|---|
| <code>cancelled</code> | re-raise, no fallback | none |
| <code>superseded</code> | re-raise, no fallback | none |
| <code>provider_compact</code> | existing compact path | none |
| <code>caller_invalid</code> | terminal, no fallback | none |
| <code>tool_error</code> | existing tool handling | none |
| <code>context_overflow</code> | existing compact path first | model capability only |
| <code>local_timeout</code> | optional fallback, no penalty by default | none |
| <code>network</code> | fallback | service transient |
| <code>upstream_timeout</code> | fallback | service transient |
| <code>rate_limited</code> | fallback and cooldown | service/model |
| <code>quota_exhausted</code> | fallback until reset | service/model |
| <code>auth_invalid</code> | child pool recovery, then fallback | child-defined |
| <code>billing_exhausted</code> | fallback and lock | service |
| <code>model_unavailable</code> | fallback and lock | exact model |
| <code>provider_unavailable</code> | fallback and cooldown | service |
| <code>response_invalid</code> | fallback when configured | service/model transient |
| <code>unknown</code> | fallback, short degradation only | service transient |

### 14.3 Minimum contract for CLI-backed providers

CLI-backed providers such as Claude Code Interactive, Codex Interactive,
Gemini/Antigravity Interactive, and related process-backed adapters usually have
no HTTP status, response headers, or trustworthy <code>Retry-After</code>. WP2
must support them explicitly rather than pretending the HTTP matrix is universal.

Their minimum contract is:

- propagate cancellation, force stop, compaction, cold-start, delta-context, and
  supersession control flow without wrapping or penalizing health;
- set <code>provider_status=0</code> and
  <code>retry_after_seconds=0</code> when the provider did not supply those
  values; never infer a status code from an arbitrary number in stderr;
- classify adapter-owned process launch, pipe, socket, session transport, and
  unexpected-exit signals as <code>network</code> or
  <code>provider_unavailable</code> only when the adapter can identify the
  origin;
- classify <code>local_timeout</code> from PawFlow's own watchdog/deadline signal,
  with no health penalty by default;
- recognize <code>auth_invalid</code>, <code>rate_limited</code>,
  <code>quota_exhausted</code>, and <code>context_overflow</code> only from typed
  CLI output or a small provider-specific allowlist of tested diagnostics;
- map every other process error to <code>unknown</code>, which permits fallback
  and only short degradation, never a terminal lock;
- redact and bound stdout/stderr before producing <code>safe_message</code>; raw
  process output never enters routing state or conversation events.

Each CLI adapter owns its diagnostic allowlist and fixtures. A shared helper may
normalize and redact observations, but there is no global regex table that
guesses provider semantics. When no reset hint exists, the router uses its local
bounded cooldown policy and does not fabricate one.

### 14.4 Context overflow ordering

Context overflow must reach AgentLoop's compaction path before the router marks a
candidate failed. Only after compaction cannot make the request fit may the
router try a known larger-context candidate.

This requires the router to re-raise context-overflow control flow rather than
catching every ordinary exception as current <code>llmFailover</code> does.

### 14.5 Authentication and pools

The child connection first decides whether another credential in its own pool
can recover an authentication failure. The router receives
<code>auth_invalid</code> only when the child declares the service attempt
unusable.

The router must not guess which credential failed from a pool index.

## 15. Cooldown and recovery

### 15.1 Cooldown calculation

Use provider <code>Retry-After</code> or a normalized reset timestamp when
available and sane.

Otherwise:

~~~text
cooldown =
  min(max_cooldown,
      base_cooldown * 2 raised to max(0, consecutive_failures - threshold))
~~~

Add bounded jitter only to background probe scheduling, not to persisted
<code>cooldown_until</code>, so diagnostics remain deterministic.

### 15.2 State transitions

~~~text
unknown -> healthy          on success
healthy -> degraded         on first transient failure
degraded -> cooldown        at threshold
cooldown -> probe           after cooldown
probe -> healthy            on success
probe -> cooldown           on failure with increased backoff
healthy -> locked           on confirmed terminal condition
locked -> healthy           on manual reset, credential/config revision, or known reset
~~~

### 15.3 Recovery probes

A recovered candidate receives at most one concurrent probe per health key.
Probe ownership is an atomic store lease with a short expiry. Normal turns skip a
candidate while another turn owns its probe.

This probe lease is operational coordination, not credential capacity. It cannot
produce an all-credentials-busy error.

### 15.4 Manual reset

The service exposes actions to:

- show candidate health;
- clear one candidate/model state;
- clear router affinity;
- run a safe one-shot health test;
- explain the last route decision.

All actions enforce the same service-scope authorization as service editing.

## 16. Credential-pool contract

Routing and credential selection remain separate layers.

### 16.1 Required behavior

- One credential can be shared by any number of concurrent calls.
- Multiple credentials may be selected round-robin or by child-provider logic.
- No local semaphore represents upstream subscription quota.
- No session is rejected because every credential is in use.
- Provider 429 or quota signals create time-based health information.
- A provider-specific container requirement may serialize physical work only
  inside that provider implementation; it does not invalidate the logical
  credential or router candidate.
- Token refresh writes back to the same credential entry under the existing pool
  lock.

### 16.2 Future stable credential IDs

Credential-granular health requires an opaque UUID stored with each credential
entry. A future migration may add <code>credential_id</code> while preserving
encrypted secret contents.

It must:

- generate a UUID once;
- retain it across token refresh;
- never derive it from the token;
- update pool UI actions to address UUID rather than array position;
- keep position only as a display order;
- migrate API-key pools away from bare string arrays before persisting per-key
  health.

This is not required for service/model routing V1.

## 17. Dedicated OmniRoute provider

### 17.1 Why a provider is useful

OmniRoute already exposes an OpenAI-compatible
<code>/v1/chat/completions</code> endpoint. The following generic configuration
should work as an initial smoke test without a dedicated provider:

~~~json
{
  "provider": "openai",
  "base_url": "http://omniroute:20128/v1",
  "api_key": "configured-secret-reference",
  "default_model": "auto"
}
~~~

A named provider adds value by making OmniRoute-specific behavior explicit:

- a required gateway URL;
- model IDs such as <code>auto</code>, <code>auto/coding</code>,
  <code>auto/fast</code>, and persisted combo names;
- request routing mode;
- strict per-request budget headers;
- response routing metadata;
- gateway version visibility;
- model discovery through <code>GET /v1/models</code>;
- gateway-specific diagnostics;
- correct usage attribution.

It must remain a provider value under <code>llmConnection</code>, not a new
controller-service type.

### 17.2 V1 configuration

~~~json
{
  "provider": "omniroute",
  "base_url": "http://omniroute:20128/v1",
  "omniroute_auth_mode": "bearer",
  "api_key": "configured-secret-reference",
  "default_model": "auto",
  "omniroute_mode": "balanced",
  "omniroute_budget_usd": 0,
  "omniroute_budget_fallback": "strict"
}
~~~

Parameters:

| Name | Required | Rules |
|---|---:|---|
| <code>base_url</code> | yes | Explicit HTTP(S) URL; no public default |
| <code>omniroute_auth_mode</code> | yes | <code>bearer</code> or explicit <code>none</code> |
| <code>api_key</code> | conditional | Required for bearer mode |
| <code>default_model</code> | yes | No silent model fallback |
| <code>omniroute_mode</code> | no | balanced, fast, quality, cheap, reliable, offline |
| <code>omniroute_budget_usd</code> | no | Zero disables request budget header |
| <code>omniroute_budget_fallback</code> | conditional | Required when budget is positive |

PawFlow must never infer <code>omniroute_auth_mode=none</code> from an empty API key.

### 17.3 Wire implementation

Add <code>omniroute</code> to <code>LLMClient.PROVIDERS</code> and dispatch it
through the OpenAI Chat Completions wire implementation.

Do not duplicate <code>LLMOpenaiMixin</code>. Introduce a narrow provider-header
hook used by both streaming and non-streaming OpenAI calls.

The provider adds these request headers when configured:

- <code>X-OmniRoute-Mode</code>;
- <code>X-OmniRoute-Budget</code>;
- <code>X-OmniRoute-Budget-Fallback</code>.

These header names, the response allowlist below, SSE comment behavior, virtual
model IDs, and <code>GET /v1/models</code> are upstream-contract assumptions, not
PawFlow-owned guarantees. Before implementation, WP1 must pin an exact OmniRoute
commit SHA and verify each item against its source and a running local instance.
Record the SHA and verified contract in the provider tests and documentation. If
an assumed feature is absent at that revision, omit or gate it explicitly rather
than emulating undocumented behavior.

Only finite positive budget values are sent. Unknown modes fail service
validation rather than being silently ignored.

Responses may contain this allowlisted metadata:

- <code>X-OmniRoute-Cache-Hit</code>;
- <code>X-OmniRoute-Cost-Saved</code>;
- <code>X-OmniRoute-Decision</code>;
- <code>X-OmniRoute-Fallback-Attempts</code>;
- <code>X-OmniRoute-Latency-Ms</code>;
- <code>X-OmniRoute-Model</code>;
- <code>X-OmniRoute-Provider</code>;
- <code>X-OmniRoute-Request-Id</code>;
- <code>X-OmniRoute-Response-Cost</code>;
- <code>X-OmniRoute-Tokens-In</code>;
- <code>X-OmniRoute-Tokens-Out</code>;
- <code>X-OmniRoute-Version</code>.

Streaming responses may repeat metadata in SSE comment lines. The OpenAI stream
parser should recognize only the exact allowlist and ignore all other comments.

### 17.4 Response representation

Add an optional, sanitized metadata dictionary to
<code>LLMResponse</code>:

~~~python
provider_metadata = {
    "gateway": "omniroute",
    "gateway_version": "...",
    "gateway_request_id": "...",
    "routing_strategy": "...",
    "upstream_provider": "...",
    "upstream_model": "...",
    "fallback_attempts": 0,
    "gateway_latency_ms": 0,
    "gateway_cost_usd": 0.0,
    "cache_hit": False,
}
~~~

Rules:

- preserve the requested logical model separately;
- use OmniRoute's reported model/provider for physical diagnostics;
- never copy arbitrary response headers;
- cap all string lengths;
- reject control characters;
- parse numeric fields with finite bounds;
- do not treat reported fallback attempts as PawFlow child attempts;
- do not trust gateway cost as PawFlow's sole budget authority.

### 17.5 Usage accounting

For an OmniRoute connection:

- <code>llm_service</code> remains the PawFlow service ID;
- <code>provider</code> remains <code>omniroute</code>;
- the resolved upstream provider/model are stored as routing metadata;
- PawFlow token counts remain authoritative when present in the normalized
  response;
- OmniRoute token headers are a fallback or cross-check, not additive;
- PawFlow pricing remains frozen from its own service config;
- reported gateway cost is diagnostic and must not be added again to PawFlow
  cost;
- a discrepancy may be logged as a metric but cannot rewrite the historical
  event.

### 17.6 Health semantics

PawFlow health applies to the OmniRoute gateway connection as a whole.

- An internal OmniRoute fallback is a successful PawFlow attempt.
- <code>X-OmniRoute-Fallback-Attempts</code> does not degrade gateway health.
- A gateway network error or terminal HTTP failure may degrade the OmniRoute
  connection.
- PawFlow does not create health records for the internal provider names reported
  by the gateway.
- Gateway 429 handling must honor <code>Retry-After</code> when present.
- A model-specific gateway error may lock the requested OmniRoute virtual model,
  not every model exposed by the gateway.

### 17.7 Model discovery

Provide a service action that calls <code>GET /v1/models</code> using the same
configured authentication and TLS behavior.

The action:

- is read-only;
- has a bounded response size and timeout;
- validates the OpenAI model-list shape;
- returns IDs and optional owned-by metadata;
- caches only public model metadata;
- never stores the endpoint key in cache or logs;
- does not mutate <code>default_model</code> automatically.

### 17.8 Protocol scope

V1 supports Chat Completions only. OmniRoute Responses API support is a separate
compatibility track because PawFlow's <code>openai-responses</code> path has
different typed items, tools, and SSE semantics.

Do not add a protocol selector that silently changes wire format. A future
<code>omniroute-responses</code> provider value or an explicit validated protocol
field is preferable.

### 17.9 Security

- Require explicit <code>base_url</code>.
- Preserve normal TLS verification.
- Permit plain HTTP only because local/private gateway deployments are valid;
  show a warning when the host is not loopback or private.
- Redact endpoint userinfo and query parameters from logs.
- Never forward caller-supplied arbitrary headers.
- Never expose the gateway key in service details.
- Warn users that prompts are sent to OmniRoute and whichever upstream it
  selects.
- Do not call undocumented dashboard or admin endpoints.
- Detect obvious recursive base URLs pointing back to PawFlow and reject them
  when possible.
- Add a bounded PawFlow gateway-hop header for future loop detection, but do not
  assume OmniRoute currently enforces it.

## 18. Routing persistence

Create <code>core/llm_routing_store.py</code> with a dedicated SQLite database
at a new <code>core.paths</code> constant under <code>data/system</code>.

Do not put mutable health rows in <code>usage.db</code>. Cost retention,
operational reset, and write rates are different concerns.

### 18.1 Tables

~~~sql
CREATE TABLE router_health (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    child_scope TEXT NOT NULL,
    child_scope_id TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    model TEXT NOT NULL,
    credential_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    last_success_at REAL NOT NULL,
    last_failure_at REAL NOT NULL,
    cooldown_until REAL NOT NULL,
    last_failure_kind TEXT NOT NULL,
    last_status INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (
        router_scope,
        router_scope_id,
        router_service_id,
        child_scope,
        child_scope_id,
        child_service_id,
        model,
        credential_id
    )
);

CREATE TABLE router_affinity (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    successful_turns INTEGER NOT NULL,
    last_selected_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (
        router_scope,
        router_scope_id,
        router_service_id,
        user_id,
        conversation_id,
        agent_name
    )
);

CREATE TABLE router_counters (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    value INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (router_scope, router_scope_id, router_service_id)
);

CREATE TABLE router_probe_leases (
    health_key TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE router_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    child_scope TEXT NOT NULL,
    child_scope_id TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    details_json TEXT NOT NULL
);
~~~

### 18.2 Store guarantees

- SQLite WAL mode;
- one process-local lock around a shared connection;
- explicit transactions for counter, affinity, and probe lease updates;
- compare-and-swap revisions where stale writers are possible;
- bounded event retention;
- indexes on time, plan, conversation, and child service;
- epoch timestamps for persistence;
- monotonic clocks only for measuring in-process duration;
- startup cleanup of expired affinity and probe leases;
- no foreign keys to mutable service files;
- every router and child reference uses the full
  <code>(scope, scope_id, service_id)</code> tuple;
- no secret-bearing columns.

### 18.3 Multi-process future

SQLite is the V1 single-server authority. If PawFlow later runs several active
server replicas, define a store interface and add a distributed driver. Do not
add Redis before a supported multi-replica runtime exists.

## 19. Usage and observability

### 19.1 Logical versus physical service

Extend each successful LLM-call usage event to distinguish:

- <code>llm_service</code>: logical service selected by the agent;
- <code>physical_llm_service</code>: child that produced the final response;
- <code>route_plan_id</code>: correlation with routing events;
- <code>route_attempt_id</code>: exact physical attempt that produced the
  response;
- <code>route_attempt_index</code>: zero-based position in the immutable plan.

Add logical and physical scope columns, or one canonical bounded service-key
column for each, so two same-named services in different scopes never aggregate
as one target. Failed attempts remain in <code>router_events</code> and do not
create zero-token cost rows. The plan-level attempt count is derived from routing
events rather than duplicated into every usage event.

A one-shot SQLite schema migration adds nullable/defaulted columns without
rewriting frozen costs.

For a direct connection, logical and physical service IDs are equal.

For OmniRoute gateway mode, the physical service is the OmniRoute
<code>llmConnection</code>; internal upstream names stay in sanitized metadata.

### 19.2 Metrics

Expose counters and histograms for:

- plans created;
- attempts per plan;
- fallback count;
- exhaustion count;
- selections by strategy and child;
- exclusions by reason;
- cooldown and lock count;
- probe outcomes;
- per-child success rate;
- per-child p50/p95 latency;
- affinity hit rate;
- route-store failures;
- OmniRoute internal fallback count as a separate gateway metric;
- reported-versus-local token discrepancies.

Metric labels must not include user IDs, conversation IDs, raw model strings with
unbounded cardinality, URLs, or errors.

### 19.3 Events

Publish safe conversation events:

- <code>llm.route.selected</code>;
- <code>llm.route.handoff</code>;
- <code>llm.route.exhausted</code>;
- <code>llm.route.recovered</code>.

The normal UI should show concise status. Detailed candidate explanations belong
in an operator view and service action response.

### 19.4 Logs

Every routing log line includes:

- plan ID prefix;
- router service ID;
- child service ID;
- attempt index;
- reason code.

It does not include prompt content, secret values, complete provider bodies, or
raw authorization-bearing URLs.

## 20. UI and service actions

### 20.1 Configuration editor

The existing service UI renders <code>service_ref</code> fields but the current
fallback list is raw JSON. Add a reusable ordered service-reference list editor
for router candidates.

The editor must support:

- add visible <code>llmConnection</code>;
- remove;
- drag reorder;
- enable/disable;
- priority;
- weight only when a weighted policy is selected;
- duplicate prevention;
- inline service scope and provider display;
- validation before save.

The backend remains authoritative. A crafted request cannot bypass reference
validation.

Likely UI files:

- <code>tasks/io/chat_ui/resources_service_dialogs.js</code>;
- <code>tasks/io/chat_ui/resources_service_login.js</code>;
- a new focused module if the existing file would exceed the size target;
- corresponding styles and i18n keys.

### 20.2 Health panel

The router service exposes a Health action showing:

- child service;
- provider/model;
- state;
- last success/failure;
- cooldown remaining;
- consecutive failures;
- affinity count;
- last safe reason;
- reset/test controls.

### 20.3 Explain action

An Explain last decision action returns:

~~~json
{
  "plan_id": "uuid",
  "strategy": "sticky_round_robin",
  "selected": "claude_subscription",
  "reason": "sticky_affinity",
  "candidates": [
    {
      "service_id": "codex_subscription",
      "eligible": false,
      "reason": "cooldown",
      "retry_at": "ISO-8601"
    },
    {
      "service_id": "claude_subscription",
      "eligible": true,
      "reason": "affinity"
    }
  ]
}
~~~

No secret or raw exception fields are returned.

### 20.4 OmniRoute actions

The OmniRoute connection exposes:

- Test gateway;
- Refresh model list;
- Show last gateway metadata.

It does not expose OmniRoute admin settings, keys, or connection management in
PawFlow V1.

## 21. Runtime integration changes

### 21.1 New service

Create <code>services/llm_router.py</code> containing:

- <code>LLMRouterService</code>;
- <code>RouterLLMClient</code>;
- <code>LLMRouteHandoffRequired</code>;
- <code>LLMRouteExhausted</code>;
- configuration validation and service actions.

The file should orchestrate helpers rather than contain policy, SQLite, and
classification implementations.

### 21.2 Agent context

Replace index-only failover fields with a route state:

~~~text
_llm_route_plan
_llm_route_attempt
_llm_route_failures
~~~

The route plan contains snapshotted <code>ResolvedServiceRef</code> values. Do not
reconstruct it from current service config or re-run scoped name resolution
during handoff.

Update:

- <code>tasks/ai/agent_context.py</code>;
- <code>tasks/ai/_agentctx_p1.py</code>;
- <code>tasks/ai/_alc_llm_turn.py</code>.

### 21.3 Turn completion hook

Update <code>tasks/ai/agent_core.py</code> to:

- finalize the active route plan;
- increment sticky successful-turn state only after terminal success;
- record logical and physical service IDs in usage;
- publish safe route metadata.

Discarded, cancelled, superseded, and failed turns call a non-success finalizer.

### 21.4 Service discovery

Replace <code>llmFailover</code> with <code>llmRouter</code> in:

- <code>core/conv_agent_config.py</code>;
- <code>tasks/ai/actions/_sf_k1.py</code>;
- any service-reference helper or filter;
- user-service listing tests;
- package/runtime schemas that enumerate LLM-capable services.

### 21.5 Provider changes

For the OmniRoute provider, update:

- <code>core/llm_client.py</code>;
- <code>core/_llm_client_driver.py</code>;
- <code>core/llm_providers/openai.py</code>;
- <code>core/llm_providers/openai_dialects.py</code> if the dialect registry is
  extended;
- <code>core/_llm_types.py</code> for sanitized provider metadata;
- <code>services/llm_connection.py</code> for schema/rules/actions;
- the service UI for conditional fields;
- provider documentation and tests.

### 21.6 Documentation and website

Update in the implementation change:

- <code>README.md</code>;
- <code>docs/02_REFERENCE_TASKS_SERVICES.md</code>;
- <code>docs/AGENT_SYSTEM.md</code>;
- <code>docs/llm_providers.md</code>;
- <code>pawflow-website/howtos.html</code>;
- <code>pawflow-website/docs.html</code>;
- relevant i18n resources.

Do not edit generated package metadata such as
<code>pawflow.egg-info/PKG-INFO</code> manually.

## 22. One-shot migration

PawFlow has no backward-compatibility requirement. The migration transforms
stored definitions and then deletes old runtime code.

### 22.1 Definition transformation

~~~text
service_type:
  llmFailover -> llmRouter

config:
  main_llm_service: A
  fallback_llm_services: [B, C]

becomes:

  candidates:
    - {service_id: A, priority: 10, weight: 1.0, enabled: true}
    - {service_id: B, priority: 20, weight: 1.0, enabled: true}
    - {service_id: C, priority: 30, weight: 1.0, enabled: true}
  strategy: ordered
~~~

The migration preserves service ID, scope, owner, enabled state, label, and
references from agents/conversations.

### 22.2 Migration requirements

- run before service instances connect;
- scan global, user, and conversation definitions;
- validate every transformed child reference;
- write atomically;
- maintain a durable version marker;
- be idempotent;
- emit a summary without secrets;
- never leave both old and new definitions active;
- include rollback instructions based on a pre-migration backup;
- remove <code>services/llm_failover.py</code> and old type registration in the
  same release after migration code is in place.

The migration helper itself may remain for one release line, but runtime
resolution must not keep an <code>llmFailover</code> compatibility alias.

### 22.3 Scope-aware failure policy

Migration runs as a dry-run inventory followed by per-definition atomic writes.
Its failure behavior depends on ownership scope:

- an unsafe global definition aborts startup before any service connects, because
  it can affect every user and requires an administrator decision;
- an unsafe user or conversation definition does not block unrelated tenants;
  the original record is copied to the protected pre-migration quarantine, then
  its active identity is replaced atomically by a disabled
  <code>llmRouter</code> placeholder with the same
  <code>(scope, scope_id, service_id)</code>;
- the placeholder contains only a safe migration error code, UUID, timestamp,
  and owner-facing remediation message; raw config and exceptions remain in the
  protected backup/quarantine path;
- disabled quarantined placeholders never connect and are excluded from LLM
  selectors, but remain visible to the owning user or conversation in the
  service editor;
- saving a corrected candidate list applies normal <code>llmRouter</code>
  validation, removes the quarantine marker, and enables the service only when
  explicitly requested;
- if the quarantine copy or placeholder write cannot complete atomically,
  startup aborts rather than risking loss of the original definition.

The durable migration report counts transformed and quarantined definitions by
scope without secrets. The version marker records quarantines so restart is
idempotent and does not create duplicate backups. No active or quarantined
definition relies on an <code>llmFailover</code> runtime alias.

## 23. Security analysis

### 23.1 Service-scope confusion

A router definition can outlive or shadow a referenced service. Validate
visibility both at install/update time and at runtime. Runtime deletion or scope
movement creates a safe exclusion event; it cannot resolve a same-named service
from another user's scope.

### 23.2 Cross-user mutable state

No shared service instance stores the current user as mutable routing state.
Identity is passed in immutable <code>RouteIdentity</code> values and included in
affinity keys.

Health may be shared for a truly shared physical global service, but affinity is
always per user/conversation/agent. The plan must state explicitly which health
scope owns each record.

### 23.3 Error leakage

The classifier stores normalized codes and short safe messages. Raw bodies are
available only in protected debug logs after existing redaction, never in route
events or user errors.

### 23.4 SSRF and gateway URLs

OmniRoute <code>base_url</code> follows the same operator-controlled endpoint
rules as other LLM services. User-controlled per-call URL override is forbidden.

### 23.5 Budget interaction

PawFlow period budgets remain authoritative. OmniRoute request budgets are an
additional upstream constraint.

If both exist:

- the stricter effective limit wins;
- a PawFlow budget block happens before the gateway call;
- OmniRoute strict budget rejection is classified as a caller/policy terminal,
  not gateway health;
- no fallback may bypass either strict budget.

### 23.6 Configuration cycles

At service installation and update, build the reference graph and reject:

- router to itself;
- router to router;
- router to aggregator;
- aggregator final service to router until explicitly designed and tested;
- any existing cycle.

## 24. Testing strategy

Nothing ships without unit and integration coverage.

### 24.1 Characterization tests before refactor

Extend the current failover tests to freeze:

- main-first order;
- lazy child resolution;
- sticky child within one turn;
- cold handoff;
- message flush before rebuild;
- cancellation and force-stop exclusion;
- unknown tool outcome preservation;
- sanitized exhaustion;
- cost config from the physical child.

These tests should pass against old code before the rename.

### 24.2 Policy unit tests

Create <code>tests/test_llm_routing_policy.py</code> covering:

- ordered;
- round robin;
- sticky round robin;
- LRU;
- deterministic ties;
- cooldown exclusions;
- locked candidates;
- all-ineligible exhaustion;
- affinity TTL;
- successful-turn counting;
- cancelled-turn non-counting;
- plan immutability;
- config revision changes;
- finite/clamped adaptive factors when later added.

Use a fake clock. Do not sleep.

### 24.3 Store tests

Create <code>tests/test_llm_routing_store.py</code> covering:

- schema creation;
- persistence across reopen;
- atomic counter increments;
- concurrent affinity updates;
- compare-and-swap revision;
- cooldown expiry;
- probe lease exclusivity and expiry;
- event retention;
- no secret fields;
- malformed details rejection;
- SQLite WAL fallback behavior.

### 24.4 Classifier tests

Create <code>tests/test_llm_failure_classifier.py</code> with fixtures for:

- 401/403 auth;
- 402 balance;
- 404 exact-model absence;
- 408 and provider timeout;
- local timeout;
- 429 with seconds and HTTP-date Retry-After;
- malformed Retry-After;
- 5xx;
- connection reset;
- context overflow;
- malformed request;
- cancellation;
- compaction;
- unknown exceptions;
- redaction and safe-message limits.

Add provider-specific CLI fixtures for typed control flow, process transport
failure, local watchdog timeout, explicit authentication/rate/quota/context
diagnostics, ambiguous stderr, missing status/reset metadata, and raw-output
redaction. Ambiguous CLI text must resolve to <code>unknown</code>, never
<code>locked</code>.

### 24.5 Router integration tests

Replace <code>tests/test_llm_failover.py</code> with
<code>tests/test_llm_router.py</code> and cover:

- one plan per turn;
- initial strategy selection;
- plan snapshot reused through cold rebuild;
- no reordering after concurrent health change;
- multiple sequential fallbacks;
- successful tool-result continuation;
- terminal success affinity update;
- physical service usage attribution;
- service deletion after plan creation;
- scope isolation;
- concurrent conversations;
- force stop followed by a clean next turn;
- direct non-AgentLoop service calls.

### 24.6 Credential invariants

Tests must assert:

- one credential supports concurrent logical sessions;
- no all-credentials-busy error exists;
- router never calls a credential acquire/release API;
- pool order changes do not affect V1 health keys;
- token refresh still writes to the correct pool entry;
- a provider's internal physical serialization does not mark the service locked.

### 24.7 OmniRoute provider tests

Create <code>tests/test_omniroute_provider.py</code> with a local fake HTTP
server covering:

- Chat Completions request path;
- bearer and explicit no-auth modes;
- <code>auto</code> model;
- request mode header;
- strict budget headers;
- no budget header for zero;
- streaming and non-streaming tool calls;
- allowlisted response headers;
- SSE metadata comments split across chunks;
- invalid numeric/header data;
- control-character rejection;
- fallback-attempt metadata;
- model discovery bounds;
- 429 Retry-After;
- gateway network failure;
- cost not double-counted;
- secret and URL redaction.

No test depends on a live public OmniRoute instance.

### 24.8 UI tests

Add source and browser-level tests for:

- candidate editor;
- service-ref filtering;
- duplicate prevention;
- rejection of a save with fewer than two enabled configured candidates;
- degraded runtime display when only one child remains eligible;
- reorder persistence;
- conditional strategy fields;
- health/explain actions;
- OmniRoute conditional parameters;
- API key secrecy;
- model refresh;
- i18n keys.

## 25. Work packages

### WP0: Characterize and freeze current behavior

Deliverables:

- expanded failover characterization tests;
- exact inventory of LLM-capable service filters;
- route identity source confirmed for web, task, sub-agent, and direct calls;
- no production behavior change.

Exit criteria:

- existing and new characterization tests pass;
- every current <code>llmFailover</code> reference is cataloged.

### WP1: OmniRoute provider

Deliverables:

- pinned OmniRoute commit and verified wire-contract inventory;
- <code>omniroute</code> provider value;
- conditional service schema;
- request control headers;
- sanitized response metadata;
- model-list service action;
- usage attribution;
- documentation and tests.

Exit criteria:

- every implemented OmniRoute endpoint, header, SSE behavior, and virtual model
  assumption is backed by the pinned upstream SHA and a local fixture;
- local fake gateway passes streaming, tools, metadata, and error tests;
- generic OpenAI providers remain byte-compatible;
- no live OmniRoute dependency in CI.

This work package can ship independently of <code>llmRouter</code>.

### WP2: Typed provider failures

Deliverables:

- structured <code>LLMCallError</code>;
- HTTP provider adapter classification;
- provider-specific minimal classification for every supported CLI-backed
  connection;
- retry-after parsing;
- safe fallback classifier;
- tests.

Exit criteria:

- HTTP router failures no longer depend primarily on string matching;
- CLI providers use typed adapter signals or narrow tested allowlists, default
  conservatively to <code>unknown</code>, and never fabricate HTTP metadata;
- cancellation and compaction remain control flow.

### WP3: Routing types and SQLite store

Deliverables:

- routing dataclasses;
- deterministic secret-free <code>definition_revision</code> derivation;
- shared schema-based sensitive-key classifier extracted from existing registry
  persistence and one-shot backfill of missing <code>created_at</code> values;
- store schema;
- health, affinity, counters, probes, events;
- cleanup and concurrency tests.

Exit criteria:

- identical definitions produce identical revisions, material config or
  recreation changes them, and secret values never affect or enter the digest;
- a legacy definition missing <code>created_at</code> receives one durable value
  and keeps the same revision across restart;
- persistence and atomicity tests pass;
- no secret-shaped data can enter the schema.

### WP4: Native deterministic policies

Deliverables:

- ordered;
- round robin;
- sticky round robin;
- least recently used;
- policy explanation records.

Exit criteria:

- deterministic fake-clock tests pass;
- missing telemetry is irrelevant to V1 selection.

### WP5: Router composite and AgentLoop handoff

Deliverables:

- <code>LLMRouterService</code>;
- immutable plan snapshot;
- new handoff and exhaustion signals;
- AgentLoop context rebuild;
- terminal turn hooks.

Exit criteria:

- all current failover continuity tests pass under the new service;
- saves with fewer than two enabled configured candidates are rejected without
  replacing the last valid definition;
- runtime child drift can reduce a valid router to one eligible candidate with a
  degraded event, while zero eligible candidates exhaust safely;
- no healthy turn rotates mid-turn;
- force stop never changes health or next-turn correctness.

### WP6: Usage, events, and diagnostics

Deliverables:

- logical/physical usage dimensions;
- route metrics and conversation events;
- Health and Explain service actions;
- retention.

Exit criteria:

- cost remains frozen and non-duplicated;
- failed attempts are observable without leaking errors.

### WP7: UI

Deliverables:

- candidate editor;
- health panel;
- decision explanation;
- i18n;
- OmniRoute forms/actions if not already shipped with WP1.

Exit criteria:

- router setup requires no hand-authored JSON;
- all backend validation remains enforced.

### WP8: One-shot migration and removal

Deliverables:

- scope-aware migration with global fail-fast and user/conversation quarantine;
- disabled owner-visible migration placeholders and durable safe report;
- backup and rollback documentation;
- replacement of service filters;
- deletion of old failover runtime and tests;
- updated docs and website.

Exit criteria:

- representative global/user/conversation definitions migrate;
- an invalid global definition aborts before service connection;
- invalid user/conversation definitions are atomically quarantined and disabled
  without preventing unrelated tenants from starting;
- quarantine failure aborts without losing or partially rewriting the original;
- restart is idempotent;
- repository contains no active <code>llmFailover</code> runtime reference.

### WP9: Health-weighted policy

Begin only after production telemetry from deterministic policies is available.

Deliverables:

- four-factor normalized scoring;
- configuration and explanations;
- shadow mode comparing decisions without applying them;
- rollout guard.

Exit criteria:

- shadow evidence shows stable, explainable selection;
- malformed/missing telemetry cannot dominate;
- policy can be disabled instantly.

## 26. Rollout

1. Ship WP1 independently behind explicit provider selection.
2. Ship typed errors and routing store without changing default routing.
3. Run native router policies in shadow mode for existing ordered failover
   services.
4. Compare proposed versus actual first candidate and record differences.
5. Enable <code>ordered</code> <code>llmRouter</code> after parity.
6. Migrate and remove <code>llmFailover</code>.
7. Enable round robin and sticky round robin as opt-in.
8. Collect health and latency evidence.
9. Consider health-weighted routing only after shadow validation.

There is no automatic opt-in to OmniRoute and no automatic adaptive-policy
upgrade.

## 27. Acceptance criteria

The project is complete when all of the following are true:

- OmniRoute can be selected as an explicit <code>llmConnection</code> provider;
- every supported OmniRoute wire feature is verified against and documented with
  an exact upstream commit SHA;
- OmniRoute streaming, tool calls, controls, and metadata are covered locally;
- <code>llmRouter</code> is the only native multi-service failover type;
- old definitions migrate one-shot;
- every route plan has UUID, timestamp, turn identity, and immutable candidates;
- every snapshotted service reference carries a deterministic, secret-free
  definition revision and cannot be captured by later scope shadowing;
- a healthy turn never changes physical child;
- classified failure cold-starts the next child without duplicate work;
- cancellation and force stop do not affect health;
- one credential remains shareable without capacity rejection;
- round robin advances per turn, not per LLM call;
- sticky counts full successful turns only;
- cooldowns honor valid retry hints and recover;
- CLI-backed providers preserve typed control flow, never fabricate HTTP
  metadata, and classify ambiguous diagnostics as <code>unknown</code>;
- model failure does not disable unrelated models;
- configuration saves enforce two enabled candidates while runtime drift may
  continue safely with one eligible candidate;
- usage shows logical and physical service without double cost;
- every decision is explainable;
- routing state survives restart;
- invalid user/conversation migrations are quarantined without blocking other
  tenants, while unsafe global migration remains fail-fast;
- no secret enters events, metrics, or health tables;
- all affected docs and website pages are updated;
- targeted tests and the full CI matrix are green.

## 28. Risks and mitigations

### 28.1 Double routing

Risk: PawFlow and OmniRoute both adaptively choose targets.

Mitigation: explicit ownership modes, warnings for adaptive-over-adaptive
topologies, and opaque treatment of gateway internals.

### 28.2 Context and cache churn

Risk: round robin destroys provider-native context or prompt-cache value.

Mitigation: turn-level selection, sticky round robin, and no mid-turn rotation.

### 28.3 False health penalties

Risk: local timeout, user error, or context overflow disables a healthy service.

Mitigation: typed origins, conservative classification, and control-flow
exclusions.

### 28.4 Stale persisted health

Risk: a configuration change leaves a candidate locked, or a same-named service
in another scope captures an old plan.

Mitigation: include scope and config/model revision in health handling, clear
applicable locks on a material child revision, and snapshot exact resolved
service references in every route plan.

### 28.5 Credential identity mismatch

Risk: array index health follows the wrong credential after reorder.

Mitigation: V1 service/model health only; stable UUID prerequisite for
credential-level state.

### 28.6 SQLite contention

Risk: routing events add hot-path write contention.

Mitigation: WAL, short transactions, bounded event batching, and measurements
before adding distributed infrastructure.

### 28.7 Misleading gateway cost

Risk: OmniRoute and PawFlow cost figures are summed twice.

Mitigation: PawFlow cost remains authoritative; gateway cost is diagnostic only.

### 28.8 UI complexity

Risk: a large router form becomes another raw JSON editor.

Mitigation: ordered candidate widget, conditional fields, and a separate health
view.

### 28.9 Monolith growth

Risk: copying OmniRoute's broad fallback engine creates an oversized service
module.

Mitigation: narrow modules, deterministic V1 policies, and explicit deferred
features.

## 29. Ideas deliberately not copied from OmniRoute

- the full strategy catalog;
- the approximately 84 KB account-fallback module structure;
- automatic free-tier aggregation;
- web-cookie and unofficial provider onboarding;
- broad fail-open budget behavior;
- a fourteen-factor score before local telemetry exists;
- internal fair-share blocking for shared credentials;
- per-key state without stable identities;
- provider-wide lockouts for model-specific errors;
- undocumented admin API integration.

The useful concepts are separation of policy and transport, exact-model lockouts,
bounded state, recovery probes, response validation, session pin recovery, and
explainable selection.

## 30. Resolved design questions

### Should PawFlow add an OmniRoute provider?

Yes. It is a low-risk explicit gateway integration and can ship before the
native router.

### Is the generic OpenAI provider enough?

It is enough for a smoke test and basic <code>model=auto</code> calls. It is not
enough for typed configuration, request controls, model discovery, response
metadata, and correct diagnostics.

### Should OmniRoute be a new service type?

No. It is a provider/dialect of <code>llmConnection</code>.

### Should the native router call OmniRoute APIs to copy its health state?

No. Treat OmniRoute as an opaque gateway boundary.

### Should <code>llmFailover</code> remain alongside <code>llmRouter</code>?

No. Migrate once, preserve ordered semantics as a strategy, and delete the old
runtime.

### Should the router rotate credentials?

No. Child connections own credentials. The router rotates physical service
candidates.

### Should round robin operate per request?

No. It operates per logical turn.

### Should health weighting ship immediately?

No. Deterministic policies and trustworthy typed telemetry come first.

### Should routing health live in the usage ledger?

No. Use a dedicated operational store and correlate it with usage events.

## 31. References

PawFlow:

- <code>services/llm_connection.py</code>
- <code>services/llm_failover.py</code>
- <code>services/llm_credential_oauth.py</code>
- <code>core/llm_client.py</code>
- <code>core/usage_ledger.py</code>
- <code>docs/02_REFERENCE_TASKS_SERVICES.md</code>
- <code>docs/llm_providers.md</code>

OmniRoute:

- https://github.com/diegosouzapw/OmniRoute
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/docs/routing/AUTO-COMBO.md
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/docs/routing/QUOTA_SHARE.md
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/open-sse/services/autoCombo/scoring.ts
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/open-sse/services/autoCombo/selfHealing.ts
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/open-sse/services/combo/failureTracker.ts
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/open-sse/services/accountFallback/exactModelLock.ts
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/src/domain/omnirouteResponseMeta.ts
- https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.50/LICENSE
