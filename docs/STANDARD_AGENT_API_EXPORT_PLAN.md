# Standard Agent API Export Plan

Status: implementation in progress; Phase 0 through Phase 2 gates complete.

## 1. Decision summary

A published PawFlow agent will be callable by unmodified OpenAI and Anthropic SDKs. A client only needs a protocol-specific base URL, the publication API key, and the published model ID. No PawFlow header, overloaded standard field, cookie, source IP, or SDK patch is part of the contract.

One existing agent publication remains the source of truth for the agent, owner, Bearer keys, enabled state, context policy, and retention settings. The new endpoints are additional dialects over the same publication and the same AgentRuntimeAPI; they are not a second agent runtime.

Continuity is protocol-specific:

- AG-UI continues to use its standard threadId.
- OpenAI Responses uses previous_response_id whenever the client supplies it.
- Chat Completions and Anthropic Messages use content-addressed transcript resolution. PawFlow hashes a versioned canonical representation of the visible transcript, finds the longest known server-output prefix in the namespace of the publication and API key, and reuses the matching isolated PawFlow conversation only when a compare-and-set claim proves that the prefix is still its current head.
- A source IP is used only for audit and rate limiting. It is never conversation identity.
- When a prefix is stale or already claimed, PawFlow forks its immutable checkpoint when one is available. If no verified checkpoint exists, PawFlow reconstructs a fresh isolated conversation from the visible history, marked client-supplied and untrusted. Reconstruction imports rows; it does not execute historical tool calls.
- Completed requests are not response-cached. A byte-equivalent retry may attach to the same still-active run during a short replay window, but a completed request is a new request unless the native protocol addresses a stored response.

The first delivery order is Chat Completions, Responses, then Anthropic Messages. All three dialects share the normalized request model, session resolver, checkpoint implementation, tool bridge, limits, and observability.

## 2. Goals

The implementation must:

1. Let standard SDKs call one published PawFlow agent without proprietary request fields.
2. Preserve PawFlow server-side conversation state on the normal multi-turn path instead of rebuilding the whole internal conversation on every request.
3. Prevent state from crossing publication, API key, export generation, protocol, or model boundaries.
4. Support streaming and non-streaming responses with the native event and error vocabulary of each dialect.
5. Support both kinds of tools:
   - PawFlow/server tools execute inside the agent loop and remain server-side.
   - Tools declared by the API client are returned to that client for execution, and their results resume the same internal state.
6. Handle retries, forks, concurrent children of one prefix, disconnects, crashes, and retention without appending a request to the wrong conversation.
7. Reuse the existing publication auth, AgentRuntimeAPI, ConversationStore, event bus, tool authorization, relay rules, and agent configuration.
8. Ship with unit, concurrency, SDK contract, security, and regression coverage plus user documentation.

## 3. Non-goals

The initial implementation does not:

- emulate every optional OpenAI or Anthropic parameter;
- expose PawFlow's AG-UI state, interrupts, or managed receipt/deposit protocol through the standard APIs;
- infer identity from an IP address, User-Agent, TLS session, connection, timing, or message text outside the canonical transcript;
- expose internal conversation IDs, relay paths, service IDs, hidden tool calls, system prompts, or reasoning traces;
- provide OpenAI Assistants, Threads, Batches, Realtime, Anthropic Message Batches, or Files APIs;
- make a shared-context publication available through these endpoints;
- promise identical model behavior to the vendor APIs. The goal is wire compatibility for a documented capability subset around a PawFlow agent;
- make Chat Completions or Anthropic Messages secretly stateful when the client supplies no recognizable prior server output.

## 4. Compatibility definition and invariants

“Compatible” means the official SDK can construct, authenticate, send, stream, and parse the request without custom fields or transport hooks. It does not mean PawFlow silently accepts unsupported generation controls.

The following invariants are mandatory:

- The model field is required and must equal the publication's explicit model ID.
- Standard structural fields that PawFlow supports are honored.
- Compatibility mode is the default. Common harness fields that are harmless or advisory for a published agent are accepted outside transcript identity and either applied when the runtime supports them or explicitly treated as no-ops. Examples include sampling hints, seed, user/metadata tags, and text response-format defaults. Fields that require a semantic feature PawFlow cannot provide still fail before the stream opens, including multiple choices, audio output, logprobs, non-text structured output, or an unenforceable required/named tool choice.
- A publication may enable strict_fields to reject every unsupported non-default field. The UI and documentation must label it as less compatible with generic harnesses.
- The publisher's agent prompt, PawFlow policy, tool authorization, and permission mode remain authoritative. Client system/developer instructions are lower-priority client input and cannot replace server policy.
- Standard API export is available only for isolated publications. Shared publication state must never be selected by transcript content.
- Every internally created message has a UUID-compatible message ID and a timestamp at creation, as required by ConversationStore.
- Every externally visible assistant boundary commits its session head through recoverable finalization; a verified checkpoint is attached when checkpoint creation succeeds.
- Partial streamed output is never entered into the reusable prefix index.
- A hash match alone never authorizes reuse. Namespace validation, a unique candidate, head equality, and a successful lease/CAS are required for in-place reuse; a verified checkpoint identity is additionally required for an exact fork.
- Same transcript plus different API keys means different state.
- Same API key plus two indistinguishable independent clients is fundamentally ambiguous. PawFlow detects multiple candidates and never selects one by IP. The operational recommendation is one publication key per harness or consumer.
- Ambiguity, expiry, generation change, eviction, or checkpoint loss falls back to reconstruction instead of making a standard SDK conversation permanently unusable. Hidden server activity is not re-executed during import; subsequent behavior has the same visible-history limitation as any stateless compatible proxy.

## 5. Existing architecture to reuse

The implementation is an adapter over shipped components:

| Concern | Existing source | Reuse |
| --- | --- | --- |
| Publication and keys | core/a2a_store.py | Same publication row, one-time raw keys, hashed key validation, revocation |
| Published endpoint auth | services/a2a_server_endpoint.py | Extract protocol-neutral publication lookup/auth; keep dialect-specific errors outside it |
| Isolated child conversation | core/a2a_runtime.py | Same owner and parent-publication relationship |
| Agent submission | core/agent_runtime_api.py | Same AgentRuntimeAPI, result waiter, live callback, permission_mode |
| Live events | core/conversation_event_bus.py and AG-UI translator in core/agui_runtime.py | Same event source; new dialect translators |
| Client-side tools | core/agui_tools.py | Extract a protocol-neutral client-tool handler and add an explicit pause outcome |
| Tool policy | core/tool_mcp_filters.py and core/tool_authorization.py | Same inherited filters, approval gate, and publication permission mode |
| Durable messages | core/conversation_store.py and its mixins | Same append-only transcript, shared context, extras, and message invariants |
| Current conversation fork | core/_conversation_store_git.py | Extend the existing fork/checkpoint vocabulary rather than create an unrelated copier |
| HTTP routes | services/agui_server_endpoint.py | Same public listener and validate-before-stream pattern |

Important gaps:

1. A2AStore has no content-addressed API-session, prefix, response, run, or client-tool ledger.
2. AgentRuntimeAPI accepts one flattened user message, not a protocol-neutral batch of structured user/tool ingress items.
3. ConversationStore.fork() clones only the current idle Git head. It cannot atomically return or fork an exact historical checkpoint named by an external transcript boundary. Ordinary git_snapshot is best-effort, runs outside the conversation lock, returns no verified commit, and skips temporary conversations.
4. AguiFrontendToolHandler relies on instructions telling the model to end its turn. A standard API bridge needs an explicit agent-loop “client tool results pending” outcome.
5. Existing outbound OpenAI/Responses/Anthropic providers parse the opposite direction. Their normalized message and usage rules are useful references, but the inbound server must have dedicated validators and serializers.
6. The existing done payload already carries tokens_in, tokens_out, finish_reason, model, provider, and final_msg_id. The missing protocol-neutral terminal data is the pending client-tool batch and an explicit paused outcome; neither may be recovered by scanning display text.
7. The current AG-UI/A2A endpoint auth helper writes an A2A-shaped HTTP error itself. Standard dialect handlers need a neutral authentication result so they can emit native error envelopes.

