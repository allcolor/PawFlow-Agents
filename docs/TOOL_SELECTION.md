# Agent Tool Selection

PawFlow exposes a broad tool surface. Individual schemas explain parameters;
this guide explains which tool family to choose when several tools appear to
solve the same problem. The complete inventory remains in
[Agent Tool Catalog](tool_catalog.md), and the storage-specific rules remain in
[Cognitive Tools](COGNITIVE_TOOLS.md).

The general rule is: choose the narrowest tool that owns the required state or
side effect. Do not duplicate the same information or workflow across several
systems.

## Delivery model

PawFlow does not inject this whole document. `core/tool_selection.py` is the
single machine-readable source for two bounded delivery paths:

1. A compact `## Tool selection` block is generated for the permanent system
   prompt. It includes only ambiguous families with at least two routes present
   in the active agent's filtered registry.
2. `get_tool_schema(family="<name>")` returns the full comparison for one
   family, again filtered to tools the agent can actually call.

`get_tool_schema(tool_name="<name>")` remains authoritative for exact
parameters. With no selector, it lists the available tools and family names.
Cold CLI providers serialize the permanent block under `## System Instructions`
in `initial_context.md`; API providers receive it in the stable system prompt.

## Discovery and execution

| Need | Use | Do not confuse it with |
|---|---|---|
| Inspect the full schema of a lazily exposed tool | `get_tool_schema` | The abbreviated catalog description |
| Compare overlapping tools available to this agent | `get_tool_schema(family="delegation")` | Reading several unrelated schemas and inferring their boundary |
| Execute a lazily exposed tool | `use_tool` after schema lookup | Calling `use_tool` through itself |
| Ask what PawFlow supports | `pawflow_help` | Guessing a task, service, or configuration name |

Call a directly exposed tool directly. The lazy pair exists for tools omitted
from the model's immediate schema set; it is not an extra execution layer to
wrap around every call.

## Files, code, and artifacts

| Need | Preferred tool | Boundary |
|---|---|---|
| Search files with regex, glob filtering, and context | `search` | Use `glob` only for a file list and `grep` for a simple content search |
| Read content for the model | `read` | `show_file` opens a file for the user and does not return its content |
| One exact replacement | `edit` | Use `apply_patch` once a file needs several separate changes |
| Repeated replacement across files | `batch_edit` | Keep unrelated edits out of the batch |
| Atomic multi-hunk change | `apply_patch` | Do not recreate patches through shell redirection |
| Give the user an existing workspace file | `show_file` | `share_file` uploads an artifact to FileStore and returns a URL |
| Inspect image, video, or audio content | `see` | `screen` controls a live desktop; `browser` controls a browser session |
| Run project tests | `run_tests` | Do not construct a generic `pytest` shell call when the dedicated tool fits |
| Run a non-test command | `bash` | Prefer dedicated read/search/edit tools for filesystem work |

All filesystem, shell, browser, desktop, and media calls execute on the selected
relay surface unless the tool explicitly targets another service. `local=true`
means the relay's authorized host surface; it does not mean the PawFlow server or
the model provider container.

## Agents and delegation

| Need | Use | Lifecycle and context |
|---|---|---|
| Ask an existing agent in the same conversation | `delegate` | Asynchronous; the named agent keeps its own conversation context and tools |
| Run independent, temporary work in parallel | `flash_delegate` | Fresh temporary agents; provide a self-contained prompt and narrow tools |
| Check whether your flash agents are still running | `flash_status` | Live + recently finished flash agents; status only, results arrive asynchronously |
| Obtain one tool-free second opinion from the current configured agent brain | `consult_agent` | Synchronous one-shot completion; mainly for thin interfaces such as voice helpers, not recursive self-delegation from a full agent turn |
| Call a configured agent outside this conversation/runtime | `a2a` | Remote asynchronous task with explicit task/context IDs for `get` or `cancel` |

Use `delegate` when identity and accumulated context matter. Use
`flash_delegate` when the work is separable and disposable. Do not delegate a
tightly coupled edit merely to create parallelism; one owner should preserve the
invariant across those files.

## Todo, plans, tasks, and flows

| Need | Use | Ownership |
|---|---|---|
| Remember the current agent's unfinished work | `todolist` | Lightweight durable ledger for one user/conversation/agent |
| Present and orchestrate explicit multi-step work | plan tools (`create_plan`, `update_plan`, assignment and verification tools) | User-visible lifecycle with approval, step ownership, and verification |
| Run a predefined autonomous job over repeated work sessions | `assign_task`, then `complete_task` / `verify_task` | Durable task definition, schedule, limits, dependencies, and optional verifier |
| Build repeatable data/application automation | `manage_flow` and flow tasks | Deterministic DAG with triggers, queues, backpressure, checkpoints, and services |

A todo item does not create an agent or execute work. A plan is not a private
notes list. An assigned task requires an existing task definition and owns its
autonomous rescheduling. Promote a repeated agent procedure to a skill; promote
a repeated operational pipeline to a flow.

## Waiting, resuming, and contacting the user

| Situation | Use | Result |
|---|---|---|
| Command should finish within about 60 seconds, or an early regex match matters | `Monitor` | Blocks the current tool call until exit, match, or timeout |
| Current work must resume after a background/external operation taking about a minute or more | `schedule_continuation` | Persists a precise resume plan, lets the current turn end, then wakes the same work |
| A check must occur at a user-requested time/date or recurring interval | `ScheduleWakeup` | Schedules a future autonomous check-in; not a log-polling loop |
| User must make a decision before work can continue | `ask_user` | Pauses for the answer |
| User only needs a proactive informational alert | `notify_user` | Sends a notification without turning it into a blocking question |

For a long background command, persist its output and final status in stable
workspace files before scheduling a continuation. Do not keep a conversation
alive with repeated sleeps, waits, or log polls.

## Knowledge and work state

| Information | Store |
|---|---|
| Durable fact, preference, event, discovery, or advice | Memory |
| Durable structured entity relationship | Knowledge Graph |
| Agent's durable first-person decision or lesson | Diary |
| Authoritative unfinished work | Todo |
| Expiring evidence, hypothesis, or resume cue | Scratchpad |
| Relay-scoped code structure | Project Graph |
| Relay-scoped sourced architecture/project knowledge | Project Wiki |
| Reusable procedure | Skill |

Memory, diary, todo, and scratchpad deliberately have different scopes and
lifetimes. See [Cognitive Tools](COGNITIVE_TOOLS.md) for full retrieval,
injection, and storage behavior.

## Resource and platform management

| Need | Use |
|---|---|
| Create or update an agent, skill, MCP server, task definition, or tool | `manage_resource` |
| Build, inspect, install, update, or remove a signed `.pfp` bundle | `manage_package` |
| Link a relay to the conversation | `link_resource` |
| Store or list secret names | `store_secret` / `list_secrets` |
| Turn established work into a deployed DAG | `manage_flow` |

Resource mutation, package installation, relay linking, and secret storage are
different security boundaries. Inspect the target and use the specific action
that matches the user's request; do not infer permission for a broader mutation.

## Audit rule

Tool descriptions and injected prompt hints are the runtime source of truth for
model behavior. This document is the human-facing decision map. When a tool is
added, removed, renamed, or its scope changes, update all three surfaces in the
same change:

1. the handler schema and description;
2. its route in `core/tool_selection.py`, when it overlaps another tool;
3. `tool_catalog.md`, this guide, and relevant public documentation.
