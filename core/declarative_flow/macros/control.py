"""Deterministic lowering for acyclic declarative control blocks."""

from __future__ import annotations

import copy
import re
from typing import Any

from core.declarative_flow.contracts import require_semantic_id
from core.declarative_flow.validation import find_cycle
from core.flow_definition_validator import normalize_relation
from core.flow_layout_contracts import relation_id_seed
from core.resource_identity import ResourceRef

LOWERING_VERSION = 1
MAX_PARALLEL_BRANCHES = 64
MAX_REPEAT_UNROLL = 8
MAX_RETRY_ATTEMPTS = 8

_WORKFLOW_AGENT_OUTPUTS = (
    "submitted", "completed", "no_change", "failed", "cancelled",
    "timed_out", "superseded", "budget_exceeded", "force_stopped",
    "failure",
)


def _port_id(block_id: str, direction: str, name: str) -> str:
    return require_semantic_id(
        f"{block_id}.{direction}.{name}", f"{direction} port id")


def _task(task_type: str, **parameters: Any) -> dict[str, Any]:
    return {"type": task_type, "parameters": parameters}


def _relation(source: str, target: str, relationship: str = "success") -> dict[str, str]:
    relation = {"from": source, "to": target, "type": relationship}
    relation["relation_id"] = relation_id_seed(relation)
    return relation


def _base_group(
    block_id: str, control_type: str, config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": block_id,
        "name": str(config.get("label") or control_type.replace("_", " ").title()),
        "description": str(config.get("description") or ""),
        "color": str(config.get("color") or "#4285f4"),
        "collapsed": True,
        "flow_ref": None,
        "tasks": {},
        "relations": [],
        "variables": {},
        "input_ports": [],
        "output_ports": [],
        "child_groups": {},
        "declarative": {
            "type": control_type,
            "version": 1,
            "lowering_version": LOWERING_VERSION,
            "config": copy.deepcopy(config),
            "ports": {"inputs": {}, "outputs": {}},
        },
    }


def _add_port(
    group: dict[str, Any], block_id: str, direction: str, name: str,
) -> str:
    port_id = _port_id(block_id, direction, name)
    task_type = "inputPort" if direction == "in" else "outputPort"
    group["tasks"][port_id] = _task(task_type, port_name=name)
    field = "input_ports" if direction == "in" else "output_ports"
    group[field].append(port_id)
    metadata_field = "inputs" if direction == "in" else "outputs"
    group["declarative"]["ports"][metadata_field][name] = port_id
    return port_id