## 6. Public HTTP surface

### 6.1 OpenAI base URL

The UI advertises:

~~~text
https://HOST/openai/{publication_id}/v1
~~~

Routes:

~~~text
GET    /openai/{publication_id}/v1/models
GET    /openai/{publication_id}/v1/models/{model_id}
POST   /openai/{publication_id}/v1/chat/completions
POST   /openai/{publication_id}/v1/responses
GET    /openai/{publication_id}/v1/responses/{response_id}
DELETE /openai/{publication_id}/v1/responses/{response_id}
~~~

Create, streaming, previous_response_id, retrieve, and delete are in scope for Responses. Stored Chat Completion CRUD is not.

Authentication is the standard Authorization: Bearer header.

### 6.2 Anthropic base URL

The UI advertises:

~~~text
https://HOST/anthropic/{publication_id}
~~~

Routes:

~~~text
GET  /anthropic/{publication_id}/v1/models
POST /anthropic/{publication_id}/v1/messages
~~~

The official Anthropic SDK appends /v1/messages to the base URL. Authentication accepts the standard x-api-key header. Authorization: Bearer may also be accepted for generic compatible clients, but x-api-key is the documented path. A supported anthropic-version is required; supported anthropic-beta values are allowlisted and unknown semantic betas fail explicitly.

### 6.3 Model identity

Enabling standard API export requires an explicit api_model_id on the publication. It has no anonymous or inferred fallback. It is validated as a bounded opaque model token and is scoped to the publication URL.

Requests for another model fail with model_not_found. Model-list responses expose only the published model, a PawFlow owner/provider label, creation time, and protocol capabilities. They never expose the underlying LLM provider model, system prompt, tools, service IDs, or credentials.

### 6.4 Publication configuration

The existing publication gains explicit standard-API fields:

- standard_api_enabled;
- api_model_id;
- api_generation;
- api_permission_mode: read_only or default;
- api_session_ttl_seconds;
- api_max_sessions_per_key;
- api_max_concurrent_runs_per_key;
- strict_fields compatibility policy;
- api_request_overrides_json: canonical allowed-field/range policy;
- api_input_modalities_json: canonical closed list including text;
- api_chat_completions_enabled;
- api_responses_enabled;
- api_anthropic_messages_enabled;
- api_disconnect_policy: cancel or finish_detached.

There is no request field that can enable, change, or bypass these settings. Existing publications remain disabled until the owner chooses all required values.

The publication row also gains delete_requested_at for the existing delete action's asynchronous drain-and-cascade lifecycle. It is server-owned status, not an API setting or client-editable field.

Any change to the published agent definition, its effective tool/MCP filters, its security policy, its model binding, or these API settings increments api_generation. The same happens on an explicit “Reset API sessions” action. Old generations become read-only for in-flight completion and then expire; new requests cannot resolve into them.

### 6.5 Owner UI: one publication, several transports

The standard API is configured in the existing owner-only publication dialog opened from the repository sidebar. It does not get a second publication list, key store, or settings page. The current A2A section is relabeled “Published agents / APIs”, and each existing publication card represents one agent publication that may expose A2A, AG-UI, OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages.

The existing global enabled field remains the master switch for the publication. Standard API export has its own standard_api_enabled switch below it, and each dialect has its own switch. Effective availability is:

~~~text
publication.enabled
AND standard_api_enabled
AND the dialect-specific enabled flag
AND the server build advertises that dialect
AND context_policy = isolated
~~~

Every term, including isolated context, is checked again at admission; the saved UI state is not an authorization boundary.

This separation is mandatory:

- turning off standard_api_enabled leaves A2A and AG-UI unchanged;
- turning off one dialect leaves the other enabled dialects unchanged;
- turning off the publication master switch stops new admissions on every transport;
- disabling does not synchronously delete the publication, configuration, API keys, or sessions; old-generation session data is removed later by the normal retention/cleanup path;
- deleting a publication remains a separate destructive action.

The publication list card shows the agent/label, global enabled state, context policy, enabled transport badges, API permission mode, model ID, and current API generation. A configured transport that is globally disabled is shown as “configured, publication disabled”, not as active. A standard API export with no live key is shown as “enabled, no client key”.

### 6.6 Standard API fieldset and validation

Editing a publication adds a “Standard OpenAI / Anthropic API” fieldset beneath the existing A2A/AG-UI fields. It is collapsed when disabled and expanded when enabled. The fieldset contains:

| UI control | Persisted value | Rule |
| --- | --- | --- |
| Enable standard API | standard_api_enabled | Off for every migrated publication. |
| OpenAI Chat Completions | api_chat_completions_enabled | At least one available dialect is required when the export is enabled. |
| OpenAI Responses | api_responses_enabled | Disabled in the UI until the server capability is shipped. |
| Anthropic Messages | api_anthropic_messages_enabled | Disabled in the UI until the server capability is shipped. |
| Published model ID | api_model_id | Required bounded opaque token; one value shared by enabled dialects. |
| Permission mode | api_permission_mode | Explicit read_only or default; read_only is visibly recommended. |
| Compatibility mode | strict_fields | Compatibility is the normal unchecked state; strict mode is labeled as less compatible with generic harnesses. |
| Session retention | api_session_ttl_seconds | Required positive duration within server-advertised bounds. |
| Sessions per key | api_max_sessions_per_key | Required bounded positive integer. |
| Concurrent runs per key | api_max_concurrent_runs_per_key | Required bounded positive integer. |
| Input modalities | api_input_modalities_json | Text is mandatory; unsupported server/model capabilities are disabled, not silently saved. |
| Request overrides | api_request_overrides_json | Advanced section; only server-advertised fields/ranges may be selected. |
| Client disconnect | api_disconnect_policy | Explicit cancel or finish_detached; cancel is the safe recommended choice and detached completion carries a side-effect warning. |
| API generation | api_generation | Read-only status with a separate Reset API sessions action. |

The UI may prefill server-provided safe suggestions for a new disabled draft, but save always sends explicit values; backend fallbacks do not make an incomplete enable request valid. Toggling the fieldset does not mutate server state until Save is pressed.

Standard API export requires context_policy=isolated. If shared is selected, the standard-API enable control is disabled with an explanation. If an existing isolated publication has standard API enabled, selecting shared is rejected until the owner disables standard API in the same save or a prior save. The backend enforces the same invariant and never relies on disabled HTML controls.

All booleans are JSON booleans, all numeric inputs are finite integers, and the complete submitted configuration is validated atomically. The dialog keeps the user's draft and displays field-specific server errors on failure; it does not optimistically render an invalid configuration as saved. On success it replaces local state with the canonical server response.

### 6.7 Enable, disable, reset, and delete semantics

The lifecycle is explicit:

