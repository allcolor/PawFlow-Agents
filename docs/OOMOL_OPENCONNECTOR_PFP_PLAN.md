# OOMOL OpenConnector PFP Integration Plan

Status: proposed implementation plan; no package or runtime deployment is approved by this document.

Audited upstream revision: `oomol-lab/open-connector@5a997e37be87b63a693d19d960dbb5de44c63353`.

Last reviewed: 2026-08-23.

## 1. Decision Summary

Integrate OpenConnector as a signed, separately installable PawFlow Package named
`community.openconnector.pfp`. PawFlow core remains connector-neutral. The package
uses OpenConnector's documented HTTP runtime API as its canonical transport and
exposes stable PawFlow tools backed by a configurable PFP service provider.

The package must not receive provider credentials. Gmail, GitHub, Slack, and other
provider credentials remain inside OpenConnector. PawFlow stores only the
OpenConnector endpoint and a least-privilege OpenConnector runtime token.

OpenConnector's MCP endpoint is useful as an optional direct mode, but it is not the
canonical adapter path because HTTP execution supports idempotency keys while MCP
`execute_action` does not. The PFP tools also give PawFlow stable names, reviewable
schemas, per-call telemetry, and a place to enforce response limits.

OpenConnector is Apache-2.0. The PawFlow adapter should be newly authored without
copying the upstream SDK, catalog, console, icons, or provider implementations. It
may therefore remain under PawFlow's MIT license while preserving upstream
attribution in package metadata and documentation.

## 2. Goals

- Install the integration entirely through a signed `.pfp` package.
- Support OOMOL-hosted and user-operated OpenConnector runtimes.
- Discover apps, connections, Actions, schemas, and agent guides.
- Execute Actions against an explicitly selected connection.
- Preserve OpenConnector's token, action, proxy, and connection restrictions.
- Make side-effecting Action retries safe through HTTP idempotency keys.
- Keep all provider credentials, OAuth refresh tokens, and connection secrets out
  of PawFlow prompts, tool payloads, transcripts, and package files.
- Provide useful errors without leaking upstream response bodies or bearer tokens.
- Support conversation- and user-scoped service instances.
- Keep installation, update, disable, and uninstall behavior deterministic.

## 3. Non-goals

- Reimplementing OpenConnector's provider catalog or Action executors.
- Copying its Web Console into PawFlow.
- Managing provider credentials through agent-callable tools.
- Giving an agent the OpenConnector admin token.
- Exposing the arbitrary provider proxy endpoint in version 1.
- Automatically creating OAuth applications for self-hosted deployments.
- Bundling OpenConnector, Node.js, its Docker image, or its TypeScript SDK.
- Treating provider names, logos, or trademarks as PawFlow assets.
- Silently choosing among multiple configured OpenConnector service instances.

## 4. Audited Upstream Contract

At the audited revision, OpenConnector provides:

- MCP at `POST /mcp` with five discovery-oriented tools;
- HTTP runtime endpoints under `/v1`;
- OpenAPI at `GET /openapi.json`;
- agent guides at `GET /api/actions/:actionId/agent.md`;
- persistent runtime tokens with independent Action, proxy, and connection grants;
- named connections selected by `x-oo-connector-alias`;
- HTTP Action idempotency through `Idempotency-Key`;
- redacted run records identified by `executionId`;
- self-hosted Node/Docker, Cloudflare, Fly.io, and OOMOL-hosted deployment modes.

The audited MCP tools are `list_apps`, `list_connections`, `search_actions`,
`get_action_guide`, and `execute_action`. MCP execution has no idempotency-key
parameter, which is why the adapter uses HTTP for execution.

The package must treat all upstream counts, provider lists, and response additions
as dynamic. It must not embed a snapshot of the provider catalog.

## 5. Package Topology

