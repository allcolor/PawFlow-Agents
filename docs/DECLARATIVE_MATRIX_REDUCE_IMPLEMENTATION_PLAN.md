# Declarative Matrix and Typed Reduce Implementation Plan

Status: planned
Priority: P1 after the multi-conversation workspace and P0 platform contracts
Date: 2026-08-30
Owner: PawFlow declarative flow compiler and runtime

## 1. Outcome

Add matrix and reduce as declarative macros that compile into the existing
FlowDefinition DAG. Users can express bounded Cartesian work, per-item policy,
and typed aggregation without introducing a second scheduler, executor, queue,
checkpoint format, or agent runtime.

The compiler must make the expanded graph deterministic, inspectable, safe, and
compatible with FlowExecutor, ContinuousFlowExecutor, Workflow Agent validation,
presentation metadata, checkpoints, and package composition.

## 2. Verified baseline

PawFlow already provides:

- FlowDefinition as the only executable workflow format;
- native fan-out by multiple outgoing relations;
- correlated FlowFiles and checkpointable mergeContent;
- bounded for_each declarative control macros;
- success, failure, and all relations;
- WorkflowLimits.max_fanout and queue/backpressure controls;
- task effect and idempotency validation;
- Process Group/sub-flow composition;
- immutable workflow version/digest pinning;
- graph, timeline, and Kanban projections.

Missing features are Cartesian expansion across multiple axes, a typed reducer
contract, deterministic per-item policy, and first-class UI/source mapping from
expanded tasks back to one macro.

## 3. Non-goals

- Do not adopt another graph runtime or distributed executor.
- Do not generate unbounded dynamic tasks at runtime.
- Do not accept arbitrary reducer code or eval expressions.
- Do not use duplicateContent for fan-out.
- Do not change routing semantics: a FlowFile is cloned to every matching
  outgoing relation.
- Do not hide expanded effects, cost, or cardinality from static validation.
- Do not make matrix order depend on hash-map iteration or completion timing.

## 4. Architectural decisions

1. matrix and reduce are compiler macros under core/declarative_flow.
2. Expansion occurs before normal FlowDefinition validation and digesting.
3. The compiled DAG is executable without macro-aware runtime branches.
4. Stable generated IDs derive from macro ID, normalized axis values, and source
   task IDs.
5. Every expanded task retains source-map metadata to its macro and matrix item.
6. Cardinality, cost, effects, and idempotency are validated before execution.
7. Reduction is a normal checkpointable task with a closed typed operation.
8. Input ordering is explicit and independent of completion order.
9. A partial/failure policy is part of the versioned contract.
10. The exported compiled graph is always available for review.

## 5. MatrixSpecV1

Required fields:

- schema_version;
- id;
- axes: ordered map of axis name to a bounded non-empty list;
- body: task/relation template or referenced Process Group;
- parameter_mapping;
- max_items;
- strategy;
- item_policy;
- presentation metadata.

Optional fields:

- include: explicit additional combinations;
- exclude: exact predicate-free combination maps;
- fail_if_empty;
- max_parallel;
- cost_hint;
- output port mapping.

Axis values must be JSON scalars or bounded JSON objects that pass canonical
serialization. Duplicate canonical values are rejected.

Supported strategies:

- cartesian: product of every ordered axis;
- zip: axes must have equal length;
- include_only: only explicit include entries.

Cartesian cardinality is calculated with overflow-safe arithmetic before
materializing tasks. max_items must not exceed the flow/workflow fan-out ceiling.

## 6. MatrixItemContextV1

Each compiled item receives immutable context:

- matrix_id and matrix_item_id;
- zero-based matrix_index;
- canonical axis values;
- item_digest;
- parent correlation ID;
- source macro location;
- policy snapshot;
- expected output contract.

Expressions resolve matrix values through an explicit matrix scope. Values do not
enter flow, conversation, user, or global parameter stores.

## 7. Per-item policy

ItemPolicyV1 declares:

- on_success: collect, emit, or ignore;
- on_failure: fail_fast, collect_error, continue, or cancel_remaining;
- timeout_seconds;
- retry policy;
- idempotency key template;
- optional effect ceiling no broader than the parent flow;
- maximum output bytes;
- sensitive output fields;
- priority and concurrency group.

Rules:

- retry validation reuses IdempotencyClass and effect receipt contracts;
- unsafe or unknown effect retries require reviewed policy;
- cancel_remaining stops pending items but does not rewrite completed outcomes;
- fail_fast records every already completed item before terminal failure;
- policy cannot override task/service authorization.

## 8. Deterministic lowering

Compiler phases:

1. parse and schema-validate macros;
2. normalize axes, include, exclude, and policy;
3. calculate cardinality, cost, and effect envelope;
4. enumerate items in declared axis/value order;
5. derive stable item IDs and generated task IDs;
6. clone body tasks and substitute matrix scope references;
7. create an explicit item input task and item terminal task;
8. connect all terminals to a correlation-preserving join;
9. attach source-map and presentation metadata;
10. validate the ordinary expanded FlowDefinition;
11. compute the final flow digest.

Generated task IDs use a bounded readable prefix plus a digest suffix. A compiler
version change that alters expansion must change the compiled flow digest.

## 9. ReduceSpecV1

Required fields:

- schema_version;
- id;
- input source or correlation group;
- operation;
- input_type and output_type;
- ordering;
- failure_policy;
- empty_policy;
- limits.

Closed V1 operations:

- collect_list;
- collect_map with a declared key field;
- merge_objects with conflict policy;
- concat_text with bounded separator;
- concat_bytes;
- sum_number;
- min_number;
- max_number;
- count;
- all_boolean;
- any_boolean;
- first;
- last.

