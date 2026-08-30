# Crypto Trader Agent Implementation Plan

Status: reviewed plan, not yet implemented
Date: 2026-08-29
Owner: PawFlow core and first-party workflow agents
Reviewed baseline: PawFlow 1.0.0-beta.251 (809208e04ab4d7ee628f54829b15e0680c39e725)
Implementation gate: this document must be accepted before WP0 starts

## 1. Review outcome

The original paper-first direction was sound, but the plan was not safe or
complete enough to implement. This revision makes the following blocking
corrections:

1. adds a governed learn mode that accumulates Telegram calls, people,
   narratives, token observations, and outcomes without placing orders;
2. separates immutable evidence, derived analysis, paper decisions, and real
   execution so learned data cannot silently change live behavior;
3. replaces the nonexistent browser_console_eval contract with a dedicated
   fixed-script social-browser surface derived from the shipped
   browser_console_extract pattern;
4. replaces the impossible cronTrigger(30s) design with a persistent,
   checkpointed interval source compatible with ContinuousFlowExecutor;
5. removes promotional return targets and all language that could be read as a
   guarantee against rugs, honeypots, loss, or drawdown;
6. adds stable identity, provenance, edit/delete history, deduplication,
   temporal validity, data-quality states, and no-lookahead rules;
7. defines exact, expiring, single-use trade authorization instead of treating
   PawFlow's generic hard_confirm label as a per-trade control;
8. separates an immediate execution kill switch from any asset-withdrawal
   operation;
9. makes public-API coverage, rate limits, freshness, and chain-specific fields
   explicit capabilities rather than assumptions;
10. adds reproducibility, promotion, rollback, privacy, and operational gates.

No production code, package, task, service, handler, migration, or dependency is
implemented by this review.

## 2. Goal and operating modes

Build a durable crypto research and trading system that:

- reads explicitly approved Telegram groups and X searches through the user's
  own logged-in, read-only Chromium profile;
- records an auditable history of calls, source identities, narratives,
  contracts, captures, edits, deletions, enrichment results, and later market
  outcomes;
- performs a global analysis of which people, groups, narratives, and call
  propagation patterns have historically been useful;
- exposes that analysis to a paper engine only through versioned, reproducible
  feature snapshots;
- permits real spot execution only after a separate security review and a
  controlled promotion of one frozen strategy revision;
- remains useful as an analyst, alert, reporting, and dashboard system even
  when execution is disabled.

The objective is to estimate risk-adjusted expectancy while constraining loss.
It is not to reproduce a promotional capital trajectory. No design can promise
that a rug, honeypot, provider error, chain reorganization, key compromise, or
market loss will never occur.

### 2.1 Independent state axes

Collection and execution are independent. A circuit breaker must stop execution
without destroying the learning record.

| Axis | State | Meaning |
|---|---|---|
| ingestion_state | enabled | Approved sources continue to be captured and enriched. |
| ingestion_state | paused | No new social capture; existing evidence remains queryable. |
| execution_mode | learn | Analyze and label only. No order, quote acceptance, signing, or simulated position is allowed. |
| execution_mode | paper | Consume an explicitly promoted analysis snapshot and simulate orders/fills. |
| execution_mode | real | Consume one frozen, approved strategy revision through the real execution gateway. |
| execution_mode | paused | No new order may be created; reconciliation and risk reporting continue. |

Learn remains active underneath paper and real in the sense that new evidence
may continue to accumulate. New evidence cannot mutate the frozen strategy or
feature snapshot used by an open paper or real decision.

### 2.2 Fail-closed state transitions

Allowed transitions are:

- learn to paper: explicit promotion record after data-quality and replay gates;
- paper to real: separate promotion procedure in WP10;
- real to paper or paused: automatic on any real circuit breaker, authority
  expiry, wallet mismatch, data-quality failure, or strategy revision change;
- any mode to paused: immediate and non-erroring;
- paused to real: never automatic.

Every transition has a UUID, UTC creation timestamp, actor, reason, previous
state, new state, configuration digest, and authorization reference.

## 3. External source review

The requested X article, How to Turn Grok Bot Into Your Own Bloomberg Terminal,
is useful as a product decomposition, not as evidence of trading performance or
system capacity.

Retained ideas:

- acquisition/monitoring, analysis, alerts, reports, dashboard, and
  conversational cockpit are distinct layers;
- continuously running specialists can feed one coherent workspace;
- the terminal remains valuable when the human makes the final execution
  decision;
- source links and an audit trail matter more than a polished summary.

Rejected or treated as unverified:

- 300 parallel agents, one-million-token memory, five-second dashboards,
  30-second universal coverage, build-in-a-week claims, and quoted annual costs
  are product claims, not PawFlow acceptance criteria;
