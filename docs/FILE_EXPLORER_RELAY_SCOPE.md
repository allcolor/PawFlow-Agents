# File explorer relay scope

The file explorer resolves a selected filesystem in the conversation that
offered it. A relay defined only in a conversation, such as `permisWS`,
must remain resolvable after it appears in the service dropdown.

## Request and resolution path

`file_explorer.js` passes its workspace surface's captured
`conversation_id` to every filesystem request through `action$` in
`rxbus.js`. Focusing another conversation does not change this scope.
`fs_list_services` resolves linked service definitions with that conversation,
and every filesystem operation in `tasks/ai/actions/files_fs.py` passes the
same ID to `find_fs_service(user_id, service_id, conversation_id)`.
For `fs_copy`, both the source and destination use this scope.

Previously, the dropdown used conversation scope but directory listing and
the other operations omitted it when resolving the live service. A relay
stored only in conversation scope therefore appeared in the dropdown and
then returned `Filesystem service not found`.

## Access checks

Filesystem actions check the requester's conversation role before service
lookup, using the existing conversation authorization helpers:

- Service listing requires read access.
- All live relay operations, including directory listing, file reads, search,
  writes, delete, mkdir, rename, copy, export to FileStore, command execution,
  and zip export require write access. This matches the existing
  `call_tool("read")` policy: reading shared conversation history does not
  authorize new access to the relay's filesystem.
- Unknown conversations and denied access return the same
  `Conversation not found` response with status 404.
- Filesystem operations require `conversation_id` (status 400 when absent).
  The service dropdown without a conversation still returns an empty list.

The existing filesystem resolver retains its linked-relay allowlist and
conversation, requester-user, then global registry lookup. An unlinked service
is rejected even when its definition exists in the user's registry. Naming a
foreign conversation does not grant access, and read-only collaborators cannot
use mutation or command execution actions. No relay is auto-linked by the
explorer.

Relay uploads carry the same captured tile conversation through
`uploadFileToRelay`. A batch retains its original service and directory even
if focus or navigation changes while a file is transferring. Closing the
explorer stops subsequent files in the batch and clears its clipboard.
Delete and paste batches also retain their original conversation, service,
paths and clipboard. Delayed responses cannot issue follow-up mutations after
the originating surface closes, and completed cuts do not erase a newer
clipboard selection. A failed operation stops the remaining batch.
`services/_http_upload_stream.py` requires a conversation and checks write
access before service lookup or body consumption. Denied uploads return the
same 404 as unknown conversations and close the unread HTTP connection.

## Focused verification

Run these selections with the PawFlow `run_tests` tool:

- `tests/test_file_explorer_relay_scope.py`: dropdown-to-directory regression,
  conversation/user scope, all operation lookups, missing context, conversation
  ACLs, unlinked relays, and copy source/destination checks.
- `tests/test_files_fs_actions.py`: existing filesystem action regressions.
- `tests/test_file_explorer_js.py`: frontend behavior, including the real
  `action$` request builder carrying conversation scope from the dropdown
  through directory navigation.
- `tests/test_file_explorer_relay_scope.py` also verifies upload permissions,
  unknown conversations, zero consumed bytes on denied uploads, and linked
  conversation-only relay uploads for owners and writers.
- `tests/js/file_explorer_upload_scope_spec.js` exercises real XHR query
  construction and preserves the tile and batch destination across changes.
- `tests/js/file_explorer_mutation_scope_spec.js` delays delete and copy
  responses across navigation and close/reopen, verifying that no follow-up
  mutation reaches another tile and that newer clipboard selections survive.

These are unit and JavaScript behavior checks. They do not connect to a live
user relay or activate a server hotpatch.
