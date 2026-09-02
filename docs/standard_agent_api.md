# Standard Agent APIs

PawFlow publications are the single owner-controlled source for A2A, AG-UI,
and the standard OpenAI- and Anthropic-compatible API transports. All
transports share the publication master switch and one-time Bearer keys.
Standard API export has additional, independently persisted policy fields and
is disabled for every existing and newly created publication until an owner
completes its configuration.

## Implementation status

The publication contract, canonical continuity model, durable session ledger,
structured runtime ingress, and client-tool pause/settlement bridge are
implemented. OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages
are advertised by the running build and support text, client tools, non-stream
responses, and native SSE streams.
Pending client calls are stored as one durable batch, must be settled completely
and exactly once under the successor run lease, and are restored if that lease
expires before completion.

Every completed server-output boundary attempts to create a verified immutable
ConversationStore Git checkpoint before the ledger transaction publishes the
new visible head. A stale or concurrently claimed unique prefix forks that exact
checkpoint without changing the source conversation. Checkpoint creation,
verification, or cloning failure never makes the API chain unusable: PawFlow
commits the visible head without a checkpoint or reconstructs the client-visible
history in a fresh isolated child. Checkpoint identities remain internal.

The owner action **a2a_get** returns a safe
**standard_api_capabilities** object containing dialect availability, allowed
permission modes and modalities, disconnect policies, numeric bounds, request
override fields, and draft suggestions. It also returns a content-free runtime
summary per publication. It never returns key hashes, raw keys, prompts,
transcripts, internal conversation IDs, provider identities, or checkpoint
identifiers.

## Publication configuration

**a2a_publication_configure** accepts these optional typed fields:

- standard_api_enabled
- api_model_id
- api_permission_mode
- api_session_ttl_seconds
- api_max_sessions_per_key
- api_max_concurrent_runs_per_key
- strict_fields
- api_request_overrides_json
- api_input_modalities_json
- api_chat_completions_enabled
- api_responses_enabled
- api_anthropic_messages_enabled
- api_disconnect_policy

Omitted standard API fields preserve their stored values. A disabled
publication may contain an incomplete draft. A request that explicitly enables
standard API export must provide the complete fieldset, use isolated context,
select at least one dialect available in the running build, and satisfy every
server-advertised bound. JSON booleans and integers are validated without
string coercion.

The initial bounds are:

| Field | Minimum | Maximum |
| --- | ---: | ---: |
| Model ID length | 1 | 128 characters |
| Session TTL | 60 seconds | 2,592,000 seconds |
| Sessions per key | 1 | 1,000 |
| Concurrent runs per key | 1 | 32 |

The currently advertised input modality is **text**. Permission mode is
**read_only** or **default**; **read_only** is the safe recommendation.
Disconnect policy is **cancel** or **finish_detached**.

## Configure a published agent in the owner UI

Open **Repository > Published agents / APIs**, then choose **Publish agents and
configure APIs/targets**. PawFlow keeps one publication card and one shared key
store for A2A, AG-UI, OpenAI, and Anthropic; there is no separate standard-API
settings page.

Create or edit a publication, keep **Context policy** set to **isolated**, and
expand **Standard OpenAI / Anthropic API** by selecting **Enable standard
OpenAI / Anthropic API**. Enabling requires explicit values for:

- at least one dialect available in the running server build;
- a published model ID;
- **read_only** or **default** permission mode;
- session retention, sessions per key, and concurrent runs per key;
- the mandatory text modality;
- **cancel** or **finish_detached** disconnect behavior.

The dialog reads dialect availability and numeric bounds from the server.
Unavailable dialect controls remain disabled and cannot be enabled by modifying
the browser DOM because the owner action validates the same capability
registry. **read_only** and **cancel** are the safe
recommendations. **finish_detached** may continue tools and side effects after
the client disconnects. Compatibility mode is the normal unchecked state;
**Strict fields** is less compatible with generic harnesses.

Availability has three independent levels: the publication master switch,
the standard-API switch, and each dialect switch. Turning off standard API
does not turn off A2A or AG-UI. Turning off one dialect leaves the others
unchanged. The publication card distinguishes active, disabled,
configured-but-publication-disabled, unavailable-in-this-build, deleting, and
enabled-without-a-live-key states.

Each card shows the exact OpenAI and Anthropic base URLs, the model ID, and
copyable Python streaming/non-streaming and curl examples for configured,
available dialects. Examples use **PAWFLOW_API_KEY** as an environment variable;
they never embed the raw credential.

Publication keys are shared by every transport. Enter a non-empty consumer
label and create one key per harness or consumer. The raw **pfa2a_** value is
shown once and is not retained in dialog state. Revoking it removes that
consumer's A2A, AG-UI, OpenAI, and Anthropic access.

Material changes advance the API generation and therefore reset session
matching after confirmation. **Reset API sessions** advances the generation
without changing configuration or keys. Disabling blocks new admissions while
already-admitted runs may drain. Deleting is a separate asynchronous action
covering every transport, shared key, and retained API session.

## OpenAI Chat Completions

