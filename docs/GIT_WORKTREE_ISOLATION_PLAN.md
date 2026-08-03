# Git Worktree Isolation for Parallel Agents — Complete Implementation Plan

Status: **proposed, implementation-ready architecture plan; no implementation yet**
Priority: **P1 / high-priority roadmap item**
Scope: isolated Git workspaces for spawned agent tasks, deterministic integration, and first-class `/batch` orchestration
Primary user outcome: parallel coding agents can modify the same repository without writing into one another's working directories, while PawFlow preserves reviewability, recovery, and control of every integration.

This plan is authoritative for the “Git worktree isolation for agents” item in
`ROADMAP.md). It supersedes the short A3 sketch in `docs/ROADMAP_GAPS.md`.
That sketch is directionally correct but unsafe as an implementation: a worktree
path must not be derived from an agent name, an agent must not receive free-form
worktree-management tools, and a completed LLM response is not sufficient reason
to mutate a checked-out target branch.

## 1. Decision summary

PawFlow will isolate **spawned tasks**, not agent identities.

A real spawned task already has a stable task ID and normally receives a
sub-conversation ID of the form:

~~~text
<parent_conversation_id>::task::<task_id>
~~~

That task ID becomes the owner of one durable `WorkspaceLease`. The lease pins:

- the owning user, conversation, task, and agent;
- the exact relay and repository identity;
- the target worktree and task branch;
- the base commit OID;
- a fencing epoch;
- the requested integration policy;
- creation, renewal, seal, integration, and cleanup timestamps.

The physical worktree is created by the repository-owning PawFlow Relay in its
managed runtime state, outside the user's checked-out repository. The model sees
the leased worktree as its logical `/workspace`; it never receives the physical
path or a worktree capability token.

The following distinctions are mandatory:

1. A `delegate` call with `context="shared"` is a private message to an
   existing conversation agent. It does not create a sub-agent task and therefore
   does not create a worktree.
2. A spawned `isolated`, `last:N`, `summary:N`, or `full` task must carry
   an explicit workspace policy. Coding tasks use `git_worktree`.
3. Internal read-only advisors may explicitly use `workspace.mode="none"`.
4. `/batch` creates a durable batch, one spawned task and worktree per task,
   waits for all task candidates, detects overlaps, and integrates candidates in
   declared task order.
5. Worktree create, seal, integrate, and remove operations are control-plane
   operations. They are not general LLM tools and are never implemented by
   interpolating model-provided strings into shell commands.
6. Automatic integration is explicit authorization. Review mode never mutates the
   target worktree. Auto mode mutates it only when the target worktree is clean,
   still points at the expected commit, and the prebuilt integration candidate
   has passed all configured validation.
7. No stash, reset, checkout, forced removal, forced branch update, force push, or
   silent fallback is part of the automatic path.

## 2. Why the roadmap sketch is insufficient

The existing sketch proposes:

~~~text
git worktree add .worktrees/{agent_name} -b {branch}
git merge .worktrees/{agent_name}
git worktree remove .worktrees/{agent_name}
~~~

That shape has several correctness and safety defects:

- one agent name can run multiple tasks, so it is not a unique owner;
- an agent name and branch supplied through a tool are command/path injection
  inputs;
- a nested `.worktrees` directory can dirty a user repository that does not
  ignore it and can be recursively discovered by tools;
- a worktree path is not a merge target; Git merges a ref or commit;
- worktrees share the repository's common Git directory and refs;
- a task may finish with dirty tracked files, untracked files, ignored artifacts,
  zero changes, or commits that are not descendants of the pinned base;
- the target branch or target working directory may change while tasks run;
- the main agent or the user may have uncommitted changes;
- two completed agents can overlap without producing a textual merge conflict;
- merging from a hidden worktree into a branch checked out elsewhere can
  desynchronize that checked-out worktree's index and files;
- relay disconnects create unknown outcomes that cannot safely be retried blindly;
- removing a failed worktree destroys the most useful debugging artifact;
- an LLM response marked “done” is not a Git transaction boundary;
- the existing `/batch` command is only a prompt wrapper and has no task,
  repository, lease, merge, cancellation, or recovery state.

The implementation must solve these conditions centrally rather than teaching the
model a more elaborate sequence of Git commands.

## 3. Goals

1. Give every write-capable spawned coding task a distinct Git working directory.
2. Keep filesystem tools, bash, CLI compatibility behavior, checkpoints, and
   read-before-edit state pinned to that task's worktree for its entire lifetime.
3. Allow the main agent, user, and sibling tasks to continue without sharing task
   working-directory writes.
4. Pin every task to an exact base OID and repository identity before any LLM cost
   is incurred.
5. Produce a reviewable candidate commit even when the agent leaves ordinary
   tracked or untracked changes uncommitted.
6. Detect path overlap, Git conflicts, unsafe repository state, target movement,
   and validation failures before changing the target worktree.
7. Support explicit review and explicit automatic integration policies.
8. Make `/batch` a durable orchestration feature rather than a prompt prefix.
9. Preserve all agent messages, tool calls, Git lifecycle events, and results in
   the webchat.
10. Make cancellation immediate while cleanup and reconciliation remain
    asynchronous.
11. Reconcile server or relay restarts without duplicate worktrees, duplicate
    branches, or duplicate merges.
12. Bound disk, process, and worktree consumption per user, repository, relay, and
    batch.
13. Work on Linux, macOS, Windows, native relay, managed relay, PawCode relay, and
    Relay Desktop through one protocol and state model.
14. Ship unit, integration, concurrency, recovery, and end-to-end tests with the
    feature.

## 4. Non-goals

The first implementation does not:

- isolate ordinary `context="shared"` conversation agents from one another;
- turn Git worktrees into a hostile-code sandbox;
- clone or synchronize a repository to another workspace relay;
- move a task to another relay after its lease is prepared;
- integrate into a remote Git hosting service or open pull requests;
- push branches or tags automatically;
- resolve semantic conflicts with another LLM silently;
- stash, rewrite, reset, clean, or discard the user's working tree;
- support bare repositories;
- support repositories with active merge, rebase, cherry-pick, revert, bisect, or
  sequencer state;
- support submodules, nested Git repositories, or Git LFS filters in v1;
- share one worktree between separate write-capable tasks;
- expose worktree paths through FileStore or `/cc_sessions`;
- reuse a worktree merely because a later task uses the same agent name;
- provide cross-repository atomic commits;
- claim that OS-level malicious code is contained by Git;
- retain the current prompt-only behavior of `/batch`.

Submodules, LFS, nested repositories, pull-request publication, and clone-based
security isolation can be separate follow-up projects after the core lifecycle is
proven.

## 5. Current architecture and integration seams

### 5.1 Spawned agents

`core/handlers/spawn_agents.py` currently has two materially different paths:

- `context="shared"` delivers a private message to an existing conversation
  agent and does not create a `SubAgentExecutor`;
- other context modes resolve an `AgentTask`, create a real background
  sub-agent, and return immediately.

`core/_agent_executor_base.py` defines `AgentTask` and `AgentResult`.
`core/_agent_executor_loop.py` derives
`<parent>::task::<task_id>`, clones the provider client, registers the live
delegate, emits UI events, runs the tool loop, and performs final cleanup.

A second delegate from the same caller to the same target is injected into the
running task. That follow-up must retain the same workspace lease. It must never
create or select a new worktree mid-task.

### 5.2 Tool routing

CLI providers register their MCP bridge with:

- `user_id`;
- `conversation_id`;
- `agent_name`.

`services/_tool_relay_registry.py` caches a context-specific registry using
those values and configures filesystem handlers with the linked/default relay.
The task ID is therefore already recoverable from the task-shaped conversation
ID without trusting model arguments.

The in-process `SubAgentExecutor` currently receives a shared registry and
builds handler definitions from it. A mutable “current worktree” field on that
shared registry would race across parallel agents. The executor must instead
resolve a task-scoped registry after the lease exists.

### 5.3 Workspace access

`core/cli_workspace_mounts.py` defines MCP relay tools as the canonical
workspace path. Direct `/workspace` bind mounts are compatibility fallbacks.

`BaseFsHandler` receives user, conversation, agent, linked services, and the
default service. Filesystem methods and `bash` ultimately call the selected
`RelayService`. The relay resolves a requested path beneath its configured
root before dispatch.

This gives PawFlow one effective enforcement seam: a task-scoped relay view can
attach the lease ID and epoch to every request, while the relay replaces its
normal root with the lease's physical worktree root.

### 5.4 Relay behavior

`pawflow_relay/_relay_dispatch.py` resolves paths before filesystem and exec
actions. Worktree lifecycle RPCs must be added beside, not inside, the
model-visible filesystem action table. The relay owns repository discovery,
locking, subprocess execution, physical paths, and reconciliation.

### 5.5 Existing `/batch`

`tasks/ai/actions/command_dispatch.py` currently turns `/batch <instruction>`
into an ordinary agent message prefixed with “BATCH MODE”. It neither parses N
tasks nor owns background work. This path is removed in one migration and
replaced by the batch coordinator described below.

## 6. Terminology

- **workspace relay**: the linked PawFlow Relay that owns the repository files.
- **repository identity**: relay-issued stable ID for one canonical Git common
  directory, scoped to a relay and user.
- **target worktree**: the user's checked-out worktree into which an approved
  candidate may be integrated.
- **task worktree**: the physical linked worktree owned by one task lease.
- **task branch**: a PawFlow-owned Git ref containing one task's candidate.
- **integration worktree**: a temporary hidden worktree used to assemble and
  validate one or more merge commits without touching the target worktree.
- **integration ref**: a PawFlow-owned ref pointing at the validated aggregate.
- **workspace lease**: the fenced authorization binding a task to a task
  worktree.
- **seal**: revoke writes, inspect changes, validate ancestry and refs, and
  produce a candidate commit or a typed no-change/failure result.
- **integration**: fast-forward the target worktree to a previously validated
  integration ref after rechecking its exact preconditions.
- **batch**: an ordered manifest of tasks sharing one repository, base OID,
  target ref, and integration policy.

## 7. Non-negotiable invariants

1. Every batch, task, lease, operation, event, and result has a UUID and creation
   timestamp at creation.
2. A lease belongs to exactly one user, parent conversation, task ID, agent,
   relay, and repository identity.
3. A task's relay, repository, base OID, worktree, and lease epoch never change
   while the task is running.
4. Physical worktree paths are relay-generated and never accepted from an LLM,
   browser, or API client.
5. Git refs use server-generated UUID components, never raw agent names or user
   text.
6. A missing, expired, sealed, mismatched, or stale-epoch lease denies every
   mutating workspace request.
7. Relative paths, absolute `/workspace` paths, an explicit default relay ID,
   and the `workspace` alias all resolve to the same leased root.
8. `local=true` is denied for worktree tasks in v1 because it bypasses the
   relay root.
9. Writes to linked secondary relays are denied while a worktree lease is
   active. Reads may be allowed by the existing binding ACL.
10. FileStore and cognitive stores remain separate explicit tools; a worktree
    lease never redirects them.
11. A shared handler or registry never carries mutable per-task root state.
12. Worktree preparation finishes before the provider call and before the task
    is announced as running.
13. A task cannot use the target worktree after its worktree preparation
    succeeds.
14. A task cannot write after seal begins.
15. A final LLM answer does not imply successful seal or integration.
16. A dirty or unsupported target repository is rejected before task launch.
17. PawFlow never stashes, resets, cleans, force-checks-out, or discards user
    changes automatically.
18. Review mode never mutates the target ref or target worktree.
19. Auto mode requires explicit authorization recorded before tasks start.
20. Integration requires both the expected target OID and a clean target
    worktree at the final gate.
21. A target move or dirty target produces `integration_blocked`; PawFlow does
    not silently rebase or retry against a new base.
22. Candidates are integrated in batch declaration order, not completion order.
23. An overlap is reported even when Git could merge it textually.
24. A conflict never leaves the user's target worktree in a merge state.
25. Unknown outcomes are reconciled before retrying any create, seal, integrate,
    or remove operation.
26. Cleanup deletes only paths and refs whose recorded owner, lease, epoch, and
    expected OID still match.
27. Broad `git worktree prune`, wildcard ref deletion, and prefix-wide process
    deletion are forbidden.
28. Failed, conflicted, and blocked candidates remain inspectable until explicit
    cleanup or TTL expiry.
29. Force-stop kills the running task immediately, is not reported as an agent
    error, and never affects the next loop. Seal/reconciliation happens
    asynchronously afterward.
30. All agent traffic and all lifecycle changes remain visible in the webchat.
31. `selectedAgent` remains non-empty; batch state never changes conversation
    selection implicitly.
32. No unavailable relay, non-Git workspace, or capacity failure silently falls
    back to the shared workspace.
33. The feature never claims security isolation from malicious code; the threat
    boundary is stated honestly in UI and docs.
34. All lifecycle methods are idempotent under an operation UUID.
35. All state transitions use compare-and-swap revisions or fenced epochs.

## 8. Supported repository contract for v1

A repository is eligible only if all of these checks pass on the selected
workspace relay:

1. the relay is linked to the user/conversation and is writable;
2. the relay advertises `git_worktree_v1`;
3. `repo_path` is a required relative path beneath the relay root;
4. canonical resolution does not cross the relay root;
5. `git rev-parse --show-toplevel` resolves exactly one non-bare repository;
6. `git rev-parse --git-common-dir` is canonical and writable;
7. the target worktree is the discovered top-level worktree;
8. the target is on a local branch, not detached HEAD;
9. the target branch and HEAD resolve to the same OID;
10. `git status --porcelain=v2 -z --untracked-files=all` is empty;
11. no merge/rebase/cherry-pick/revert/bisect/sequencer operation is active;
12. no Git submodule entry, nested repository, or LFS filter is detected;
13. Git meets the documented minimum version;
14. the relay runtime state directory and configured disk budget are available;
15. the repository and user have available worktree quota.

All checks return typed codes and human-readable remediation. No check is
converted to a shared-workspace fallback.

The minimum Git version should be selected from the exact porcelain, worktree,
and merge-tree options used by the implementation and pinned in tests. The plan
assumes Git 2.31 or newer unless implementation testing proves a higher floor is
required.

## 9. User-visible workspace policy

### 9.1 Delegate task schema

For a real spawned task, `workspace` becomes required:

~~~json
{
  "agent": "backend",
  "message": "Implement the API endpoint and its unit tests.",
  "context": "isolated",
  "workspace": {
    "mode": "git_worktree",
    "repo_path": ".",
    "base_ref": "HEAD",
    "integration": "review"
  }
}
~~~

Rules:

- `mode` is `git_worktree` for normal spawned coding tasks.
- `mode="none"` is reserved for internal or explicitly read-only work that does
  not receive filesystem mutation tools.
- `context="shared"` does not accept a worktree policy because it is message
  delivery, not a task.
- `repo_path`, `base_ref`, and `integration` are explicit; no anonymous
  repository or merge-policy fallback is invented.
- `base_ref` is resolved once to an OID. The task stores the OID, not a moving
  textual ref.
- `integration` is `review` or `auto`.
- callers cannot provide physical paths, branch names, ref names, lease IDs, or
  epochs.

The tool description and common agent prompt must explain that spawned tasks
without a valid workspace policy are rejected before spawn.

### 9.2 Shared delegates

Shared delegates keep the conversation workspace and current relay bindings.
They are not advertised as isolated.

A running worktree task may send a reply or question to its source agent. It may
not spawn another write-capable shared collaborator in v1, because that would
either escape the lease or create two writers in one worktree. Read-only advisor
calls can explicitly inherit a sealed snapshot view. Independent write work must
be requested from the parent coordinator as another batch task.

### 9.3 Persistent tasks

`persist=true` retains the task conversation and worktree lease in
`suspended` state after seal only when there is no integration or cleanup.
Resume requires the exact prior task ID and a successful epoch renewal. Agent
name alone never resumes a worktree.

A sealed or integrated task cannot be resumed for writes. A new task receives a
new lease and base.

## 10. `/batch` command contract

The current prompt wrapper is removed. `/batch` becomes a server-side command
family shared by webchat, PawCode, VS Code, and API clients.

### 10.1 Natural-language planning

~~~text
/batch plan <instruction>
~~~

This runs a read-only planning pass and creates a durable proposed manifest. It
does not create worktrees or start write-capable agents. The response shows:

- selected repository and exact base OID;
- ordered task IDs;
- assigned agents;
- task instructions;
- expected file scopes when known;
- proposed validation commands;
- unresolved dependencies;
- requested integration policy.

The user can edit or reject the manifest.

For one-shot compatibility at the user-experience level, bare
`/batch <instruction>` is parsed as `/batch plan <instruction>`. It is not
the legacy prompt prefix and never starts mutation without a run command.

### 10.2 Direct structured run

Users who already know the task split can skip planning:

~~~text
/batch run --manifest <batch_id> --integrate review
/batch run --file fs://filestore/<file_id>/batch.json --integrate auto
~~~

The machine API accepts the same explicit JSON manifest. `--integrate` is
required. Missing integration policy is an error.

A direct manifest contains:

~~~json
{
  "repo_path": ".",
  "base_ref": "HEAD",
  "integration": "review",
  "tasks": [
    {
      "id": "uuid",
      "agent": "backend",
      "message": "Implement the endpoint.",
      "expected_paths": ["core/api/**", "tests/test_api.py"],
      "validation": ["tests/test_api.py"]
    },
    {
      "id": "uuid",
      "agent": "docs",
      "message": "Update the relevant English documentation.",
      "expected_paths": ["docs/**"],
      "validation": []
    }
  ]
}
~~~

Task IDs are minted by PawFlow if the manifest is created through the UI. An
external manifest may supply UUIDs only if they are unique and unused.

### 10.3 Lifecycle commands

~~~text
/batch status <batch_id>
/batch diff <batch_id> [task_id]
/batch integrate <batch_id>
/batch cancel <batch_id> [task_id]
/batch retry-integration <batch_id>
/batch cleanup <batch_id>
~~~

- `status` is read-only.
- `diff` returns a bounded summary plus a FileStore artifact for the complete
  patch when needed.
- `integrate` is an explicit user mutation and applies only a validated,
  current integration candidate.
- `cancel` immediately stops running tasks and asynchronously seals their
  worktrees.
- `retry-integration` creates a new integration operation after revalidation;
  it never blindly repeats an unknown prior call.
- `cleanup` removes only exact PawFlow-owned refs and worktrees after a clear
  confirmation when unintegrated changes exist.

### 10.4 Concurrency semantics

The batch coordinator bypasses delegate pair de-duplication. Two batch tasks may
use the same agent definition concurrently because identity is `task_id`, not
`(caller, target)`.

The number of runnable tasks is the minimum of:

- batch limit;
- per-user worktree limit;
- per-repository worktree limit;
- relay capacity;
- LLM service capacity;
- `SubAgentExecutor` capacity.

Capacity waits are visible states, not silent serial execution.

## 11. Core data model

### 11.1 `WorkspaceLease`

~~~text
lease_id: UUID
created_at: UTC timestamp
updated_at: UTC timestamp
revision: integer
epoch: integer
state: preparing | active | sealing | sealed | suspended |
       integrating | integrated | cancelled | cleanup_pending |
       cleaned | orphaned | error
user_id
parent_conversation_id
task_conversation_id
task_id
agent_name
relay_id
repository_id
repo_relative_path
target_worktree_id
target_ref
base_oid
task_ref
candidate_oid
integration_policy
physical_locator_id
expires_at
last_heartbeat_at
sealed_at
integrated_at
cleaned_at
error_code
error_detail
~~~

`physical_locator_id` is a relay-local opaque identifier, not a path.

### 11.2 `WorktreeBatch`

~~~text
batch_id: UUID
created_at
updated_at
revision
user_id
conversation_id
coordinator_agent
relay_id
repository_id
repo_relative_path
target_ref
base_oid
integration_policy
state
ordered_task_ids
integration_operation_id
integration_ref
integration_oid
validation_profile
error_code
error_detail
~~~

### 11.3 `WorktreeTaskRecord`

~~~text
task_id: UUID
batch_id
position
agent_name
message_hash
state
lease_id
base_oid
candidate_oid
changed_paths
change_summary
validation_status
result_file_id
patch_file_id
overlap_codes
error_code
error_detail
created_at
started_at
finished_at
sealed_at
integrated_at
cleaned_at
~~~

The full task prompt is already stored through conversation/task context and is
not duplicated into relay lease state.

### 11.4 `WorktreeOperation`

Every relay mutation records:

~~~text
operation_id: UUID
kind
lease_id
batch_id
expected_revision
expected_epoch
expected_ref_oid
state
request_hash
result_hash
created_at
finished_at
error_code
error_detail
~~~

An identical operation ID and request hash returns the prior result. Reuse with a
different hash is rejected.

## 12. Durable state ownership

The PawFlow server stores orchestration state in a dedicated SQLite database:

~~~text
data/runtime/worktree_isolation.sqlite
~~~

Tables:

- `worktree_batches`;
- `worktree_tasks`;
- `workspace_leases`;
- `worktree_operations`;
- `worktree_events`.

SQLite transactions provide local compare-and-swap revisions and restart-safe
batch state. No in-memory registry is the source of truth.

The relay stores authoritative physical lease state under its configured runtime
state directory:

~~~text
<relay_state>/git-worktrees/leases.sqlite
<relay_state>/git-worktrees/repos/<repository_id>/<task_id>/
<relay_state>/git-worktrees/integration/<operation_id>/
~~~

The physical directories must not live under the checked-out project and must
not require modification of the user's `.gitignore`.

The server stores only opaque locator IDs. The relay stores canonical paths and
never returns them to models or ordinary clients.

## 13. Relay capability and protocol

### 13.1 Capability advertisement

Relay registration adds a versioned capability:

~~~json
{
  "git_worktree": {
    "version": 1,
    "git_version": "2.x",
    "max_worktrees": 16,
    "state_available": true
  }
}
~~~

Absence means unsupported. The server does not probe by sending Git commands and
does not fall back.

### 13.2 Internal RPCs

Add typed, internal-only relay operations:

- `git_repo_inspect`;
- `git_worktree_prepare`;
- `git_worktree_heartbeat`;
- `git_worktree_status`;
- `git_worktree_seal`;
- `git_integration_prepare`;
- `git_integration_validate`;
- `git_integration_apply`;
- `git_worktree_remove`;
- `git_worktree_reconcile`.

They are methods on the server-side relay adapter and relay dispatcher, not
entries in `fs_actions.ACTIONS`, `ToolRegistry`, MCP schemas, or LLM prompts.

Each mutation carries:

- operation UUID;
- user and conversation ownership;
- repository ID;
- lease ID and epoch;
- expected state revision;
- expected Git OIDs.

Each response carries:

- operation UUID;
- resulting revision and epoch;
- typed status;
- relevant OIDs;
- timestamps;
- bounded diagnostics.

### 13.3 Execution rules

Git is invoked with an argv list through `subprocess`, never a shell string.
The relay supplies every ref and path after validation. User text is permitted
only in commit-message body trailers after control characters and length are
sanitized.

The relay uses explicit `--` path separators where applicable and validates
every OID as lowercase hexadecimal of the repository's object format.

## 14. Repository identity and locking

Repository identity is minted by the relay from:

- relay installation identity;
- canonical repository top-level path;
- canonical Git common-directory path;
- repository object format.

The server cannot choose or forge it.

All prepare, seal, ref, integration, and cleanup operations acquire a
cross-process repository lock. A Python thread lock alone is insufficient because
the user, Relay Desktop, PawCode, and multiple server connections may operate in
separate processes.

Implement a small cross-platform `RepositoryLock`:

- POSIX: `fcntl.flock`;
- Windows: an equivalent OS file lock;
- metadata sidecar: operation UUID, PID, start time, lease, and purpose;
- bounded acquisition timeout;
- no stale-lock decision based only on file existence.

The lock serializes PawFlow operations. It cannot prevent an external human
process from editing files, so every final mutation still rechecks Git OIDs and
worktree cleanliness.

## 15. Worktree preparation algorithm

For each task:

1. Authorize the user against the conversation's linked/default relay.
2. Require the workspace policy and `repo_path`.
3. Inspect the repository under the relay's repository lock.
4. Enforce the v1 repository contract.
5. Resolve `base_ref` and target ref to immutable OIDs.
6. Require a clean target worktree.
7. Snapshot PawFlow-owned and non-PawFlow refs relevant to tamper detection.
8. Reserve quota and mint lease ID, epoch 1, task ref, and opaque locator.
9. Persist relay state as `preparing`.
10. Create the task ref:
    `refs/heads/pawflow/tasks/<user-hash>/<conversation-uuid>/<task-uuid>`.
11. Create the physical worktree using the exact base OID and task ref.
12. Write a relay-owned marker outside the task's visible tree containing lease,
    epoch, repository, expected path, and ref/OID.
13. Verify `git worktree list --porcelain`, HEAD, branch, common directory,
    cleanliness, and ownership.
14. Persist relay state as `active`.
15. Persist the server lease with the returned revision and timestamps.
16. Build the task-scoped registry and only then start the LLM provider.

Any failure compensates only resources created by that operation and only after
ownership/OID checks. An unknown result is reconciled before compensation or
retry.

## 16. Task-scoped filesystem routing

Introduce a `WorkspaceRoute` value:

~~~text
mode
lease_id
epoch
relay_id
repository_id
logical_root = /workspace
read_secondary_relays
write_secondary_relays = false
allow_local = false
~~~

Introduce a `LeasedRelayView` implementing the same filesystem interface as
the current `RelayService`. It wraps calls with lease and epoch metadata.

Registry changes:

1. `ToolRelayService._get_registry` resolves a `WorkspaceRoute` from the
   full task conversation ID.
2. Its cache key includes lease ID and epoch.
3. Default-relay filesystem handlers receive `LeasedRelayView`.
4. Explicit resolution of the same relay ID returns the same leased view.
5. Secondary relay writes are rejected centrally.
6. `local=true` is rejected before host-helper forwarding.
7. A sealed/expired epoch invalidates the cached registry and denies mutation.

`SubAgentExecutor` changes:

1. accept a `registry_resolver(task, task_conversation_id, workspace_route)`;
2. prepare the lease before tool definitions are built;
3. resolve one task-scoped registry;
4. never mutate the parent registry's handlers;
5. include the route in provider call context;
6. seal/release it from the executor's single lifecycle `finally`.

Every filesystem handler, `bash`, `run_tests`, `security_scan`,
`project_graph`, and any composite tool that reaches a relay must traverse this
view. A source audit plus runtime tests must prove there is no direct resolver
bypass.

## 17. CLI provider behavior

MCP remains canonical. For worktree tasks:

- the MCP bridge registers with the task-shaped conversation ID;
- the server resolves that ID to the lease;
- direct compatibility mounting of the original workspace is disabled;
- `build_cli_workspace_mount_args` must never mount the target worktree at
  `/workspace` for an isolated task;
- v1 may mount the leased worktree only when an explicit, verified co-location
  contract exists; otherwise it emits no workspace bind;
- linked secondary relay mounts are read-only or absent;
- the session home under `/cc_sessions` remains provider-specific and unchanged.

Claude Code, Codex, Gemini, and interactive providers must share an executable
contract test showing that an isolated task cannot see or mutate the target
workspace through a native fallback.

The lease is workspace state, not provider-session state. Provider cold starts,
failover, compaction, and injected follow-ups keep the same lease and epoch.

## 18. Git command guard and threat boundary

Git worktrees share their common Git directory. Therefore they isolate ordinary
file edits but do not provide a security boundary against a malicious process
that intentionally mutates refs or the common directory.

PawFlow must state this plainly.

To prevent mistakes, worktree tasks add a command policy that denies through
`bash` and equivalent tools:

- `git worktree`;
- `git update-ref`;
- `git branch -f/-D/-d/-m/-M`;
- `git switch` or `git checkout` to another branch;
- `git merge`, `rebase`, `cherry-pick`, `revert`, `bisect`;
- `git reset --hard`, `git clean`, `git gc`, `git prune`;
- `git push`, `git fetch`, and remote mutation;
- commands that resolve and write the common Git directory directly.

Read-only Git inspection is allowed. Task-local `git add` and `git commit`
may be allowed, but PawFlow does not depend on the agent committing.

This policy is defense in depth, not a complete parser for arbitrary code.
A user who needs hostile-code containment must use a separate clone/container/VM
security boundary, which is outside v1.

At seal, the relay compares protected refs and repository operation state against
the preparation snapshot. Unexpected mutation quarantines the candidate as
`git_metadata_tampered`.

## 19. Seal and candidate creation

Seal begins only after the provider process and in-flight mutating tool calls have
stopped or returned a kill receipt.

Algorithm:

1. atomically transition lease `active -> sealing` and increment its epoch;
2. reject all older-epoch writes;
3. acquire the repository lock;
4. verify worktree identity, task ref, expected common directory, and ownership;
5. verify no Git operation is active;
6. compare protected refs with the preparation snapshot;
7. enumerate status with porcelain v2 and NUL delimiters;
8. build exact tracked/untracked/deleted/renamed path and byte manifests;
9. reject paths, symlinks, nested repositories, special files, excessive file
   counts, excessive bytes, or policy violations;
10. run secret scanning and configured candidate checks before staging;
11. if there are no changes and no task commits, return `no_changes`;
12. verify every task commit descends from `base_oid`;
13. reject merge commits in the task branch in v1;
14. stage the exact manifest with argv-safe path arguments;
15. create a synthetic final task commit if the worktree remains dirty;
16. run normal repository commit hooks; do not bypass them;
17. verify the worktree is clean and candidate OID is the task-ref OID;
18. produce bounded diffstat, path manifest, commit list, and patch artifact;
19. persist `sealed` with candidate OID and seal timestamp.

If a commit hook or scan fails, the task becomes `seal_blocked`; the worktree is
retained. PawFlow does not use `--no-verify` or discard changes.

Synthetic commits use an explicit PawFlow service identity and trailers for
batch, task, agent, conversation, base OID, and lease ID. They never include
secrets or the full user prompt.

## 20. Overlap analysis

After all batch tasks are sealed, compare their canonical change manifests.

Detect at least:

- exact same-path modification;
- add/add;
- modify/delete;
- rename/rename;
- rename against modification or deletion;
- directory deletion against descendant changes;
- case-fold collisions on case-insensitive filesystems;
- executable-bit and symlink-type changes;
- generated or binary artifact overlap.

Default batch policy is to block integration on any overlap. A future explicit
policy may permit textually clean overlaps, but the UI must still show them.

Expected path scopes are advisory for planning and diagnostics. Actual Git
manifests are authoritative.

## 21. Integration preparation

Integration never starts in the user's target worktree.

Under the repository lock:

1. require all selected candidates to be sealed and current;
2. require the target ref still equals the batch `base_oid`;
3. create a hidden integration ref from the base OID;
4. create a hidden integration worktree under relay runtime state;
5. merge candidate refs into it in declared task order with explicit non-fast-
   forward merge commits;
6. stop on the first conflict;
7. abort and verify the hidden integration worktree, never the user's target;
8. run batch-level hooks and validation commands in the integration worktree;
9. verify it is clean;
10. record integration ref, integration OID, ordered parents, validation results,
    and operation UUID;
11. remove the hidden integration worktree only after the ref and results are
    durable.

A conflict leaves all task candidates intact and marks the batch
`integration_conflict`. The complete conflict report is stored as a FileStore
artifact and summarized in webchat.

Validation commands are operator/user-approved manifest data, executed on the
workspace relay under existing exec policy. Missing approval or unavailable exec
capacity blocks integration; it does not skip validation silently.

## 22. Final integration into the target worktree

### 22.1 Review policy

Review mode stops at `ready_for_review`. It exposes:

- task commits and ordered aggregate commit;
- changed paths and diffstats;
- overlap and validation results;
- complete patch artifact;
- target/base/integration OIDs;
- an explicit Integrate action.

### 22.2 Auto policy

Auto mode proceeds only when all gates pass.

Final apply algorithm under the repository lock:

1. rediscover and verify the exact target worktree;
2. require target ref OID equals recorded base OID;
3. require target HEAD equals target ref OID;
4. require target status is completely clean;
5. require integration ref still equals recorded integration OID;
6. require base OID is an ancestor of integration OID;
7. run `git merge --ff-only <integration-ref>` in the target worktree;
8. verify target ref, HEAD, index, and files now match integration OID;
9. persist `integrated` with receipt and timestamp;
10. emit the durable integration event before cleanup.

The expensive merges and tests already occurred in the hidden integration
worktree, so final apply is a short fast-forward.

If the target moved or became dirty, return `integration_blocked`. Do not
rebuild automatically on a new base. The user can commit their work and request a
new integration-preparation operation.

### 22.3 Unknown outcomes

If the connection drops during final apply, the server records
`integration_outcome_unknown`. Reconciliation asks the relay for the operation
receipt and compares:

- target ref OID;
- target HEAD;
- integration ref OID;
- operation journal.

Only then does it mark integrated, blocked, or safely retryable.

## 23. Conflict resolution

PawFlow never asks a task agent to edit the user's target worktree.

For a conflicted or target-moved batch, offer explicit choices:

1. keep candidates and let the user resolve manually;
2. start a new dedicated resolver task in a new worktree based on the new target,
   with the candidate commits and conflict report as context;
3. abandon selected candidates;
4. export patches and clean up later.

A resolver result is a new candidate with its own lease, base, tests, and review.
It does not rewrite the original task refs invisibly.

## 24. Cleanup and retention

Successful integrated or no-change tasks are eligible for automatic cleanup.
Failed, cancelled, blocked, or conflicted tasks are retained for a configurable
TTL.

Cleanup sequence:

1. require exact lease ownership and terminal state;
2. acquire repository lock;
3. verify physical locator and worktree registration;
4. require worktree cleanliness when a candidate was sealed;
5. remove the exact task worktree without `--force`;
6. delete the exact task ref only if it still points at the recorded candidate
   OID;
7. delete the exact integration ref only if it matches its recorded OID and all
   needed receipts are durable;
8. remove relay lease metadata;
9. persist server state `cleaned`;
10. emit a timestamped cleanup event.

If any check fails, mark `cleanup_blocked`; never broaden the deletion.

Default retention values must be explicit configuration seeded by installation,
for example:

- integrated/no-change: cleanup after receipt;
- failed/cancelled: 24 hours;
- conflict/integration-blocked: 7 days;
- suspended persistent task: explicit TTL with heartbeat.

## 25. Restart and reconciliation

At server startup and relay reconnect:

1. load non-terminal server operations;
2. ask the owning relay for exact operation/lease records;
3. compare lease ID, epoch, repository ID, refs, OIDs, and physical markers;
4. finish known completed transitions;
5. mark absent or mismatched state `orphaned`;
6. do not create replacements until the prior outcome is known;
7. schedule exact cleanup for expired terminal leases;
8. surface unresolved orphans to operators and the owning user.

At relay startup:

1. load relay lease state;
2. enumerate only PawFlow-owned recorded worktrees and refs;
3. compare them with `git worktree list --porcelain`;
4. recover valid records;
5. quarantine ambiguous records;
6. never run broad `git worktree prune`;
7. never delete a path solely because its name resembles a PawFlow task.

Heartbeats renew active leases. Expiry fences writes immediately but does not
delete unsealed changes.

## 26. Cancellation and force-stop

Cancellation order:

1. record a timestamped cancel request;
2. revoke/advance the write epoch;
3. kill the provider and in-flight relay operations immediately;
4. publish `cancelled` to the UI;
5. asynchronously wait for kill receipts;
6. seal whatever changes remain, if safely inspectable;
7. retain the candidate/worktree according to policy;
8. clean up only through the normal exact cleanup state machine.

Cancellation is a normal terminal outcome, not an error injected into the next
agent turn.

Batch cancellation can target one task or all still-running tasks. Completed
sealed tasks remain reviewable unless explicitly abandoned.

## 27. Quotas and backpressure

Required explicit settings:

- maximum active worktrees per user;
- maximum active worktrees per repository;
- maximum tasks per batch;
- maximum aggregate worktree bytes;
- maximum changed file count per task;
- maximum changed bytes per task;
- maximum retained failed worktrees;
- prepare/seal/integration lock timeouts;
- active lease TTL and heartbeat period;
- terminal retention TTLs;
- allowed target refs or ref namespace;
- approved validation profiles.

Quota reservation happens before `git worktree add`. Exhaustion returns a
visible queued or rejected state. It never falls back to shared writes.

Disk usage is measured by the relay. Git object sharing means directory size
alone is insufficient; report both task-worktree files and PawFlow-attributable
new object estimates where feasible.

## 28. Security and authorization

1. The caller must own or be explicitly authorized for the conversation and
   linked workspace relay.
2. The relay binding must be writable for task preparation and integration.
3. Auto integration requires an explicit user-originated policy or approval
   receipt; an LLM cannot upgrade review to auto.
4. Lease tokens and epochs are server-to-relay metadata, never model arguments.
5. Unauthorized lease lookup returns the same not-found shape as an unknown
   lease.
6. Repository IDs and physical locators are opaque outside the relay.
7. Branch/ref segments are generated and validated.
8. All Git invocations use argv, fixed subcommands, bounded output, and timeouts.
9. Environment secrets are not written to commit messages, state databases,
   patches, or events.
10. Patch artifacts use existing FileStore ownership and authorization.
11. `local=true` and host-helper execution are denied for leased workspace
    operations.
12. Hooks execute only under the repository's existing exec/approval policy.
13. Secret scanning happens before synthetic commit and again before integration
    when configured.
14. Worktree tools cannot enumerate another user's leases or physical paths.
15. Server admin diagnostics show opaque IDs by default and reveal paths only in
    a privileged relay-local diagnostic surface.

## 29. Observability and webchat events

Persist and publish events such as:

- `batch_created`;
- `batch_manifest_ready`;
- `worktree_prepare_started`;
- `worktree_prepared`;
- `worktree_prepare_failed`;
- `task_started`;
- `task_seal_started`;
- `task_candidate_ready`;
- `task_no_changes`;
- `task_seal_blocked`;
- `batch_overlap_detected`;
- `integration_prepare_started`;
- `integration_conflict`;
- `integration_validation_started`;
- `integration_ready`;
- `integration_apply_started`;
- `integration_blocked`;
- `integration_outcome_unknown`;
- `integration_completed`;
- `worktree_cleanup_started`;
- `worktree_cleaned`;
- `worktree_cleanup_blocked`;
- `worktree_orphaned`.

Every event includes UUID, timestamp, user/conversation/batch/task correlation,
state revision, and safe OIDs. Events never include secret values or physical
paths.

The existing sub-agent tool/thinking/text events continue unchanged and remain
nested under the appropriate task in the webchat.

## 30. Web UI and client experience

Add a batch card to the conversation timeline and active-agent panel.

The card shows:

- batch ID and repository label;
- base and target short OIDs;
- ordered tasks and assigned agents;
- queued/preparing/running/sealing/ready/error states;
- changed file counts and diffstats;
- overlap, conflict, and validation badges;
- integration policy;
- retained-until time;
- actions allowed in the current state.

Actions:

- inspect task transcript;
- inspect bounded diff;
- download full patch;
- cancel task/batch;
- approve integration;
- retry preparation after target cleanup;
- start resolver task;
- abandon and clean up.

All actions call the server-side coordinator. No client runs Git.

PawCode, VS Code, API, and webchat receive the same structured command results.
The server command parser remains the single slash-command source of truth.

## 31. Public and internal APIs

### 31.1 Server services

Add:

- `core/worktree_models.py`;
- `core/worktree_store.py`;
- `core/worktree_manager.py`;
- `core/worktree_batch.py`;
- `core/worktree_routing.py`;
- `core/worktree_reconcile.py`.

Suggested interfaces:

~~~python
prepare_task(spec, principal) -> WorkspaceLease
resolve_route(user_id, conversation_id, agent_name) -> WorkspaceRoute | None
heartbeat(lease_id, epoch) -> WorkspaceLease
seal_task(lease_id, epoch, result) -> CandidateResult
prepare_integration(batch_id, expected_revision) -> IntegrationCandidate
apply_integration(batch_id, operation_id, approval) -> IntegrationReceipt
cancel_task(task_id, actor) -> CancelReceipt
cleanup_task(task_id, actor) -> CleanupReceipt
reconcile_relay(relay_id) -> ReconcileReport
~~~

All mutations are async at the HTTP/UI boundary. Methods may perform blocking
relay calls only in worker execution, never on the HTTP or SSE event loop.

### 31.2 Relay adapter

Add typed methods on `RelayService` matching the internal RPC list. Ordinary
filesystem consumers never call them.

### 31.3 Agent executor

Extend `AgentTask` and `AgentResult` with structured workspace fields rather
than free-form dictionaries once parsing is complete. Required fields should
have no anonymous/default fallback.

### 31.4 Tool registry

A registry factory replaces reuse of parent mutable handlers for task execution.
The registry cache is invalidated on lease epoch change, seal, cancellation, and
cleanup.

## 32. State machines

### 32.1 Task

~~~text
requested
  -> preparing
  -> queued_capacity | prepare_failed | running
running
  -> cancelling | sealing
cancelling
  -> sealing
sealing
  -> no_changes | candidate_ready | seal_blocked | orphaned
candidate_ready
  -> integration_queued | retained | cleanup_pending
integration_queued
  -> integrating | integration_blocked
integrating
  -> integrated | conflict | validation_failed |
     integration_blocked | outcome_unknown
integrated | no_changes
  -> cleanup_pending -> cleaned | cleanup_blocked
~~~

### 32.2 Batch

~~~text
proposed
  -> preparing
  -> running | prepare_failed | cancelled
running
  -> sealing
sealing
  -> ready_for_review | overlap_blocked | failed | cancelled
ready_for_review
  -> integrating | retained
integrating
  -> integrated | conflict | validation_failed |
     integration_blocked | outcome_unknown
integrated
  -> cleanup_pending -> completed | cleanup_blocked
~~~

Invalid transitions raise typed errors and do not mutate state.

## 33. Failure taxonomy

Use stable machine codes, including:

- `relay_unavailable`;
- `relay_capability_missing`;
- `repository_not_found`;
- `repository_bare`;
- `repository_unsupported_submodule`;
- `repository_unsupported_lfs`;
- `target_detached`;
- `target_dirty`;
- `target_operation_in_progress`;
- `target_moved`;
- `quota_exceeded`;
- `lock_timeout`;
- `lease_not_found`;
- `lease_expired`;
- `lease_epoch_stale`;
- `lease_owner_mismatch`;
- `route_escape_blocked`;
- `secondary_write_blocked`;
- `local_execution_blocked`;
- `git_metadata_tampered`;
- `candidate_too_large`;
- `candidate_secret_detected`;
- `candidate_hook_failed`;
- `candidate_non_descendant`;
- `candidate_merge_commit_unsupported`;
- `batch_overlap`;
- `merge_conflict`;
- `validation_failed`;
- `integration_outcome_unknown`;
- `cleanup_not_clean`;
- `cleanup_identity_mismatch`;
- `orphaned_state`.

Messages should explain remediation without blaming Git, the OS, a cache, or the
relay.

## 34. Validation profiles

A batch manifest references a named validation profile or explicit approved test
files, not an arbitrary hidden command.

A profile can define:

- `git diff --check`;
- language-specific lint/type checks;
- focused test selections per task;
- aggregate tests after integration preparation;
- security/secret scan;
- timeout and output cap;
- required versus advisory checks.

The implementation should reuse `run_tests` and `security_scan` semantics
where possible, while executing against the leased/integration route.

A required check that cannot run is a failure, not a pass.

## 35. Test plan

### 35.1 Unit tests

Cover:

- schema validation and required fields;
- UUID/timestamp creation;
- state-transition tables;
- revision and epoch compare-and-swap;
- task-conversation ID parsing;
- repository/ref/path sanitization;
- branch-name generation;
- lease-to-route resolution;
- explicit/default relay alias equivalence;
- local and secondary-write denial;
- registry cache keys and invalidation;
- error-code rendering;
- retention and quota calculations;
- `/batch` parser and help output.

### 35.2 Relay Git integration tests

Use real temporary Git repositories and assert:

- prepare creates one exact worktree/ref from the pinned OID;
- two tasks receive different paths and refs;
- agent names with metacharacters cannot affect paths or refs;
- dirty base is rejected without mutation;
- detached target is rejected;
- in-progress Git operations are rejected;
- no-Git, bare, submodule, LFS, and nested-repo cases fail explicitly;
- relative and absolute logical paths stay within the leased root;
- symlink/path traversal is blocked;
- stale epochs cannot write;
- seal captures tracked, untracked, deletion, rename, executable-bit, and binary
  changes;
- no-change seal creates no candidate commit;
- hook and secret-scan failures retain work;
- protected-ref mutation quarantines the task;
- exact cleanup cannot remove an unrelated worktree or moved ref.

Run the relay suite on POSIX and Windows.

### 35.3 Concurrency tests

Prove:

- N tasks edit in N distinct worktrees;
- same agent definition can run two batch tasks;
- a follow-up to a live task stays on its lease;
- task-scoped registries do not leak roots across threads;
- capacity backpressure does not fall back to shared workspace;
- repository locks serialize prepare/seal/integrate;
- two servers or relay connections cannot reuse one lease epoch;
- cancellation racing with a write fences the late write;
- cleanup racing with resume fails closed.

### 35.4 Integration tests

Cover:

- disjoint candidates integrate in declared order;
- reversed completion order does not change merge order;
- exact overlaps block before integration;
- textual conflicts occur only in hidden integration worktree;
- target worktree never enters conflict state;
- validation failure leaves target unchanged;
- review mode leaves target unchanged;
- auto mode fast-forwards a clean unchanged target;
- dirty or moved target blocks final apply;
- unknown apply outcome reconciles to exactly one result;
- restart between every lifecycle transition;
- relay disconnect/reconnect during prepare, seal, apply, and cleanup;
- retained candidate diff/patch remains accessible and authorized.

### 35.5 Provider contract tests

For Claude Code, Codex, Gemini, and interactive providers:

- MCP calls resolve to the leased worktree;
- provider cold start keeps the lease;
- failover keeps the lease;
- direct original-workspace compatibility mount is absent;
- `local=true` cannot escape;
- force-stop kills promptly and late tool results cannot write;
- all tool and lifecycle traffic remains visible.

### 35.6 End-to-end acceptance tests

1. Two agents modify disjoint files; review shows both; integration succeeds.
2. Two agents modify the same file; overlap blocks; target is untouched.
3. Main agent edits the target while tasks run; final integration blocks without
   losing either side.
4. Relay disconnects after final apply but before reply; reconciliation reports
   exactly one integration.
5. Server restarts with three running tasks; leases recover or become explicit
   orphans, never duplicate.
6. A cancelled agent leaves useful uncommitted changes; seal preserves a
   reviewable candidate.
7. A task attempts explicit default-relay and absolute `/workspace` bypasses;
   both still reach only its worktree.
8. A task attempts a secondary relay write or `local=true`; both are denied.
9. The webchat shows every task, tool call, lifecycle state, conflict, and final
   result.
10. Cleanup removes only PawFlow-owned exact worktrees/refs.

## 36. Implementation phases

### Phase W0 — contracts and durable state

Deliver:

- models, enums, machine error codes;
- SQLite store and migrations;
- state-transition tests;
- `WorkspaceRoute` resolution;
- relay capability schema;
- documentation of supported repository constraints.

Gate: no filesystem or Git mutation yet; every transition and serialization test
passes.

### Phase W1 — relay lifecycle primitives

Deliver:

- relay repository inspection;
- cross-platform repository lock;
- prepare/status/heartbeat/seal/remove/reconcile RPCs;
- exact ref/path ownership;
- runtime state directory and quotas;
- real-Git relay tests.

Gate: relay tests prove create/seal/reconcile/cleanup are idempotent and never
touch unrelated worktrees or refs.

### Phase W2 — task routing

Deliver:

- `AgentTask` workspace contract;
- task-scoped registry factory;
- leased relay view;
- CLI bridge lookup by task conversation;
- epoch fencing;
- local/secondary-write denial;
- direct compatibility-mount protections;
- follow-up persistence on the same lease.

Gate: two concurrent agents cannot observe each other's writes or the target's
post-spawn writes through any supported filesystem path.

### Phase W3 — candidate and integration pipeline

Deliver:

- manifest and secret gates;
- synthetic candidate commit;
- hidden integration worktree/ref;
- deterministic ordered merges;
- validation profiles;
- review and auto final-apply paths;
- unknown-outcome reconciliation.

Gate: target remains byte/ref unchanged for every failure before final apply, and
successful apply produces the exact validated OID.

### Phase W4 — first-class batch orchestration

Deliver:

- durable batch coordinator;
- new `/batch` command family;
- direct structured manifest API;
- same-agent parallel tasks;
- overlap analysis;
- cancellation and result aggregation;
- command help and client rendering.

Gate: the prompt-only `/batch` path is deleted; webchat, PawCode, VS Code, and
API use the same batch state.

### Phase W5 — UI, operations, and hardening

Deliver:

- batch timeline card and active-agent integration;
- diff/patch review;
- operator diagnostics;
- TTL cleanup and quota reporting;
- startup/reconnect reconcilers;
- full provider and platform matrix;
- docs and release notes.

Gate: full repository tests pass, new E2E scenarios pass, and manual relay
disconnect/restart validation is documented.

## 37. File-level implementation map

Expected new files:

- `core/worktree_models.py`;
- `core/worktree_store.py`;
- `core/worktree_manager.py`;
- `core/worktree_batch.py`;
- `core/worktree_routing.py`;
- `core/worktree_reconcile.py`;
- `pawflow_relay/git_worktrees.py`;
- focused test modules for each layer.

Expected existing integration points:

- `core/_agent_executor_base.py`;
- `core/_agent_executor_loop.py`;
- `core/agent_executor.py`;
- `core/handlers/spawn_agents.py`;
- `services/_tool_relay_registry.py`;
- `services/_tool_relay_execute.py`;
- `services/_filesystem_ops.py`;
- `services/tool_relay_service.py`;
- `pawflow_relay/_relay_dispatch.py`;
- `core/cli_workspace_mounts.py`;
- `tasks/ai/actions/command_dispatch.py`;
- `tasks/ai/actions/_cmd_help.py`;
- `tasks/ai/actions/_command_result.py`;
- webchat batch/task rendering modules;
- Relay Desktop packaging/runtime sync;
- relay capability and manager status surfaces.

The exact split must preserve the repository's approximately 800-line code-file
target. Large lifecycle or UI modules should be split before they exceed it, not
after.

## 38. Documentation changes required with implementation

Update in the same implementation changes:

- `docs/AGENT_SYSTEM.md`: task versus shared delegation and workspace policy;
- `docs/CLAUDE_CODE_INTERACTIVE.md`: leased routing and mount behavior;
- relay documentation: capability, state directory, quotas, and recovery;
- user command documentation: `/batch` lifecycle and examples;
- security documentation: collision isolation versus hostile-code isolation;
- operations documentation: orphan inspection and exact cleanup;
- `README.md` and public website feature descriptions once shipped;
- `ROADMAP.md`: move the item to completed only after all release gates pass;
- `CHANGELOG.md` and `PROJECT_SUMMARY.md` at release time.

All documentation and comments remain in English.

## 39. Migration and rollout

This is a one-shot migration, not a dual implementation.

1. Add the database/schema and relay capability first.
2. Deploy relays that advertise `git_worktree_v1`.
3. Replace the prompt-only `/batch` parser.
4. Make spawned-task workspace policy explicit in tool schemas and prompts.
5. Update every internal `AgentTask` constructor and test fixture.
6. Remove any legacy fallback that sends a requested isolated task to the shared
   workspace.
7. Reject stale clients/manifests with a clear schema-version error.
8. Do not import unmanaged `.worktrees` directories.
9. Existing ordinary conversations and shared delegates remain unchanged.
10. Enable automatic integration only after review-mode soak tests and
    restart/disconnect reconciliation pass.

A temporary operator feature gate may hide the new command before relay rollout,
but once enabled there is one state machine and one protocol. The gate must not
select the old prompt wrapper.

## 40. Release gates

The feature is not complete until:

1. every new handler, parser, state transition, relay method, and UI action has
   unit coverage;
2. real-Git concurrency and recovery tests pass on Linux and Windows;
3. no task-scoped handler shares mutable root state;
4. bypass tests cover explicit relay IDs, aliases, absolute paths, composite
   tools, `bash`, and `local=true`;
5. target-dirty, target-moved, conflict, hook failure, validation failure, and
   unknown-outcome tests prove the target is preserved;
6. force-stop and late-result tests pass;
7. cleanup tests prove unrelated worktrees and refs are untouched;
8. provider contract tests pass for every shipped CLI execution path;
9. all lifecycle events are visible and durably ordered in webchat;
10. full pytest, relay, command-parser, UI, and gauge-invariant suites pass;
11. the implementation docs listed above are updated;
12. a manual test performs a multi-agent batch, relay disconnect, server restart,
    review, integration, and cleanup against a disposable repository.

## 41. Acceptance criteria

The roadmap item can move to “Recently Completed” only when a user can:

1. run a batch with at least two parallel coding tasks;
2. observe a distinct task/worktree/lease for each task;
3. continue editing the main workspace without task writes colliding;
4. inspect each candidate and the aggregate before integration;
5. see overlaps and conflicts without the target entering a merge state;
6. choose review or explicitly authorized auto integration;
7. receive a deterministic integrated result when the target is unchanged;
8. receive a precise blocked state when it is dirty or moved;
9. cancel or restart PawFlow without losing or duplicating candidates;
10. clean up exact retained artifacts safely;
11. see the entire orchestration and all traffic in webchat;
12. verify that no silent shared-workspace fallback occurred.

## 42. Final architectural position

The feature is not “run `git worktree add` before delegate and `git merge`
afterward.”

It is a fenced workspace-lease system with Git as its storage mechanism:

~~~text
explicit batch/task manifest
        |
        v
pin clean repository + exact base OID
        |
        v
relay-owned worktree lease per task
        |
        v
task-scoped MCP/filesystem routing
        |
        v
seal -> candidate commit -> overlap analysis
        |
        v
hidden deterministic integration + validation
        |
        v
review or guarded fast-forward of unchanged clean target
        |
        v
exact receipt-driven cleanup and reconciliation
~~~

That additional structure is what makes parallel agent worktrees dependable in a
long-running, multi-user, relay-backed PawFlow runtime rather than a convenient
shell convention.
