# Maestro PFP Integration Plan

Status: Deferred. No implementation or distribution is approved.
Audited upstream revision: `Blizaine/Maestro@60951bb7519a53264e8fc57d991700ccdd0c4f4a`.
Last reviewed: 2026-08-05.

## Decision Summary

Maestro is a promising self-hosted image, video, and audio production system.
Its asynchronous generation API and persistent Director pipelines are useful
integration points for PawFlow. The integration must remain a standalone PFP
package so PawFlow core stays provider-neutral and MIT-licensed.

The current WanGP Non-Commercial Evaluation License 1.1 expressly treats APIs,
wrappers, plugins, and bridges as part of the software or as derivatives for the
purposes of that license. The proposed package therefore remains a no-go for
commercial distribution or commercial service use until the WanGP licensor
provides suitable written commercial terms.

This plan is a design record for possible future work. It does not approve
shipping, bundling, advertising, or operating Maestro through PawFlow.

## Goals

- Provide a separately installable PFP connector for a user-operated Maestro
  instance.
- Reuse PawFlow's standard image and video media-provider contracts where the
  Maestro API has a stable equivalent.
- Expose durable submission, status, cancellation, reconnection, and artifact
  retrieval without exposing the whole Maestro API.
- Add a later, separately reviewed Director surface for multi-clip production.
- Keep all Maestro-specific code, notices, configuration, and tests outside
  PawFlow core.
- Preserve PawFlow's normal relay, package-inspection, capability, provenance,
  cancellation, and FileStore boundaries.

## Non-Goals

- Bundling Maestro, WanGP, models, weights, LoRAs, plugins, or generated assets.
- Installing, upgrading, configuring, or administering Maestro.
- Proxying arbitrary Maestro paths, methods, bodies, files, or filesystem paths.
- Exposing model deletion/download, plugin management, system configuration,
  workspace mutation, arbitrary output deletion, or local-LLM administration.
- Reproducing the Maestro Studio UI inside PawFlow.
- Treating a package acknowledgement as a substitute for a commercial license.
- Adding Maestro-specific code or configuration to the PawFlow server image.

## Legal and Distribution Gates

Implementation may begin as an unpublished development package only after all of
the following are recorded:

1. The exact Maestro and WanGP revisions to support are pinned.
2. Their authoritative license texts and all relevant model/weight licenses are
   reviewed again.
3. The intended use is classified as either:
   - non-commercial evaluation permitted by the current license; or
   - commercial use covered by a written license from the WanGP licensor.
4. The package license, notices, UI attribution, documentation, and generated
   output attribution are approved.
5. A maintainer decides where the package may be distributed.

Until written commercial permission exists:

- do not bundle the PFP with PawFlow;
- do not publish it in an official paid offering or enable it in a hosted
  PawFlow service;
- do not imply that installing the PFP grants rights to Maestro or its models;
- keep the experimental PFP in a separate repository and release channel;
- require an explicit license notice during inspection and installation.

The connector should contain only original integration code. It must not copy
Maestro source, schemas, UI assets, model metadata, presets, or generated
examples unless their licenses independently allow redistribution.

## Proposed Package Boundary

Provisional package id: `community.maestro-connector`.

The source and release artifact live outside this repository:

```text
community.maestro-connector.pfpdir/
  pfp.json
  LICENSE
  THIRD_PARTY_NOTICES.md
  content/
    service-providers/
      image/provider.py
      video/provider.py
    tools/
      maestro-jobs/tool.py
      maestro-director/tool.py
    skills/
      maestro-help/SKILL.md
    prompts/
      license-notice.md
```

The initial package should include only the media providers and the job tool.
The Director tool is a separate optional object added in a later version after
its own review. Users must be able to install the base provider without granting
Director operations.

No built-in PawFlow service type is required. The PFP runtime proxy, relay
selection, package-scoped configuration, and brokered artifact handling are the
host integration points.

## Deployment Model

Maestro and the PFP execute as separate components:

```text
Agent or Flow
  -> PawFlow media tool / package tool
  -> PFP runtime on the selected relay
  -> allowlisted HTTP client
  -> user-operated Maestro instance
  -> job id and status
  -> generated artifact stream
  -> PFP controlled output directory
  -> PawFlow FileStore or requested relay destination
```

Requirements:

- Maestro is installed and operated by the user outside PawFlow.
- The configured base URL is fixed at service creation time and is never
  supplied by an agent call.
- Private or loopback URLs require explicit installer configuration and follow
  PawFlow's existing private-endpoint policy.
- TLS verification defaults to enabled. A custom CA may be configured through a
  reviewed file/secret binding; `verify=false` is not supported.
