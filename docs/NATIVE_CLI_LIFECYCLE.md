# Native CLI lifecycle

PawFlow packages Cursor, Grok Build and OpenCode in the existing
`pawflow-claude-code:latest` tools image. The server requires no npm or Node
installation. Both `docker/claude-code/build.sh` and `core/update_manager.py`
resolve releases before building the same image.

## Installation, versions and updates

| CLI | Official release lookup | Image installation | Native maintenance command |
| --- | --- | --- | --- |
| Cursor | Build ID embedded in [official installer](https://cursor.com/install) | Complete versioned Linux bundle in `/opt/pawflow/cursor`; explicit `cursor-agent` symlink | `cursor-agent update` |
| Grok Build | [Stable channel](https://x.ai/cli/stable) | Versioned official Linux binary in `/usr/local/bin/grok` | `grok update` |
| OpenCode | npm `opencode-ai/latest` | Global pinned npm package in the tools image | `opencode upgrade` (native/package-manager installations) |

The image installation paths are independent of auth homes. Neither native
installer's ambiguous `agent` alias is installed. Cursor's whole bundle is
retained because its launcher needs adjacent resources. Native release
lookup failures abort a managed rebuild before Docker starts. Existing npm
resolution retains its `latest` fallback.

`stamp_versions.sh` records actual binary output in
`/opt/pawflow/cli_versions.json`. The inventory includes `cursor`, `grok`
and `opencode`. Cursor keeps its complete dated build identifier: a later
date is newer, while a changed same-day hash has no provable order and is
not automatically marked as an upgrade.

Managed upgrades use the existing administrator Updates dialog and its
asynchronous image rebuild, progress events and restart workflow. Rebuilding
does not edit mounted auth homes or profiles. CLI-native self-upgrade commands
are documented for separately managed installations; PawFlow does not run
them in a user's active container. Custom image overrides must be rebuilt
by their operator. This integration does not claim full image arm64 support:
the existing rclone and Antigravity ACP downloads still constrain the image.

## Service actions

The `llmConnection` schema exposes these actions for `cursor-acp`,
`grok-build-acp` and `opencode`:

| Server action | UI flow | Behavior |
| --- | --- | --- |
| `native_cli_server_login` | `native_cli_login_server` | Start a native browser/device login inside the existing noVNC dialog |
| `native_cli_status` | `simple` | Report stored native credentials and configured provider-environment presence without exposing values |
| `native_cli_versions` | `simple` | Check installed runtime-image version and latest release asynchronously |
| `native_cli_update` | `native_cli_update` | Require admin and matching managed image, then open the existing Updates dialog |

Login polling and cleanup use `native_cli_server_login_status` and
`native_cli_server_login_cleanup`; each session is bound to its initiating
user and service. Polling reads cached status, while Docker, version lookups
and credential collection run in background workers. A login container has
a bounded lifetime. Login is available after saving the service.

No new OAuth credential-pool provider IDs are introduced. Authentication
belongs to the native CLI service, matching the Antigravity ACP pattern.
Stored-auth status deliberately does not claim that a token is valid; the
native CLI verifies tokens when connecting.

## Shared runtime/auth contract

`core/native_cli_auth.py` provides:

- `native_cli_home(provider, user_id, service_id)`
- `native_cli_image(provider)`
- `native_cli_binary(provider)`
- `native_cli_user_spec()`

Cursor and Grok homes live under
`RUNTIME_DIR/sessions/native-cli/homes/<identity-digest>`; the digest includes
provider, user and service. Runtimes must use the same helper for auth.
Cursor login retains its whole private service home because the vendor does
not document its credential-file layout. The successful-login marker is only
a presence signal; it is not a credential. Cursor's CLI owns configuration
and auth contents; an image rebuild does not replace them.

Grok login runs `grok --no-auto-update login --device-auth` and merges
`.grok/auth.json` by scope key, preserving other profiles. API-key execution
uses `XAI_API_KEY`; Cursor supports `CURSOR_API_KEY`. Runtime configuration
must set their provider environment consistently.

OpenCode delegates image, binary and home selection to `OpenCodePool`.
The image defaults to `PAWFLOW_OPENCODE_IMAGE=pawflow-claude-code:latest`,
and the binary to `PAWFLOW_OPENCODE_BIN=opencode`. Login runs
`opencode auth login` in a temporary home and merges credentials into
`OpenCodePool.home_dir(user_id, service_id)/.local/share/opencode/auth.json`.
Other providers in that file are preserved. Writes retain the existing inode
so session auth symlinks remain connected. The service uses native provider
authentication (`auth_mode=none`); it does not confuse provider credentials
with HTTP server authentication.

Cursor overrides are `PAWFLOW_CURSOR_IMAGE` and `PAWFLOW_CURSOR_BIN`;
Grok overrides are `PAWFLOW_GROK_BUILD_IMAGE` and `PAWFLOW_GROK_BUILD_BIN`.
Defaults are the shared image and `cursor-agent`/`grok`.
Login executes with numeric `PAWFLOW_RUN_UID/GID` (default `1000:1000`).

## Validation and limits

Claude native permission hooks honor the conversation's `read_only` allowlist
before presenting a prompt and again before returning consent. A native allow
answer cannot override that mode, including when the mode changes while the
prompt is pending. Native questions remain available.

Cursor bundles and Grok binaries currently use versioned HTTPS downloads without
an independently verified checksum or signature. Version pinning does not verify
artifact integrity. Adding trusted vendor digest/signature verification remains
separate supply-chain hardening work. Login VNC retains `x11vnc -nopw`, as accepted
by the deployment operator.

Focused tests cover release parsing/failure, build parity, version stamping,
Cursor ordering, isolated homes, credential preservation and permissions,
login commands, session ownership, action guards and runtime-image version
selection. Shell and JavaScript syntax checks accompany these tests.

A separate validation image built on 2026-09-05 contains Claude Code
2.1.261, Codex 0.153.4, Gemini 0.58.0, Cursor 2026.09.02-c22c1a3,
Grok 1.0.13, OpenCode 1.18.29, Antigravity 1.1.26 and Antigravity ACP 1.1.1.
Offline checks exercised Claude question answers and cancellation through
both control messages and hooks, OpenCode health/session CRUD/SSE, and
Cursor/Grok ACP initialization. These checks used no authenticated model
calls. Interactive native logins and the tmux UI remain untested.
Cursor same-day release ordering and its credential schema remain vendor
limitations. The validation image did not replace the production image.

Official references:
[Cursor auth](https://cursor.com/docs/cli/reference/authentication),
[Cursor installation](https://cursor.com/docs/cli/installation),
[Grok installer](https://x.ai/cli/install.sh),
[Grok authentication](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md),
[OpenCode CLI](https://opencode.ai/docs/cli/).