1. Creating an A2A publication creates a standard-API-disabled publication. The owner may save an incomplete standard API draft while it remains disabled.
2. First enable requires isolated context, an explicit model ID, permission mode, retention and quota values, and at least one available dialect. It also initializes api_generation=1.
3. Saving a material standard API change while enabled shows “This resets API session matching” before confirmation. The backend applies the configuration and generation bump in one transaction.
4. Disabling the standard API or one dialect commits the new flag and generation before the UI reports success. For a standard API request, publication disabled, standard API disabled, dialect disabled, or delete_requested_at set all produce the same constant-shape native 404 response; none is a 403.
5. Runs admitted before a disable or generation bump may finish under their captured generation; they cannot publish state into the new generation. The UI explains that disable blocks new calls while already-admitted calls drain.
6. Re-enabling preserves keys and configuration but starts from the new generation. Old sessions never become current again.
7. Reset API sessions is a dedicated owner action, a2a_publication_reset_api_sessions. It requires confirmation, increments generation, preserves keys/configuration, and schedules old session cleanup.
8. Delete publication is labeled as affecting A2A, AG-UI, OpenAI, Anthropic, all shared keys, and retained API state. The server atomically sets delete_requested_at and disables admissions. services/a2a_server_endpoint.py::_publication plus the AG-UI and standard handlers then treat the publication as not found. The cleanup worker cancels or drains leases according to their captured policy, deletes every A2A, AG-UI, and standard-API isolated child through ConversationStore, and only then deletes the publication row; the foreign-key cascade therefore removes references to children that were already reaped instead of losing the cleanup inventory. The action returns the durable deleting state rather than blocking the HTTP worker.

Changing only label or description does not bump api_generation. Once an API generation exists, changes to the publication master enabled state, standard_api_enabled, agent binding, context policy, model ID, permission mode, dialects, compatibility policy, modalities, overrides, disconnect policy, or effective tool/security configuration do. Quota reductions apply to new admissions; retention changes apply to the next cleanup pass and still bump generation so identity never crosses policy versions.

### 6.8 Endpoints, SDK snippets, and keys

When a publication is selected, the dialog shows a transport section with:

- the A2A Agent Card and endpoint already present;
- the AG-UI endpoint already present;
- the OpenAI base URL and model ID;
- the Anthropic base URL and model ID;
- an active, disabled, or unavailable-in-this-build badge for every dialect;
- Copy buttons for each base URL and the model ID;
- Python and curl snippets for each enabled dialect.

Snippets use the official SDK constructor fields and an environment-variable placeholder, never a persisted raw credential. OpenAI snippets use base_url plus api_key; Anthropic snippets use base_url plus api_key and show the required max_tokens argument. Streaming and non-streaming examples are provided, with streaming recommended for Anthropic long-running calls.

The existing publication keys are intentionally shared across all transports. The UI states this next to key management: revoking one key revokes its A2A, AG-UI, OpenAI, and Anthropic access. The UI requires a non-empty consumer label and repeats the recommendation to create one key per harness/consumer for content-addressed Chat/Messages continuity. The existing backend fallback label “A2A client” remains unchanged for non-UI A2A callers, so this UI validation does not alter their behavior.

The raw pfa2a_ key remains one-time-only. Immediately after creation, an isolated reveal panel allows Copy key and Copy configured snippet; the snippet still contains the environment-variable placeholder, while Copy key is the only action that copies the raw secret. Closing the panel, changing publication, or reloading irreversibly removes the raw value from UI state. Existing rows show only label, prefix, creation/last-use metadata, and revoked state.

### 6.9 UI/action data contract

Reuse the existing a2a_get and a2a_publication_configure owner actions:

- a2a_get returns publication config, safe key metadata, a server capability object (available dialects, permission modes, modalities, override fields, and numeric bounds), and a safe runtime summary (effective enabled/deleting state, current generation, session count, active-run count, and draining generations);
- a2a_publication_configure accepts the complete typed standard API fieldset as part of the same publication update and returns the canonical publication plus runtime summary;
- a2a_publication_reset_api_sessions is the only new configuration action;
- create/revoke key and delete continue to use the existing publication actions.

Standard API fields omitted from a2a_publication_configure preserve their stored values, matching the current managed_mode and thread_ttl_seconds update contract. This keeps existing callers valid and permits disabled drafts. A request that sets standard_api_enabled=true must carry the complete standard API fieldset; enable never succeeds from preserved or inferred missing values.

The action handler remains owner-only through the existing conversation-owner check. Every new mutating action is registered as write in the agent_resource.py action policy map; confirmation for reset, disabling changes, key revocation, and delete is enforced by the owner UI. Responses never contain key hashes, raw canonical transcript data, checkpoint IDs, internal conversation IDs, system prompts, provider/service identifiers, or client content.

The UI is capability-driven. A server build that has not shipped Responses or Anthropic returns those dialects as unavailable; the controls are disabled and cannot be forged successfully in the configure action. Route registration and admission read the same persisted flags and capability registry as the UI, so a displayed state cannot diverge from runtime enforcement.

### 6.10 UI implementation constraints

Keep one dialog and one state object, but do not grow resources_a2a.js into another oversized module. Add resources_standard_api.js for pure rendering, validation, payload collection, endpoint/snippet formatting, and lifecycle handlers; resources_a2a.js remains the publication/target coordinator. serve_chat_ui.py loads the new module before resources_a2a.js.

All visible strings are added to the English, French, and Spanish catalogs. Every input has a programmatic label and inline error target; the fieldset, advanced controls, confirmations, one-time key reveal, and status badges are keyboard reachable. The dialog preserves focus on re-render, restores focus to the opener on close, and remains usable at the existing narrow/mobile dialog width. Secrets are never placed in title attributes, URLs, logs, analytics, or long-lived DOM data attributes.

The literal integration assertions in tests/test_a2a.py::test_a2a_ui_is_loaded_and_translated remain satisfied or are updated in the same implementation change. Relabeling the repository entry is i18n-only: the existing a2aRepository and a2aConfigure keys, showA2AConfigDialog hook, collapse key, managed-mode controls, and A2A/AG-UI assertions are retained.

## 7. Protocol-neutral normalized model

Each dialect parser produces a NormalizedApiTurn before any conversation lookup:

- publication, key, generation, dialect, and model ID;
- ordered visible transcript items;
- client instructions with their native priority and scope;
- the actionable suffix for this request;
- request-scoped client tool definitions and tool choice;
- stream flag and supported generation overrides;
- input attachments after URL/data validation;
- a protocol request ID and body fingerprint;
- Responses previous_response_id when present.

Normalized visible items use a small closed vocabulary:

- client_instruction;
- user_message;
- assistant_message;
- client_tool_call_batch;
- client_tool_result_batch.

The normalized model preserves ordered content parts, role, names, tool call IDs, tool names, validated arguments, result/error state, and supported media references. It excludes transport-only fields such as stream, metadata, request headers, and vendor tracing fields.

Client instructions do not become PawFlow's authoritative system prompt. On ingestion they are wrapped as authenticated-client instructions beneath the publisher's agent and policy prompt. Imported history is marked as client-supplied history. Only assistant/tool boundaries already present in PawFlow's prefix ledger are trusted as server-produced state.

## 8. Canonicalization and hash chain

### 8.1 Versioned canonical form

Canonicalization is schema-driven, not a generic dump of the incoming body. Version 1 uses UTF-8 and RFC 8785-style canonical JSON for normalized objects:

- object keys are sorted;
- arrays retain order;
- each item type projects an explicit semantic allowlist before hashing. SDK replay defaults such as assistant content null versus empty string, refusal null, empty annotations/citations, or absent cache_control normalize to the same value when the native protocol treats them equivalently; meaningful null versus absent distinctions remain distinct;
- non-finite numbers are rejected;
- strings are preserved byte-for-byte after JSON decoding; no whitespace or Unicode normalization merges distinct client text;
- tool argument strings are parsed and canonicalized as JSON when valid, otherwise retained as a tagged raw string;
- data and HTTP media references hash their declared media type and exact normalized reference, not fetched remote bytes;
- unsupported fields never enter the hash because validation has already rejected them.

Each dialect has golden canonical fixtures. A canonicalization version change creates a new namespace; old and new digest meanings never mix.

### 8.2 Namespace and chain

The namespace is:

~~~text
publication_id
+ api_generation
+ key_id
+ dialect
+ api_model_id
+ canonicalization_version
+ hash_secret_version
~~~

