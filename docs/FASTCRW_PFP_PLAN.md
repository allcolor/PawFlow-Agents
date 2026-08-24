# fastCRW Non-Bundled PFP Integration Plan

Status: proposed implementation plan; no package, external download, or runtime
deployment is approved by this document.

Audited upstream revision: `us/crw@37e9c25b2562b78a3ca22ed542173977291d8dac`.

Last reviewed: 2026-08-23.

## 1. Decision Summary

Integrate fastCRW through a signed PawFlow adapter package named
`community.fastcrw.pfp`, but do not bundle, mirror, vendor, compile, or install
fastCRW itself inside that artifact.

The PFP contains only newly authored PawFlow adapter code and documentation. It
talks over the documented REST API to either:

- the managed fastCRW API;
- an already running user-operated fastCRW service; or
- a fastCRW runtime obtained separately and directly from its upstream publisher
  after a distinct marketplace review/confirmation step.

The fastCRW engine and MCP server are AGPL-3.0. Its Python and TypeScript SDKs are
MIT, but version 1 does not need either SDK: the adapter uses the public REST
contract. The separate-process/API boundary avoids copying AGPL code into
PawFlow or its PFP. It does not remove the operator's obligations for the
fastCRW runtime they choose to run. This plan is an engineering distribution
boundary, not legal advice.

Do not use `npx crw-mcp@latest` from the PFP. It is unpinned executable code,
belongs to the AGPL runtime surface, and would blur the separate-download and
review boundary.

## 2. Goals

- Provide first-class search, scrape, map, crawl, extract, capability, and
  document-parse tools through a signed PFP adapter.
- Keep PawFlow core and official images free of fastCRW code and binaries.
- Support hosted and self-hosted fastCRW through the same service type.
- Discover runtime capabilities instead of assuming hosted/self-hosted parity.
- Normalize documented response-shape differences.
- Handle asynchronous crawl/extract jobs without blocking HTTP workers.
- Preserve large outputs as artifacts rather than oversized JSON.
- Make external runtime acquisition separately visible and separately
  consented to in the marketplace.
- Record upstream version, artifact hash, source URL, and license provenance.
- Allow users to uninstall the adapter without deleting the external runtime or
  its data.

## 3. Non-goals

- Shipping fastCRW, `crw-server`, `crw-mcp`, Rust crates, npm packages, or
  container layers in PawFlow or the PFP.
- Running upstream `curl | sh` installers automatically.
- Mirroring upstream binaries in the PawFlow package registry.
- Claiming that process separation waives AGPL obligations.
- Replacing PawFlow's built-in `fetch` or `web_search` tools.
- Silently routing ordinary PawFlow web calls through fastCRW.
- Providing unrestricted browser automation through `crw-browse`.
- Enabling private-network crawling by default.
- Adding fastCRW-specific code to PawFlow core.

## 4. License and Distribution Boundary

### 4.1 What the PFP may contain

- PawFlow-authored Python adapter code;
- PawFlow-authored schemas, tests, skill instructions, and UI assets;
- links to upstream documentation and source;
- SPDX/provenance metadata;
- no upstream executable or copied implementation code.

The adapter should be MIT-licensed and identify fastCRW only for
interoperability. It must not include upstream logos unless separately
authorized.

### 4.2 What remains external

- fastCRW Rust engine/server;
- `crw-mcp` and `crw-browse`;
- Docker images;
- upstream install scripts;
- local runtime configuration and data;
- any renderer/browser dependencies.

These remain separate works obtained and operated by the user.

### 4.3 Marketplace rule

The PawFlow registry entry for `community.fastcrw` points only to the signed
adapter PFP.