def _validate_condition(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a condition object")
    return copy.deepcopy(value)


def _lower_route(
    group: dict[str, Any], block_id: str, routes: dict[str, Any], default: str,
) -> None:
    entry = _add_port(group, block_id, "in", "input")
    route_id = require_semantic_id(f"{block_id}.route", "route task id")
    group["tasks"][route_id] = _task(
        "routeOnAttribute", routing_strategy="route_to_matched",
        routes=copy.deepcopy(routes), default_relationship=default,
    )
    group["relations"].append(_relation(entry, route_id))
    for output in [*routes, default]:
        port_id = _add_port(group, block_id, "out", output)
        group["relations"].append(_relation(route_id, port_id, output))


def _lower_if(group: dict[str, Any], block_id: str, config: dict[str, Any]) -> None:
    if config.get("evaluation_failure"):
        raise ValueError(
            "evaluation_failure requires the fail-closed route task extension")
    _lower_route(
        group, block_id,
        {"true": _validate_condition(config.get("condition"), "condition")},
        "false",
    )


def _lower_switch(group: dict[str, Any], block_id: str, config: dict[str, Any]) -> None:
    cases = config.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise ValueError("cases must be a non-empty object")
    routes = {
        require_semantic_id(name, "case name"): _validate_condition(
            condition, f"case '{name}'")
        for name, condition in cases.items()
    }
    default = require_semantic_id(config.get("default"), "default")
    if default in routes:
        raise ValueError("default must not duplicate a case name")
    _lower_route(group, block_id, routes, default)


def _branch_names(config: dict[str, Any]) -> list[str]:
    branches = config.get("branches")
    if not isinstance(branches, list):
        raise ValueError("branches must be a list")
    result = [require_semantic_id(item, "branch name") for item in branches]
    if len(result) < 2 or len(result) > MAX_PARALLEL_BRANCHES:
        raise ValueError(
            f"branches must contain 2..{MAX_PARALLEL_BRANCHES} items")
    if len(set(result)) != len(result):
        raise ValueError("branch names must be unique")
    return result


def _lower_parallel(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    entry = _add_port(group, block_id, "in", "input")
    for branch in _branch_names(config):
        output = _add_port(group, block_id, "out", branch)
        group["relations"].append(_relation(entry, output))


def _lower_join(group: dict[str, Any], block_id: str, config: dict[str, Any]) -> None:
    branches = _branch_names(config)
    max_bin_age = config.get("missing_branch_timeout_seconds", 300)
    if isinstance(max_bin_age, bool) or not isinstance(max_bin_age, int) or max_bin_age < 1:
        raise ValueError("missing_branch_timeout_seconds must be an integer >= 1")
    merge_id = require_semantic_id(f"{block_id}.merge", "merge task id")
    group["tasks"][merge_id] = _task(
        "mergeContent", separator=str(config.get("separator", "\n")),
        min_entries=len(branches), correlation_attribute="fragment.identifier",
        max_bin_age=max_bin_age,
        max_bin_flowfiles=int(config.get("max_flowfiles", len(branches))),
        max_bin_bytes=int(config.get("max_accumulated_bytes", 64 * 1024 * 1024)),
    )
    for branch in branches:
        entry = _add_port(group, block_id, "in", branch)
        group["relations"].append(_relation(entry, merge_id))
    output = _add_port(group, block_id, "out", "success")
    group["relations"].append(_relation(merge_id, output))


def _single_entry_body(
    body: Any, field: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    if not isinstance(body, dict):
        raise ValueError(f"{field} must be an object")
    tasks = body.get("tasks")
    relations = body.get("relations", [])
    entries = body.get("entries")
    exits = body.get("exits")
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError(f"{field}.tasks must be a non-empty object")
    if not isinstance(relations, list):
        raise ValueError(f"{field}.relations must be a list")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError(f"{field}.entries must contain exactly one task id")
    if not isinstance(exits, list) or len(exits) != 1:
        raise ValueError(f"{field}.exits must contain exactly one task id")
    task_ids = {require_semantic_id(task_id, "body task id") for task_id in tasks}
    entry = require_semantic_id(entries[0], "body entry")
    exit_id = require_semantic_id(exits[0], "body exit")
    if entry not in task_ids or exit_id not in task_ids:
        raise ValueError(f"{field} entry and exit must reference body tasks")
    normalized = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise ValueError(f"{field} relation must be an object")
        item = normalize_relation(relation)
        if item["from"] not in task_ids or item["to"] not in task_ids:
            raise ValueError(f"{field} relation endpoints must reference body tasks")
        normalized.append(item)
    cycle = find_cycle(task_ids, normalized)
    if cycle:
        raise ValueError(f"{field} must be acyclic: " + " -> ".join(cycle))
    return copy.deepcopy(tasks), normalized, entry, exit_id


def _lower_repeat_n(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    times = config.get("times")
    if (isinstance(times, bool) or not isinstance(times, int)
            or times < 1 or times > MAX_REPEAT_UNROLL):
        raise ValueError(f"times must be an integer in 1..{MAX_REPEAT_UNROLL}")
    body_tasks, body_relations, body_entry, body_exit = _single_entry_body(
        config.get("body"), "repeat body")
    group_entry = _add_port(group, block_id, "in", "input")
    previous = group_entry
    for iteration in range(1, times + 1):
        mapping = {
            task_id: require_semantic_id(
                f"{block_id}.iteration_{iteration}.{task_id}",
                "generated repeat task id",
            )
            for task_id in body_tasks
        }
        for task_id, task in body_tasks.items():
            if not isinstance(task, dict):
                raise ValueError(f"body task '{task_id}' must be an object")
            group["tasks"][mapping[task_id]] = copy.deepcopy(task)
        group["relations"].append(_relation(previous, mapping[body_entry]))
        for relation in body_relations:
            group["relations"].append(_relation(
                mapping[relation["from"]], mapping[relation["to"]],
                relation["type"],
            ))
        previous = mapping[body_exit]
    output = _add_port(group, block_id, "out", "success")
    group["relations"].append(_relation(previous, output))


def _clone_body(
    group: dict[str, Any], block_id: str, role: str, body: Any,
) -> tuple[list[str], str, str]:
    tasks, relations, entry, exit_id = _single_entry_body(body, f"{role} body")
    mapping = {
        task_id: require_semantic_id(
            f"{block_id}.{role}.{task_id}", f"generated {role} task id")
        for task_id in tasks
    }
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            raise ValueError(f"{role} body task '{task_id}' must be an object")
        group["tasks"][mapping[task_id]] = copy.deepcopy(task)
    for relation in relations:
        if relation["type"] == "failure":
            raise ValueError(
                f"{role} body cannot declare failure relations; "
                "the Try/Catch boundary owns them")
        group["relations"].append(_relation(
            mapping[relation["from"]], mapping[relation["to"]], relation["type"]))
    return list(mapping.values()), mapping[entry], mapping[exit_id]


def _lower_try_catch(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    entry = _add_port(group, block_id, "in", "input")
    try_tasks, try_entry, try_exit = _clone_body(
        group, block_id, "try", config.get("try"))
    catch_tasks, catch_entry, catch_exit = _clone_body(
        group, block_id, "catch", config.get("catch"))
    success = _add_port(group, block_id, "out", "success")
    caught = _add_port(group, block_id, "out", "caught")
    failure = _add_port(group, block_id, "out", "failure")
    group["relations"].append(_relation(entry, try_entry))
    group["relations"].append(_relation(try_exit, success))
    for task_id in try_tasks:
        group["relations"].append(_relation(task_id, catch_entry, "failure"))
    group["relations"].append(_relation(catch_exit, caught))
    for task_id in catch_tasks:
        group["relations"].append(_relation(task_id, failure, "failure"))


def _lower_timer(
    group: dict[str, Any], block_id: str, config: dict[str, Any], field: str,
) -> None:
    value = config.get(field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field} is required")
    entry = _add_port(group, block_id, "in", "input")
    timer_id = require_semantic_id(f"{block_id}.timer", "timer task id")
    group["tasks"][timer_id] = _task("durableTimer", **{field: value})
    group["relations"].append(_relation(entry, timer_id))
    for output in ("elapsed", "cancelled", "failure"):
        port = _add_port(group, block_id, "out", output)
        group["relations"].append(_relation(timer_id, port, output))


def _lower_wait_duration(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    _lower_timer(group, block_id, config, "duration")


def _lower_wait_until(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    _lower_timer(group, block_id, config, "until")


def _interaction_route_task(routes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return _task(
        "routeOnAttribute", routing_strategy="route_to_matched",
        routes=routes, default_relationship="failure")


def _lower_user_interaction(
    group: dict[str, Any], block_id: str, config: dict[str, Any], kind: str,
) -> None:
    if not str(config.get("message") or "").strip():
        raise ValueError("message is required")
    entry = _add_port(group, block_id, "in", "input")
    request_id = require_semantic_id(f"{block_id}.request", "request task id")
    wait_id = require_semantic_id(f"{block_id}.wait", "wait task id")
    route_id = require_semantic_id(f"{block_id}.route", "route task id")
    request_parameters = {
        key: copy.deepcopy(value)
        for key, value in config.items()
        if key in {
            "message", "title", "options", "response_schema", "expires_in",
            "requester_label",
        }
    }
    request_parameters["kind"] = kind
    group["tasks"][request_id] = _task("requestUserInput", **request_parameters)
    group["tasks"][wait_id] = _task(
        "durableWait", signal_id_attribute="interaction.signal_id",
        timeout=config.get("timeout", ""))
    if kind == "confirm":
        routes = {
            "yes": {"attribute": "interaction.answer", "operator": "equals",
                    "value": "yes"},
            "no": {"attribute": "interaction.answer", "operator": "equals",
                   "value": "no"},
            "timeout": {"attribute": "interaction.status",
                        "operator": "matches_regex", "value": "^(timeout|expired)$"},
            "cancelled": {"attribute": "interaction.status", "operator": "equals",
                          "value": "cancelled"},
        }
        outputs = ("yes", "no", "timeout", "cancelled", "failure")
    else:
        routes = {
            "answered": {"attribute": "interaction.status", "operator": "equals",
                         "value": "answered"},
            "timeout": {"attribute": "interaction.status",
                        "operator": "matches_regex", "value": "^(timeout|expired)$"},
            "cancelled": {"attribute": "interaction.status", "operator": "equals",
                          "value": "cancelled"},
        }
        outputs = ("answered", "timeout", "cancelled", "failure")
    group["tasks"][route_id] = _interaction_route_task(routes)
    group["relations"].extend([
        _relation(entry, request_id),
        _relation(request_id, wait_id),
        _relation(wait_id, route_id),
    ])
    for output in outputs:
        port = _add_port(group, block_id, "out", output)
        if output == "failure":
            group["relations"].append(_relation(request_id, port, "failure"))
            group["relations"].append(_relation(wait_id, port, "failure"))
        group["relations"].append(_relation(route_id, port, output))


def _lower_ask_user(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    _lower_user_interaction(
        group, block_id, config, str(config.get("kind") or "text"))


def _lower_confirm(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    _lower_user_interaction(group, block_id, config, "confirm")


def _lower_wait_event(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    signal_id = str(config.get("signal_id") or "").strip()
    if not signal_id:
        raise ValueError("signal_id is required")
    entry = _add_port(group, block_id, "in", "input")
    wait_id = require_semantic_id(f"{block_id}.wait", "wait task id")
    route_id = require_semantic_id(f"{block_id}.route", "route task id")
    group["tasks"][wait_id] = _task(
        "durableWait", signal_id=signal_id, timeout=config.get("timeout", ""))
    group["tasks"][route_id] = _interaction_route_task({
        "signaled": {"attribute": "durable.wait.status", "operator": "equals",
                     "value": "signaled"},
        "timeout": {"attribute": "durable.wait.status", "operator": "equals",
                    "value": "timeout"},
        "cancelled": {"attribute": "durable.wait.status", "operator": "equals",
                      "value": "cancelled"},
    })
    group["relations"].extend([_relation(entry, wait_id), _relation(wait_id, route_id)])
    for output in ("signaled", "timeout", "cancelled", "failure"):
        port = _add_port(group, block_id, "out", output)
        if output == "failure":
            group["relations"].append(_relation(wait_id, port, "failure"))
        group["relations"].append(_relation(route_id, port, output))


def _validate_repeated_body_idempotency(
    tasks: dict[str, Any], config: dict[str, Any], boundary: str,
) -> None:
    from core import TaskFactory
    from core.agent_contracts import IdempotencyClass

    reviewed = config.get("idempotency_policy") == "reviewed"
    retry_key = str(config.get("idempotency_key") or "").strip()
    safe = {
        IdempotencyClass.PURE,
        IdempotencyClass.NATURAL,
        IdempotencyClass.RUN_CACHED,
    }
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            raise ValueError(f"{boundary} body task '{task_id}' must be an object")
        task_type = str(task.get("type") or "")
        try:
            task_class = TaskFactory.get(task_type)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unknown {boundary} body task type '{task_type}'") from exc
        raw = getattr(task_class, "IDEMPOTENCY", None)
        try:
            idempotency = raw if isinstance(raw, IdempotencyClass) else IdempotencyClass(raw)
        except (TypeError, ValueError):
            idempotency = IdempotencyClass.UNSAFE
        if idempotency in safe:
            continue
        parameters = task.get("parameters") or {}
        keyed = idempotency == IdempotencyClass.KEYED_EFFECT and (
            retry_key or str(parameters.get("idempotency_key") or "").strip())
        if keyed or reviewed:
            continue
        raise ValueError(
            f"{boundary} body task '{task_id}' is not safely idempotent; provide an "
            "idempotency key or choose idempotency_policy='reviewed'")


def _retryable_router(
    group: dict[str, Any], block_id: str, attempt: int, codes: list[str],
) -> str:
    router_id = require_semantic_id(
        f"{block_id}.attempt_{attempt}.retryable", "retry router task id")
    pattern = "^(?:" + "|".join(re.escape(code) for code in codes) + ")$"
    group["tasks"][router_id] = _task(
        "routeOnAttribute", routing_strategy="route_to_matched",
        routes={"retryable": {
            "attribute": "error.code", "operator": "matches_regex",
            "value": pattern,
        }},
        default_relationship="exhausted",
    )
    return router_id


def _lower_retry(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    max_attempts = config.get("max_attempts")
    if (isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
            or max_attempts < 2 or max_attempts > MAX_RETRY_ATTEMPTS):
        raise ValueError(
            f"max_attempts must be an integer in 2..{MAX_RETRY_ATTEMPTS}")
    from core.confirmation_store import parse_timeout_seconds
    try:
        backoff_seconds = parse_timeout_seconds(config.get("backoff", 0))
    except ValueError as exc:
        raise ValueError(f"invalid retry backoff: {exc}") from exc
    body_tasks, _relations, _entry, _exit = _single_entry_body(
        config.get("body"), "retry body")
    _validate_repeated_body_idempotency(body_tasks, config, "retry")
    raw_codes = config.get("retryable_error_codes", [])
    if not isinstance(raw_codes, list):
        raise ValueError("retryable_error_codes must be a list")
    codes = [str(code).strip() for code in raw_codes if str(code).strip()]
    if len(codes) != len(set(codes)):
        raise ValueError("retryable_error_codes must be unique")

    entry = _add_port(group, block_id, "in", "input")
    success = _add_port(group, block_id, "out", "success")
    exhausted = _add_port(group, block_id, "out", "exhausted")
    cancelled = _add_port(group, block_id, "out", "cancelled")
    failure = _add_port(group, block_id, "out", "failure")
    attempts: list[tuple[list[str], str, str, str]] = []
    for attempt in range(1, max_attempts + 1):
        task_ids, body_entry, body_exit = _clone_body(
            group, block_id, f"attempt_{attempt}", config.get("body"))
        marker = require_semantic_id(
            f"{block_id}.attempt_{attempt}.marker", "retry marker task id")
        group["tasks"][marker] = _task(
            "updateAttribute", **{
                "set": {"retry.attempt": str(attempt),
                        "retry.max_attempts": str(max_attempts)},
                "delete": ["route.relationship", "route"],
            })
        group["relations"].append(_relation(marker, body_entry))
        group["relations"].append(_relation(body_exit, success))
        group["relations"].append(_relation(marker, failure, "failure"))
        attempts.append((task_ids, marker, body_entry, body_exit))
    group["relations"].append(_relation(entry, attempts[0][1]))

    for index, (task_ids, _marker, _body_entry, _body_exit) in enumerate(attempts):
        if index == max_attempts - 1:
            for task_id in task_ids:
                group["relations"].append(_relation(task_id, exhausted, "failure"))
            continue
        retry_target = attempts[index + 1][1]
        timer_id = ""
        if backoff_seconds > 0:
            timer_id = require_semantic_id(
                f"{block_id}.backoff_{index + 1}", "retry timer task id")
            group["tasks"][timer_id] = _task(
                "durableTimer", duration=config.get("backoff"))
            group["relations"].append(_relation(timer_id, retry_target, "elapsed"))
            group["relations"].append(_relation(timer_id, cancelled, "cancelled"))
            group["relations"].append(_relation(timer_id, failure, "failure"))
            retry_target = timer_id
        if codes:
            router = _retryable_router(
                group, block_id, index + 1, codes)
            for task_id in task_ids:
                group["relations"].append(_relation(task_id, router, "failure"))
            group["relations"].append(_relation(
                router, retry_target, "retryable"))
            group["relations"].append(_relation(
                router, exhausted, "exhausted"))
            group["relations"].append(_relation(router, failure, "failure"))
        else:
            for task_id in task_ids:
                group["relations"].append(_relation(
                    task_id, retry_target, "failure"))


def _positive_int(config: dict[str, Any], field: str, maximum: int) -> int:
    value = config.get(field)
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < 1 or value > maximum):
        raise ValueError(f"{field} must be an integer in 1..{maximum}")
    return value


def _lower_for_each(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    max_iterations = config.get("max_iterations", 0)
    max_flowfiles = config.get("max_flowfiles", 0)
    for field, value in (("max_iterations", max_iterations),
                         ("max_flowfiles", max_flowfiles)):
        if (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"For Each {field} must be an integer >= 0")
    if max_iterations != max_flowfiles:
        raise ValueError("For Each max_iterations must equal max_flowfiles")
    max_duration = config.get("max_duration_seconds", 0)
    if (isinstance(max_duration, bool) or not isinstance(max_duration, (int, float))
            or max_duration < 0):
        raise ValueError("max_duration_seconds must be a number >= 0")
    if config.get("accumulation") != "merge":
        raise ValueError("For Each accumulation must explicitly be 'merge'")
    max_bytes = config.get("max_accumulated_bytes", 0)
    if (isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
            or max_bytes < 0):
        raise ValueError("max_accumulated_bytes must be an integer >= 0")
    tasks, relations, body_entry, body_exit = _single_entry_body(
        config.get("body"), "for each body")
    if any(relation["type"] == "failure" for relation in relations):
        raise ValueError("for each body failure is owned by the composite boundary")

    entry = _add_port(group, block_id, "in", "input")
    success = _add_port(group, block_id, "out", "success")
    empty = _add_port(group, block_id, "out", "empty")
    exhausted = _add_port(group, block_id, "out", "exhausted")
    cancelled = _add_port(group, block_id, "out", "cancelled")
    failure = _add_port(group, block_id, "out", "failure")
    split = require_semantic_id(f"{block_id}.split", "for each split task id")
    guard = require_semantic_id(f"{block_id}.guard", "for each guard task id")
    before_body = require_semantic_id(
        f"{block_id}.before_body", "for each marker task id")
    before_merge = require_semantic_id(
        f"{block_id}.before_merge", "for each marker task id")
    merge = require_semantic_id(f"{block_id}.merge", "for each merge task id")
    group["tasks"][split] = _task(
        "splitJSON",
        json_path_expression=str(config.get("collection_path") or "$"),
        max_fragments=max_iterations, empty_relationship="empty",
        started_at_attribute="fragment.started_at",
    )
    group["tasks"][guard] = _task(
        "boundedLoopGuard", max_duration_seconds=max_duration,
        max_flowfiles=max_flowfiles,
        started_at_attribute="fragment.started_at",
        count_attribute="fragment.count",
    )
    marker_parameters = {"delete": ["route.relationship", "route"], "set": {}}
    group["tasks"][before_body] = _task(
        "updateAttribute", **copy.deepcopy(marker_parameters))
    group["tasks"][before_merge] = _task(
        "updateAttribute", **copy.deepcopy(marker_parameters))
    mapping = {
        task_id: require_semantic_id(
            f"{block_id}.body.{task_id}", "generated for each task id")
        for task_id in tasks
    }
    for task_id, task in tasks.items():
        group["tasks"][mapping[task_id]] = copy.deepcopy(task)
    for relation in relations:
        group["relations"].append(_relation(
            mapping[relation["from"]], mapping[relation["to"]], relation["type"]))
    group["tasks"][merge] = _task(
        "mergeContent", separator=str(config.get("separator", "\n")),
        min_entries=1, correlation_attribute="fragment.identifier",
        expected_count_attribute="fragment.count",
        max_bin_age=int(max_duration),
        max_bin_flowfiles=max_flowfiles, max_bin_bytes=max_bytes,
    )
    group["relations"].extend([
        _relation(entry, split),
        _relation(split, guard, "success"),
        _relation(split, empty, "empty"),
        _relation(split, failure, "failure"),
        _relation(guard, before_body, "continue"),
        _relation(guard, exhausted, "exhausted"),
        _relation(guard, cancelled, "cancelled"),
        _relation(guard, failure, "failure"),
        _relation(before_body, mapping[body_entry]),
        _relation(before_body, failure, "failure"),
        _relation(mapping[body_exit], before_merge),
        _relation(before_merge, merge),
        _relation(before_merge, failure, "failure"),
        _relation(merge, success),
        _relation(merge, failure, "failure"),
    ])
    for task_id in mapping.values():
        group["relations"].append(_relation(task_id, failure, "failure"))


def _lower_repeat_until(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    max_iterations = config.get("max_iterations", 0)
    if (isinstance(max_iterations, bool) or not isinstance(max_iterations, int)
            or max_iterations < 0):
        raise ValueError("max_iterations must be an integer >= 0")
    max_duration = config.get("max_duration_seconds", 0)
    if (isinstance(max_duration, bool) or not isinstance(max_duration, (int, float))
            or max_duration < 0):
        raise ValueError("max_duration_seconds must be a number >= 0")
    iteration_timeout = config.get("iteration_timeout_seconds", 0)
    if (isinstance(iteration_timeout, bool)
            or not isinstance(iteration_timeout, (int, float))
            or iteration_timeout < 0):
        raise ValueError("iteration_timeout_seconds must be a number >= 0")
    condition = _validate_condition(config.get("condition"), "condition")
    body_tasks, _relations, _entry, _exit = _single_entry_body(
        config.get("body"), "repeat until body")
    _validate_repeated_body_idempotency(body_tasks, config, "repeat until")
    entry = _add_port(group, block_id, "in", "input")
    controller = require_semantic_id(
        f"{block_id}.controller", "repeat until controller task id")
    parameters = {
        "body": copy.deepcopy(config.get("body")),
        "condition": condition,
        "max_iterations": max_iterations,
        "max_duration_seconds": max_duration,
        "iteration_delay": config.get("iteration_delay", 0),
        "iteration_timeout_seconds": iteration_timeout,
    }
    group["tasks"][controller] = _task("repeatUntil", **parameters)
    group["relations"].append(_relation(entry, controller))
    for output in ("success", "exhausted", "cancelled", "failure"):
        port = _add_port(group, block_id, "out", output)
        group["relations"].append(_relation(controller, port, output))


def _lower_workflow_agent(
    group: dict[str, Any], block_id: str, config: dict[str, Any],
) -> None:
    try:
        agent_ref = ResourceRef.from_dict(config.get("agent_ref") or {})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"workflow_agent requires an exact agent_ref: {exc}") from exc
    if agent_ref.resource_type != "agent":
        raise ValueError("workflow_agent agent_ref must identify an agent")
    if not str(config.get("message") or "").strip():
        raise ValueError("workflow_agent message is required")

    entry = _add_port(group, block_id, "in", "input")
    invoke_id = require_semantic_id(
        f"{block_id}.invoke", "Workflow Agent task id")
    parameters = copy.deepcopy(config)
    profile_ref = str(parameters.pop("executor_profile", "") or "")
    for field in ("label", "description", "color"):
        parameters.pop(field, None)
    group["tasks"][invoke_id] = _task("invokeWorkflowAgent", **parameters)
    group["relations"].append(_relation(entry, invoke_id))
    for output in _WORKFLOW_AGENT_OUTPUTS:
        port = _add_port(group, block_id, "out", output)
        group["relations"].append(_relation(invoke_id, port, output))
    primary = (
        {"executor_profile": profile_ref}
        if profile_ref else {
            "kind": "workflow_agent", "agent_ref": agent_ref.to_dict(),
        }
    )
    group["execution"] = {
        "strategy": "single", "roles": {"primary": primary},
    }


_LOWERERS = {
    "if": _lower_if,
    "switch": _lower_switch,
    "parallel": _lower_parallel,
    "join": _lower_join,
    "repeat_n": _lower_repeat_n,
    "try_catch": _lower_try_catch,
    "wait_duration": _lower_wait_duration,
    "wait_until": _lower_wait_until,
    "ask_user": _lower_ask_user,
    "confirm": _lower_confirm,
    "wait_event": _lower_wait_event,
    "retry": _lower_retry,
    "for_each": _lower_for_each,
    "repeat_until": _lower_repeat_until,
    "workflow_agent": _lower_workflow_agent,
}


def lower_control_block(
    block_id: Any, control_type: Any, config: Any,
) -> dict[str, Any]:
    """Lower one supported semantic control block to an inline ProcessGroup."""
    block_id = require_semantic_id(block_id, "block_id")
    control_type = str(control_type or "")
    if control_type not in _LOWERERS:
        raise ValueError(f"unsupported control block '{control_type}'")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    group = _base_group(block_id, control_type, config)
    _LOWERERS[control_type](group, block_id, config)
    return group


__all__ = [
    "LOWERING_VERSION", "MAX_PARALLEL_BRANCHES", "MAX_REPEAT_UNROLL",
    "MAX_RETRY_ATTEMPTS",
    "lower_control_block",
]
