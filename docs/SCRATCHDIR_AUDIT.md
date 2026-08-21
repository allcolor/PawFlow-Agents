# ScratchDir Production Temporary-Path Audit

Audit date: 2026-08-21

## Rule

ScratchDir owns temporary files that are agent-visible, resumable across tool
calls, or scoped to a user, conversation, agent, and relay. Process-local
staging that is created and removed inside one operation remains an operating
system temporary. Atomic sibling files, OS sockets, generated container
credentials, container tmpfs mounts, and relay service runtime files are also
not ScratchDir consumers.

The audit searched production Python under `core/`, `tasks/`, `services/`,
`pawflow_relay/`, and `tools/` for `tempfile`, `/tmp`, `.pawflow/`,
and `.scratch/`.

## Migrated or removed

| Location | Decision |
|---|---|
| `core/pfp_runtime/_bridge.py` package cache and run inputs/outputs | Migrated to `fs://scratchdir/pfp/...`; successful runs clear, failed runs remain until expiry. |
| `core/pfp_runtime/_bridge.py::_child_env` | Removed the last `.pawflow/sdk` fallback. `PAWFLOW_PFP_SDK_PATH` is mandatory and missing wiring fails closed. |
| `tasks/ai/actions/files_fs.py` relay zip creation | Existing one-operation relay staging remains process-local; the result crosses into FileStore before return. No resumable state. |

## Retained: bounded one-operation transfer or media staging

These paths exist only for the duration of one synchronous operation, are
cleaned in `finally` or a context manager, and are never returned as an agent
workspace:

- `core/handlers/copy.py`
- `core/handlers/read.py`
- `core/handlers/see.py`
- `core/handlers/show_file.py`
- `core/scratchdir_manager.py` (ScratchDir-to-FileStore promotion)
- `core/service_install.py`
- `core/pfp_runtime/_bridge.py` (final server-local FlowFile copy)
- `tasks/ai/actions/files_fs.py`
- `tasks/ai/actions/media.py`
- `tasks/io/_telegram_voice.py`
- `tasks/io/azure_tasks.py`
- `tasks/io/gcs_tasks.py`
- `tasks/io/s3_tasks.py`
- `tasks/io/sftp_tasks.py`
- `tasks/system/execute_script.py`
- `services/_comfyui_client.py`
- `services/_relay_http_response.py`
- `services/luxtts_service.py`
- `services/package_runtime_service.py`
- `services/voxcpm_tts_service.py`
- `services/web_search_service.py`
- `tools/screen_actions_cua.py`

## Retained: atomic writes and bounded spools

These are implementation-private siblings or anonymous spools whose lifetime is
one atomic operation:

- `core/checkpoint.py`
- `core/project_graph.py`
- `tasks/ai/actions/_conv_base.py`
- `tasks/io/flow_management.py`

## Retained: explicit multi-request protocols

`tasks/ai/actions/_conv_import.py` stages a chunked archive across multiple
HTTP requests. It is server-owned protocol state keyed by an unguessable import
ID, explicitly deleted on completion/error, and is not an agent filesystem.
Changing it to relay-scoped ScratchDir would move an HTTP upload protocol to the
wrong machine.

Background shell output in `core/handlers/bash.py` is server-owned job output
with its own background-result lifecycle. It is not general temporary storage
and cannot be moved to a conversation relay without changing where server
background jobs execute.

## Retained: isolated runtime and operating-system paths

The following are runtime sockets, logs, FUSE mounts, generated credentials,
sandbox allowlists, or tmpfs mounts. They are deliberately local to the process,
relay, or disposable container and are not user file state:

- `core/_cci_pool_spawn.py`
- `core/antigravity_observer_pool.py`
- `core/claude_code_pool.py`
- `core/codex_pool.py`
- `core/gemini_pool.py`
- `core/handlers/web_execute.py`
- `pawflow_relay/_relay_codeserver.py`
- `pawflow_relay/_relay_desktop.py`
- `pawflow_relay/_relay_fs_setup.py`
- `pawflow_relay/_thread_docker.py`
- `pawflow_relay/remote_mounts.py`
- `pawflow_relay/server_fs_mount.py`
- `pawflow_relay/worker.py`
- `tasks/ai/actions/_sf_k9.py`
- `tools/ag_observer_proxy.py`
- `tools/cc_interactive_common.py`
- `tools/fs_exec.py`
- `tools/fs_screen.py`

`core/installer_deployment.py` only documents the durable PawFlow installation
runtime under `~/.pawflow/runtime`; it does not create ad-hoc temporary files.

## Gate

No production occurrence remains that uses `/tmp`, `.pawflow`, `.scratch`,
or `tempfile` as resumable agent file storage. New resumable temporary-file
consumers must use `fs://scratchdir/`; unavailable ScratchDir capability must
fail closed.