- a shared JSON file is not a safe source of truth for concurrent,
  reproducible, privacy-sensitive trading evidence;
- long model context is not durable memory and cannot replace a database;
- HIGH CONVICTION must never route directly to execution;
- broad web coverage cannot replace licensed feeds or prove completeness;
- named conversational agents may present views, but they do not share signing
  authority or bypass the deterministic risk gateway.

PawFlow adopts the layered terminal shape while keeping the evidence store,
analysis snapshots, and execution ledger as separate authoritative systems.

## 4. Validated PawFlow baseline

| Existing primitive | Live contract on beta.251 | Decision for this plan |
|---|---|---|
| ContinuousFlowExecutor | Queue/backpressure scheduler with persistent sources and checkpoints. | Reuse it for durable lanes and recovery. |
| cronTrigger | Five-field, minute-granularity persistent source; it suppresses duplicate fires in the same minute. | Use for daily/minute jobs only, never for a 30-second lane. |
| FlowExecutionAuthority | Freezes task capability effects, services, relays, and authorization lineage for a run. | Reuse as the outer workflow authority, not as an economic order approval. |
| Workflow task safety | Re-authorizes each attempt with declared effects, idempotence, target fingerprints, service snapshots, and relay/path bounds. | Require it for every first-party trader workflow task. |
| Tool authorization | Hard and policy-gate grants are exact-call only; generic hard_confirm currently covers capability-widening tools such as store_secret, not trades. | Add a trade-specific exact-call contract and map it through the same authorization machinery. |
| SecretResolver and secret bindings | Resolve authorized secrets server-side with scope and agent policy. | Wallet keys are referenced by binding name and materialized only inside the wallet service. |
| browser_console_extract | Fixed server-owned scripts, target/origin binding, bounded output, and workflow-confined writes. Current scripts cover website inventory only and use a run-isolated profile. | Extend the pattern with a separate, opt-in social profile surface; do not expose arbitrary evaluation. |
| Website Creator phase tools | Explicit per-phase allowlists and capability ceilings. | Reuse the pattern for collect, normalize, enrich, review, paper, and real phases. |
| http_fetch public_only | Revalidates public origins, including redirects. | Reuse for public APIs with additional provider, quota, schema, and freshness controls. |
| pandas and numpy | Core dependencies. | Reuse for analysis. |
| scikit-learn, web3, solana, solders | Not current project dependencies. | Add only in the work package that needs them; wallet libraries must not burden learn/paper installs. |

The Website Creator 1.1 extraction work is therefore a design foundation, not
a drop-in Telegram/X extractor. Its security invariants must be preserved while
the profile lifecycle and shipped scripts are extended deliberately.

## 5. Reviewed decisions

| ID | Decision |
|---|---|
| D1 | Social collection uses an explicitly selected persistent Chromium profile because the approved Telegram groups belong to the user's real account. Profile data is never put in a cache directory and is never deleted by workflow cleanup. |
| D2 | The relay owns a private CDP pipe. The model cannot submit JavaScript, raw CDP commands, selectors, chat text, or arbitrary URLs. Only versioned server-shipped scripts/actions for approved Telegram/X origins are accepted. |
| D3 | Social access is read-only. Allowed actions are bounded navigation, selection of a configured source, scrolling, and extraction. Sending, reacting, liking, following, posting, downloading arbitrary files, or opening unapproved origins is denied structurally. |
| D4 | Learn is the first executable milestone. It never creates either paper or real orders. |
| D5 | SQLite WAL is the authoritative metadata and ledger store. Large or sensitive raw captures are encrypted artifacts referenced by digest. Generic MemoryStore and Knowledge Graph entries are optional projections, never execution inputs. |
| D6 | Every persisted entity and event has a UUID and UTC creation timestamp. Source timestamps are stored separately and are never substituted for creation time. |
| D7 | Stable source identity is required. A missing Telegram peer/group/message identity produces quarantined evidence; display-name-plus-group is not an identity fallback. |
| D8 | Contracts are canonicalized by chain plus address. Tickers and names are aliases only and can never authorize a trade. |
| D9 | All features used for a decision are computed as-of the decision time. Later edits, labels, prices, entity merges, and narrative assignments cannot rewrite an old feature snapshot. |
| D10 | Public providers are untrusted observations. Each chain has an explicit capability matrix and required evidence set. Missing, stale, contradictory, unsupported, or schema-drifted evidence means observe, watch, or reject. |
| D11 | No inaccessible market or safety field is scraped through Chromium as an API workaround. Browser collection is limited to the approved social sources. |
| D12 | Risk disqualification precedes scoring, but rules are chain- and market-structure-specific. No universal LP-lock, holder concentration, mint-authority, or tax rule is assumed valid on every chain or pool type. |
| D13 | Paper and real engines share order, quote, fill, position, ledger, and reconciliation contracts, but never share side-effect implementations. |
| D14 | Real signing runs in a dedicated WalletExecutionService. The agent, prompts, FlowFiles, transcripts, generic tools, and analyst processes never receive key material or an unsigned transaction that can be mutated after approval. |
| D15 | Each real order needs an exact, expiring, single-use approval until a later, separately reviewed policy explicitly permits bounded automation. Approval binds all economic and technical fields plus configuration and strategy digests. |
| D16 | The kill switch immediately freezes new execution and cancels or abandons safe-to-cancel pending work. It is not a wallet sweep. Emergency withdrawal is a separate, exact-target, confirmed operation. |
| D17 | No automatic cross-chain bridge is in v1. Capital and risk accounting are per-chain plus consolidated reporting. |
| D18 | Goals drive reports only. Risk constraints and authority always dominate target pacing. |
| D19 | Backfill is never allowed to manufacture a historical call. Unresolved ticker-only evidence remains unresolved or censored and cannot receive retroactive caller credit. |
| D20 | A model or rule revision must be explicitly promoted. New learning data cannot silently modify live weights, thresholds, narratives, identity links, or execution behavior. |