If PawFlow adds marketplace-guided external runtimes, that is a separate record,
not a file inside `pfp.lock.json`:

    {
      "id": "external.fastcrw",
      "kind": "external_runtime",
      "license": "AGPL-3.0-only",
      "source_url": "https://github.com/us/crw",
      "download_origin": "upstream",
      "version": "<pinned release>",
      "artifacts": [
        {
          "platform": "linux-amd64",
          "url": "<upstream release URL>",
          "size": 0,
          "sha256": "<upstream-pinned hash>"
        }
      ]
    }

Required semantics:

- separate UI section and separate `--confirm-external-download` consent;
- exact version, platform, size, SHA-256, source offer, and license shown before
  download;
- bytes fetched directly from the upstream release origin;
- no PawFlow CDN/proxy/cache unless PawFlow intentionally accepts distributor
  obligations;
- no automatic execution of an installer;
- install into a user-controlled external-runtime location, never PFP content;
- inventory and uninstall instructions recorded separately;
- failure to install the external runtime does not corrupt PFP installation;
- PFP uninstall never deletes an external runtime implicitly.

This `external_runtime` contract does not exist in current PFP. Until it is
implemented and reviewed, marketplace installation stops after installing the
adapter and gives the user upstream/manual or existing-service instructions.

## 5. Supported Deployment Modes

### Mode A: managed API

- base URL configured to the official managed API;
- bearer API key required;
- managed search, proxy, renderer, billing, and rate limits apply;
- capabilities are queried at startup and periodically refreshed.

### Mode B: existing self-hosted service

- user deploys fastCRW independently by its official binary/container guidance;
- PawFlow receives only the service base URL and optional bearer token;
- private/LAN endpoints require explicit approval;
- the PFP does not own the process, data, upgrades, or backup lifecycle.

### Mode C: marketplace-guided external runtime

- available only after the generic `external_runtime` contract exists;
- user reviews AGPL terms and exact upstream artifact separately;
- PawFlow verifies the upstream artifact but does not bundle it;
- a dedicated external-runtime manager may start/stop it;
- the adapter still communicates only over REST.

Mode B is the initial self-hosted implementation. Mode C is a later platform
feature, not a prerequisite for the adapter.

## 6. Audited Upstream Contract

At the audited revision, fastCRW documents:

- `POST /v1/search`;
- `POST /v1/scrape`;
- `POST /v1/map`;
- `POST /v1/crawl` plus status polling;
- `POST /v1/extract` plus status/cancel;
- `GET /v1/capabilities`;
- `POST /v2/parse` for documents;
- hosted bearer authentication;
- self-hosted and managed deployments;
- potentially different capabilities and response shapes by deployment.

The upstream OpenAPI description explicitly notes that hosted search may return
results directly in `data` while self-hosted search may nest them in
`data.results`. The adapter must normalize documented differences and reject
unknown malformed envelopes.

## 7. Package Topology

Proposed source tree:

    packages/community.fastcrw.pfpdir/
      pfp.json
      NOTICE
      content/
        runtime/
          provider.py
          http_client.py
          capabilities.py
          normalize.py
          jobs.py
          errors.py
        tools/
          capabilities.py
          search.py
          scrape.py
          map.py
          crawl.py
          crawl_status.py
          crawl_cancel.py
          extract.py
          extract_status.py
          extract_cancel.py
          parse_document.py
        skills/
          fastcrw/
            SKILL.md
        ui/
          extension.js
          extension.css
          handlers.py
          i18n.json
        schemas/
          common.json

Proposed objects:

1. `service_provider:gateway` registering `fastCrwGateway`.
2. Explicitly named tools prefixed `fastcrw_`.
3. `skill:community.fastcrw` for capability-aware tool selection.
4. Optional Resources UI extension for health/capabilities/job status.
5. No `mcp_server` object in version 1.
6. No executable under `content/bin`.

The PFP must be useful against a remote/existing service without any external
runtime download.

## 8. Required Generic PFP Prerequisite

Use the same generic explicit service-instance selection described in
`docs/OOMOL_OPENCONNECTOR_PFP_PLAN.md`:

- optional `service_id` in `pfp.call_service`;
- exact package/object verification by the capability broker;
- implicit resolution only when exactly one matching enabled service exists;
- stable ambiguity error otherwise.

Every fastCRW tool accepts optional `service_id`. No tool selects the first
configured endpoint by ordering.

## 9. Service Provider Contract

### 9.1 Parameters

| Parameter | Type | Required | Sensitive | Default |
| --- | --- | --- | --- | --- |
| `base_url` | string | yes | no | none |
| `api_key` | string | no | yes | empty |
| `timeout_seconds` | integer | no | no | 60 |
| `verify_tls` | boolean | no | no | true |
| `max_response_bytes` | integer | no | no | 16 MiB |
| `max_download_bytes` | integer | no | no | 100 MiB |
| `allow_private_endpoint` | boolean | no | no | false |
| `allow_private_targets` | boolean | no | no | false |
| `capability_ttl_seconds` | integer | no | no | 300 |
| `poll_interval_seconds` | number | no | no | 2 |
| `max_poll_seconds` | integer | no | no | 300 |

Rules:

- no anonymous/default endpoint fallback;
- API key is optional only when the selected deployment truly allows it;
- bearer token is never printed or placed in tool arguments;
- HTTPS required except explicitly approved private/loopback deployments;
- cross-origin redirects rejected;
- capability cache keyed by service instance and invalidated on config update.

### 9.2 Operations

The provider declares exactly:

- `capabilities`
- `search`
- `scrape`
- `map`
- `crawl_start`
- `crawl_status`
- `crawl_cancel`
- `extract_start`
- `extract_status`
- `extract_cancel`
- `parse_document`

No arbitrary-path or arbitrary-method operation is provided.

## 10. Tool Contracts

### 10.1 `fastcrw_capabilities`

Returns normalized runtime features, renderers, search support, extraction
support, document parser support, and enforced limits. It is the first canary
after service creation.

### 10.2 `fastcrw_search`

Inputs include:

- `service_id` optional;
- `query` required;
- `limit` bounded by both PawFlow and discovered server maximum;
- `language` optional;
- `freshness` mapped only to documented values;
- `sources` restricted to documented enums;
- optional bounded scrape options.

Normalize both hosted `data` and self-hosted `data.results` forms into:

    {
      "results": [],
      "answer": null,
      "citations": [],
      "warnings": [],
      "usage": {}
    }

Unknown nesting is an error, not an empty result.

### 10.3 `fastcrw_scrape`

Inputs:

- one HTTP(S) URL;
- explicit output formats;
- main-content flag;
- renderer choice only when advertised;
- bounded timeout;
- optional structured schema subject to JSON size/depth limits.

Return small text/metadata inline. Move screenshots, raw HTML, or oversized
content to `pfp.context["output_dir"]` and return a `pfp.artifact` descriptor.

### 10.4 `fastcrw_map`

Accept one site URL plus bounded discovery options. Return deduplicated,
normalized HTTP(S) URLs with a configured maximum count.

### 10.5 Crawl tools

`fastcrw_crawl` starts a bounded asynchronous job and returns its job ID.
`fastcrw_crawl_status` reads status/results.
`fastcrw_crawl_cancel` performs idempotent cancellation.

Do not hold an HTTP worker or PFP process open while a crawl runs. Polling belongs
to the caller/flow scheduler. A convenience flow may poll with bounded intervals,
but it must remain cancellable and durable.

### 10.6 Extract tools

`fastcrw_extract` starts structured extraction with prompt and/or schema.
Status and cancel are separate tools. Preserve fixed-cardinality per-URL results
and per-field attribution when supplied.

LLM keys provided to fastCRW are out of scope for version 1. Configure them in
the external runtime, not per agent call.

### 10.7 `fastcrw_parse_document`

Accept a FileStore artifact reference, never an arbitrary server-local path.