A server secret provides domain separation and prevents cross-key transcript enumeration:

~~~text
H0 = HMAC-SHA256(secret, canonical(namespace))
Hi = HMAC-SHA256(secret, Hi-1 || uint64_be(length(item_bytes)) || item_bytes)
~~~

The server computes the chain in one pass over the request. O(total request bytes) hashing is unavoidable because Chat Completions and Messages send their history. Database lookup is batched: eligible prefix digests are queried in one statement from newest to oldest, never one query per message.

Hash secrets use a small versioned keyring. Rotation makes a new version active, retains old verification keys only through the maximum session/response retention window, and bumps api_generation when old namespaces must be invalidated. Secret values never enter SQLite rows or logs.

Only client-visible server output boundaries are indexed:

- a completed assistant message;
- a completed assistant client-tool-call batch;
- a completed Responses output boundary.

User-only heads are not used to collapse independent initial requests or to invent retry idempotency.

### 8.3 Longest-prefix lookup

For Chat Completions and Anthropic Messages:

1. Validate auth, publication generation, model, body limits, transcript grammar, tools, and parameters.
2. Compute every eligible prefix digest.
3. Query known candidates from longest to shortest.
4. Discard candidates from another namespace, expired sessions, invalid checkpoints, or mismatched item counts.
5. If the longest digest maps to exactly one eligible session:
   - reuse it only if its current head equals that digest and an idle-to-running CAS succeeds;
   - if its head advanced or it is already running, fork the verified checkpoint attached to the matched prefix when available, otherwise reconstruct.
6. If the digest maps to several sessions, it is ambiguous. Never select by recency or IP; reconstruct a new isolated conversation from the identical visible history.
7. If there is no usable candidate, create a new isolated conversation and reconstruct completed visible history before submitting the actionable suffix.
8. Reconstruction is valid in both read_only and default permission modes. Imported history is marked client-supplied/untrusted, and historical tool calls are data rather than work to execute. A current tool-result suffix that does not settle a durable pending client call is instead a 400 invalid request.
9. After successful terminal output, finalize across the filesystem checkpoint and SQLite using a recoverable two-phase protocol:
   - create and verify the immutable checkpoint first;
   - commit prefixes, head, response/tool records, and lease release in one SQLite transaction;
   - garbage-collect an orphan checkpoint after a crash before the SQLite commit;
   - reconstruct if checkpoint creation fails, while still committing the visible head without a checkpoint.
10. On error, cancel, or disconnect, release/quarantine the run without indexing partial output.

For Responses, a valid previous_response_id is the primary lookup key. It resolves directly to a namespace, session, visible head, stored visible chain, and optional checkpoint. Concurrent children fork from a verified checkpoint or reconstruct that stored visible chain. If previous_response_id is omitted, the request starts a new response chain. Content digests still protect integrity, support auditing/checkpoints, and allow full-input reconstruction where applicable; they do not replace the standard response ID.

## 9. Durable store

Keep publication and key ownership in A2AStore. Add a focused mixin, for example core/_a2a_standard_api.py, so the following tables live in the same SQLite database and can share transactions with publication/key validation:

### api_export_sessions

- session_id;
- publication_id, key_id, api_generation, dialect, api_model_id;
- internal_conversation_id;
- visible_head_hash and item_count;
- head_checkpoint_id;
- state: idle, running, quarantined, expired;
- lease_id, lease_deadline, heartbeat_at;
- created_at, last_seen_at, expires_at.

### api_export_prefixes

- full namespace;
- prefix_hash and item_count;
- session_id and checkpoint_id;
- boundary kind;
- created_at and last_seen_at.

The key is not unique by digest: multiple independent sessions may legitimately have the same visible transcript. Ambiguity must remain representable.

### api_export_runs

- protocol request/run ID;
- session and lease IDs;
- body fingerprint;
- status: admitted, running, completed, failed, canceled, abandoned;
- response ID when applicable;
- terminal error code;
- start, heartbeat, and finish times.

This is a run ledger and crash-recovery source, not a completed request-response cache. It also owns a bounded normalized event buffer and follower list for the short active-run replay window. A follower with the same namespace, visible parent, and body fingerprint can attach to the admitted run and replay buffered events before joining live fanout.

### api_export_responses

- opaque resp_ ID;
- namespace and session;
- previous response ID;
- checkpoint and visible head;
- normalized public output plus the native response envelope needed by retrieve;
- created, deleted, and expiry times.

Deleting a response tombstones that response ID and makes it unusable as previous_response_id. Session/checkpoint garbage collection occurs only when no live prefix, response, run, or descendant references it.

### api_export_tool_batches and api_export_tool_calls

- batch and call IDs;
- session/run/visible boundary;
- tool name, canonical arguments, schema fingerprint;
- pending, settled, or canceled state;
- result fingerprint and settlement time.

A current tool result settles only a pending call issued by that namespace and checkpoint. A forged or duplicate current result fails before agent submission.

SQLite operations use BEGIN IMMEDIATE for admission/finalization CAS, foreign keys, bounded indexed lookups, and no raw transcript content in prefix tables. Raw content remains in ConversationStore and, where required for response retrieval, in the response record under the same storage protections.

## 10. Exact checkpoints and forks

ConversationStore.fork() is the starting vocabulary, but it currently clones the current idle Git head only. Add protocol-neutral primitives:

~~~text
create_checkpoint(conversation_id, expected_generation, expected_max_seq)
fork_from_checkpoint(checkpoint_id, owner_user_id, new_conversation_id)
release_checkpoint(checkpoint_id)
~~~

Requirements:

1. create_checkpoint is a synchronous, verified operation. It verifies the conversation is idle and that generation/max_seq still match the completed run, creates the commit, reads the resulting commit ID back, and fails explicitly if any Git operation failed.
2. The checkpoint captures the durable source-of-truth files already used by conversation history: transcript, shared context, extras, and relay bindings. Per-agent context and bucket summaries remain derived and are purged/rebuilt exactly as current rollback/fork logic does.
3. Tool calls and results, including hidden server tool activity, remain in the checkpoint transcript. This prevents a branch from re-executing a hidden side effect merely because that activity was not client-visible.
4. The checkpoint is immutable and returns a stable ID plus transcript generation/max sequence. A protected tag pins the commit; ordinary conversation Git retention must preserve every live checkpoint tag.
5. fork_from_checkpoint creates a new independent isolated conversation, rewrites owner/fork/API metadata, clears live runtime/session state, rebuilds derived context, and never leaves a Git remote pointing at the source.
6. API-session retention, not the normal ConversationStore TTL shortcut, owns these conversations and checkpoints. API child conversations carry no store TTL because is_temporary currently disables git_snapshot. api_export_sessions.expires_at and its cleanup worker are the sole expiry mechanism.
7. Checkpoints are reference-counted by prefix/response/session rows and are removed only after leases and descendants are gone. Releasing the last reference removes the protected tag; only then may ordinary retention prune the commit.
8. Checkpoint failure is an optimization miss, not a permanent protocol conflict. The resolver reconstructs from visible history and records checkpoint_unavailable.
9. The API resolver calls ConversationStore methods only; it never copies conversation files directly.

Delivery may stage this safely:

- Stage A supports current-head CAS reuse and visible-history reconstruction for read_only publications and is sufficient to ship Chat Completions.
- Stage B adds exact checkpoint forks as a state-preservation optimization before Responses branching and broader write-capable rollout.
- No stage may choose an advanced conversation and append an old suffix to it.

## 11. Runtime submission and message ingestion

Introduce a protocol-neutral structured turn request rather than placing OpenAI/Anthropic fields in AgentRequest. It contains:

- owner and isolated conversation ID;
- target agent;
- one or more normalized ingress messages (user and/or client-tool results);
- source/provenance attributes;
- validated attachments;
- permission mode;
- request-scoped client tools and tool-choice policy;
- bounded provider overrides;
- live callback and run handle.