Proposed source tree:

    packages/community.openconnector.pfpdir/
      pfp.json
      NOTICE
      content/
        runtime/
          provider.py
          http_client.py
          normalize.py
          errors.py
        tools/
          apps.py
          connections.py
          search_actions.py
          action_guide.py
          execute_action.py
        skills/
          openconnector/
            SKILL.md
        ui/
          extension.js
          extension.css
          handlers.py
          i18n.json
        schemas/
          common.json

Proposed package objects:

1. `service_provider:gateway`
   - registers service type `openConnectorGateway`;
   - owns endpoint, runtime token, timeout, TLS, and response-limit configuration;
   - declares only the six operations listed in section 7.

2. Five read/execute tool objects
   - `tool:openconnector_apps`;
   - `tool:openconnector_connections`;
   - `tool:openconnector_search_actions`;
   - `tool:openconnector_action_guide`;
   - `tool:openconnector_execute_action`.

3. `skill:community.openconnector`
   - teaches discovery before execution;
   - requires explicit connection selection when more than one is visible;
   - explains side-effect and idempotency behavior;
   - never contains credentials or fixed provider catalogs.

4. Optional `ui_extension:openconnector`
   - shows service health, version/capability status, and safe connection labels;
   - links to the upstream console for credential/OAuth administration;
   - never renders or receives provider credentials.

The initial signed package should allow selecting these objects independently.
Installing the provider does not create a service instance. The normal PawFlow
Resources form creates the configured instance after install.

## 6. Required Generic PFP Prerequisite

Current package-qualified `pfp.call_service` resolution returns the first visible
matching provider instance. That is unsafe when multiple instances exist.

Before shipping the wrapper tools, extend the generic PFP host-call contract:

- add optional `service_id` to `pfp.call_service` and its host envelope;
- verify that the selected service instance is visible in the caller's scope;
- verify that its installed package/object matches the accepted
  `allowed_services` grant;
- if `service_id` is omitted, succeed only when exactly one matching enabled
  instance exists;
- return a stable ambiguity error when zero or multiple instances match;
- never fall back to a differently scoped or differently packaged service.

This is a generic PFP correction, not OpenConnector-specific core code. It needs
unit tests in the PFP capability broker/runtime host and documentation in
`docs/PFP_PACKAGES.md` and `docs/PFP_DEVELOPER_GUIDE.md`.

Every OpenConnector tool accepts optional `service_id`. The UI and agent skill
should supply it when more than one instance is configured.

## 7. Service Provider Contract

### 7.1 Parameters

The `openConnectorGateway` service type declares:

| Parameter | Type | Required | Sensitive | Default |
| --- | --- | --- | --- | --- |
| `base_url` | string | yes | no | none |
| `runtime_token` | string | yes | yes | none |
| `timeout_seconds` | integer | no | no | 30 |
| `verify_tls` | boolean | no | no | true |
| `max_response_bytes` | integer | no | no | 8 MiB |
| `max_guide_bytes` | integer | no | no | 512 KiB |
| `user_agent` | string | no | no | PawFlow/OpenConnector-PFP |
| `allow_private_endpoint` | boolean | no | no | false |

Rules:

- `base_url` is normalized once and stored without a trailing slash.
- HTTPS is mandatory unless the endpoint resolves to loopback/private space and
  `allow_private_endpoint` was explicitly accepted.
- Userinfo, fragments, non-HTTP schemes, and path traversal are rejected.
- Redirects may not change origin unless an explicit future allowlist permits it.
- `runtime_token` is stored through the normal encrypted service-config path.
- No configuration or error message may include the token.
- `verify_tls=false` is high-risk and must be visible in service review.

### 7.2 Operations

The provider declares exactly:

- `health`
- `list_apps`
- `list_connections`
- `search_actions`
- `get_action_guide`
- `execute_action`

Lifecycle and introspection methods remain blocked by the existing
`pfp.call_service` rules.

## 8. Agent Tool Contracts

### 8.1 `openconnector_apps`

Inputs:

- `service_id` optional;
- `query` optional;
- `limit` optional, bounded to 100;
- `cursor` optional.