## 6. Architecture

    APPROVED SOCIAL SOURCES
      Telegram Web persistent profile       X persistent profile
                                             /
                     fixed scripts and bounded actions
                               |
                     immutable evidence ledger
                               |
          identity resolution / call claims / narrative observations
                               |
             chain resolution / provider evidence / market labels
                               |
             versioned as-of feature and statistics snapshots
                         /                 |
                  reports/dashboard       promoted read-only view
                                            |
                              deterministic risk and sizing
                                   /                 |
                              paper engine       real gateway
                                                   |
                                exact approval + wallet service
                                                   |
                                          chain adapter

Authoritative components:

- trader evidence store: immutable capture lineage and privacy controls;
- trader analytical store: versioned entities, narratives, labels, features,
  statistics, model versions, and promotions;
- execution ledger: decisions, approvals, quotes, orders, fills, positions,
  balances, reconciliation, and circuit breakers;
- artifact store: encrypted raw captures, exported datasets, reports, model
  artifacts, and replay manifests;
- conversational and dashboard views: read models only.

The LLM is permitted only for bounded annotation proposals and narrative
summaries. Deterministic code validates every output. The LLM never resolves a
wallet identity, changes a strategy revision, sizes a real order, approves an
order, signs, submits, reconciles, resets a circuit breaker, or promotes a
model.

## 7. Data and provenance contracts

### 7.1 Common record envelope

Every row and append-only event contains:

- id: UUID generated at creation;
- created_at: timezone-aware UTC timestamp generated at creation;
- schema_version;
- actor_type and actor_id;
- source_record_ids where applicable;
- config_revision and code/extractor version where applicable;
- valid_from and valid_to for temporal facts;
- supersedes_id or invalidates_id for corrections;
- a canonical content digest for replay and tamper evidence.

No table relies on a mutable display name, ticker, wall-clock ordering, or
implicit latest row as its identity.

### 7.2 Core store families

The implementation may split these into normalized tables, but it must preserve
the following contracts.

Evidence and source scope:

- source_profiles: user-approved relay/profile binding and revocation state;
- source_scopes: platform, stable group/search identity, approved origin, and
  collection bounds;
- capture_batches: checkpoint, extractor/schema versions, start/end times,
  completeness, failures, and output artifact digest;
- social_events: append-only create/edit/delete observations with platform
  event identity, platform timestamp, observed timestamp, encrypted raw
  artifact reference, normalized preview digest, and capture provenance;
- source_checkpoints: committed only after evidence is durably stored.

Identity and entity resolution:

- principals: platform-scoped people or channels;
- principal_aliases: time-bounded display names and handles;
- identity_links: explicit, evidenced links between platform principals;
  uncertain links remain separate and require manual review;
- assets: canonical chain plus contract identity;
- asset_aliases: time-bounded tickers/names that never replace the contract;
- resolution_candidates: ambiguous evidence, candidates, reasons, confidence,
  and review state.

Calls and narratives:

- call_claims: principal, source event, asset, call time, claimed metrics,
  direction/type, extraction revision, and resolution state;
- call_clusters: copy/paste or syndicated-call groups so repeated text does not
  masquerade as independent confirmation;
- narratives: versioned concepts with aliases and lifecycle state;
- narrative_observations: evidence that a source event mentions a narrative;
- asset_narrative_memberships: time-bounded, evidenced associations;
- annotation_reviews: candidate, accepted, rejected, superseded, or revoked,
  with reviewer and rationale.