AgentRuntimeAPI remains the only entry into the agent runtime. Existing one-message callers can be migrated to the normalized request or use a small convenience constructor; there must still be one ingestion and idempotence path. The new adapter preserves the existing fenced agent.request_msg_id/turn-ID path and must not introduce a second append-and-wake boundary.

The ingress transaction must:

1. create or fork the isolated conversation if needed;
2. seed completed imported history only during reconstruction;
3. mint deterministic-in-session internal message IDs and creation timestamps;
4. append a batch of current tool results and/or user messages in native PawFlow roles;
5. register request-scoped client tools;
6. enqueue exactly one correlated agent wake-up;
7. return a waiter before any event can be missed.

System/developer/client instructions are explicitly delimited client input below publisher policy. They are included in canonical continuity, but cannot mutate the published agent resource.

The protocol adapter never scans final display text to discover semantics. Existing done fields supply usage, finish reason, model/provider label, terminal error, and final message ID. The extension adds normalized client-tool calls and the explicit paused outcome to that same terminal/event path.

## 12. Tool semantics

### 12.1 PawFlow/server tools

Server tools come from the published agent and inherited tool/MCP filters. They execute through the normal ToolApprovalGate and permission_mode.

Their calls/results:

- remain in the internal transcript and checkpoint;
- are filtered from OpenAI/Anthropic client-tool output;
- may produce ordinary answer text or attachments;
- cannot be enabled, selected, or granted extra permission by request tool definitions;
- continue inside the same agent turn until the agent produces final output or a client-tool batch.

Publication permission_mode is mandatory. read_only is the recommended default for new exports. default must be an explicit owner choice. Exact checkpoint/fork support is a rollout-quality gate before default is broadly enabled, not a runtime requirement: checkpoint failure still reconstructs.

### 12.2 API client tools

Request tools are untrusted, request-scoped client capabilities. Extract the common behavior from AguiFrontendToolHandler into a protocol-neutral ClientToolHandler:

- strict bounded name/description/JSON Schema validation;
- no collision with a server tool or another client tool after canonical name resolution;
- no annotations or descriptions treated as authorization;
- calls produce stable opaque IDs and a durable pending batch;
- the handler never performs the external action.

The agent loop gains an explicit client_tool_pending outcome. After a model round:

1. server tool calls in a mixed batch execute normally;
2. client tool calls are recorded;
3. the loop pauses after the complete client batch instead of feeding fake results back to the model;
4. the dialect returns that batch with its native finish/stop reason;
5. the next request must settle every pending call exactly once, order-independently;
6. valid results are appended as tool-role ingress and the loop resumes.

This removes reliance on a prompt asking the model to stop. Parallel client calls are supported. A publication/provider that cannot enforce required or named tool_choice rejects those values; auto and none are the baseline. parallel_tool_calls=false is supported only after the agent loop can enforce it, otherwise a non-default false value is rejected.

Tool mappings:

| Normalized event | Chat Completions | Responses | Anthropic Messages |
| --- | --- | --- | --- |
| Client tool definition | tools[].function | tools[] function | tools[] |
| Client call | assistant.tool_calls[] | function_call output item | tool_use content block |
| Client result | role=tool + tool_call_id | function_call_output | user tool_result block |
| Pause reason | finish_reason=tool_calls | completed response containing function_call item(s) | stop_reason=tool_use |

A current tool result for an unknown, foreign-key, old-generation, already-settled, or canceled call is an invalid request. Historical untrusted tool blocks may be reconstructed as visible history but never settle a live pending call.

## 13. Dialect contracts

### 13.1 OpenAI Chat Completions

Baseline accepted request fields:

- model;
- messages;
- stream;
- tools;
- tool_choice values supported by the runtime;
- parallel_tool_calls when enforceable;
- stream_options.include_usage;
- bounded generation controls explicitly enabled by publication policy.

In default compatibility mode, temperature, top_p, seed, presence_penalty, frequency_penalty, stop, user, metadata, and equivalent advisory harness fields are accepted outside the transcript hash. The published agent owns generation settings; a field is applied only when the structured runtime declares support, otherwise it is a documented no-op. response_format with type=text and store=false are accepted no-ops. max_tokens/max_completion_tokens are accepted as a publisher-bounded output ceiling. strict_fields converts ignored non-default fields into invalid-request errors.

Version 1 supports n=1 and text plus configured image inputs. n greater than 1, audio output, logprobs, prediction, web-search-specific fields, store=true for Chat Completion CRUD, and non-text response formats fail explicitly.

Non-stream output uses chat.completion with an opaque chatcmpl_ ID, created timestamp, published model ID, one choice, assistant text/tool_calls, native finish_reason, and real usage when available.

Streaming uses chat.completion.chunk frames, stable choice/index and tool-call indices, delta role/content/tool arguments, a terminal finish_reason chunk, an optional usage chunk when requested, then data: [DONE]. Validation and admission finish before req.complete_stream so auth/model/body/conflict errors keep their real HTTP status. SSE comment keepalives are allowed and ignored by the official SDK.

### 13.2 OpenAI Responses

Accepted input covers string input and the message/function-call/function-call-output item forms needed for multi-turn text and client tools. instructions, tools, tool_choice, stream, previous_response_id, store, and supported bounded overrides are validated. Responses instructions are request-scoped: previous_response_id does not inherit the prior request's instructions, so only instructions present on the current request are injected.

previous_response_id:

- must belong to the same publication, key, generation, and model;
- may have multiple children;
- resolves to its exact visible parent and, when available, its verified checkpoint;
- cannot reference a deleted, expired, failed, or incomplete response;
- cannot be combined with contradictory reconstructed history.

Non-stream output uses a native response object with an opaque resp_ ID, published model, status, previous_response_id, normalized output message/function_call items, error/incomplete details, supported settings, and real usage.

Streaming emits the native semantic lifecycle, including response.created, response.in_progress, output item/content part events, text or function-argument deltas, completed item events, and response.completed or response.failed. It does not append [DONE].

GET returns the stored native response envelope. DELETE tombstones it using the native deleted-response shape. store=false prevents later GET and makes the response ineligible as previous_response_id after the create call completes; it does not claim that PawFlow erased all internal audit/conversation data. This retention distinction is documented. A request that needs stateful continuation must use store=true.

background, conversation objects, hosted OpenAI tools, and remote MCP fields are outside the first delivery.

### 13.3 Anthropic Messages

Accepted fields include:

- model;
- max_tokens;
- messages;
- top-level system;
- stream;
- tools and supported tool_choice;
- bounded generation controls enabled by publication policy;
- supported metadata fields that do not change identity.

The parser validates Anthropic alternation/content-block grammar, merges consecutive compatible roles only where the official contract permits, and normalizes text, image, tool_use, and tool_result blocks. The top-level system value becomes the leading client_instruction item. max_tokens is mandatory and enforced as a publisher-bounded output ceiling; common sampling and metadata hints follow the same compatibility/strict_fields policy as Chat Completions.

Non-stream output uses type=message, an opaque msg_ ID, role=assistant, published model, native text/tool_use blocks, stop_reason, stop_sequence, and real usage.

Streaming emits named SSE events: every frame carries event: message_start/content_block_start/content_block_delta/content_block_stop/message_delta/message_stop plus a JSON data object with the matching type. Tool JSON uses input_json_delta partial strings. A ping event or SSE comment may keep a silent connection alive and is ignored by the SDK. Post-header failures use event: error with Anthropic's native error object. Documentation recommends streaming for turns that may exceed the official SDK's non-stream timeout guidance.

## 14. Errors and HTTP behavior

Before a stream opens, each dialect returns its normal envelope:

OpenAI:

~~~json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "param": "messages",
    "code": "invalid_tool_result"
  }
}
~~~