Returns only normalized app/provider identifiers, display names, safe connection
labels, and pagination metadata. It never returns credentials or raw upstream
configuration.

### 8.2 `openconnector_connections`

Inputs:

- `service_id` optional;
- `provider` optional.

Returns stable connection ID, safe alias/name, provider ID, and account label
fields already redacted by OpenConnector. The adapter applies a second denylist
for keys containing token, secret, credential, cookie, authorization, or password.

### 8.3 `openconnector_search_actions`

Inputs:

- `service_id` optional;
- `query` required;
- `provider` optional;
- `limit` bounded to 50.

Returns Action ID, title, summary, provider, required scopes, and an abbreviated
input schema. Large schemas are available only through the guide tool.

### 8.4 `openconnector_action_guide`

Inputs:

- `service_id` optional;
- `action_id` required;
- `connection_name` optional.

Returns the upstream guide as bounded Markdown plus normalized metadata. It
rejects unknown Actions and content above `max_guide_bytes`.

### 8.5 `openconnector_execute_action`

Inputs:

- `service_id` optional;
- `action_id` required;
- `input` required object;
- `connection_name` optional;
- `idempotency_key` optional;
- `timeout_seconds` optional within the configured maximum.

Behavior:

- execute through `POST /v1/actions/:actionId`, never MCP;
- set `x-oo-connector-alias` only when a connection was explicitly selected;
- generate a UUID idempotency key for the logical call when the caller omits one;
- reuse that key only for transport retries of the same Action/input/connection;
- never retry a completed upstream response;
- surface `executionId` and `auditPersisted`;
- preserve `403 connection_not_allowed`, `404 unknown_action`,
  `409 idempotency_key_conflict`, and
  `409 idempotency_request_in_progress` as stable typed errors;
- cap request depth and serialized size before dispatch;
- cap and JSON-validate the response before returning it.

The tool description must state that Actions may have external side effects.
PawFlow policy/UI should require confirmation for an Action not explicitly marked
read-only by trusted metadata. Missing metadata is treated as side-effecting.

## 9. Authentication and Secret Boundary

Use a dedicated persistent OpenConnector runtime token, not the bootstrap token
and never the admin token.

Recommended upstream token policy:

- allow only required Actions or provider namespaces;
- block dangerous Actions explicitly;
- keep `allowedProxies` empty in version 1;
- restrict `allowedConnections` to exact stable connection IDs where possible;
- rotate the token without rotating provider credentials;
- create separate tokens for materially different PawFlow users/scopes.

PawFlow must:

- encrypt `runtime_token` at rest;
- redact it from logs, traces, errors, install records, and service summaries;
- inject it only into the Relay-side PFP runtime invocation;
- send it only in the Authorization header;
- strip Authorization before recording diagnostics;
- never expose it to browser JavaScript.

The optional UI handler may use a separately bound admin token only in a future
admin-only package object. It must never share runtime metadata with agent tools.
Version 1 should link users to the upstream console instead.

## 10. HTTP Client and Normalization

Use Python standard-library HTTP facilities or a dependency already present in
the Relay image. Do not vendor the Connector SDK.

The client module must centralize:

- URL normalization and same-origin redirects;
- Authorization and content-type headers;
- JSON depth, byte, and timeout limits;
- deterministic error mapping;
- bounded retry policy;
- cancellation propagation;
- redacted diagnostics;
- response envelope normalization.

Normalize OpenConnector's `success/message/data/meta` envelope into:

    {
      "ok": true,
      "data": {},
      "meta": {
        "execution_id": "...",
        "action_id": "...",
        "audit_persisted": true
      }
    }

Never silently turn a malformed success envelope into an empty success.

## 11. Files and Large Results

Version 1 accepts JSON-only Action inputs and bounded JSON outputs.

A later file-transit phase may add:

