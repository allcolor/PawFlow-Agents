"""Auto-poke — restart an agent that stopped on unfinished work.

The plan orchestrator advances on one signal only: the agent calling
``update_plan``. When a turn ends without that call, the step stays
``in_progress`` and nothing ever wakes the agent again — the plan is stalled
until a human notices. That is the common failure mode: not a wrong answer, an
early exit.

This module decides whether a finished turn left such a step behind, and how
many times it is worth saying so. The decision is pure: callers pass the plans
and the agent that just ran, and get back the message to deliver, or nothing.

Guardrails, because a poke re-enters the agent loop:

* Only a step this agent owns, ``in_progress``, unpaused, on a plan that is
  itself running — never a plan waiting for approval, a step waiting for its
  verifier, or a step somebody else owns.
* At most :func:`poke_limit` consecutive pokes for the same step. The counter
  resets as soon as the step changes or its status moves, so real progress
  always buys a fresh budget and a stuck agent is never poked forever.
* Callers must not poke an errored, interrupted or force-stopped turn. A force
  stop is a decision, not a failure, and must never affect the next loop.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Consecutive pokes allowed for one step before giving up on it.
DEFAULT_LIMIT = 2

#: Steps tracked at once.
MAX_TRACKED = 256

#: Plan states in which a stalled step is worth poking about.
RUNNING_PLAN_STATUSES = ("in_progress", "approved")


def poke_limit() -> int:
    """Consecutive pokes allowed per step. 0 disables the feature.

    Reads ``PAWFLOW_AUTO_POKE_LIMIT`` first, then ``auto_poke_limit`` in
    ``global_parameters.json``, so it can be turned off from the UI.
    """
    raw = os.environ.get("PAWFLOW_AUTO_POKE_LIMIT", "").strip()
    if not raw:
        try:
            from core.expression import _load_global_parameters
            raw = str(_load_global_parameters().get("auto_poke_limit", "")).strip()
        except Exception:
            logger.debug("Could not read auto_poke_limit", exc_info=True)
            raw = ""
    if not raw:
        return DEFAULT_LIMIT
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_LIMIT


def stalled_step(plans: List[Dict[str, Any]],
                 agent_name: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """First (plan, step) this agent left running, or None.

    ``pending_verification`` is deliberately excluded: that step is waiting on
    somebody else, and poking its author would produce a duplicate report.
    """
    for plan in plans or []:
        if not isinstance(plan, dict):
            continue
        if plan.get("status") not in RUNNING_PLAN_STATUSES:
            continue
        for step in plan.get("steps") or []:
            if step.get("status") != "in_progress" or step.get("paused"):
                continue
            owner = step.get("assigned_to") or plan.get("created_by", "")
            if owner and agent_name and owner != agent_name:
                continue
            return plan, step
    return None


def poke_text(plan: Dict[str, Any], step: Dict[str, Any]) -> str:
    """The message handed back to the agent.

    It names the two acceptable exits — finish, or report the blocker — because
    an agent that stopped early usually believes it is done.
    """
    total = len(plan.get("steps") or [])
    index = step.get("index", 0)
    plan_id = plan.get("id", "")
    return (
        f"Your turn ended while step {index}/{total} is still in progress: "
        f"{step.get('description', '')}\n\n"
        f"Nothing restarts this plan until you report on it. Either finish the "
        f"step and call:\n"
        f"  update_plan(plan_id=\"{plan_id}\", updates=[{{\"step\": {index}, "
        f"\"status\": \"done\", \"note\": \"what you did\"}}])\n"
        f"or, if you cannot, report the blocker with status \"error\" and a note "
        f"saying what stopped you. Do not stop silently, and do not skip ahead "
        f"to other steps."
    )


class PokeLedger:
    """Consecutive-poke counter, keyed per step and bounded."""

    _instance: Optional["PokeLedger"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "PokeLedger":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (conv_id, plan_id) -> (step_marker, consecutive_poke_count)
        self._counts: OrderedDict = OrderedDict()

    def claim(self, conversation_id: str, plan: Dict[str, Any],
              step: Dict[str, Any], limit: int) -> bool:
        """Consume one poke for this step. False when the budget is spent.

        Keyed by plan rather than by step so that moving to another step resets
        the budget: progress is exactly what should buy more patience.
        """
        if limit <= 0:
            return False
        key = (conversation_id or "", str(plan.get("id", "")))
        marker = (step.get("index", 0), step.get("status", ""), step.get("note", ""))
        with self._lock:
            previous = self._counts.get(key)
            count = previous[1] + 1 if previous and previous[0] == marker else 1
            if count > limit:
                return False
            self._counts[key] = (marker, count)
            self._counts.move_to_end(key)
            while len(self._counts) > MAX_TRACKED:
                self._counts.popitem(last=False)
            return True

    def forget(self, conversation_id: str, plan_id: str) -> None:
        """Drop the counter for a plan (completed, cancelled, deleted)."""
        with self._lock:
            self._counts.pop((conversation_id or "", str(plan_id or "")), None)


def poke_for_turn(conversation_id: str, user_id: str,
                  agent_name: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    """Decide whether the turn that just ended deserves a poke.

    Returns ``(plan, step, message)`` or None. Loads the plans itself so the
    caller stays a one-liner; never raises.
    """
    limit = poke_limit()
    if limit <= 0 or not conversation_id or not user_id:
        return None
    try:
        from core.plan_store import PlanStore
        plans = PlanStore.instance().list_plans(user_id, conversation_id)
    except Exception:
        logger.debug("Auto-poke could not list plans", exc_info=True)
        return None

    found = stalled_step(plans, agent_name)
    if not found:
        return None
    plan, step = found
    if not PokeLedger.instance().claim(conversation_id, plan, step, limit):
        logger.info("[auto-poke] budget spent for plan %s step %s — leaving it alone",
                    plan.get("id", ""), step.get("index", 0))
        return None
    return plan, step, poke_text(plan, step)
