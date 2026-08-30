# Governed Connectors Implementation Plan

Status: planned
Priority: platform P0 after the multi-conversation workspace and effect receipts
Date: 2026-08-30
Owner: PawFlow packages, services, MCP, authorization, and UI

## 1. Outcome

Provide a versioned connector and action manifest that turns SaaS, messaging,
database, and business API integrations into governed PawFlow capabilities.
Every action must declare authentication, secret bindings, effects, egress,
quotas, idempotency, reconciliation, schemas, and redaction before it can become
an agent tool or workflow task.

This is an extension of PFP, MCP, ServiceRegistry, CapabilityMetadata, and normal
tool authorization. It is not a separate connector runtime or a bundled catalog
of unreviewed third-party credentials and actions.

## 2. Verified baseline

PawFlow already has:

- signed and selectively installed PFP packages with provenance;
- MCP server definitions and tool discovery;
- scoped services and explicit secret bindings;
- OAuth providers and linked accounts;
- CapabilityEffect and IdempotencyClass enforcement;
- exact service snapshots inside WorkflowRunContext;
- package install, update, uninstall, and dependency validation;
- user, conversation, and global resource scopes;
- authorization targets and effect ceilings;
- FileStore for durable artifacts.

Current gaps:

- no common action manifest across PFP tasks, MCP tools, and service adapters;
- no mandatory per-action OAuth scope and secret declaration;
- no normalized egress, quota, cost, pagination, webhook, or reconciliation
  contract;
- no registry view that explains why an action is safe, unavailable, or blocked;
- uneven idempotency and redaction metadata between connectors.

## 3. Non-goals

- Do not import an entire upstream connector catalog.
- Do not run arbitrary connector JavaScript in the PawFlow server.
- Do not infer OAuth scopes from prose or provider responses.
- Do not expose secret values in manifests, logs, tool schemas, or UI.
- Do not bypass MCP/PFP signatures, provenance, or install permissions.
- Do not turn a read action into a write action through runtime parameters.
- Do not allow manifests to expand a workflow effect ceiling.
- Do not claim provider support without contract and live qualification tests.

## 4. Architectural decisions

1. ConnectorManifestV1 is package metadata, not executable code.
2. ConnectorActionManifestV1 is the unit exposed as a tool or workflow task.
3. A connector ships through a signed PFP, an MCP server, or a first-party
   built-in package. The manifest records which execution adapter owns it.
4. Manifest validation occurs at package build, install, service bind, tool
   publication, and workflow static validation.
5. Secrets are referenced by binding name only.
6. OAuth grants are exact scopes tied to one linked account and connector
   revision.
7. All network destinations are checked against normalized egress policy.
8. Every effectful action integrates with EffectReceiptV1.
9. Registry data is one indexed projection of installed resources, not another
   installation store.
10. A connector remains unavailable until all required bindings and live health
    checks pass.

## 5. ConnectorManifestV1

Required fields:

- schema_version;
- connector_id, display_name, description, and vendor;
- connector_version and package identity/digest;
- license and source provenance;
- adapter_kind: pfp_task, mcp_tool, service_adapter, or first_party;
- adapter reference and minimum PawFlow version;
- supported scopes;
- authentication profiles;
- secret binding definitions;
- egress policy;
- shared quotas and cost metadata;
- action IDs;
- health-check action;
- webhook declarations when supported;
- documentation and support references;
- deprecation and replacement metadata.

Connector IDs and action IDs are immutable within a major schema version.

## 6. ConnectorActionManifestV1

Each action declares:

### Identity and schemas

- action_id, display_name, description, and version;
- exact input, output, error, and pagination schemas;
- stable schema digest;
- bounded attachment and payload limits;
- deterministic normalization rules.

### Governance

- CapabilityEffect values;
- IdempotencyClass;
- authorization target fields and redaction policy;
- required OAuth scopes;
- required secret bindings;
- allowed egress destinations and methods;
- data classifications read, transmitted, and returned;
- retention and audit policy;
- rate limit, concurrency, cost, and timeout budgets.

### Runtime behavior

