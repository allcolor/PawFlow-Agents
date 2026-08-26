"""Idempotent LLM-step state for :mod:`core.workflow_run_store`."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class WorkflowBudgetExceeded(RuntimeError):
    """A run cannot start another charged step within its stored limits."""


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowRunStoreLLMMixin:
    """Transactional step cache, call reservations, usage, and budgets."""

    def cache_step_result(self, run_id: str, task_id: str, input_hash: str,
                          result: Any, usage: dict[str, Any]) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO workflow_step_results VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, task_id, input_hash) DO NOTHING""",
                (_required(run_id, "run_id"), _required(task_id, "task_id"),
                 _required(input_hash, "input_hash"), _json(result),
                 _json(usage), time.time()))
            return cursor.rowcount == 1

    def begin_llm_step(self, run_id: str, task_id: str,
                       input_hash: str) -> dict[str, Any] | None:
        """Reserve one LLM call, or return its already committed cache row."""
        run_id = _required(run_id, "run_id")
        task_id = _required(task_id, "task_id")
        input_hash = _required(input_hash, "input_hash")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status, usage_json, limits_json FROM workflow_runs "
                "WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError("workflow run does not exist")
            if run["status"] != "running":
                raise RuntimeError(
                    f"workflow LLM step requires running, got {run['status']}")
            cached = connection.execute(
                "SELECT result_json, usage_json FROM workflow_step_results "
                "WHERE run_id=? AND task_id=? AND input_hash=?",
                (run_id, task_id, input_hash)).fetchone()
            if cached is not None:
                return {
                    "result": json.loads(cached["result_json"]),
                    "usage": json.loads(cached["usage_json"]),
                }
            usage = json.loads(run["usage_json"])
            limits = json.loads(run["limits_json"])
            active = int(connection.execute(
                "SELECT COUNT(*) FROM workflow_llm_reservations "
                "WHERE run_id=?", (run_id,)).fetchone()[0])
            maximum_calls = limits.get("max_llm_calls")
            if (
                int(maximum_calls or 0) > 0
                and int(usage.get("llm_calls", 0)) + active
                >= int(maximum_calls)
            ):
                raise WorkflowBudgetExceeded(
                    "workflow LLM call budget exhausted")
            maximum_cost = limits.get("max_cost_usd")
            if float(maximum_cost or 0.0) > 0 and float(
                    usage.get("cost_usd", 0.0)) >= float(maximum_cost):
                raise WorkflowBudgetExceeded("workflow cost budget exhausted")
            try:
                connection.execute(
                    "INSERT INTO workflow_llm_reservations VALUES (?, ?, ?, ?)",
                    (run_id, task_id, input_hash, time.time()))
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(
                    "workflow LLM step is already in progress") from exc
        return None

    def commit_llm_step(
        self, run_id: str, task_id: str, input_hash: str,
        result: Any, usage: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit a cached result and aggregate usage exactly once."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute(
                "SELECT result_json, usage_json FROM workflow_step_results "
                "WHERE run_id=? AND task_id=? AND input_hash=?",
                (run_id, task_id, input_hash)).fetchone()
            inserted = cached is None
            if inserted:
                run_state = connection.execute(
                    "SELECT status FROM workflow_runs WHERE run_id=?",
                    (run_id,)).fetchone()
                if run_state is None:
                    raise KeyError("workflow run does not exist")
                if run_state["status"] != "running":
                    raise WorkflowBudgetExceeded(
                        "workflow run stopped before LLM result commit")
                reservation = connection.execute(
                    "SELECT 1 FROM workflow_llm_reservations WHERE "
                    "run_id=? AND task_id=? AND input_hash=?",
                    (run_id, task_id, input_hash)).fetchone()
                if reservation is None:
                    raise RuntimeError("workflow LLM step was not reserved")
                connection.execute(
                    "INSERT INTO workflow_step_results VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, task_id, input_hash, _json(result), _json(usage),
                     time.time()))
                row = connection.execute(
                    "SELECT usage_json FROM workflow_runs WHERE run_id=?",
                    (run_id,)).fetchone()
                if row is None:
                    raise KeyError("workflow run does not exist")
                aggregate = json.loads(row["usage_json"])
                for key in (
                    "llm_calls", "tokens_in", "tokens_out", "cache_read",
                    "cache_write", "duration_ms", "cost_usd",
                    "virtual_cost_usd",
                ):
                    aggregate[key] = aggregate.get(key, 0) + usage.get(key, 0)
                connection.execute(
                    "UPDATE workflow_runs SET usage_json=?, updated_at=? "
                    "WHERE run_id=?", (_json(aggregate), time.time(), run_id))
            else:
                result = json.loads(cached["result_json"])
                usage = json.loads(cached["usage_json"])
                aggregate_row = connection.execute(
                    "SELECT usage_json FROM workflow_runs WHERE run_id=?",
                    (run_id,)).fetchone()
                aggregate = json.loads(aggregate_row["usage_json"])
            connection.execute(
                "DELETE FROM workflow_llm_reservations WHERE "
                "run_id=? AND task_id=? AND input_hash=?",
                (run_id, task_id, input_hash))
        return {
            "inserted": inserted, "result": result,
            "step_usage": dict(usage), "run_usage": aggregate,
        }

    def abort_llm_step(self, run_id: str, task_id: str,
                       input_hash: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM workflow_llm_reservations WHERE "
                "run_id=? AND task_id=? AND input_hash=?",
                (run_id, task_id, input_hash))
            return cursor.rowcount == 1

    def mark_budget_exceeded(self, run_id: str, reason: str) -> bool:
        run = self.get_run(run_id)
        if run is None or run["status"] != "running":
            return False
        return self.transition(run_id, "running", "budget_exceeded", reason)

    def get_step_result(self, run_id: str, task_id: str,
                        input_hash: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT result_json, usage_json FROM workflow_step_results
                   WHERE run_id=? AND task_id=? AND input_hash=?""",
                (run_id, task_id, input_hash)).fetchone()
        if row is None:
            return None
        return {
            "result": json.loads(row["result_json"]),
            "usage": json.loads(row["usage_json"]),
        }


__all__ = ["WorkflowBudgetExceeded", "WorkflowRunStoreLLMMixin"]