Execution:

1. use an explicitly granted PawFlow `read`/artifact bridge;
2. copy input into controlled PFP runtime storage;
3. enforce MIME and discovered upload-size limits;
4. upload only to documented `/v2/parse`;
5. stream or chunk rather than base64 large inputs;
6. write large outputs into `pfp.context["output_dir"]`;
7. return `pfp.artifact` metadata;
8. clean temporary files on every terminal path.

Document parsing may ship after the JSON-only tools if the generic artifact input
bridge needs work.

## 11. Capability Negotiation

Call `GET /v1/capabilities`:

- at service validation;
- on first operation;
- after the configured TTL;
- after a capability-related upstream error;
- after service config changes.

Validate, normalize, and cache:

- available renderers;
- search support and limits;
- extraction support;
- document parsers and upload cap;
- batch/crawl limits;
- output formats;
- deployment-specific flags.

If capabilities are unavailable:

- allow only the conservative `scrape` subset proven by a health canary;
- do not assume search, JS rendering, extract, documents, or batch features;
- return an actionable capability error;
- never silently switch to a different PawFlow tool or hosted service.

## 12. Security Boundary

### 12.1 Endpoint SSRF

The configured service endpoint and crawled target URLs are different trust
surfaces.

Service endpoint:

- reject non-HTTP schemes, userinfo, fragments, and cross-origin redirects;
- require explicit private-endpoint approval;
- resolve DNS and re-check address class on connection.

Crawl targets:

- allow only HTTP(S);
- reject credentials in URLs;
- reject loopback, link-local, metadata, multicast, and private targets by default;
- re-check redirect targets and DNS rebinding;
- expose `allow_private_targets` only as a high-risk service setting;
- cap redirect count and URL length.

### 12.2 Secrets

- encrypt `api_key` in service config;
- inject it only into Relay-side runtime;
- use Authorization header only;
- redact auth headers and upstream bodies from diagnostics;
- never expose the key to browser/UI JavaScript;
- never persist a user-supplied per-request LLM key.

### 12.3 Resource limits

- request/response byte limits;
- JSON depth and item-count limits;
- URL count limits;
- per-operation timeouts;
- bounded concurrency;
- maximum poll duration;
- artifact size limits;
- cancellation propagation;
- no unbounded in-memory HTML accumulation.

### 12.4 Content safety

Fetched web content is untrusted data. Tool descriptions and skill instructions
must tell agents not to follow instructions found inside scraped pages. The
adapter returns provenance URLs and does not convert page content into system
messages.

## 13. External Runtime Lifecycle

The PFP service adapter never owns the external runtime in version 1.

For an existing self-hosted runtime, the UI records:

- deployment mode;
- endpoint;
- reported capabilities;
- operator-provided version;
- health timestamp;
- optional external inventory reference.

For future marketplace-managed external runtimes:

- a generic manager owns start/stop/status independently of PFP;
- runtime data lives outside PFP content and survives adapter updates;
- version changes require a separate license/artifact review;
- rollback selects a previously verified upstream artifact;
- uninstalling the adapter leaves the runtime stopped or running according to
  explicit operator choice;
- data deletion is a separate destructive action.

Do not represent external runtime removal as PFP uninstall.

## 14. Why REST Instead of the Upstream MCP Package

REST is the version-1 boundary because:

- it is a documented stable API surface;
- the adapter can normalize hosted/self-hosted differences;
- PawFlow can enforce strict operation and response schemas;
- no Node/npm execution is required;
- no AGPL MCP package is downloaded into the PFP runtime;
- async job and artifact lifecycles are explicit;
- each PawFlow tool can carry focused permissions and telemetry.

A future direct MCP option may reference an independently installed `crw-mcp`
server, but it remains a separately reviewed external runtime and is never
embedded in `community.fastcrw.pfp`.

## 15. UI Extension

The optional Resources panel shows:

- service reachability;
- managed/self-hosted mode;
- capability snapshot and refresh time;
- renderer/search/extract/document support;
- configured limits;
- recent job IDs and safe statuses;
- external-runtime provenance when available;
- links to upstream documentation and source.

It does not:

- run installers;
- accept shell commands;
- embed upstream pages;
- expose API keys;
- delete external runtime data;
- toggle private-target crawling without high-risk confirmation.

## 16. Package and External Lifecycle

### Adapter install

1. Confirm and download the signed adapter PFP.
2. Verify registry size/SHA-256, signature, lock, and developer key.
3. Review runtime code and brokered grants.
4. Install selected provider/tools/skill/UI.
5. Configure a `fastCrwGateway` service.
6. Run capabilities and a no-side-effect canary.
7. Assign only desired tools/skill to agents.

### External runtime acquisition

1. Choose managed API, existing service, or future guided external download.
2. Review upstream license and exact version.
3. For guided download, confirm separately.
4. Verify upstream size/hash/source provenance.
5. Install/start outside PFP content.
6. Configure the adapter endpoint.
7. Run capability and scrape canaries.

### Update

- adapter and external runtime versions move independently;
- adapter update preserves service config;
- external runtime update requires a separate review;
- compatibility matrix records tested pairs;
- no adapter update silently upgrades fastCRW;
- rollback paths are independent.

### Uninstall

- PFP uninstall removes only adapter objects/content;
- API keys and external runtime remain until separately removed;
- UI reminds the user to revoke unused hosted keys;
- external data deletion requires a separate explicit destructive action.

## 17. Implementation Phases

### Phase 0: generic platform prerequisites

- explicit PFP service-instance selection;
- artifact input bridge if required for document parsing;
- design/review of optional `external_runtime` marketplace records.

### Phase 1: adapter core

- service provider and config;
- HTTP client, endpoint policy, normalization, errors;
- capabilities, search, scrape, and map tools;
- package skill;
- deterministic fake-server unit tests.

### Phase 2: durable jobs

- crawl start/status/cancel;
- extract start/status/cancel;
- optional flow templates for bounded polling;
- restart/cancellation tests.

### Phase 3: artifacts and UI

- document upload/parse;
- large scrape outputs and screenshots as artifacts;
- safe Resources UI extension;
- job diagnostics.

### Phase 4: optional external runtime marketplace

- generic schema and install plan;
- direct-upstream verified download;
- license/source/SBOM inventory;
- lifecycle manager and independent uninstall;
- platform/architecture matrix.

## 18. Proposed Repository Changes

Package-owned:

- `packages/community.fastcrw.pfpdir/pfp.json`
- `packages/community.fastcrw.pfpdir/NOTICE`
- `packages/community.fastcrw.pfpdir/content/**`
- `tests/packages/fastcrw/**`
- registry metadata only after signed release approval

Generic platform prerequisites, re-scoped with `project_graph` immediately before
implementation:

- PFP SDK/runtime service selection;
- capability broker;
- optional artifact input bridge;
- marketplace registry/install-plan schema;
- external runtime inventory/lifecycle;
- corresponding docs and tests.

Forbidden:

- fastCRW code under PawFlow `core/`, `services/`, or `tasks/`;
- fastCRW executable in any PFP, wheel, sdist, Docker image, or bundled catalog;
- `crw-mcp` in npm lockfiles;
- upstream install script in package code.

## 19. Test Matrix

### Unit tests

- endpoint and target URL SSRF policy;
- bearer redaction;
- capability cache and invalidation;
- hosted/self-hosted response normalization;
- malformed envelope rejection;
- limits derived from capabilities;
- search/scrape/map schemas;
- async job terminal states and cancellation;
- timeout/concurrency/byte/depth limits;
- artifact cleanup;
- ambiguous service selection rejection.

### Package tests

