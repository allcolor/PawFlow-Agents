"""Spend budgets — cumulative daily/monthly caps on top of the usage ledger.

Distinct from the existing `max_budget_usd` LLM-service parameter (a
single-agent-loop-invocation cap, see `tasks/ai/_alc_base.py:_check_budget`):
these are cross-turn, period-based budgets scoped to a user, conversation,
agent, LLM service, or the whole deployment (`global`), persisted so they
survive restarts and evaluated against real spend in `core/usage_ledger.py`
(subscription services' virtual cost never counts toward a budget).

Two independent checks:

- `enforce_pre_turn` — called once per external agent-loop turn, BEFORE the
  LLM call. Raises `BudgetExceededError` (message starts with "Budget
  exceeded:", matching the existing fatal-error string match in
  `tasks/ai/_alc_llm_turn.py`) for any `policy="block"` budget whose
  period-to-date spend, as of turn start, already reached its limit. This
  stops the NEXT turn, not the one that crossed the line — a turn's cost is
  only known once it completes.
- `check_and_notify` — called from `UsageLedger.record()` after every
  event, across all channels, best-effort (never raises). Posts a
  conversation notification the first time a budget's period-to-date spend
  crosses 50/80/100%, deduplicated per (budget, period) so it never
  re-fires until the next period.
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths

logger = logging.getLogger(__name__)

SCOPE_TYPES = ("global", "user", "conversation", "agent", "llm_service")
PERIODS = ("daily", "monthly")
POLICIES = ("warn", "block")
THRESHOLDS = (100, 80, 50)  # checked highest-first so the right one fires

_SCOPE_TO_FILTER_KEY = {
    "user": "user_id",
    "conversation": "conversation_prefix",
    "agent": "agent_name",
    "llm_service": "llm_service",
}


class BudgetExceededError(RuntimeError):
    """Raised by enforce_pre_turn; message always starts with 'Budget exceeded:'."""


@dataclass
class Budget:
    id: str
    scope_type: str
    scope_value: str
    period: str
    limit_usd: float
    policy: str
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    # Dedup state for check_and_notify — which threshold was last announced
    # for which period key ("2026-07-20" for daily, "2026-07" for monthly).
    last_notified_pct: int = 0
    last_notified_period_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Budget":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def label(self) -> str:
        if self.scope_type == "global":
            return "global"
        return f"{self.scope_type}:{self.scope_value}"


def _validate(scope_type: str, scope_value: str, period: str,
             limit_usd: float, policy: str) -> None:
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"scope_type must be one of {SCOPE_TYPES}")
    if scope_type != "global" and not scope_value:
        raise ValueError(f"scope_value is required for scope_type={scope_type!r}")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    if not (limit_usd > 0):
        raise ValueError("limit_usd must be a positive number")


class BudgetStore:
    """Singleton JSON-backed store of spend budgets."""

    _instance: Optional["BudgetStore"] = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = Path(path or str(_paths.USAGE_BUDGETS_FILE))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._store_lock = threading.Lock()
        self._budgets: Dict[str, Budget] = {}
        self._loaded = False

    @classmethod
    def instance(cls) -> "BudgetStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for item in raw.get("budgets", []):
                    b = Budget.from_dict(item)
                    self._budgets[b.id] = b
            except Exception as e:
                logger.warning("[budgets] failed to load %s: %s",
                               self._path, e)

    def _save(self):
        tmp = self._path.with_suffix(".tmp")
        payload = {"budgets": [b.to_dict() for b in self._budgets.values()]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._path)

    def create(self, *, scope_type: str, scope_value: str, period: str,
              limit_usd: float, policy: str, created_by: str) -> Budget:
        _validate(scope_type, scope_value, period, limit_usd, policy)
        now = time.time()
        b = Budget(id=uuid.uuid4().hex[:12], scope_type=scope_type,
                   scope_value=scope_value if scope_type != "global" else "",
                   period=period, limit_usd=float(limit_usd), policy=policy,
                   created_by=created_by, created_at=now, updated_at=now)
        with self._store_lock:
            self._ensure_loaded()
            self._budgets[b.id] = b
            self._save()
        return b

    def update(self, budget_id: str, **fields) -> Budget:
        with self._store_lock:
            self._ensure_loaded()
            b = self._budgets.get(budget_id)
            if b is None:
                raise KeyError(f"No budget with id {budget_id!r}")
            merged = {
                "scope_type": fields.get("scope_type", b.scope_type),
                "scope_value": fields.get("scope_value", b.scope_value),
                "period": fields.get("period", b.period),
                "limit_usd": float(fields.get("limit_usd", b.limit_usd)),
                "policy": fields.get("policy", b.policy),
            }
            _validate(**merged)
            b.scope_type = merged["scope_type"]
            b.scope_value = (merged["scope_value"]
                             if b.scope_type != "global" else "")
            b.period = merged["period"]
            b.limit_usd = merged["limit_usd"]
            b.policy = merged["policy"]
            b.updated_at = time.time()
            # Scope/period/limit changed → dedup state no longer applies.
            b.last_notified_pct = 0
            b.last_notified_period_key = ""
            self._save()
            return b

    def delete(self, budget_id: str) -> bool:
        with self._store_lock:
            self._ensure_loaded()
            if budget_id not in self._budgets:
                return False
            del self._budgets[budget_id]
            self._save()
            return True

    def get(self, budget_id: str) -> Optional[Budget]:
        with self._store_lock:
            self._ensure_loaded()
            return self._budgets.get(budget_id)

    def list(self, *, scope_type: str = "", scope_value: str = "") -> List[Budget]:
        with self._store_lock:
            self._ensure_loaded()
            out = list(self._budgets.values())
        if scope_type:
            out = [b for b in out if b.scope_type == scope_type]
        if scope_value:
            out = [b for b in out if b.scope_value == scope_value]
        return sorted(out, key=lambda b: b.created_at)

    def _save_one(self, b: Budget) -> None:
        """Persist a mutated Budget already present in the store (dedup state)."""
        with self._store_lock:
            self._ensure_loaded()
            if b.id in self._budgets:
                self._save()


# -- period math ----------------------------------------------------------

def period_bounds(period: str, now: Optional[float] = None) -> (float, str):
    """Return (period_start_ts, period_key) in local time."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    if period == "daily":
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                             0, 0, 0, 0, 0, -1))
        key = time.strftime("%Y-%m-%d", lt)
    else:  # monthly
        start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        key = time.strftime("%Y-%m", lt)
    return start, key