- Authentication is required before any non-loopback deployment is supported.
  If the audited Maestro revision has no adequate authentication, a
  user-operated authenticated reverse proxy is mandatory.
- Credentials are package secrets and never enter package records, logs,
  prompts, results, or provenance payloads.
- The connector never launches Maestro as a subprocess.

## API Allowlist

The audited Maestro revision exposes 133 HTTP routes. The connector must reject
any operation not represented by a typed local method and a fixed
method/path-template pair.

Candidate base allowlist, subject to contract tests against the pinned revision:

| Purpose | Method and route | Notes |
|---|---|---|
| Capability probe | fixed read-only endpoint | Freeze the exact endpoint during the spike; do not infer capabilities by trying mutations. |
| Submit generation | `POST /api/v1/generate` | Body built from a strict per-operation schema. |
| Read status | `GET /api/v1/status/{job_id}` | Validate the job id locally before interpolation. |
| Cancel job | `POST /api/v1/cancel/{job_id}` | Idempotent local semantics even if upstream differs. |
| Reconnect jobs | `GET /api/v1/jobs` | Filter responses to jobs owned by this package instance where ownership can be proven. |
| Upload media | `POST /api/v1/upload` | Stream with lower PFP-configured limits than Maestro's upstream maximum. |
| Upload audio | `POST /api/v1/upload-audio` | Include only when an operation actually requires it. |
| Locate result | narrowly scoped read-only output route | Freeze only after proving path containment and job-to-output ownership. |

The generic output listing endpoint must not become an agent-visible gallery.
The connector may retrieve only artifacts associated with the job it submitted.
It must not expose output move, delete, rejoin, workspace, model, LoRA, plugin,
download, configuration, or arbitrary file routes.

Every response is size-limited and schema-validated. Redirects are disabled or
restricted to the configured origin. Returned URLs and paths are untrusted:
normalize them, reject origin changes and traversal, and stream only supported
media types into the PFP-controlled output directory.

## Package Objects and Operations

### Image service provider

Candidate operations:

- `generate`
- `edit` only after an audited, deterministic request mapping exists

The provider maps PawFlow's normalized prompt, dimensions, seed, and optional
input artifact to a fixed Maestro request schema. Unknown arguments fail closed.

### Video service provider

Candidate operations:

- `generate`
- `image_to_video`
- `reference_to_video` only when input handling is proven safe

The provider must report normalized progress and propagate PawFlow cancellation
to Maestro. It returns a PFP artifact reference, never media bytes or an
untrusted Maestro filesystem path.

### Job tool

Candidate operations:

- `submit`
- `status`
- `cancel`
- `result`
- `reconnect`

This object supports long-running work without keeping an HTTP worker blocked.
A durable local record stores package scope, Maestro origin fingerprint,
upstream job id, operation, creation time, last known state, artifact references,
and terminal error metadata. It stores no credential or raw prompt unless the
user explicitly opts into normal PawFlow provenance.

### Director tool, later phase

Director is not part of the first package release. A later optional object may
wrap only reviewed persistent-pipeline operations such as listing/getting one
pipeline, repairing it, cancelling repair, rerunning a selected clip, and
rejoining completed clips.

Planning endpoints and pipeline mutations require separate schemas and
authorization decisions. The tool must never expose a raw route name or request
body. Pipeline ids and clip indices are validated, and mutation operations
require the same explicit approval treatment as comparable PawFlow tools.

## State and Ownership

Maestro job ids alone are not authorization. The PFP records every submitted job
under the current package installation scope and refuses status, cancellation,
or artifact retrieval for unknown ids.

Reconnection is allowed only when the connector can match a remote job to a
durable local record. If the upstream API cannot provide a trustworthy ownership
marker, global `GET /api/v1/jobs` results are never imported automatically.

State transitions are monotonic:

```text
submitted -> queued -> running -> completed
                            \-> failed
                            \-> cancelled
```

Terminal states do not revert. Cancellation is immediate from PawFlow's
perspective, best-effort upstream, and never reported as an inference error.

## Security Requirements

- Fixed base URL and fixed route templates; no user- or agent-controlled URL.
- Explicit private-network opt-in and DNS rebinding protections consistent with
  other PawFlow providers.
- No generic HTTP, shell, filesystem, plugin, model, or configuration operation.
- Strict request and response schemas with unknown fields rejected where safe.
- Bounded connect/read/total timeouts, retries only for proven idempotent reads,
  and no automatic retry of generation submission.
- Streaming uploads/downloads with byte limits, MIME allowlists, filename
  normalization, path containment, and cleanup on failure.
- Secrets redacted from exceptions and debug logs.
- Bounded upstream error bodies before they enter logs or agent context.
- Package capabilities and network reachability visible during PFP inspection.
- Provenance records include provider id, operation, job id, pinned compatibility
  profile, timing, and output hashes, but not secrets.