Anthropic:

~~~json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "..."
  },
  "request_id": "req_..."
}
~~~

Map consistently:

- 400 malformed/unsupported request, invalid current tool result, or another permanent client conflict;
- 401 missing or invalid key;
- 403 origin or policy refusal only;
- 404 publication/model/response not found, including disabled/unavailable API surfaces, without leaking cross-key existence;
- 409 only for a transient admission/finalization conflict that a retry can resolve, with Retry-After;
- 413 request/tool schema/media limits;
- 429 concurrency/rate/session quota;
- 500 internal failure with sanitized detail;
- 503 no live agent runtime or transient admission failure.

Every response includes the dialect's request ID header. Logs use request/publication/key-prefix/session/run IDs and error codes, never raw keys, prompt bodies, tool results, or internal paths.

Anthropic uses its native top-level error types: authentication_error, permission_error, not_found_error, rate_limit_error, overloaded_error, and invalid_request_error. OpenAI/Anthropic SDKs retry several 408/409/429/5xx cases, so a permanent condition must never masquerade as a retryable 409.

After stream headers, emit the dialect's legal error event where one exists and close. Partial assistant output is marked failed and never becomes a reusable head.

## 15. Concurrency, retry, and crash rules

### 15.1 Claims and forks

A session head has one running lease. Admission compares publication generation, visible head, checkpoint, state, and lease version in one transaction.

- Current unique idle head: CAS and reuse.
- Current unique busy head: attach as a follower when namespace, visible parent, and body fingerprint match an active run inside the replay window; otherwise fork the matched checkpoint or reconstruct when it is unavailable.
- Known old prefix of an advanced session: fork that prefix checkpoint or reconstruct when it is unavailable.
- Several candidates for the same visible prefix: ambiguous; reconstruct without choosing a candidate.
- Responses children of one previous_response_id: each child gets the parent checkpoint when available and otherwise reconstructs the stored visible chain; one may reuse only when no sibling can observe mutation, otherwise fork/reconstruct.

No global per-key serialization is used. Independent sessions and branches run concurrently within configured quotas.

### 15.2 Retries

Chat Completions and Messages have no standard idempotency token. To prevent an SDK transport retry from duplicating an active tool-using run, the admission ledger coalesces only an exact same-key, same-generation, same-dialect, same-visible-parent, same-body-fingerprint request while the original lease is active and inside a short configured replay window. The follower replays the bounded normalized event journal and then joins live fanout. After terminal completion, the same request is admitted anew; there is no completed-response cache.

Internal deterministic message IDs and agent.request_msg_id fencing prevent a transport write from appending twice inside the admitted run. The coalescing decision is durable and made before a second wake-up can be enqueued.

Responses retrieval is idempotent by response ID. Create follows vendor semantics unless a future standard idempotency field is adopted.

### 15.3 Disconnect and cancellation

A stream disconnect removes that subscriber. It cancels the isolated active run only when no follower remains and publication policy does not allow detached completion. Cancellation releases its lease and marks the run canceled. Force-stop remains an immediate kill, not an agent error, and cannot poison the next turn. Any internal partial conversation after cancellation is quarantined or restored from its pre-run checkpoint before reuse.

Non-stream HTTP worker threads never run the model inline; they wait on AgentResultWaiter while the existing async runtime works.

### 15.4 Process crash

Runs heartbeat durably. Startup recovery:

1. finds expired running leases;
2. marks their runs abandoned;
3. never trusts the possibly partial internal head;
4. restores/forks from the last committed checkpoint or quarantines the session;
5. releases quotas;
6. removes unreferenced partial response/tool records.

Finalization is idempotent by run ID and body fingerprint. Replaying finalization cannot publish a second prefix, response, or tool batch.

## 16. Security and resource controls

- Namespace by authenticated key, never IP.
- Reuse the one-time key display, SHA-256-at-rest validation, HMAC compare, revocation, owner checks, origin policy, and private gateway.
- Extract publication auth into a neutral helper; dialect handlers own error wording.
- Require isolated context; reject shared and unsupported external runtime kinds.
- Make permission_mode explicit at publication, never request-selectable.
- Keep client tool schemas/results and imported history tagged untrusted.
- Reject client/server tool-name collisions.
- Run server tools through existing filters, ToolApprovalGate, relay routing, and read-only restrictions.
- Apply request-byte, item-count, per-item text, attachment, URL, tool-count, schema-depth, schema-byte, output, concurrent-run, session, and retained-checkpoint quotas.
- Reuse relay-aware URL validation and FileStore rules for media; never fetch arbitrary private URLs directly from the server process.
- Use HMAC/domain-separated transcript digests. Do not store raw canonical transcripts in the prefix index.
- Use constant-shape not-found responses across keys and generations.
- Never return hidden reasoning, server tool traces, internal prompts, paths, conversation IDs, checkpoint IDs, or service/provider identities.
- Ensure key revocation prevents new lookup and admission immediately; leased runs are canceled or allowed to finish according to one documented policy, then all state expires.
- Record audit events for enable/disable, key operations, generation bump, admission outcome, fork/rebuild/ambiguity, tool batch, cancellation, and cleanup without content.

## 17. Retention and cleanup

api_session_ttl_seconds is sliding from successful completion, not while a run/tool batch is open. Cleanup is lease-aware and transactionally claims expired rows before deleting conversations or checkpoints.

LRU quotas apply per publication/key and globally. Eviction order excludes running sessions, pending tool batches, live Responses parents, and referenced checkpoints. A cleanup tombstone prevents a concurrent request from resurrecting a session being deleted.

Cleanup removes:

1. expired/tombstoned prefix and response references;
2. unreferenced checkpoints;
3. the isolated internal conversation through ConversationStore deletion;
4. run/tool audit rows after their longer audit retention.

Generation bump stops new resolution into the old generation immediately and schedules it for the same cleanup path. Revoked-key state is not reassigned to a replacement key.

## 18. Implementation structure

Suggested modules, subject to local naming review:

- core/standard_api_types.py: normalized turn/output/tool dataclasses.
- core/standard_api_canonical.py: dialect canonicalizers and hash chain.
- core/_a2a_standard_api.py: SQLite schema, CAS, prefixes, responses, tool batches, cleanup.
- core/standard_api_runtime.py: resolve/rebuild/fork/submit/finalize orchestration.
- core/client_tools.py: shared request-scoped client tool handlers and pending outcome.
- core/standard_api_dialects/openai_chat.py: request parser and response/SSE translator.
- core/standard_api_dialects/openai_responses.py: response objects, semantic SSE, retrieve/delete.
- core/standard_api_dialects/anthropic_messages.py: request/content blocks and SSE translator.
- services/published_agent_auth.py: neutral publication/key/owner/origin lookup.
- services/standard_api_endpoint.py: route registration, headers, limits, pre-stream admission.
- core/_conversation_store_git.py or a focused checkpoint mixin: exact checkpoint/fork primitives.
- tasks/io/chat_ui/resources_standard_api.js: standard API fieldset, lifecycle controls, endpoint cards, and SDK snippets.

Do not put all dialects or store logic in one oversized endpoint file. Do not make outgoing provider mixins serve inbound HTTP requests. Shared normalization is below the dialect boundary; native JSON/SSE remains inside each dialect.

Expected existing-file changes:

- core/a2a_store.py: mixin/schema initialization and publication fields.
- core/agent_runtime_api.py: structured ingress and normalized terminal data.
- agent loop/tool execution modules: explicit client-tool-pending outcome and mixed-batch behavior.
- core/agui_tools.py and AG-UI runtime/tests: reuse the common client-tool primitive without changing AG-UI wire behavior.
- services/http listener startup/route registration: register standard endpoints idempotently.
- tasks/ai/actions/_agentres_k7.py and tasks/ai/actions/agent_resource.py: owner-only typed configuration, reset action, capability/runtime view model, and write classification.
- tasks/io/chat_ui/resources_a2a.js: keep the unified publication dialog and delegate the standard fieldset to the focused module.
- tasks/io/chat_ui/resources_render.js: relabel the repository entry as Published agents / APIs without changing its single-dialog behavior.
- tasks/io/chat_ui/i18n/en.json, fr.json, and es.json: every label, help text, state, validation, warning, and confirmation.
- tasks/io/serve_chat_ui.py: load resources_standard_api.js before resources_a2a.js.
- docs/a2a_integration.md and docs/agui_integration.md: link the new transport and clarify protocol-specific continuity.
- a new standard API user reference plus relevant parameter, security, and troubleshooting docs: reproduce the UI workflow and SDK snippets.

## 19. Migration

Use a one-shot schema migration consistent with PawFlow's no-backward-compatibility policy:

Add publication columns with the store's existing migration mechanism: inspect PRAGMA table_info(a2a_publications) and issue fixed-literal ALTER TABLE ADD COLUMN statements inside the appropriate _initialize_* mixin, as the current thread_ttl_seconds and managed_mode migrations already do.

1. Add required publication columns and API tables/indexes.
2. Set standard_api_enabled=false for every existing publication.
3. Do not synthesize api_model_id or permission mode.
4. Owners explicitly enable the feature and select model ID, permission mode, TTL, quotas, and dialects.
5. Initialize api_generation=1 on first enable.
6. Add resource/config change hooks that bump generation.
7. No existing A2A context or AG-UI thread is imported into the prefix index.
8. Rollback disables route registration and admissions; it does not reinterpret or merge stored sessions.

The migrated UI renders every existing publication with standard API disabled, no inferred model ID or permission mode, and an explicit “Configure to enable” state. It must warn that one API key should identify one harness/consumer when content-addressed Chat/Messages continuity is desired. For multiple indistinguishable sessions, issue separate keys or use Responses/AG-UI, which carry standard state identifiers.

## 20. Test plan

### 20.1 Canonicalization

Golden fixtures for every accepted message/content/tool shape:

- key ordering, meaningful absent/null distinctions, and SDK-default null/empty equivalence;
- Unicode and whitespace preservation;
- valid semantically equivalent tool JSON;
- invalid raw tool argument strings;
- ordered multimodal parts;
- Chat system/developer, Anthropic top-level system, Responses items;
- no collision across dialect/key/publication/generation/model/canonical/hash-secret version;
- body metadata and stream flag excluded from transcript identity;
- property tests for deterministic serialization and chain extension.

### 20.2 Store and resolver

- first request creates a session;
- next full-history request finds the longest server-output prefix and appends only the suffix;
- same transcript under another key/publication/generation/dialect never matches;
- identical initial user requests create independent sessions;
- multiple candidate heads are represented and detected;
- stale/busy heads use an exact checkpoint when available and reconstruct when it is not;
- no old request appends to an advanced head;
- SQLite head/prefix/response finalization is atomic and idempotent, while cross-store checkpoint finalization is two-phase and recoverable;
- API expiry/LRU/key revocation/generation cleanup is lease-aware;
- crash recovery never exposes a partial head.

### 20.3 Checkpoint correctness

- fork contains transcript, shared context, extras, bindings, hidden server tool calls/results;
- derived agent contexts are rebuilt and do not retain later rows;
- fork at boundary N excludes N+1;
- source mutation after checkpoint does not change it;
- API child conversations have no ConversationStore TTL, while api_export_sessions expiry still cleans them;
- references prevent early deletion;
- a forced checkpoint failure reconstructs rather than producing a permanent conflict.

### 20.4 Tool loops

For each dialect:

1. client declares one tool;
2. agent emits a native call;
3. response stops with the native tool reason;
4. client returns the result;
5. resolver finds the prior call boundary;
6. the pending call settles once;
7. agent returns final text.

Also cover parallel calls, out-of-order results, duplicate/missing/unknown/foreign calls, changed tool definitions, name collisions, mixed server/client call batches, server tool invisibility, read-only enforcement, disconnect while pending, and rebuild attempts with forged historical tool blocks.

### 20.5 SDK contract tests

Run pinned official Python SDKs against a real local listener:

- OpenAI chat.completions.create, stream false/true, text, image where enabled, tool round trip, model errors.
- OpenAI responses.create, stream false/true, previous_response_id branch, function call output, retrieve/delete.
- Anthropic messages.create and messages.stream, system/text/image where enabled, tool round trip, version/auth errors.

Assert parsed SDK objects, not only raw JSON. Add raw-wire golden tests for chunk/event order, IDs, finish reasons, usage, error bodies, headers, keepalive behavior, and [DONE] only where required.

### 20.6 Concurrency and faults

- two simultaneous children of one Chat/Messages prefix;
- two simultaneous Responses children;
- exact retry during a running lease attaches once, replays buffered events in order, and never enqueues a second wake-up;
- same visible parent with a different body does not attach to the active run;
- kill between model completion and finalization;
- kill during checkpoint, fork, and cleanup;
- database busy/rollback;
- SSE disconnect before and after first delta;
- force stop followed immediately by a clean next request;
- ambiguous same-key identical sessions;
- max-concurrency and session quota races.

Use barriers/fault injection, not timing sleeps.

### 20.7 Security and regressions

- malformed auth and cross-key response IDs have indistinguishable not-found behavior;
- client fields cannot set permission_mode, principal, run handle, internal ID, or tool policy;
- SSRF/media and schema limits;
- no raw key/content/internal path in logs or errors;
- A2A and classic/managed AG-UI suites remain green;
- existing Telegram/flow/Google Chat AgentRuntimeAPI callers retain their behavior;
- publication UI owner ACL and key lifecycle tests.

### 20.8 Publication UI and lifecycle

- resources_standard_api.js is loaded before resources_a2a.js, and the unified dialog remains reachable from the renamed repository section;
- existing test_a2a_ui_is_loaded_and_translated literals remain valid or are updated atomically, while existing i18n keys and A2A/AG-UI controls remain present;
- English, French, and Spanish catalogs contain every new key;
- migrated publications render standard API off without invented required values;
- global, standard-API, and per-dialect switches produce the exact typed save payload and effective-state badges;
- shared context cannot enable standard API in either the DOM path or a forged action payload;
- unavailable build capabilities cannot be enabled by editing the DOM;
- first enable validates model ID, permission mode, dialect selection, TTL, quotas, modalities, overrides, and disconnect policy;
- a failed save preserves the draft, shows field errors, and does not change displayed server state;
- disabling standard API leaves A2A/AG-UI enabled, while global disable affects every transport;
- disable, re-enable, material edit, and Reset API sessions have the specified generation and draining behavior;
- key creation is one-time-only, raw keys leave UI state on close, and revocation warns that it affects every transport;
- endpoint/model copy controls and OpenAI/Anthropic SDK snippets use the exact advertised base URLs without proprietary headers;
- keyboard focus, labels, confirmations, narrow viewport behavior, and secret non-disclosure have automated coverage;
- handler tests cover owner ACL, strict JSON boolean/integer validation, atomic configuration, capability validation, safe runtime summaries, and asynchronous delete state.

## 21. Observability

Add bounded metrics:

- requests and latency by dialect/stream/status;
- prefix lookup hit, miss, ambiguous, current-head reuse, active-run attachment, checkpoint fork, reconstruction, and checkpoint-unavailable fallback;
- hash bytes/time and lookup candidate count;
- active sessions/runs/leases/pending tool batches;
- checkpoint create/fork time and retained bytes;
- disconnect, cancel, abandoned recovery, cleanup;
- errors by stable code;
- input/output/cached token usage when available.

