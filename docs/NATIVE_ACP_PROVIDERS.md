# Native ACP providers

PawFlow's `cursor-acp` and `grok-build-acp` providers use the same outbound
ACP process engine as `acp`: session reuse and loading, conversation/agent/service
ownership, ephemeral calls, MCP bridge credentials, attachment capability checks,
PawFlow tool authorization, and cancellation. Native questions use durable provider
interactions and always wait for a user response, including in automatic approval
mode.

## Configuration and registration

| Provider | Module and mixin | Validator | Default executable and arguments | Authentication |
| --- | --- | --- | --- | --- |
| `cursor-acp` | `core.llm_providers.cursor_acp.LLMCursorAcpMixin` | `validate_cursor_acp_config` | `cursor-agent acp` | `cursor_login` |
| `grok-build-acp` | `core.llm_providers.grok_build_acp.LLMGrokBuildAcpMixin` | `validate_grok_build_acp_config` | `grok agent stdio` | `xai.api_key` when effective `XAI_API_KEY` is set; otherwise `cached_token` |

Place both mixins before `LLMAcpMixin` in the client MRO and dispatch both providers
to `_stream_acp`. The shared `ACP_PROVIDERS` set includes both ids for lifecycle,
session cloning, abort, and delta-context handling. The validators return the
normalized ACP configuration dictionary, extended with the managed image and
runtime user. Both mixins inherit `LLMNativeAcpRuntimeMixin`; no extra client MRO
entry is required.

Use `auth_mode=none` and the existing `acp_*` service fields:

- `acp_cwd` is required and must name an existing non-root directory on the
  PawFlow server. Its resolved directory alone is mounted at the same absolute
  path and is the ACP session working directory. Docker host path translation
  applies to the mount source, while the path seen by ACP remains unchanged.
  Mounts overlapping native HOME, bridge files, or system runtime paths are rejected.
- `acp_command` overrides the executable **inside the managed image**. It is not
  looked up on the PawFlow host. An override must be installed in that image.
- `acp_args` is an optional JSON array of strings, replacing the complete default
  argument list when nonempty. Missing, blank, `[]`, and the UI string `"[]"`
  select native defaults: `["acp"]` for Cursor and `["agent", "stdio"]` for Grok.
  Commands run as argv, without a shell.
- `acp_env` is an optional JSON object of environment strings. Use
  `CURSOR_API_KEY` or `CURSOR_AUTH_TOKEN` for Cursor, and `XAI_API_KEY` for Grok,
  or use the service's **Login via server** action. Only explicitly configured
  environment entries are forwarded into the container, by name; host provider
  keys are not inherited. `HOME`, runtime/loader variables, `DOCKER_*`, `XDG_*`,
  and `PAWFLOW_*` are reserved. `auth_mode` must be `none`; top-level `api_key`
  and `credential_service_id` are rejected.
- `acp_auth_method_id` can explicitly select an advertised authentication method.
  Grok authentication sends `_meta.headless=true`.
- `acp_mcp_mode` defaults to `pawflow`; it requires the connected tool relay.
  `none` disables MCP injection. `acp_use_client_io` controls ACP client
  filesystem capabilities, while native questions and permission requests remain
  available independently.
- `acp_reuse_process` and `acp_load_session` default to true.
  `acp_additional_directories` must name existing non-root server directories.
  Descendants of `acp_cwd` share its mount; other explicit directories
  get individual mounts at the same absolute path and matching ACP metadata.
  Additional-directory capability checks and `acp_title_override` remain shared.

The managed image and default binary come from `native_cli_image(provider)` and
`native_cli_binary(provider)`, shared with login and version actions. Defaults are
`pawflow-claude-code:latest`, `cursor-agent`, and `grok`. Server overrides are
`PAWFLOW_CURSOR_IMAGE` / `PAWFLOW_CURSOR_BIN` and
`PAWFLOW_GROK_BUILD_IMAGE` / `PAWFLOW_GROK_BUILD_BIN`. `acp_command` takes precedence
over the binary default. `PAWFLOW_RUN_UID` / `PAWFLOW_RUN_GID` select the same
numeric runtime user as login. Images are used with `--pull never`; this connector
does not build, update, or install CLIs.

Each live ACP process owns a unique `docker run -i --rm` container. It mounts the
exact `native_cli_home(provider, user_id, service_id)` at `HOME=/native-home`, so
credentials written by **Login via server** persist across conversations and
container replacement. No other user's or service's auth HOME is automatically
mounted. The configured workspace and additional directories are writable;
the MCP bridge and its dependencies are mounted read-only when MCP is enabled.
No ports or Docker socket are exposed, no shared sessions root is mounted, and
the container drops Linux capabilities and prevents privilege escalation.

The shared ACP engine still owns process reuse, session loading, native
extensions, and cancellation. Ephemeral calls own separate containers. Force
stop removes the actual container before closing the Docker client, killing its
native agent and MCP children; clean close, failed startup, and stale-process
replacement also remove the owned container. Auth HOME remains on disk. Server
ownership labels let the existing shutdown reaper find these containers.