Market and safety evidence:

- provider_capabilities: provider, chain, endpoint, schema version, quota,
  required fields, semantics, last contract test, and enabled state;
- enrichment_snapshots: raw digest, provider time, received time, block
  height/hash where relevant, freshness, quality flags, and normalized fields;
- price_observations: pool identity, quote asset, price, liquidity, volume,
  supply basis, and provenance;
- outcome_labels: horizon, as-of rule, price source, censored/missing state, and
  label revision;
- provider_disagreements and chain reorganization corrections are append-only.

Analysis and promotion:

- feature_snapshots: immutable as-of inputs used by one decision;
- caller_stats_snapshots: window, cohort, shrinkage parameters, sample count,
  uncertainty, and cutoff time;
- narrative_stats_snapshots: propagation, reach, novelty, concentration,
  outcome distribution, and cutoff time;
- strategy_versions: frozen rules, thresholds, feature schema, code version,
  training data manifest, and digest;
- model_versions: algorithm, hyperparameters, artifact digest, calibration,
  validation slices, limitations, and state;
- promotions: candidate to paper, paper to shadow-real, real-canary, active,
  revoked, with authorization and evidence.

Execution:

- trade_decisions: mode, strategy/config/feature digests, deterministic reasons,
  risk decision, and proposed order;
- order_authorizations: exact bound proposal, expiry, nonce, one-use state, and
  PawFlow authorization reference;
- quotes, orders, submissions, fills, fees, positions, cash movements, and
  portfolio snapshots are normalized records, not opaque JSON inside one trade
  row;
- reconciliation_events record provider/chain truth and discrepancies;
- circuit_breaker_events are append-only; current breaker state is a derived
  projection.

### 7.3 Privacy and retention

Telegram and X captures can contain personal and sensitive data. The plan
requires:

- explicit source-scope consent and a visible inventory of monitored groups and
  searches;
- encryption at rest for raw social artifacts using a data-encryption key
  referenced through PawFlow secret bindings;
- least-privilege filesystem paths and no raw content in logs, prompts,
  dashboards, generic memory, or error events;
- configurable raw-retention and derived-retention periods;
- source/profile revocation, export, and deletion workflows with tombstones so
  training manifests can report that evidence became unavailable;
- reports that use stable pseudonymous principal IDs unless the user explicitly
  requests current display names.

## 8. Learning semantics

### 8.1 What learn collects

Learn accumulates:

- every observed call candidate, including rejected, ambiguous, duplicated, and
  unresolved candidates;
- people/channels and time-bounded aliases;
- who repeated whom, in which group, and how quickly;
- narratives discovered in messages and their evolution over time;
- token/pool safety and market evidence available at each observation time;
- market outcomes at declared horizons, including missing and censored labels;
- provider failures, selector failures, data gaps, and disagreements.

It produces:

- caller and group reliability with uncertainty, not raw leaderboard ratios;
- narrative lifecycle and propagation graphs;
- correlated/syndicated caller clusters;
- timing, liquidity, chain, regime, and market-cap-conditioned performance;
- explicit data-quality and coverage reports;
- immutable feature datasets that can be replayed from a cutoff.

### 8.2 Identity and deduplication

- Telegram peer, group, and message IDs are platform-scoped identifiers.
- X account and post IDs are platform-scoped identifiers.
- Display names, usernames, tickers, and copied text are aliases or evidence.
- Cross-group or cross-platform identity merges require evidence and are
  versioned. Fuzzy-name similarity alone cannot merge people.
- The same source event captured twice is one event with multiple capture
  observations.
- Edited or deleted messages append a new observation and invalidate derived
  facts prospectively; history is not overwritten.
- Multiple calls for the same contract remain distinct claims, while exact or
  near-copy propagation is clustered to avoid false independent confirmation.

### 8.3 Temporal and anti-leakage rules

- Event time, observation time, provider time, block time, label time, and
  record creation time are distinct fields.
- A decision snapshot may use only facts whose observed_at is at or before its
  cutoff and whose validity interval includes that cutoff.
- Historical enrichment must select a pool that existed at the historical
  cutoff; today's most liquid pool cannot be substituted silently.
- Missing historical data is censored, not imputed as a win or loss.
- A contract learned later cannot retroactively convert ticker chatter into an
  attributable historical call.
- Dataset splits are walk-forward, grouped by asset/call cluster, and purged or
  embargoed so the same token episode cannot leak across train and test.
- Hyperparameter and rule tuning never sees the locked final evaluation slice.
- Paper fills and real fills are execution labels; they do not replace
  independent market-outcome labels.

### 8.4 Promotion boundary

The learn store exposes a candidate analytical view. Paper and real consumers
may read only a named promoted snapshot. Promotion freezes:

- source cutoff and dataset manifest;
- entity and narrative revisions;
- feature schema and normalization;
- chain capability matrix;
- scoring/risk configuration;
- model artifact and calibration;
- code version and dependency lock.

Any change creates a new candidate revision. It cannot mutate an already
promoted revision or an open position's decision record.

## 9. Provider and chain contracts

### 9.1 Capability matrix

Each enabled chain must declare which provider supplies each required field,
the field semantics, maximum age, quota, retry budget, and fallback policy.
Contract tests run against recorded and live fixtures before a chain becomes
tradeable.

The initial policy is:

- Ethereum: learn/observe first; paper only after the full evidence contract
  passes;
- Solana: learn/observe first; paper only after the full evidence contract
  passes;
- Robinhood Chain: observe only despite current GoPlus documentation claiming
  Token Security API support for chain ID 4663; promotion requires independent
  contract fixtures and market/execution coverage;
- every other chain: unsupported unless added explicitly.

GoPlus is one risk signal, not proof of safety. DexScreener and GeckoTerminal
are market observations, not security oracles. Etherscan and public RPC data
must be bound to the intended chain ID and checked for freshness/finality.

### 9.2 Current documented constraints to encode

As reviewed on 2026-08-29:

- GeckoTerminal documents 30 public calls per minute; OHLCV is pool-address
  based, and market_cap_usd may be null when supply is unverified;
- DexScreener documents endpoint-specific limits of 60 or 300 requests per
  minute and returns nullable market fields;
- Etherscan documents a free limit of 3 calls per second and 100,000 calls per
  day on most supported chains;
- GoPlus adds fields and chains over time, so adapters must pin and validate
  schema semantics rather than accepting unknown fields silently.

Quotas are global budgets shared by backfill, live learning, paper, reports,
and reconciliation. Backfill cannot starve real risk or reconciliation calls.

### 9.3 Safety evidence

A risk rule records supported, unsupported, pass, fail, stale, and conflicting
states. Unsupported is never equivalent to pass.

Examples requiring chain-specific semantics include:

- honeypot or transfer restrictions;
- owner, mint, freeze, upgrade, proxy, or pause authority;
- buy/sell/pool fees;
- liquidity ownership, lock, burn, concentration, and pool type;
- deployer and holder concentration;
- token supply and decimal consistency;
- pair age, quote asset, route depth, and executable liquidity;
- quote/simulation consistency and chain finality.

A reverse quote or transaction simulation is necessary evidence but does not
prove sellability. Any real canary round trip is itself a trade and must pass
normal risk and approval controls.

## 10. Work packages

### WP0 — Threat model, contracts, package boundary, and store

Deliver:

- an explicit threat model covering malicious social content, poisoned token
  metadata, selector drift, provider compromise, replay, approval reuse, nonce
  races, quote substitution, key exposure, chain reorganization, and database
  corruption;
- a first-party package boundary so learn/paper does not install wallet
  dependencies;
- strict contract models for configuration, common record envelopes, source
  scopes, provider capabilities, strategy versions, decisions, approvals,
  orders, fills, and circuit breakers;
- normalized SQLite WAL stores with migrations, foreign keys, transactions,
  busy handling, backup/restore, integrity checks, and encrypted raw artifacts;
- no implicit configuration fallbacks: required limits, chain IDs, wallet
  bindings, source scopes, time zones, and retention settings must be present.

Dependencies:

- reuse pandas and numpy;
- defer scikit-learn to WP9;
- defer web3, solana, and solders to WP10;
- pin every new dependency and document its license and security surface.

Tests:

- schema/migration/concurrency/crash recovery;
- UUID and UTC timestamp creation on every record;
- append-only correction semantics;
- encryption, redaction, retention, export, and deletion;
- deterministic canonical digests and replay manifests.

### WP1 — Persistent social browser and fixed extractors

Deliver a dedicated social-browser service derived from the fixed-script
Website Creator implementation:

- explicit user selection of relay, persistent profile alias, Telegram groups,
  X searches/accounts, origins, lookback, cadence, and retention;
- relay-owned private CDP pipe with no debug TCP port;
- persistent profile path outside cache and outside run cleanup;
- origin-bound, target-bound, profile-bound, versioned fixed actions;
- shipped scripts such as telegram_messages_v1 and x_posts_v1, each with a
  closed options schema and output schema;
- bounded navigation and scrolling state machines;
- confined output paths, byte/item/time budgets, checkpoints, and hashes;
- selector/schema mismatch stops that source and raises a privacy-safe event;
- no display-name identity fallback and no general DOM/evaluation tool.

Tests:

- fake CDP transports and recorded DOM fixtures;
- target/profile/origin mismatch, unapproved navigation, unknown script/action,
  write attempts, timeout, output cap, chunk order, and disconnect;
