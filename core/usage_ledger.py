"""UsageLedger — event-level LLM usage and cost ledger (SQLite).

Single source of truth for token/cost tracking. Every LLM call records ONE
event with full dimensions (user, conversation, agent, llm_service, model,
provider, channel) and the cost FROZEN at the price configured on the LLM
service at call time — later pricing changes never rewrite history.

Pricing comes from the LLM service config (`cost_per_1m_input` /
`cost_per_1m_output` on the llmConnection service). There is NO hardcoded
price table — when a caller does not pass pricing, the event is recorded
at $0 (tokens are still tallied). Cache pricing defaults: read = 10% of
input, write = 125% of input (Anthropic's published ratios).

Channels (documented convention, free-form string, never empty):
  chat                normal conversation turn
  task                autonomous task iteration (sub-conv)
  subagent            delegate / flash_delegate sub-agent run
  aggregator_advisor  llmAggregator advisor call
  compaction          context compaction / summarizer call
  realtime            LiveKit realtime voice/video session
  system              internal calls (title generation, learn, ...)
  migrated            synthetic events imported from the legacy
                      token_usage.json aggregates

Replaces the former TokenTracker (JSON aggregates, no conversation
dimension) and CostTracker (in-memory, lost on restart). The legacy
`data/runtime/token_usage.json` is imported once at first init: each
per-user agent::service aggregate becomes one synthetic `migrated` event
(day-level and per-model history of the legacy file are not portable
without double counting and are dropped), then the file is renamed to
`token_usage.json.migrated`.
"""

import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths

logger = logging.getLogger(__name__)

# Whitelists — group/dimension names map to columns, never interpolate
# caller strings into SQL.
_GROUP_COLUMNS = ("llm_service", "agent_name", "model", "channel",
                  "user_id", "conversation_id", "provider")