Three independent switches must be on before Chat Completions is reachable:
the publication master switch, **standard_api_enabled**, and
**api_chat_completions_enabled**. The publication must use isolated context.
The same one-time **pfa2a_** Bearer keys used by A2A and AG-UI authenticate the
OpenAI-compatible routes; create a separately labeled key per consumer so it
can be revoked independently.

The configured OpenAI base URL and model are:

~~~text
Base URL: https://HOST/openai/{publication_id}/v1
Model:    {api_model_id}
~~~

An unmodified official OpenAI Python SDK can call the publication:

~~~python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["PAWFLOW_API_KEY"],
    base_url="https://HOST/openai/PUBLICATION_ID/v1",
)

completion = client.chat.completions.create(
    model="PUBLISHED_MODEL_ID",
    messages=[{"role": "user", "content": "Hello"}],
)
print(completion.choices[0].message.content)
~~~

Streaming uses the same client without a custom transport or PawFlow header:

~~~python
stream = client.chat.completions.create(
    model="PUBLISHED_MODEL_ID",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
~~~

The initial Chat contract supports:

- text **system**, **developer**, **user**, **assistant**, and **tool** history;
- client function tools, **tool_choice** values **auto** and **none**, and
  complete parallel tool-result batches;
- **n=1**, **store=false**, text **response_format**, and
  **stream_options.include_usage**;
- real usage when the selected provider reports it;
- compatibility-mode acceptance of common advisory harness fields such as
  sampling hints, seed, metadata, and token hints. These fields are currently
  documented no-ops because the published agent owns generation settings.

Strict-field mode rejects non-default compatibility no-ops. Stored Chat
Completion CRUD, non-text response formats, logprobs, audio, forced named tool
selection, **n>1**, and **parallel_tool_calls=false** fail before a stream
opens. The server returns native OpenAI-style error objects with an
**x-request-id**. A failure after SSE headers have opened is emitted as an
OpenAI-style error frame followed by **data: [DONE]**.

Client tools use the native two-request Chat pattern. PawFlow returns an
assistant message with **finish_reason=tool_calls**; the next request must replay
that assistant tool-call batch and provide exactly one **tool** result for every
call ID. PawFlow then resumes the same isolated internal conversation. PawFlow
server tools remain internal and are never exposed as client tool calls.

## OpenAI Responses

Responses uses the same OpenAI base URL and Bearer key as Chat Completions. The
publication master switch, **standard_api_enabled**, and
**api_responses_enabled** must all be on.

An unmodified official OpenAI Python SDK can create a stored response:

~~~python
response = client.responses.create(
    model="PUBLISHED_MODEL_ID",
    input="Summarize the deployment state",
    instructions="Answer in three short bullets",
)
print(response.output_text)
~~~

The **input** field accepts a non-empty string or supported text
**message**, **function_call**, and **function_call_output** items. Client
function definitions use the native Responses shape:

~~~python
tools = [{
    "type": "function",
    "name": "lookup",
    "description": "Look up a value",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}]
~~~

**tool_choice** supports **auto** and **none**.
**parallel_tool_calls=false**, hosted OpenAI tools, remote MCP fields,
background mode, conversation objects, and forced/named tool choice are rejected
before a stream opens. Sampling and output-token hints are accepted as
compatibility no-ops while the published agent owns provider settings; strict
field mode rejects them.

Stored responses can be continued and forked by ID:

~~~python
child = client.responses.create(
    model="PUBLISHED_MODEL_ID",
    previous_response_id=response.id,
    input="Now turn the second bullet into a checklist",
)
~~~

The parent must be a live, stored, completed response in the same publication,
Bearer key, API generation, dialect, and model namespace. Deleted, expired,
failed, incomplete, cross-key, and **store=false** response IDs are returned as
not found. A parent may have multiple children. Top-level **instructions** are
request-scoped: PawFlow injects only the instructions supplied on the current
create call and does not copy them into the retained visible response chain.

Client tools use the native Responses two-request pattern. A completed response
may contain one or more **function_call** output items. The successor request
references that response through **previous_response_id** and supplies exactly
one **function_call_output** for every pending **call_id**, with the same tool
schema. The batch is settled exactly once before the agent resumes.

Streaming emits named native lifecycle events, including
**response.created**, **response.in_progress**, output-item/content-part events,
**response.output_text.delta** or function-argument events, and exactly one
terminal **response.completed** or **response.failed** event. Responses streams
do not use a **[DONE]** sentinel.

Responses are stored by default for the configured session-retention window.
Retrieve or tombstone one with the official SDK:

~~~python
stored = client.responses.retrieve(response.id)
deleted = client.responses.delete(response.id)
~~~

The matching HTTP routes are **GET /responses/{response_id}** and
**DELETE /responses/{response_id}** under the configured OpenAI base URL.
DELETE returns the native **response.deleted** shape and immediately makes the
ID unusable for retrieval or continuation. **store=false** skips the public
response record, but it does not claim to erase PawFlow's internal
conversation/audit data created to execute the request.

## Anthropic Messages

The Anthropic base URL omits **/v1** because the official SDK appends
**/v1/messages**:

~~~text
Base URL: https://HOST/anthropic/{publication_id}
Model:    {api_model_id}
~~~

Use the same one-time publication key through Anthropic's native **x-api-key**
header. **Authorization: Bearer** is also accepted for generic compatible
clients. Every request requires **anthropic-version: 2023-06-01**. This build
does not advertise any **anthropic-beta** value, so a non-empty beta header is
rejected before the stream opens.

An unmodified official Anthropic Python SDK can call the publication:

~~~python
import os
from anthropic import Anthropic

client = Anthropic(
    api_key=os.environ["PAWFLOW_API_KEY"],
    base_url="https://HOST/anthropic/PUBLICATION_ID",
)

message = client.messages.create(
    model="PUBLISHED_MODEL_ID",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)
~~~

For long-running agents, prefer streaming:

~~~python
with client.messages.stream(
    model="PUBLISHED_MODEL_ID",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
~~~

**max_tokens** is a required positive integer. It is validated against the
request-safety bound, while the published agent remains the authority for its
generation settings. **temperature**, **top_p**, **top_k**, **stop_sequences**,
and **metadata.user_id** are accepted compatibility hints and are not forwarded
while request overrides remain closed; strict-field mode rejects their
non-default values.

The text-only contract accepts top-level **system**, user/assistant text blocks,
and the official client-tool shapes. Tool definitions use **name**,
**description**, and **input_schema**. PawFlow returns **tool_use** content
blocks with **stop_reason=tool_use**. The next request must replay that assistant
message and provide exactly one user **tool_result** block for every
**tool_use_id**. The durable batch is settled exactly once before the same
isolated conversation resumes.

Streaming uses named Anthropic events: **message_start**,
**content_block_start**, **content_block_delta**, **content_block_stop**,
**message_delta**, and **message_stop**. Tool arguments use
**input_json_delta.partial_json**. A failure after headers emits a native
**error** event and closes the stream. Pre-stream failures use Anthropic's
native error envelope and always include **request-id**.

## Generation and lifecycle

The first successful enable creates API generation 1. Any material API,
security, model, context, dialect, quota, retention, or publication master-state
change increments the generation after one exists. Label and description
changes do not. Old generations are never selected for new admissions.

**a2a_publication_reset_api_sessions** is an owner-only write action that
advances the generation without changing keys or configuration. Publication
deletion is asynchronous: **a2a_publication_delete** first disables admissions
and persists **delete_requested_at**; public A2A, AG-UI, and standard lookup
then treats the publication as not found while cleanup can drain and remove
child state.

## Session continuity and retention

Chat Completions and Anthropic Messages resolve continuity from a versioned,
HMAC-protected canonical form of the client-visible transcript. Responses resolves an explicit
**previous_response_id** to its stored canonical visible chain; when its exact
session head has advanced or is already claimed, PawFlow forks the verified
checkpoint attached to that boundary. If the checkpoint is unavailable, it
reconstructs a separate child from the stored visible chain. State is isolated
by publication, API generation, key, dialect, model, and
canonicalization/secret versions. Only completed server-output boundaries are
indexed; user-only heads never merge independent initial requests.

A unique current head is reused only after an atomic idle-to-running
compare-and-set. A unique stale or busy head can fork only when its checkpoint
ref still resolves to the exact immutable Git commit stored by the ledger.
Multiple identical candidates are intentionally ambiguous and cause
visible-history reconstruction in a fresh isolated conversation. The imported
history is marked client-supplied and historical tool calls are not executed.
An exact retry may attach to the same still-running request during a short replay
window, including replay of its bounded event journal. Completed requests are
never response-cached.

API session retention is owned by the durable publication ledger. Internal API
conversations have no normal ConversationStore TTL. Successful completion
slides the configured session expiry; running leases heartbeat independently.
Expired leases quarantine their sessions, and cleanup deletes the isolated
conversation before removing its ledger row. A generation reset immediately
schedules idle older-generation sessions for cleanup while allowing active
runs to drain; those runs expire as soon as they finalize.

## HTTP route shapes

OpenAI-compatible base URL:

~~~text
https://HOST/openai/{publication_id}/v1
~~~

Available OpenAI routes are:

~~~text
GET  /openai/{publication_id}/v1/models
GET  /openai/{publication_id}/v1/models/{model_id}
POST /openai/{publication_id}/v1/chat/completions
POST /openai/{publication_id}/v1/responses
GET  /openai/{publication_id}/v1/responses/{response_id}
DELETE /openai/{publication_id}/v1/responses/{response_id}
~~~

Anthropic-compatible base URL:

~~~text
https://HOST/anthropic/{publication_id}
~~~

Available Anthropic routes are:

~~~text
GET  /anthropic/{publication_id}/v1/models
POST /anthropic/{publication_id}/v1/messages
~~~

The registered route shapes match the approved design in
[STANDARD_AGENT_API_EXPORT_PLAN.md](STANDARD_AGENT_API_EXPORT_PLAN.md).
Authentication and owner/agent resolution use the neutral
**services/published_agent_auth.py** helper; each transport remains responsible
for its native error vocabulary.

See [A2A Integration](a2a_integration.md) and
[AG-UI Integration](agui_integration.md) for the already shipped transports.