- FileStore input copied into the Relay runtime;
- upload to OpenConnector `POST /api/files`;
- replacement of local paths with returned transit URLs;
- download of declared result files into `pfp.context["output_dir"]`;
- `pfp.artifact` results with hash, size, MIME type, and filename;
- cleanup on success, failure, cancellation, and timeout.

Do not expose arbitrary local filesystem paths or arbitrary upstream download
URLs. Every download must be same-origin or explicitly allowlisted and size
bounded.

## 12. Optional Direct MCP Mode

A selectable `mcp_server` object may be added only after PawFlow supports safe
install-time endpoint configuration for package MCP resources.

Direct MCP mode:

- points to `<base_url>/mcp`;
- binds the runtime token into Authorization;
- remains disabled until explicitly assigned to an agent/conversation;
- exposes OpenConnector's five native MCP tools;
- is documented as lacking HTTP idempotency-key execution;
- does not replace the stable PFP tools.

Do not ship a fixed localhost or hosted URL as an implicit fallback. Do not abuse
the secret store to carry a non-secret endpoint merely to template `mcp.json`.

## 13. UI Extension

The optional Resources-panel extension may provide:

- service health and reachability;
- upstream capability/version display;
- safe connection/app counts;
- token-policy warning when proxy grants are non-empty;
- deep links to the OpenConnector console;
- buttons to copy service IDs for agent configuration.

It must not:

- collect provider API keys or OAuth client secrets;
- proxy arbitrary admin requests;
- render upstream HTML;
- load remote scripts, fonts, or provider logos;
- expose raw run request/response bodies without separate review.

## 14. Package Lifecycle

### Install

1. Download the signed PFP after explicit confirmation.
2. Verify registry size/SHA-256, Ed25519 signature, and lock file.
3. Review high-risk runtime objects and accepted service grants.
4. Install selected provider/tools/skill/UI objects.
5. Create an `openConnectorGateway` service instance through Resources.
6. Enter endpoint and runtime token; persist token encrypted.
7. Run health and discovery canaries.
8. Bind tools/skill to selected agents.

### Update

- pin the developer key on first install;
- publish immutable package versions;
- display API/schema and permission diffs;
- preserve service instances and encrypted config;
- skip locally modified resources unless `force`;
- run compatibility canaries before enabling the new runtime;
- support rollback to the prior signed package while preserving config.

### Disable and uninstall

- disabling a service stops new calls without deleting configuration;
- uninstall removes only package objects and content;
- uninstall does not delete PawFlow secrets or OpenConnector data;
- existing OpenConnector connections/tokens remain upstream until the operator
  revokes them;
- the UI must remind the operator to revoke an unused runtime token.

## 15. Implementation Phases

### Phase 0: generic PFP service selection

- add explicit `service_id` to package service calls;
- reject ambiguous implicit resolution;
- add broker/runtime/SDK tests and docs.

### Phase 1: provider and read-only tools

- provider config and HTTP client;
- health, apps, connections, Action search, and guide operations;
- package skill;
- unit tests with a deterministic fake OpenConnector server.

### Phase 2: Action execution

- typed execution tool;
- idempotency-key lifecycle;
- side-effect confirmation metadata;
- stable upstream error mapping;
- cancellation and bounded retries.

### Phase 3: UI and observability

- safe Resources panel;
- redacted call metrics;
- execution ID links/deep links;
- service health diagnostics.

### Phase 4: files and optional MCP

- transit-file bridge;
- artifact downloads;
- configurable direct MCP object after the generic configuration contract exists.

## 16. Proposed Repository Changes

Package-owned files:

- `packages/community.openconnector.pfpdir/pfp.json`
- `packages/community.openconnector.pfpdir/NOTICE`
- `packages/community.openconnector.pfpdir/content/**`
- package tests under `tests/packages/openconnector/`
- registry metadata only when the signed package is ready to publish

Generic prerequisite files, identified again with `project_graph` immediately
before implementation:

- PFP SDK `call_service` API;
- package runtime host-call envelope and service resolver;
- capability broker validation;
- PFP package/runtime docs;
- focused PFP tests.

No OpenConnector-specific code belongs in `core/`, `services/`, `tasks/`, or the
PawFlow server/Relay images.

## 17. Test Matrix

### Unit tests

- endpoint parsing and SSRF protections;
- authorization redaction;
- every response envelope and known error code;
- JSON size/depth bounds;
- same-origin redirect enforcement;
- connection-name handling;
- idempotency key creation/reuse/conflict;
- malformed/oversized guide rejection;
- exact operation allowlist;
- ambiguous service selection rejection;
- cancellation and timeout cleanup.

### Package tests

- manifest validation;
- deterministic build;
- lock/signature verification;
- install-plan permissions and risk;
- secret binding never stored as plaintext;
- selective install/update/uninstall;
- provider rehydration after restart;
- conversation/user scope resolution;
- no copied upstream SDK/catalog/assets.

### Integration tests

Run against a pinned OpenConnector container or commit fixture:

- health;
- no-auth Action discovery and execution;
- token Action allow/block;
- connection allowlist and named connection selection;
- HTTP idempotency replay/conflict/in-progress behavior;
- redacted audit metadata;
- server restart and PFP service rehydration;
- cancellation;
- optional self-hosted TLS.

### Security tests

- token absent from transcript, logs, exceptions, process arguments, and browser;
- admin endpoints unreachable from agent tools;
- provider proxy endpoint unavailable;
- cross-origin redirect rejected;
- private endpoint requires explicit configuration;
- malicious Action ID cannot alter path;
- untrusted upstream JSON cannot request PawFlow tool calls.

## 18. Acceptance Criteria

The integration is ready for marketplace publication only when:

- a fresh user can install the signed PFP and configure one service without core
  OpenConnector code;
- read-only discovery works against hosted and self-hosted deployments;
- an Action can execute against an explicit connection with idempotent retry;
- multiple service instances never resolve by ordering;
- no provider credential or OpenConnector token appears in PawFlow-visible output;
- the agent cannot call admin or arbitrary proxy endpoints;
- restart, update, rollback, disable, and uninstall tests pass;
- all five PawFlow CI gates pass;
- package and user documentation are updated in English;
- the registry entry pins artifact size, SHA-256, and developer key.

## 19. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Dynamic Action catalog changes | Discover at runtime; pin compatibility tests, not catalog data |
| Side-effecting Action retries | HTTP idempotency key; no blind retry after response |
| Wrong account selection | Explicit connection name/ID; no fallback |
| Excessive token scope | Dedicated restricted runtime tokens and warnings |
| Credential leakage | Credentials remain upstream; double redaction |
| SSRF through endpoint config | URL validation, explicit private-endpoint switch, same-origin redirects |
| Large schemas/results | Strict byte/depth limits and later artifact path |
| Multiple instances | Explicit `service_id` and ambiguity errors |
| Upstream contract drift | Audited SHA, compatibility matrix, canary suite |
| Trademark/catalog reuse | Text-only interoperability identifiers; no copied assets |

## 20. Authoritative Sources

- Upstream repository: https://github.com/oomol-lab/open-connector
- Audited commit: https://github.com/oomol-lab/open-connector/commit/5a997e37be87b63a693d19d960dbb5de44c63353
- Runtime API and MCP: https://github.com/oomol-lab/open-connector/blob/main/docs/runtime-api.md
- Configuration: https://github.com/oomol-lab/open-connector/blob/main/docs/configuration.md
- Credentials: https://github.com/oomol-lab/open-connector/blob/main/docs/credentials.md
- Apache-2.0 license: https://github.com/oomol-lab/open-connector/blob/main/LICENSE.txt
- PawFlow package format: `docs/PFP_PACKAGES.md`
- PawFlow package development: `docs/PFP_DEVELOPER_GUIDE.md`
- PawFlow marketplace: `docs/marketplace.md`