_BUCKETS = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d",
            "month": "%Y-%m"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL DEFAULT '',
    llm_service TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    virtual_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_user_ts ON usage_events (user_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_conv_ts
    ON usage_events (conversation_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events (day);
"""

_SUM_COLS = ("SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
             "SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write, "
             "COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, "
             "SUM(virtual_cost_usd) AS virtual_cost_usd")


def compute_cost(tokens_in: int, tokens_out: int, cache_read: int,
                 cache_write: int,
                 cost_per_1m_input: Optional[float],
                 cost_per_1m_output: Optional[float],
                 cost_per_1m_cache_read: Optional[float] = None,
                 cost_per_1m_cache_write: Optional[float] = None) -> float:
    """Cache-aware cost in USD; unset cache rates derive from input rate."""
    ci = float(cost_per_1m_input or 0.0)
    co = float(cost_per_1m_output or 0.0)
    ccr = (float(cost_per_1m_cache_read)
           if cost_per_1m_cache_read is not None else ci * 0.1)
    ccw = (float(cost_per_1m_cache_write)
           if cost_per_1m_cache_write is not None else ci * 1.25)
    return (tokens_in * ci + tokens_out * co
            + cache_read * ccr + cache_write * ccw) / 1_000_000


class UsageLedger:
    """Singleton SQLite ledger of LLM usage events."""

    _instance: Optional["UsageLedger"] = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = Path(path or str(_paths.USAGE_DB_FILE))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path),
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._db_lock:
            self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                logger.debug("WAL not available", exc_info=True)
            self._conn.commit()
        self._migrate_legacy_json()

    @classmethod
    def instance(cls) -> "UsageLedger":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance._conn.close()
                except Exception:
                    logger.debug("close failed", exc_info=True)
            cls._instance = None

    # -- write ------------------------------------------------------------

    def record(self, *, user_id: str, channel: str,
               tokens_in: int = 0, tokens_out: int = 0,
               conversation_id: str = "", agent_name: str = "",
               llm_service: str = "", model: str = "", provider: str = "",
               cache_read: int = 0, cache_write: int = 0,
               duration_ms: int = 0,
               cost_per_1m_input: Optional[float] = None,
               cost_per_1m_output: Optional[float] = None,
               cost_per_1m_cache_read: Optional[float] = None,
               cost_per_1m_cache_write: Optional[float] = None,
               virtual_cost_usd: float = 0.0,
               subscription: bool = False,
               ts: Optional[float] = None) -> float:
        """Record one usage event; returns the REAL cost recorded (USD).

        user_id and channel are REQUIRED (no anonymous fallback). Cost is
        computed from the caller-supplied per-1M rates and frozen in the
        event; omitted pricing = $0.

        subscription=True (flat-rate service, e.g. a Claude/Codex/Gemini
        subscription login): the computed cost is what the tokens WOULD
        have cost at the configured API-equivalent rates — it is stored as
        virtual_cost_usd, real cost_usd stays 0, and 0.0 is returned so
        budgets/gauges never count subscription traffic as real spend.
        """
        if not user_id:
            raise ValueError("BUG: user_id required for usage recording")
        if not channel:
            raise ValueError("BUG: channel required for usage recording")
        cost = compute_cost(tokens_in, tokens_out, cache_read, cache_write,
                            cost_per_1m_input, cost_per_1m_output,
                            cost_per_1m_cache_read, cost_per_1m_cache_write)
        if subscription:
            virtual_cost_usd = float(virtual_cost_usd or 0.0) + cost
            cost = 0.0
        ts = float(ts if ts is not None else time.time())
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        with self._db_lock:
            self._conn.execute(
                "INSERT INTO usage_events (id, ts, day, user_id, "
                "conversation_id, agent_name, llm_service, model, provider, "
                "channel, tokens_in, tokens_out, cache_read, cache_write, "
                "duration_ms, cost_usd, virtual_cost_usd) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), ts, day, user_id, conversation_id,
                 agent_name, llm_service, model, provider, channel,
                 int(tokens_in), int(tokens_out), int(cache_read),
                 int(cache_write), int(duration_ms), cost,
                 float(virtual_cost_usd or 0.0)))
            self._conn.commit()
        return cost

    # -- filter helper ----------------------------------------------------

    @staticmethod
    def _where(user_id="", conversation_id="", agent_name="",
               llm_service="", model="", channel="", since=0.0, until=0.0,
               conversation_prefix=""):
        clauses, params = [], []
        for col, val in (("user_id", user_id),
                         ("conversation_id", conversation_id),
                         ("agent_name", agent_name),
                         ("llm_service", llm_service),
                         ("model", model), ("channel", channel)):
            if val:
                clauses.append(f"{col} = ?")
                params.append(val)
        if conversation_prefix:
            # The conversation plus its task sub-conversations
            # (`<cid>::task::<tid>`) — how the UI totals a conversation.
            clauses.append("(conversation_id = ? OR conversation_id LIKE ?)")
            params.extend([conversation_prefix,
                           conversation_prefix + "::%"])
        if since:
            clauses.append("ts >= ?")
            params.append(float(since))
        if until:
            clauses.append("ts < ?")
            params.append(float(until))
        return ((" WHERE " + " AND ".join(clauses)) if clauses else "",
                params)

    def _query(self, sql: str, params) -> List[sqlite3.Row]:
        with self._db_lock:
            return self._conn.execute(sql, params).fetchall()

    # -- reads ------------------------------------------------------------

    def summary(self, **filters) -> Dict[str, Any]:
        """Aggregate totals for arbitrary filters (see _where kwargs)."""
        where, params = self._where(**filters)
        # SQL pieces are module constants + fixed column names; all caller
        # values are bound parameters (same below).
        row = self._query(
            f"SELECT {_SUM_COLS} FROM usage_events{where}", params)[0]  # nosec B608
        return {k: (row[k] or 0) for k in row.keys()}

    def timeseries(self, *, bucket: str = "day", group_by: str = "",
                   **filters) -> List[Dict[str, Any]]:
        """Bucketed totals, optionally grouped by one dimension column."""
        fmt = _BUCKETS.get(bucket)
        if fmt is None:
            raise ValueError(f"bucket must be one of {sorted(_BUCKETS)}")
        if group_by and group_by not in _GROUP_COLUMNS:
            raise ValueError(f"group_by must be one of {_GROUP_COLUMNS}")
        where, params = self._where(**filters)
        group_sel = f", {group_by} AS grp" if group_by else ""
        group_clause = f", {group_by}" if group_by else ""
        # fmt/group_by whitelisted above, values bound as parameters
        rows = self._query(
            f"SELECT strftime('{fmt}', ts, 'unixepoch', 'localtime') "  # nosec B608
            f"AS bucket{group_sel}, {_SUM_COLS} FROM usage_events{where} "
            f"GROUP BY bucket{group_clause} ORDER BY bucket", params)
        return [dict(r) for r in rows]

    def top(self, *, dimension: str = "conversation_id", limit: int = 10,
            order_by: str = "cost_usd", **filters) -> List[Dict[str, Any]]:
        """Top-N values of one dimension by cost (or tokens)."""
        if dimension not in _GROUP_COLUMNS:
            raise ValueError(f"dimension must be one of {_GROUP_COLUMNS}")
        if order_by not in ("cost_usd", "tokens_in", "tokens_out", "calls"):
            raise ValueError("order_by must be cost_usd/tokens_in/"
                             "tokens_out/calls")
        where, params = self._where(**filters)
        # dimension/order_by whitelisted above, values bound as parameters
        rows = self._query(
            f"SELECT {dimension} AS value, {_SUM_COLS} "  # nosec B608
            f"FROM usage_events{where} GROUP BY {dimension} "
            f"ORDER BY {order_by} DESC LIMIT ?", params + [int(limit)])
        return [dict(r) for r in rows]

    def export_rows(self, *, limit: int = 10000,
                    **filters) -> List[Dict[str, Any]]:
        """Raw events, newest first, for CSV/JSON export."""
        where, params = self._where(**filters)
        # where clause is fixed column names, values bound as parameters
        rows = self._query(
            f"SELECT * FROM usage_events{where} ORDER BY ts DESC LIMIT ?",  # nosec B608
            params + [int(limit)])
        return [dict(r) for r in rows]

    # -- compatibility-shaped reads (former tracker consumers) ------------

    def conversation_cost(self, conversation_id: str) -> Dict[str, Any]:
        """{total, by_model} for one conversation (CostTracker shape)."""
        rows = self._query(
            "SELECT model, SUM(tokens_in) i, SUM(tokens_out) o, "
            "SUM(cache_read) cr, SUM(cache_write) cw, SUM(cost_usd) c "
            "FROM usage_events WHERE conversation_id = ? GROUP BY model",
            (conversation_id,))
        by_model = {r["model"]: {"in": r["i"] or 0, "out": r["o"] or 0,
                                 "cache_read": r["cr"] or 0,
                                 "cache_write": r["cw"] or 0,
                                 "cost": r["c"] or 0.0}
                    for r in rows}
        return {"total": sum(m["cost"] for m in by_model.values()),
                "by_model": by_model}

    def conversation_breakdown(self, conversation_id: str,
                               recent_limit: int = 30) -> Dict[str, Any]:
        """Full cost/token picture of one conversation for the UI panel.

        Includes the conversation's task sub-conversations
        (`<cid>::task::<tid>`). Returns totals plus by_agent / by_channel /
        by_model groupings and the most recent events.
        """
        totals = self.summary(conversation_prefix=conversation_id)
        out: Dict[str, Any] = {"conversation_id": conversation_id,
                               "totals": totals}
        for key, dimension in (("by_agent", "agent_name"),
                               ("by_channel", "channel"),
                               ("by_model", "model")):
            out[key] = self.top(dimension=dimension, limit=50,
                                order_by="cost_usd",
                                conversation_prefix=conversation_id)
        out["recent"] = [
            {k: r[k] for k in ("ts", "agent_name", "llm_service", "model",
                               "channel", "tokens_in", "tokens_out",
                               "cache_read", "cache_write", "cost_usd")}
            for r in self.export_rows(conversation_prefix=conversation_id,
                                      limit=recent_limit)]
        return out

    def total_cost(self) -> float:
        row = self._query("SELECT SUM(cost_usd) c FROM usage_events", ())[0]
        return row["c"] or 0.0

    def user_usage(self, user_id: str) -> Dict[str, Any]:
        """Per-user rollup in the shape the /usage and /cost surfaces use:
        totals + daily + per-model + per-agent::llm_service aggregates."""
        out: Dict[str, Any] = {"total_in": 0, "total_out": 0,
                               "total_cache_read": 0, "total_cache_write": 0,
                               "daily": {}, "models": {}, "agents": {}}
        totals = self.summary(user_id=user_id)
        out["total_in"] = totals["tokens_in"]
        out["total_out"] = totals["tokens_out"]
        out["total_cache_read"] = totals["cache_read"]
        out["total_cache_write"] = totals["cache_write"]
        for r in self._query(
                "SELECT day, SUM(tokens_in) i, SUM(tokens_out) o, "
                "SUM(cache_read) cr, SUM(cache_write) cw FROM usage_events "
                "WHERE user_id = ? GROUP BY day", (user_id,)):
            out["daily"][r["day"]] = {
                "in": r["i"] or 0, "out": r["o"] or 0,
                "cache_read": r["cr"] or 0, "cache_write": r["cw"] or 0}
        for r in self._query(
                "SELECT model, SUM(tokens_in) i, SUM(tokens_out) o, "
                "SUM(cache_read) cr, SUM(cache_write) cw FROM usage_events "
                "WHERE user_id = ? AND model != '' GROUP BY model",
                (user_id,)):
            out["models"][r["model"]] = {
                "in": r["i"] or 0, "out": r["o"] or 0,
                "cache_read": r["cr"] or 0, "cache_write": r["cw"] or 0}
        for r in self._query(
                "SELECT agent_name, llm_service, SUM(tokens_in) i, "
                "SUM(tokens_out) o, SUM(cache_read) cr, "
                "SUM(cache_write) cw, COUNT(*) n, SUM(cost_usd) c, "
                "SUM(virtual_cost_usd) vc "
                "FROM usage_events WHERE user_id = ? AND agent_name != '' "
                "GROUP BY agent_name, llm_service", (user_id,)):
            key = r["agent_name"] + "::" + r["llm_service"]
            out["agents"][key] = {
                "agent": r["agent_name"], "llm_service": r["llm_service"],
                "in": r["i"] or 0, "out": r["o"] or 0,
                "cache_read": r["cr"] or 0, "cache_write": r["cw"] or 0,
                "calls": r["n"] or 0, "cost": r["c"] or 0.0,
                "virtual_cost": r["vc"] or 0.0}
        return out

    def all_usage(self) -> Dict[str, Dict[str, Any]]:
        """Per-user rollups for every user (admin surface)."""
        users = [r["user_id"] for r in self._query(
            "SELECT DISTINCT user_id FROM usage_events", ())]
        return {u: self.user_usage(u) for u in users}

    # -- legacy import -----------------------------------------------------

    def _migrate_legacy_json(self):
        """One-shot import of the pre-ledger token_usage.json aggregates."""
        legacy = Path(str(_paths.TOKEN_USAGE_FILE))
        if not legacy.exists():
            return
        try:
            import json
            data = json.loads(legacy.read_text(encoding="utf-8"))
            n = 0
            for user_id, entry in (data or {}).items():
                agents = (entry or {}).get("agents", {}) or {}
                for stats in agents.values():
                    self.record(
                        user_id=user_id, channel="migrated",
                        tokens_in=int(stats.get("in", 0) or 0),
                        tokens_out=int(stats.get("out", 0) or 0),
                        cache_read=int(stats.get("cache_read", 0) or 0),
                        cache_write=int(stats.get("cache_write", 0) or 0),
                        agent_name=str(stats.get("agent", "") or "unknown"),
                        llm_service=str(stats.get("llm_service", "")
                                        or "unknown"))
                    n += 1
            legacy.rename(legacy.with_suffix(".json.migrated"))
            logger.info("[usage] migrated %d legacy aggregate(s) from %s",
                        n, legacy)
        except Exception:
            logger.warning("[usage] legacy token_usage.json migration "
                           "failed — file left in place", exc_info=True)