PawFlow MCP uses the in-container Python bridge. Relay and internal tokens travel
in ACP `mcpServers` environment fields, never Docker argv. Container-accessible
relay URLs replace loopback addresses. Tools routed through PawFlow retain its
approval and relay checks; native CLI tools operate within the mounted scope.
ACP client filesystem callbacks retain the generic relay path contract: configured
absolute paths are preserved in the container, ACP metadata, permission grants,
and relay calls. When `acp_use_client_io` is enabled, the connected relay must
expose the same project at those configured paths. A server-directory mount does
not remap an independent remote relay; disable client I/O if that relay uses a
different filesystem layout. Native CLI tools still use the mounted directories.

## Native interactions

Antigravity permission requests containing `interaction_*` option ids are treated
as questions before reaching the tool approval gate. Every supplied option id is
kept verbatim. Several options may all have `kind=allow_once`; that kind never
selects an answer. Cancellation or an invalid answer returns ACP's cancelled
outcome, and a question never grants filesystem write access.

Cursor supports:

- `cursor/ask_question`: single or multiple selections return exact question ids
  and `selectedOptionIds`, not labels.
- `cursor/create_plan`: displays the supplied plan and waits for explicit acceptance
  or rejection; returns the documented nested `outcome` object.
- `cursor/update_todos`: maintains merge/replace semantics by todo id, and emits
  complete snapshots through existing streamed tool-result blocks.

Grok supports plain or wrapped `x.ai/ask_user_question` and `x.ai/exit_plan_mode`
requests. Question answers are keyed by the original question text and contain
selected labels, as required by Grok's native extension. Custom text becomes
`annotations.notes`; single selections retain the option's `preview`. Exiting
plan mode asks for approval, abandonment, or requested changes. Requested changes
collect feedback before replying. Cancellation abandons the plan. An absent plan
body is explicitly shown as absent; the connector does not read native home
directories to infer a plan.

Native forms use `requester_kind=provider` and the existing cancellation event.
The shared form limits apply (16 questions, 64 options per question, 16,000
characters per question). Cursor and Antigravity do not enable custom answers.

Both plain extension names and underscore-prefixed wire names are supported.
SDK 0.12.1 requires exact plain-name aliases on its per-connection router; the
adapter registers only the provider's known methods and notifications.

## Grok completion and limits

Each Grok prompt includes a fresh opaque id as both `_meta.promptId` and
`_meta.requestId`. The standard ACP response races the private
`x.ai/session/prompt_complete` notification. A notification must match both
session and prompt id. Only one terminal result is published, and all pending
completion state is removed after success, failure, or cancellation.

Unlike the pinned reference's session-only fallback, id-less completion
notifications are ignored: they cannot safely distinguish a late previous turn
from the current turn. A CLI that only emits id-less completion and never returns
the standard prompt response must be updated to provide correlated completions.
Missing/unknown stop reasons and native errors fail the turn; rate limits remain
errors. They are never converted into successful completion.

The connector does not implement t3code's entire model catalog, model-option
picker, task/image notification UI, or private plan-file reconstruction.
Model/effort launch options can be supplied in `acp_args`; the generic service
model string is not negotiated through a native model picker. Cursor MCP support
depends on the installed CLI; its public docs describe project/user
`.cursor/mcp.json` and exclude dashboard team MCP servers.

Native CLI stderr is discarded to prevent a verbose process from blocking on an
unread pipe; use the CLI directly when authentication diagnostics are needed.

Validation uses mocked interactions plus real subprocess JSON-RPC fixtures.
Authenticated live Cursor/Grok CLI sessions were not exercised by these tests.
An offline initialization check of the built image also exercised Cursor
2026.09.02-c22c1a3 and Grok 1.0.13. Cursor advertised `cursor_login`; Grok
advertised `xai.api_key` when a fictitious environment key was supplied.
Without credentials, Grok advertised only its interactive `grok.com` login,
so the connector correctly requires login before starting a headless session.
This check used no network and did not validate credentials or invoke a model.

## Sources and verification

Contracts were checked against
[Cursor's ACP documentation](https://cursor.com/docs/cli/acp),
[Grok Build's headless and ACP documentation](https://docs.x.ai/build/cli/headless-scripting),
and [Grok's CLI reference](https://docs.x.ai/build/cli/reference).

Grok's private extension shapes and correlation metadata are based on
[t3code XAiAcpExtension.ts at dd7bc147](https://github.com/pingdotgg/t3code/blob/dd7bc147f799f290eb58578a7f81643ecf9ad52e/apps/server/src/provider/acp/XAiAcpExtension.ts);
Cursor's reference is
[CursorAcpExtension.ts at the same revision](https://github.com/pingdotgg/t3code/blob/dd7bc147f799f290eb58578a7f81643ecf9ad52e/apps/server/src/provider/acp/CursorAcpExtension.ts).
Cursor response envelopes follow its public documentation when the pinned
reference differs.

Managed launch/auth/mount/cancellation coverage lives in
`tests/test_native_acp_runtime.py`. Interaction coverage lives in
`tests/test_native_acp_providers.py` with
`tests/fixtures/native_acp_agent.py`. Existing provider, process-session,
Antigravity, and SDK conformance tests cover the shared engine.