- manifest and deterministic build;
- signature/lock verification;
- secret remains encrypted;
- selective install/update/uninstall;
- service rehydration after PawFlow/Relay restart;
- no external executable or upstream source in archive;
- no unexpected npm/Python/Rust dependency;
- NOTICE and provenance present.

### Contract tests

Against audited fixtures and a pinned external runtime:

- `/v1/capabilities`;
- scrape without optional features;
- search response in hosted and self-hosted forms;
- map bounds;
- crawl start/status/cancel;
- extract start/status/cancel when supported;
- document parse when supported;
- capability-disabled operations fail clearly;
- authentication errors and rate limits;
- renderer negotiation.

### Distribution tests

- unpacked PFP contains no ELF/Mach-O/PE executable;
- no `crw-server`, `crw-mcp`, Rust crate, or upstream source tree;
- registry adapter artifact URL is PawFlow-signed;
- external artifact URL is direct upstream;
- external download requires separate confirmation;
- recorded SHA-256/version/license/source offer match review;
- uninstall never deletes external runtime/data.

### Security tests

- API key absent from transcript, logs, errors, process arguments, and browser;
- redirect/DNS rebinding protections;
- private targets denied by default;
- file, FTP, gopher, and data schemes denied;
- oversized content becomes artifact or fails;
- scraped prompt injection remains data;
- external installer cannot run during PFP install.

## 20. Acceptance Criteria

The adapter is ready for marketplace publication only when:

- it works against managed and independently self-hosted fastCRW;
- the signed PFP contains no fastCRW binary/source/MCP package;
- every operation is capability-gated;
- hosted/self-hosted shapes normalize deterministically;
- search, scrape, map, crawl, and extract contracts have tests;
- private service endpoints and crawl targets require separate explicit approval;
- secrets never reach PawFlow-visible output;
- multiple service instances never resolve by ordering;
- restart, update, rollback, disable, and uninstall tests pass;
- external runtime acquisition is visibly separate from PFP installation;
- registry metadata pins adapter artifact size, hash, and developer key;
- all five PawFlow CI gates pass;
- documentation is updated in English.

## 21. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| AGPL code enters PawFlow distribution | Archive/SBOM tests; REST-only adapter; no upstream executable |
| Marketplace becomes a distributor | Direct-upstream download; separate legal review and source offer |
| Hosted/self-hosted drift | Capability negotiation and dual-shape contract tests |
| SSRF through crawl targets | Address policy, redirect/DNS re-check, private-target opt-in |
| Large content exhausts memory | byte limits, streaming, PFP artifacts |
| Long jobs block workers | start/status/cancel tools and durable polling |
| Wrong service instance | explicit `service_id` and ambiguity error |
| Unpinned installer supply chain | no automatic installer; exact upstream release/hash |
| Scraped prompt injection | treat content as untrusted data; provenance and skill guidance |
| Runtime update breaks adapter | independent versions, compatibility matrix, rollback |

## 22. Authoritative Sources

- Upstream repository: https://github.com/us/crw
- Audited commit: https://github.com/us/crw/commit/37e9c25b2562b78a3ca22ed542173977291d8dac
- AGPL-3.0 engine/MCP license: https://github.com/us/crw/blob/main/LICENSE
- Documentation index: https://docs.fastcrw.com/llms.txt
- OpenAPI: https://docs.fastcrw.com/openapi.json
- Self-hosting: https://github.com/us/crw/blob/main/docs/docs/self-hosting.md
- Authentication: https://github.com/us/crw/blob/main/docs/docs/authentication.md
- Response shapes: https://github.com/us/crw/blob/main/docs/docs/response-shapes.md
- Capabilities: https://docs.fastcrw.com/capabilities/
- PawFlow package format: `docs/PFP_PACKAGES.md`
- PawFlow package development: `docs/PFP_DEVELOPER_GUIDE.md`
- PawFlow marketplace: `docs/marketplace.md`