Structured logs correlate request_id, publication_id, key_id, api_generation, session_id, run_id, response_id, and outcome. Content and secrets are excluded.

## 22. Delivery phases and gates

### Phase 0: Contract fixtures and neutral auth

- Freeze accepted-field matrices and native JSON/SSE golden fixtures.
- Extract publication authentication without changing A2A/AG-UI behavior.
- Add publication configuration fields disabled by default.
- Extend the owner action/view model with capability data and typed standard API fields, still disabled.

Gate: existing A2A/AG-UI tests plus auth/error fixture tests pass.

### Phase 1: Canonicalization, session store, CAS, and reconstruction

- Implement normalized types, canonicalizers, HMAC chain, tables, CAS, active-run replay, cleanup, and visible-history reconstruction.
- Add generation invalidation hooks.

Gate: canonical/property, store/concurrency, replay, reconstruction, crash, expiry, and cross-namespace tests pass.

### Phase 2: Structured ingress and client tools

- Add protocol-neutral structured AgentRuntimeAPI submission.
- Extract ClientToolHandler and explicit pending outcome.
- Normalize terminal usage/finish/tool data.
- Keep AG-UI behavior stable on the shared primitive.

Gate: mixed server/client tool, permission, idempotent ingress, and AG-UI regression tests pass.

### Phase 3: OpenAI Chat Completions

- Implement models and chat/completions, non-stream then stream.
- Enable read_only canary publications only.
- Ship the unified owner UI with global/standard/Chat switches, model and policy controls, key warning, endpoint cards, snippets, disable/re-enable, and Reset API sessions. The Responses and Anthropic controls remain visibly unavailable.

Gate: official OpenAI SDK, raw-wire, concurrency, disconnect, security, owner-action, and publication UI lifecycle suites pass.

### Phase 4: Verified checkpoints and exact forks

- Add synchronous verified checkpoint commits, protected tags, tag-aware retention, reference release, and fork-from-checkpoint.
- Keep reconstruction as the required fallback for every checkpoint failure.

Gate: checkpoint/fork boundaries, retention, two-phase finalization, orphan recovery, and fault-injection suites pass.

### Phase 5: OpenAI Responses

- Implement create/stream, previous_response_id branching, function calls, retrieve/delete, store semantics.
- Advertise the capability and enable the Responses UI control/snippets only after the server gate passes.

Gate: official SDK response chains, parallel children, retrieval/deletion, and semantic SSE suites pass.

### Phase 6: Anthropic Messages

- Implement Messages parser, content blocks, auth/version, non-stream and native SSE.
- Advertise the capability and enable the Anthropic UI control/snippets only after the server gate passes.

Gate: official Anthropic SDK, tool, content-block, error, and concurrency suites pass.

### Phase 7: UI, documentation, rollout, and write-capable mode

- Complete owner runtime summaries, draining/deleting states, metrics/admin cleanup, and write-capable warnings; the core owner configuration already ships with Phase 3.
- Update all reference/user docs.
- Canary read_only, then broader read_only.
- Enable default/write-capable after exact checkpoint/fork, reconstruction, and side-effect fault gates are green; checkpoint failure still degrades to reconstruction.

Gate: full CI, end-to-end SDK smoke tests behind a reverse proxy, cleanup soak, security review, and no A2A/AG-UI regression.

## 23. Acceptance criteria

The project is complete when:

1. The official OpenAI SDK can use the advertised base URL/key/model for Chat Completions and Responses, streaming and non-streaming.
2. The official Anthropic SDK can use the advertised base URL/key/model for Messages, streaming and non-streaming.
3. A normal Chat/Messages follow-up reuses one internal conversation by longest known server prefix and appends only the new suffix.
4. Responses continuity and branches use previous_response_id.
5. Concurrent/stale/ambiguous requests never append to the wrong state.
6. Client tools complete a native two-request round trip; server tools remain internal and policy-gated.
7. No IP, proprietary header, overloaded user field, or SDK customization is required.
8. Cross-key/publication/generation/protocol state isolation, checkpoint safety, cleanup, and crash recovery have automated coverage.
9. Existing A2A and AG-UI wire behavior remains unchanged; the owner-triggered publication delete lifecycle becomes asynchronous for every transport so isolated children are reaped before the publication-row cascade.
10. User docs explain capabilities, unsupported fields, state semantics, per-consumer keys, retention, and safe permission modes.
11. Expired, evicted, ambiguous, old-generation, and checkpoint-less visible histories reconstruct without a permanent SDK dead end.
12. An exact retry can attach only to its matching active run; no completed-response cache changes native create semantics.
13. The owner can configure and independently enable/disable the global publication, standard API export, and each shipped dialect from the existing publication dialog, with backend-enforced isolated context and typed policy/limit validation.
14. The UI exposes exact base URLs, model ID, official-SDK snippets, shared key scope, one-time credential handling, API generation/reset, and the observable drain/delete lifecycle without exposing internal state.

## 24. Claude review disposition

Claude reviewed this document against the current PawFlow runtime/store and the protocol contracts. After integration, the final gate verdict was APPROVED with no remaining blockers. The review's three initial blocking findings were accepted:

1. Default compatibility mode now accepts common harmless/advisory harness fields, with strict_fields as an explicit less-compatible option.
2. Ambiguity, expiry, generation change, eviction, and checkpoint loss now reconstruct visible history in every permission mode instead of returning a permanent 409.
3. Checkpoints are now specified as synchronous verified commits pinned by protected tags, with no ConversationStore TTL on API children and reconstruction on checkpoint failure.

The review also corrected the existing done-payload capability, added schema-specific null/default canonicalization, request-scoped Responses instructions, native Anthropic SSE event names/errors, active-run retry attachment, preservation of the existing fenced ingress path, and delivery of Chat before checkpoint work. The reviewer explicitly agreed with key-scoped identity rather than IP, HMAC domain separation, server-boundary indexing, head CAS, no completed response cache, isolated-only publication, publication-owned permission mode, untrusted collision-checked client tools, neutral auth, native dialect streaming, and an explicit client-tool-pending outcome.

One additional clarification was incorporated during review: filesystem checkpoint creation and SQLite publication cannot be cross-store atomic, so finalization is a recoverable two-phase protocol with orphan cleanup.

After the owner identified the missing UI lifecycle, Claude performed a second review limited to sections 6.4-6.10 and their implementation, migration, test, delivery, and acceptance hooks. Its initial CHANGES_REQUIRED findings were all integrated: uniform 404 behavior for disabled standard surfaces, ordered ConversationStore child cleanup before publication-row cascade, the actual write-only action policy, partial-update preservation with a complete enable payload, preservation of existing UI/i18n assertions, UI-only key-label validation, isolated-context admission enforcement, and the repository's existing PRAGMA table_info plus ALTER TABLE migration pattern. The final UI gate verdict was APPROVED with no remaining blockers.

## 25. References

Protocol references checked for this plan:

- OpenAI Chat Completions API reference: https://platform.openai.com/docs/api-reference/chat/create
- OpenAI Responses API reference: https://platform.openai.com/docs/api-reference/responses/create
- Anthropic Messages API reference: https://docs.anthropic.com/en/api/messages
- Anthropic Messages streaming: https://docs.anthropic.com/en/api/messages-streaming
- AG-UI RunAgentInput types: https://docs.ag-ui.com/sdk/python/core/types
- AG-UI events: https://docs.ag-ui.com/concepts/events

Relevant PawFlow sources:

- core/a2a_store.py
- core/a2a_runtime.py
- core/agui_runtime.py
- core/agui_tools.py
- core/agent_runtime_api.py
- core/conversation_store.py
- core/_conversation_store_git.py
- core/tool_mcp_filters.py
- services/a2a_server_endpoint.py
- services/agui_server_endpoint.py
- docs/a2a_integration.md
- docs/agui_integration.md