- Telegram message create/edit/delete and virtualized scrolling;
- X post/search capture and explicitly sampled, not global, volume metrics;
- profile persistence across restart and survival of cache cleanup;
- source revocation and login-expiry behavior.

### WP2 — Evidence ingestion, identity, calls, and narratives

Deliver deterministic ingestion before any scoring:

1. validate and commit the capture batch;
2. append source observations;
3. resolve stable platform identities;
4. detect address/ticker candidates;
5. resolve chain plus contract without using ticker as authority;
6. cluster duplicate/syndicated calls;
7. propose narratives and entity links;
8. validate or quarantine proposals;
9. commit derived facts with full provenance.

The LLM receives bounded untrusted text and has no tools. It returns a closed
annotation schema. Unknown keys, invalid references, unsupported chains, and
low-confidence identity links are quarantined.

Tests include adversarial prompt content, Unicode/confusable addresses,
ambiguous tickers, copied calls, alias changes, edits/deletions, idempotent
recapture, and cross-platform false-merge prevention.

### WP3 — Enrichment, price tracking, and outcome labels

Deliver chain/provider adapters and a global quota scheduler:

- capability negotiation and schema drift detection;
- public-only HTTP with redirect revalidation;
- request deduplication, conditional caching where valid, backoff, and
  per-provider budgets;
- block/pool/provider provenance;
- snapshots at declared horizons;
- historical pool existence checks;
- missing/censored labels and provider disagreement records;
- priority lanes so reconciliation and risk checks outrank reports/backfill.

Tests use recorded provider contracts, malformed/null responses, rate limits,
timeouts, redirects, stale blocks, reorg corrections, pool substitution, and
quota starvation.

### WP4 — Learn backfill workflow

Implement a durable workflow agent with phases:

1. present source, privacy, retention, chain, and lookback contract;
2. persist explicit user confirmation;
3. capture one bounded Telegram/X batch per task attempt;
4. ingest and checkpoint only after durable commit;
5. resolve identities/calls/narratives;
6. enrich under shared quotas;
7. create outcome labels and as-of snapshots;
8. generate data-quality, coverage, and global-analysis reports;
9. export a replayable dataset manifest.

It reuses workflow run storage, durable waits, phase allowlists, capability
ceilings, authorization lineage, and confined resource roots. A long backfill
is passive and resumable, never an interactive blocking loop.

### WP5 — Analysis, deterministic score, and risk engine

Caller statistics use rolling and regime-aware cohorts with Bayesian shrinkage
and uncertainty. Narrative analysis measures novelty, propagation order,
source concentration, and performance without treating correlated reposts as
independent evidence.

Scoring v1 is deterministic and versioned. Its output is enter_candidate,
watch, or reject; it cannot execute. Features include caller posterior,
evidence quality, liquidity/route depth, stage, narrative state, duplication,
and staleness. Every contribution and disqualification is machine-readable.

Risk runs before sizing and again immediately before order submission. It
covers:

- provider capability, freshness, and disagreement;
- chain-specific safety evidence;
- portfolio NAV including unrealized P&L;
- per-chain and consolidated exposure;
- position, asset, caller-cluster, narrative, and liquidity concentration;
- daily loss, drawdown, consecutive failure, stale quote, reconciliation, and
  source-health breakers;
- explicit quote asset and conversion-rate freshness;
- bounded position size and maximum acceptable price impact.

All risk thresholds are required configuration. No defaults silently authorize
risk.

### WP6 — Live learning lanes and supervision

Add a TraderTickSource persistent source with a validated interval in seconds,
monotonic scheduling, jitter policy, checkpointed last-success time, coalescing,
and backpressure awareness. Do not emulate this with cronTrigger.

Separate lanes:

- Telegram collection: configured 30-60 second interval if the source contract
  and account risk allow it;
- X narrative sampling: slower configured interval;
- provider enrichment: quota-driven;
- price/outcome tracking: horizon-driven;
- reconciliation/risk: highest priority;
- daily reports: cronTrigger at minute granularity.

A watchdog must observe durable heartbeats, queue age, last successful capture,
provider budgets, task errors, and checkpoint age. A stalled or selector-broken
source raises a privacy-safe error event. Restart never bypasses a breaker or
duplicates a committed event.

### WP7 — Paper engine and simulation validation

Define one deterministic execution domain with separate PaperExecutionAdapter:

- quote, preflight, submit, reconcile, and cancel contracts;
- order and fill idempotency;
- pool/route-aware fees, slippage, price impact, latency, partial fills,
  failures, and liquidity caps;
- normalized ledger and portfolio accounting;
- stop and tranched-exit strategies as versioned candidates, not three
  simultaneous positions accidentally counted as capital;