No arbitrary callback, script, shell command, or LLM reducer is accepted. A
normal executeScript or inferLLM task may follow the reducer under its existing
effects and limits when custom processing is required.

## 10. Reduction semantics

Reducer inputs use a ReduceEnvelopeV1 containing item identity, index, status,
content type, bounded content/reference, attributes, and error projection.

Ordering modes:

- matrix_order;
- completion_order only when explicitly requested and marked nondeterministic;
- key_order using a declared stable scalar key.

Failure policies:

- require_all;
- allow_partial with minimum success count;
- collect_failures;
- ignore_failures only for outputs explicitly marked optional.

Empty policies are fail, emit_empty, or skip. Type mismatch, duplicate map key,
size overflow, and conflict are stable typed errors.

Large or binary inputs remain in FileStore and the reducer operates on references
unless concat_bytes is explicitly authorized and bounded.

## 11. Checkpoints and recovery

Expanded tasks use existing task/FlowFile checkpoints. Add a compact matrix
manifest to run metadata containing item IDs, policy digests, and completion
projection.

On restart:

- completed item checkpoints are reused;
- pending items re-enter normal scheduling;
- effectful unknown items reconcile before retry;
- the reducer resumes from a durable accumulator checkpoint or rebuilds
  deterministically from completed item envelopes;
- completion order never changes matrix_order output.

The macro does not introduce a second queue or scheduler.

## 12. Static safety and limits

Validation rejects:

- missing or empty axes;
- cardinality overflow;
- max_items above platform or workflow limits;
- generated ID collisions;
- unresolved matrix references;
- output types incompatible with the reducer;
- broader item effect ceilings;
- unsafe retry policies;
- unbounded content aggregation;
- fail-fast plus an impossible cancellation contract;
- nested matrix depth above the configured limit.

Nested matrix is disabled in V1. Matrix inside a referenced child flow may be
supported later after combined cardinality analysis.

## 13. Presentation and UI

The graph editor shows one matrix group by default with:

- axis names and counts;
- total item count;
- strategy and concurrency;
- effect/cost envelope;
- body tasks;
- reducer and failure policy.

Users can expand the generated DAG and export it. Runtime timeline/Kanban groups
items by matrix and exposes progress, failures, retries, cost, and reducer state.
Every item links back to its source macro and exact axis values.

No visual card claims completion until the ordinary run store records it.

## 14. Package and sub-flow integration

PFP flow resources may contain macros. Package build and inspect compile and
validate them. Process Group references must be exact version and port mapping.

The compiled graph and compiler version are included in package provenance.
Install rejects a package whose embedded compiled digest does not match a fresh
compile.

## 15. Migration

No existing flow changes semantics.

1. add contracts and compiler diagnostics;
2. add matrix/reduce parsing behind a schema version;
3. compile to ordinary DAGs for validation-only previews;
4. enable execution for first-party canaries;
5. add editor/UI support;
6. publish package author documentation;
7. remove any experimental aliases before release.

There is no legacy runtime state to migrate.

## 16. Work packages

### WP0 — Contracts and red tests

Add MatrixSpecV1, MatrixItemContextV1, ItemPolicyV1, ReduceSpecV1, and stable
diagnostics. Prove current for_each cannot express Cartesian axes or typed reduce.

### WP1 — Matrix compiler

Implement normalization, cardinality checks, enumeration, IDs, source maps, and
deterministic DAG lowering.

### WP2 — Typed reducer task

Implement ReduceEnvelopeV1, closed operations, type/size/order/failure policies,
and checkpointable accumulators.

### WP3 — Safety integration

Connect effects, idempotency, receipts, workflow limits, cost estimates, and
static Workflow Agent validation.

### WP4 — Recovery and cancellation

Cover checkpoints, fail-fast, cancel_remaining, partial results, and restart.

### WP5 — Package and composition

Support PFP build/inspect/install and exact Process Group references.

### WP6 — UI and observability

Add compact/expanded graph views, item progress, reducer state, source mapping,
metrics, and accessible diagnostics.

### WP7 — Documentation and delivery

Update flow guide, expressions, task reference, package authoring, examples, and
operations. Run focused and full CI.

## 17. Test matrix

Required tests include:

1. deterministic Cartesian and zip enumeration;
2. include/exclude normalization;
3. duplicate axis value rejection;
4. overflow-safe cardinality failure;
5. generated ID stability and collision failure;
6. expression substitution only from matrix scope;
7. compiled graph passes ordinary parser/executor;
8. fan-out routing produces exactly one copy per item;
9. max_parallel and backpressure hold;
10. per-item effect ceiling cannot broaden;
11. unsafe retry is rejected;
12. crash recovery reuses completed checkpoints;
13. unknown effects reconcile before retry;
14. every reducer operation passes type and limit tests;
15. matrix_order is independent of completion order;
16. partial/fail-fast/cancel policies preserve completed evidence;
17. FileStore references avoid binary memory amplification;
18. PFP compiled digest verification works;
19. UI source mapping survives generated IDs;
20. existing for_each and mergeContent behavior remains unchanged;
21. Python 3.10–3.13 and package CI pass.

## 18. Definition of done

A flow author can declare a bounded multi-axis workload and typed aggregation,
preview the exact expansion and cost/effect envelope, execute it on the existing
runtime, recover it without duplicate effects, and explain every output item and
reducer decision from durable run state.

## 19. Upstream influence and license ledger

The matrix/reduce and per-target policy ideas were evaluated from
agentenv/agentflow at SHA 09df017 under MIT. Selective reuse requires attribution.
PawFlow retains its own FlowDefinition, compiler, executor, Relay abstraction,
checkpoint format, authorization, and UI.