# -- matching + spend -------------------------------------------------------

def _matches(budget: Budget, *, user_id: str, conversation_id: str,
            agent_name: str, llm_service: str) -> bool:
    if budget.scope_type == "global":
        return True
    if budget.scope_type == "user":
        return bool(user_id) and budget.scope_value == user_id
    if budget.scope_type == "conversation":
        if not conversation_id:
            return False
        return (conversation_id == budget.scope_value
                or conversation_id.startswith(budget.scope_value + "::"))
    if budget.scope_type == "agent":
        return bool(agent_name) and budget.scope_value == agent_name
    if budget.scope_type == "llm_service":
        return bool(llm_service) and budget.scope_value == llm_service
    return False


def _matching_budgets(store: BudgetStore, **dims) -> List[Budget]:
    return [b for b in store.list() if _matches(b, **dims)]


def current_spend(ledger, budget: Budget) -> float:
    """Real (non-virtual) period-to-date spend for one budget's scope."""
    start, _ = period_bounds(budget.period)
    filters: Dict[str, Any] = {"since": start}
    key = _SCOPE_TO_FILTER_KEY.get(budget.scope_type)
    if key:
        filters[key] = budget.scope_value
    return float(ledger.summary(**filters).get("cost_usd", 0.0) or 0.0)


# -- enforcement + notification ---------------------------------------------