- market-outcome labels separate from simulated execution outcomes.

Simulation parameters must be calibrated from observable quotes and later real
canary evidence, with pessimistic bounds when unknown. Paper results report
sensitivity to costs and missing data.

Paper promotion requires a frozen learn snapshot, replay success, coverage
thresholds, and explicit user approval. There is no universal 50-trade default.

### WP8 — Conversational cockpit, alerts, reports, and dashboard

Implement read models for:

- source health, coverage, and unresolved evidence;
- caller/group/narrative global analysis;
- call propagation and correlated clusters;
- open paper positions, ledger, P&L, NAV, drawdown, and breaker state;
- every decision with source links, as-of inputs, reasons, and strategy digest;
- provider freshness/quotas/disagreements;
- promotion candidates and drift.

The cockpit can propose source/config changes, pause ingestion, pause execution,
or request a promotion workflow. It cannot mutate a promoted revision, reset a
real breaker, approve its own proposal, or invoke signing.

Alerts are evidence-linked and deduplicated. Reports clearly distinguish
captured universe from total market coverage and sampled X metrics from global
metrics. Dashboard files are read models, never the source of truth.

### WP9 — Model v2 and governance

Add scikit-learn only here if logistic regression remains the chosen minimal
model.

Training requires:

- a versioned dataset manifest and as-of feature builder;
- grouped, purged walk-forward validation;
- calibration and uncertainty;
- baseline comparison against deterministic v1;
- performance by chain, regime, liquidity, caller cluster, and narrative;
- cost/slippage sensitivity;
- class imbalance and missingness analysis;
- locked final evaluation data;
- reproducible artifact digest and dependency lock.

Promotion states are candidate, paper, shadow_real, real_canary, active, and
revoked. A model never self-promotes. Drift, schema changes, selector changes,
capability degradation, or performance violations revoke it to paper/learn.

### WP10 — Real execution and paper-to-real procedure

WP10 starts only after a new dedicated security review.

WalletExecutionService requirements:

- exact user, service, chain, chain ID/genesis, wallet, and secret binding;
- secret.use, network.write, and external-side-effect capabilities;
- key material only inside one bounded signing operation;
- transaction construction and simulation inside the service;
- returned approval view is canonical and non-secret;
- signed bytes cannot differ from the approved digest;
- idempotency key, nonce/blockhash ownership, expiry, submission receipt, and
  reconciliation;
- no shell, generic HTTP tool, arbitrary RPC method, or model-controlled
  destination.

Each exact order authorization binds at least:

- user/conversation/agent and wallet service revision;
- chain and network identity;
- asset contract, side, amount, quote asset, recipient, route, and fees;
- minimum received or maximum spent;
- maximum slippage/price impact;
- quote digest and expiry;
- strategy, configuration, feature, risk, and capability-matrix digests;
- order idempotency key and transaction intent digest.

Changing any field retires the approval. Approval is single-use and expires.
The first configured canary trades require exact confirmation. Any later
bounded automation is out of v1 unless separately reviewed and authorized.

Promotion requires:

1. the exact strategy revision has completed its configured paper and
   shadow-real evaluation without post-hoc tuning;
2. statistically justified sample, regime, data-quality, and downside criteria
   are met;
3. pessimistic costs preserve positive expectancy;
4. all risk limits, wallet bindings, withdrawal address, and chain contracts
   are explicit;
5. restore, reconciliation, kill, revocation, and approval-reuse tests pass;
6. a small dedicated hot wallet and explicit canary capital are configured;
7. the user approves the frozen promotion manifest.

The immediate kill switch freezes new submissions, retires unused approvals,
and cancels safe-to-cancel pending orders. Force stop is immediate, is not
reported as an error, and does not contaminate the next loop. Emergency
withdrawal to a preconfigured address is a separate confirmed workflow.

No claim of on-chain position-limit enforcement is made unless an audited
on-chain wallet policy actually implements it. V1 otherwise limits loss by
small hot-wallet funding plus deterministic off-chain controls.

## 11. Sequencing

    WP0 contracts/store
      |
      +--> WP1 social browser
      |      |
      +--> WP2 evidence/identity/narratives
              |
              +--> WP3 enrichment/outcomes
                      |
                      +--> WP4 learn backfill
                      |
                      +--> WP5 analysis/risk
                              |
                              +--> WP6 live learning
                              |
                              +--> WP7 paper
                                      |
                                      +--> WP8 cockpit/reports
                                      |
                                      +--> WP9 governed model
                                              |
                                              +--> WP10 real execution

WP1-WP6 must ship a useful learn-only terminal before paper execution is
enabled. WP7 cannot start real work until replayable learn snapshots exist.
WP10 remains last and requires a fresh approval and security review.