- adapter operation;
- sync, async-job, webhook, or streaming response mode;
- provider idempotency key support;
- provider reference extraction;
- verification and reconciliation capabilities;
- retryable and terminal error codes;
- pagination/cursor contract;
- webhook signature and replay policy;
- result normalization and FileStore offload rules.

Unknown fields and undeclared effects are rejected.

## 7. Authentication and secret model

AuthenticationProfileV1 supports API key, OAuth2 authorization code, OAuth2
device code, service account, signed request, and explicit none.

Rules:

- none is allowed only when declared and validated;
- every secret has a stable binding name, type, scope ceiling, and purpose;
- package manifests never include values or environment snapshots;
- OAuth requests use the exact declared scopes and redirect contract;
- refresh credentials stay in the existing encrypted service/secret storage;
- changing account, scopes, or secret binding creates a new effective service
  snapshot;
- missing or broader-than-approved scopes fail closed;
- revocation immediately makes dependent actions unavailable.

The UI may display presence, owner, scope, expiry, and last health result, never
the value.

## 8. Egress, quotas, and cost

EgressPolicyV1 contains normalized HTTPS origins, optional path templates,
methods, redirect policy, DNS/private-network policy, proxy requirement, TLS
requirements, and webhook callback origins.

Runtime checks resolve variables before authorization and compare the final
destination. Redirects are revalidated. User-controlled arbitrary URLs are
forbidden unless the action explicitly declares a URL-input policy and SSRF
guard.

QuotaPolicyV1 defines provider rate windows, local concurrency, daily/monthly
budgets, maximum pages/items, payload bytes, and optional estimated price.
Enforcement is shared and atomic per linked account and connector action.

Provider Retry-After is bounded by the action policy. Quota exhaustion produces a
typed wait or refusal, not an uncontrolled retry loop.

## 9. Compilation and publication

Add a ConnectorManifestCompiler that:

1. validates connector and action schemas;
2. resolves package/MCP/service ownership;
3. verifies secret and OAuth declarations;
4. validates effects and idempotency;
5. compiles action definitions into normal LLMToolDefinition and workflow task
   metadata;
6. attaches an immutable governance snapshot;
7. publishes only available actions to the permitted agent;
8. records stable rejection reasons for every omitted action.

No runtime LLM decides whether an action exists or which effects it has.

MCP tools without a governed manifest remain available only through the current
legacy policy during migration. They cannot claim verified idempotency or
automatic reconciliation.

## 10. Execution path

    agent/workflow request
      -> action schema validation
      -> effective connector/service snapshot
      -> capability/effect authorization
      -> quota and egress reservation
      -> effect receipt preparation
      -> adapter execution
      -> normalized result/evidence
      -> receipt declaration/verification
      -> quota settlement
      -> transcript/UI projection

Reservation and settlement are idempotent. A crash after provider acceptance
uses the receipt reconciler before another attempt.

## 11. Registry and lifecycle

ConnectorRegistry indexes installed, visible manifests by user and conversation.
It reports:

- installed and available revisions;
- package provenance and signature status;
- configured authentication profile;
- missing bindings/scopes;
- health and last qualification;
- action effects and data classifications;
- quota/cost status;
- deprecation and update availability.

Install/update remains owned by manage_package and existing resource actions.
Registry entries cannot install code themselves.

Updates are immutable revisions. A running workflow keeps its exact connector,
action, service, and schema digests. Uninstall refuses while a live run depends
on the revision unless the existing force policy explicitly covers it.

## 12. Webhooks and subscriptions

WebhookManifestV1 declares path ownership, provider signature scheme, replay
window, event schema, subscription lifecycle, verification handshake, and
deduplication key.

Ingress rules:

- authenticate before parsing expensive content;
- preserve provider event ID and receive timestamp;
- enforce body and rate limits;
- deduplicate transactionally;
- map to a conversation/flow only through an explicit binding;
- never accept a manifest-provided arbitrary callback handler;
- reconcile subscription state after restart.

## 13. UI

Add a Connectors resource view with:

- searchable connector cards and exact versions;
- installed/configured/healthy/blocked states;
- authentication and linked-account setup;
- action table with read/write/destructive effects;
- required scopes, egress destinations, data classes, quota, and cost;
- test-connection and revoke controls;
- action availability/rejection reasons;
- package provenance, license, and update information.