def enforce_pre_turn(ledger, *, user_id: str = "", conversation_id: str = "",
                     agent_name: str = "", llm_service: str = "") -> None:
    """Raise BudgetExceededError if any matching block-policy budget is
    already at or past its limit as of now. Call once per external turn,
    BEFORE the LLM call. Best-effort at the caller's discretion — this
    function itself always either passes silently or raises.
    """
    store = BudgetStore.instance()
    dims = dict(user_id=user_id, conversation_id=conversation_id,
               agent_name=agent_name, llm_service=llm_service)
    for b in _matching_budgets(store, **dims):
        if b.policy != "block":
            continue
        spend = current_spend(ledger, b)
        if spend >= b.limit_usd:
            raise BudgetExceededError(
                f"Budget exceeded: {b.label()} spent ${spend:.2f} of "
                f"${b.limit_usd:.2f} this {b.period} period. New turns on "
                f"this scope are blocked until the period resets or the "
                f"budget is raised.")


def check_and_notify(ledger, *, user_id: str = "", conversation_id: str = "",
                     agent_name: str = "", llm_service: str = "") -> None:
    """Best-effort: post a conversation notification the first time a
    matching budget's period-to-date spend crosses 50/80/100%. Never
    raises — callers (UsageLedger.record) must not have usage recording
    depend on notification delivery.
    """
    try:
        store = BudgetStore.instance()
        dims = dict(user_id=user_id, conversation_id=conversation_id,
                   agent_name=agent_name, llm_service=llm_service)
        for b in _matching_budgets(store, **dims):
            if b.limit_usd <= 0:
                continue
            _start, period_key = period_bounds(b.period)
            if b.last_notified_period_key != period_key:
                b.last_notified_pct = 0
                b.last_notified_period_key = period_key
            spend = current_spend(ledger, b)
            pct = spend / b.limit_usd * 100.0
            fired = None
            for t in THRESHOLDS:
                if pct >= t and b.last_notified_pct < t:
                    fired = t
                    break
            if fired is None:
                continue
            b.last_notified_pct = fired
            store._save_one(b)
            _notify(b, spend, fired, conversation_id, agent_name)
    except Exception:
        logger.debug("[budgets] check_and_notify failed", exc_info=True)


def _notify(budget: Budget, spend: float, pct_threshold: int,
           conversation_id: str, agent_name: str) -> None:
    """Post the threshold-crossing message into the triggering conversation.

    Same persist+SSE pattern as core.handlers.push_notification (stamped
    system message + `new_message`/`notification` SSE events) so it renders
    as a bell row and fires the same attention signals. No conversation to
    post into (e.g. a user- or global-scoped budget crossed by a channel
    with no conversation_id) is a silent no-op — logged only.
    """
    verb = "BLOCKED" if (pct_threshold >= 100 and budget.policy == "block") \
        else "reached"
    message = (
        f"Budget alert: {budget.label()} ({budget.period}) has {verb} "
        f"{pct_threshold}% — ${spend:.2f} of ${budget.limit_usd:.2f}.")
    if not conversation_id:
        logger.info("[budgets] %s (no conversation to notify)", message)
        return
    try:
        import uuid as _uuid
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        msg_id = _uuid.uuid4().hex[:12]
        stamped = stamp_message({
            "role": "user",
            "content": message,
            "msg_id": msg_id,
            "source": {"type": "system", "name": "notification",
                      "agent": agent_name or "", "status": "proactive"},
        }, conversation_id)
        sse_events = [
            {"type": "new_message", "cid": conversation_id, "data": {
                "role": "user", "content": message, "msg_id": msg_id,
                "source": stamped["source"]}},
            {"type": "notification", "cid": conversation_id, "data": {
                "msg_id": msg_id, "content": message,
                "agent": agent_name or "", "status": "proactive",
                "ts": time.time()}},
            {"type": "budget.updated", "cid": conversation_id, "data": {
                "budget_id": budget.id, "pct": pct_threshold,
                "spend_usd": spend, "limit_usd": budget.limit_usd}},
        ]
        writer = ConversationWriter.for_conversation(conversation_id)
        writer.enqueue_message(stamped, agent_name=agent_name or "",
                               user_id="", sse_events=sse_events)
        logger.info("[budgets] %s", message)
    except Exception:
        logger.warning("[budgets] notification delivery failed for %s",
                       budget.id, exc_info=True)