No line-count or one-week estimate is approved. Each package closes only when
its tests, documentation, security checks, and acceptance gates pass.

## 12. Acceptance gates

### G0 — Source and privacy

- fixed-script social surface has no arbitrary evaluation or unapproved action;
- persistent profile survives restart and cache cleanup;
- stable identities, edits/deletes, revocation, retention, and encryption pass;
- selector drift stops capture rather than silently emitting wrong evidence.

### G1 — Learn correctness

- an immutable capture set deterministically reproduces entities, calls,
  narratives, labels, features, and reports;
- no-lookahead and retroactive-credit tests pass;
- ambiguous, missing, censored, duplicated, and conflicting evidence remains
  visible;
- global analysis states its captured-universe coverage.

### G2 — Paper validity

- paper and market labels are separate;
- fee/slippage/latency/liquidity sensitivity is reported;
- order/fill/ledger invariants and idempotency pass;
- strategy and data snapshots are frozen and replayable.

### G3 — Operational safety

- queue/backpressure, quota priority, crash recovery, stale heartbeat, provider
  outage, and chain reorganization tests pass;
- pause/kill is immediate and non-erroring;
- no restart resets a breaker or duplicates an event/order.

### G4 — Execution security

- secrets never appear in prompts, transcripts, logs, FlowFiles, exceptions,
  artifacts, or tool results;
- exact authorization cannot be replayed, widened, or reused;
- quote substitution, transaction mutation, nonce races, wrong chain, wrong
  wallet, expiry, and duplicate submission fail closed;
- reconciliation detects every simulated ambiguity.

### G5 — Real promotion

- the paper/shadow revision equals the real candidate by digest;
- configured statistical, coverage, downside, and cost criteria pass;
- canary capital and hot-wallet limits are explicit;
- user approval is recorded against the exact promotion manifest;
- rollback to paper/paused and emergency procedures are rehearsed.

## 13. Security and policy invariants

- No private key, seed phrase, decrypted key, bearer token, or authenticated
  browser state enters model-visible data.
- No model-authored JavaScript or raw CDP command is accepted.
- Social content and provider responses are untrusted data, never instructions.
- Missing required data denies execution.
- Browser read-only is structural, not prompt-only.
- A ticker or narrative never identifies an asset for execution.
- A score never authorizes an order.
- The risk engine and circuit breakers cannot be bypassed by an agent.
- The execution task declares and receives the minimum capabilities and exact
  service/relay targets needed.
- Every external side effect is idempotent or explicitly non-retryable.
- Database, model, provider, extractor, and service revisions are traceable
  from evidence to decision to fill.
- Learning continues through execution pauses unless ingestion is separately
  paused.
- No paid API is required by v1. If free coverage is insufficient, the chain
  remains observe-only or unsupported; the browser is not an evasion path.
- All implementation actions are asynchronous and must not block UI or HTTP
  workers.

## 14. Explicit non-goals

- guaranteed returns, 400-to-400k targets, or guaranteed rug avoidance;
- automatic trading from an LLM confidence label;
- automatic cross-chain bridging or arbitrage;
- leverage, derivatives, lending, perpetuals, or centralized limit-order books;
- front-running, sandwiching, sniping, or market manipulation;
- posting, reacting, following, messaging, or joining groups on Telegram/X;
- scraping licensed or inaccessible data through a logged-in browser;
- universal market coverage or Bloomberg-equivalent data;
- auto-merging people by display name;
- using generic PawFlow memory or a shared JSON file as the trading source of
  truth;
- automatic model, narrative, identity, threshold, or paper-to-real promotion;
- wallet sweep as a conversational kill-switch side effect;
- real execution on a chain whose safety, quote, signing, submission, and
  reconciliation contracts have not all passed.

## 15. Reviewed sources

External design source:

- https://x.com/RohOnChain/status/2092626965224415557

Live PawFlow contracts reviewed on the beta.251 source tree:

- engine/continuous_executor.py
- tasks/system/cron_trigger.py
- core/flow_run_authorization.py
- core/workflow_task_safety.py
- core/gating_policy.py
- core/tool_authorization_contracts.py
- core/handlers/browser_console_extract.py
- tasks/ai/workflow/website_creator_tasks.py
- docs/WEBSITE_CREATOR_SCALING_PLAN.md
- docs/WEBSITE_CREATOR_WORKFLOW_AGENT.md
- pyproject.toml

Provider documentation reviewed on 2026-08-29:

- https://docs.gopluslabs.io/go/changelog/token-security-api
- https://docs.dexscreener.com/api/reference
- https://apiguide.geckoterminal.com/faq
- https://docs.etherscan.io/set-up-your-api-key