Action approval dialogs show the connector, account, target, effect, relevant
fields, and receipt/retry behavior. They never render hidden secret fields.

## 14. Security

- Signed package provenance remains mandatory.
- Connector manifests are untrusted until fully validated.
- Adapter code executes only on its declared runtime surface.
- All inputs are schema-bound and size-limited.
- Egress is deny-by-default and redirect-aware.
- OAuth state, PKCE, nonce, expiry, and account binding are validated.
- Webhook signatures use constant-time verification and replay protection.
- Logs and receipts use manifest-defined allowlists plus platform redaction.
- Cross-user and cross-conversation connector/account access is denied.
- Connector actions cannot call manage_package, modify their own manifest, or
  broaden their effects.
- Provider HTML, error text, and schemas are data, never instructions.

## 15. Migration

1. Add contracts, compiler, and registry without changing publication.
2. Add manifests to first-party reference connectors.
3. Add a compatibility wrapper for existing MCP/PFP actions with explicit
   unverified metadata.
4. Require manifests for new connector packages.
5. Migrate installed connector revisions through package updates.
6. Require manifests for automatic receipt reconciliation.
7. remove the compatibility wrapper in a one-shot breaking release.

No secrets are copied during migration. Existing bindings are referenced after
the user reviews the requested scopes and destinations.

## 16. Work packages

### WP0 — Contracts and validators

Implement connector, action, auth, secret, egress, quota, webhook, and error
contracts with stable diagnostics.

### WP1 — PFP and MCP integration

Add manifest files, package build/inspect/install validation, MCP association,
dependency checks, and immutable digests.

### WP2 — Compiler and publication

Compile governed actions to tool/task metadata and preserve rejection reasons.

### WP3 — Auth and secrets

Bind linked accounts and secrets by explicit names/scopes; add grant, refresh,
revoke, and health flows.

### WP4 — Egress, quota, and receipt integration

Enforce destinations, redirects, budgets, and effect receipts at dispatch.

### WP5 — Registry and APIs

Build the scoped index and read/mutation actions with optimistic generation.

### WP6 — Webhooks and async jobs

Add signed ingress, deduplication, subscription reconciliation, and durable
provider job handling.

### WP7 — UI and author tooling

Add connector cards, action governance details, setup wizard, manifest lint, and
local author validation.

### WP8 — Migration, docs, and rollout

Migrate reference connectors, publish author docs, run qualification suites, and
remove legacy publication on schedule.

## 17. Test matrix

Required tests include:

1. unknown manifest field and duplicate action ID fail;
2. undeclared effect fails static validation;
3. missing secret and OAuth scope keep the action unavailable;
4. secret values never appear in schema, log, receipt, or UI;
5. redirect to a forbidden/private origin is blocked;
6. quota reservation is atomic under concurrency;
7. Retry-After is bounded;
8. effectful action creates a receipt before dispatch;
9. lost provider response enters unknown and reconciles;
10. running workflow remains pinned across connector update;
11. uninstall dependency protection works;
12. webhook signature, replay, size, and dedupe gates work;
13. cross-scope linked-account access fails;
14. package signature and provenance failures block install;
15. registry rejection reasons are deterministic;
16. MCP and PFP adapters produce the same normalized action contract;
17. pagination and FileStore offload obey limits;
18. revoke makes dependent actions unavailable immediately;
19. migration never copies secret values;
20. full package, MCP, workflow, security, and Python CI matrices pass.

## 18. Definition of done

A connector is done only when every published action can answer: who owns the
code, which exact revision runs, what credentials and scopes it needs, where it
can connect, what data and effects it can produce, how it is rate/cost bounded,
how retries are classified, and how an uncertain effect is reconciled.

## 19. Upstream influence and license ledger

The normalized SaaS action/catalog patterns were evaluated from
oomol-lab/open-connector at SHA 10a71c5 under Apache-2.0. Selective reuse requires
Apache attribution and NOTICE review.

PawFlow does not copy the upstream runtime, generated catalog, credentials,
branding, or connector claims wholesale. The manifest, governance, PFP/MCP, ACL,
receipt, and workflow integration are PawFlow-native.