- Concurrent calls must not cross package, user, conversation, or job ownership
  boundaries.
- All mutation methods have explicit unit tests and threat-model cases.

## Compatibility Strategy

Maestro is young and does not yet provide a stable, versioned connector contract.
The PFP therefore uses compatibility profiles keyed by an exact upstream commit
or a future release version. A profile contains only typed request/response
adapters and route constants.

Startup capability probing fails closed when:

- the upstream revision is unknown;
- a required field or route is missing;
- the server identity cannot be established;
- authentication is absent where required; or
- a response exceeds limits or fails validation.

No heuristic fallback to a similar route is permitted. Supporting another
Maestro revision requires fixtures, contract tests, documentation, and a new PFP
release.

## Delivery Phases

### Phase 0: licensing and API contract

- Obtain the legal/distribution decision.
- Pin a supported Maestro release or commit.
- Capture sanitized HTTP fixtures for capability, submit, status, cancel, upload,
  and output retrieval.
- Decide the authenticated deployment baseline.
- Write the threat model and data-retention policy.

Exit criterion: written go decision plus a small, frozen HTTP contract.

### Phase 1: non-commercial development spike

- Create the standalone `.pfpdir`.
- Implement the typed HTTP client and job ownership store.
- Add one text-to-image operation.
- Stream one generated artifact through `pfp.artifact`.
- Prove cancellation, timeout, restart/reconnection, and cleanup.

Exit criterion: a local, unpublished package passes mocked and live tests against
the pinned revision without PawFlow core changes.

### Phase 2: base media provider

- Add supported image/video operations one at a time.
- Add package inspection metadata, secrets, license notice, skill, and operator
  documentation.
- Add compatibility fixtures and end-to-end relay tests.
- Complete security and license review.

Exit criterion: signed PFP candidate with reproducible builds and no bundled
third-party code or assets.

### Phase 3: Director

- Audit the exact persistent-pipeline routes again.
- Add a separate optional tool object and grants.
- Implement durable pipeline ownership, progress, repair, cancellation, selected
  clip reruns, and result collection.
- Add approval and adversarial cross-scope tests.

Exit criterion: Director operations cannot address unowned pipelines or invoke
undeclared mutations.

### Phase 4: distribution decision

- Recheck all upstream licenses and supported model licenses.
- Verify written commercial rights if any commercial channel is intended.
- Choose community-only, private, or official distribution.
- Publish the PFP and its source independently from PawFlow.

Exit criterion: maintainer and legal sign-off match the actual distribution and
deployment model.

## Test Plan

Unit tests:

- schema mapping for every operation;
- route allowlist and rejection of arbitrary paths/methods;
- URL, redirect, traversal, filename, MIME, and byte-limit handling;
- secret redaction and bounded errors;
- retry and idempotency rules;
- state-machine monotonicity and job ownership;
- cancellation before submit, while queued/running, and after terminal state;
- cleanup after upload, download, validation, and cancellation failures;
- package scope isolation under concurrent jobs.

Contract tests against recorded fixtures:

- known pinned revision accepted;
- unknown or changed contract rejected;
- submit/status/cancel/result lifecycle;
- partial, malformed, oversized, and contradictory responses;
- upstream restart and lost-job behavior.

Live tests, run only by an operator with a licensed installation:

- text-to-image and selected video operations;
- progress and cancellation under real GPU load;
- relay/container routing and authenticated reverse proxy;
- restart/reconnection;
- large artifact streaming and FileStore persistence;
- no access to excluded administrative routes.

Release checks:

- deterministic PFP build reproduced byte-for-byte;
- signature and lock verification;
- package inspection shows network, secret, executable, and license risks;
- no Maestro/WanGP code, weights, assets, credentials, or generated samples;
- source and binary license notices match;
- uninstall removes package state without deleting remote Maestro jobs or media.

## Go/No-Go Checklist

Proceed only when every applicable item is yes:

- [ ] Written licensing decision covers the intended use and distribution.
- [ ] A stable Maestro release or explicitly pinned commit is available.
- [ ] Required routes have typed, captured, passing contract fixtures.
- [ ] Authentication and private-network deployment are acceptable.
- [ ] The connector contains no upstream code or restricted assets.
- [ ] Base media operations pass isolation, cancellation, and artifact tests.
- [ ] Director, if included, has a separate security review and install grant.
- [ ] Attribution is visible in package inspection, documentation, and UI.
- [ ] Deterministic build and signature verification pass.
- [ ] PawFlow core remains unchanged and provider-neutral.

Any unchecked licensing, authentication, ownership, or arbitrary-route item is a
release blocker, not a warning.
