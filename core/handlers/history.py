"""Read conversation history tool — lets the LLM pull messages outside ctx."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)


_ROLE_FILTERS = {"user", "assistant", "tool", "thinking"}

# Cap every action that returns a list so one call can't shove 40k
# messages (a super-bucket range) into the LLM's context.
_MAX_LIMIT = 100
_DEFAULT_LIMIT = 50


def _paginate(items, offset, limit):
    """Slice items[offset:offset+limit] with offset>=0 and limit<=_MAX_LIMIT.

    Returns (slice, effective_offset, effective_limit, total). The
    caller adds a 'next page' hint to the rendered output whenever
    effective_offset + len(slice) < total.
    """
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        raw_limit = int(limit) if limit is not None else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        raw_limit = _DEFAULT_LIMIT
    limit = max(1, min(raw_limit, _MAX_LIMIT))
    total = len(items)
    return items[offset:offset + limit], offset, limit, total


def _page_footer(offset: int, limit: int, returned: int, total: int,
                 action: str) -> str:
    """Render the Showing X..Y of Z banner + pagination hint."""
    if returned == 0:
        return ""
    last = offset + returned - 1
    foot = f"\nShowing {offset}..{last} of {total} (limit={limit})."
    if offset + returned < total:
        foot += (f" More available — repeat the same "
                 f"read_history(action=\"{action}\", ...) call "
                 f"with offset={offset + limit}.")
    return foot


def _msg_ts(m) -> float:
    if isinstance(m, dict):
        return float(m.get("ts") or m.get("timestamp") or 0.0)
    return float(getattr(m, "timestamp", 0.0) or 0.0)


def _msg_seq(m) -> int:
    if isinstance(m, dict):
        return int(m.get("seq") or 0)
    return int(getattr(m, "seq", 0) or 0)


def _msg_id(m) -> str:
    if isinstance(m, dict):
        return m.get("msg_id", "") or ""
    return getattr(m, "msg_id", "") or ""


def _msg_role(m) -> str:
    if isinstance(m, dict):
        return m.get("role", "") or ""
    return getattr(m, "role", "") or ""


def _msg_thinking(m) -> str:
    if isinstance(m, dict):
        return m.get("thinking", "") or ""
    return getattr(m, "thinking", "") or ""


def _msg_agents_involved(m) -> set:
    """Return the set of agent names this message involves.

    Covers both endpoints of a turn:
      * source.name        → speaker (assistant / agent-to-agent delegate)
      * source.target_agent → addressee (user → {agent}, agent → agent reply)
      * source.from / source.to → agent_delegate private routing

    Used by agent_filter so `user → claude between dates` works naturally
    alongside `assistant(claude) between dates` — both return messages
    whose agent_filter is 'claude'.
    """
    src = m.get("source") if isinstance(m, dict) else getattr(m, "source", None)
    if not isinstance(src, dict):
        return set()
    out = set()
    for key in ("name", "target_agent", "from", "to"):
        v = src.get(key)
        if isinstance(v, str) and v:
            out.add(v)
    return out


def _parse_date(s: str) -> Optional[float]:
    """Parse an ISO 8601 date/datetime into an epoch float. None on invalid."""
    if not s:
        return None
    try:
        # Accept either "YYYY-MM-DD" or full ISO datetime.
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s, "%Y-%m-%d").timestamp()
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _scope_label(role_filter: str, agent_filter: str) -> str:
    """One-shot label describing active filters for log/header lines."""
    parts = []
    if role_filter:
        parts.append(f"role={role_filter}")
    if agent_filter:
        parts.append(f"agent={agent_filter}")
    return ", ".join(parts)


def _apply_filters(msgs: List, role_filter: str, agent_filter: str) -> List:
    """Filter a message list by pseudo-role and/or agent name. AND-combined.

    role_filter ∈ {user, assistant, tool, thinking}. "thinking" selects
    assistant messages that carry a non-empty thinking field — the
    output renders the thinking text instead of content.

    agent_filter matches any agent involved in the message (speaker OR
    addressee, via _msg_agents_involved). Combining the two lets a
    caller ask e.g. "all user messages addressed to claude between
    date A and date B" (role_filter=user + agent_filter=claude + a
    date scope) — all three filters AND together.
    """
    out = msgs
    if role_filter == "thinking":
        out = [m for m in out
               if _msg_role(m) == "assistant" and _msg_thinking(m)]
    elif role_filter:
        out = [m for m in out if _msg_role(m) == role_filter]
    if agent_filter:
        out = [m for m in out if agent_filter in _msg_agents_involved(m)]
    return out


class ReadHistoryHandler(ToolHandler):
    """Read conversation history on demand.

    The LLM's context is compacted after each response. This tool lets it
    access the full uncompacted history when needed — like reading a file
    instead of keeping everything in memory.
    """

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "read_history"

    @property
    def description(self) -> str:
        return (
            "Read conversation history — access messages outside the live "
            "context (compacted-away messages, earlier phases, etc.).\n"
            "\n"
            "Actions:\n"
            "  recent         — last N messages (tail).\n"
            "  search         — full-text match (case-insensitive).\n"
            "  read           — a single message by numeric index.\n"
            "  count          — total message count.\n"
            "  range          — inclusive slice [from_msg_id, to_msg_id]. "
            "The UUIDs quoted in compact/bucket summary headers "
            "(\"from_msg_id=...\", \"to_msg_id=...\") are the inputs here.\n"
            "  range_by_seq   — inclusive slice [from_seq, to_seq].\n"
            "  range_by_date  — inclusive slice [from_date, to_date] "
            "(ISO 8601 'YYYY-MM-DD' or full datetime).\n"
            "  around         — N messages before/after an anchor. Anchor = "
            "exactly one of from_msg_id / from_seq / from_date. "
            "limit>0 walks forward, limit<0 walks backward; "
            "the anchor itself is included.\n"
            "\n"
            "Optional filters (AND-combined with each other AND with any "
            "action's range/scope):\n"
            "  role_filter    — user / assistant / tool / thinking. "
            "'thinking' selects assistant turns whose reasoning field is "
            "non-empty and renders the thinking text instead of content.\n"
            "  agent_filter   — matches any agent involved in the message "
            "(speaker via source.name, addressee via source.target_agent, "
            "or delegate endpoints source.from / source.to). "
            "Examples: role_filter=user + agent_filter=claude + "
            "from_date/to_date → every user message addressed to claude "
            "between the two dates; role_filter=assistant + "
            "agent_filter=qwen → every qwen reply.\n"
            "\n"
            "Tip: when compact/bucket summaries quote a UUID range, call "
            "read_history(action=\"range\", from_msg_id=..., to_msg_id=...) "
            "to retrieve the raw messages behind that phase.\n"
            "\n"
            "Pagination: every call returns at most 100 messages. When the "
            "matched set is larger, the response ends with a line telling "
            "you the next offset to pass back with the SAME action + "
            "filters. Default limit is 50. Super-bucket ranges can cover "
            "tens of thousands of messages — always read by pages, never "
            "try to fetch a whole SB in one shot."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "recent", "search", "read", "count",
                        "range", "range_by_seq", "range_by_date", "around",
                    ],
                    "description": (
                        "recent: last N messages (default). "
                        "search: find messages matching a query. "
                        "read: read a specific message by index. "
                        "count: total message count. "
                        "range: inclusive slice between two msg_ids. "
                        "range_by_seq: inclusive slice between two seq values. "
                        "range_by_date: inclusive slice between two ISO dates. "
                        "around: N messages before/after an anchor point "
                        "(set exactly one of from_msg_id / from_seq / from_date; "
                        "limit positive = forward, negative = backward, "
                        "anchor itself is included)."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Pagination cursor. For 'recent' it skips the N most "
                        "recent filtered messages; for every other list "
                        "action (range, range_by_seq, range_by_date, search) "
                        "it skips the first N results of the filtered match "
                        "set. Default 0. Pass the value suggested at the end "
                        "of the previous response to step to the next page."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max messages to return. Default 50, hard-capped at "
                        "100. For action=around the sign also chooses "
                        "direction: +N forward, -N backward (anchor "
                        "inclusive); |limit| is clamped to 100."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action). Case-insensitive text match.",
                },
                "index": {
                    "type": "integer",
                    "description": "Message index to read (for read action, 0-based).",
                },
                "from_msg_id": {
                    "type": "string",
                    "description": "Start msg_id. Used by range (inclusive start) and around (anchor).",
                },
                "to_msg_id": {
                    "type": "string",
                    "description": "End msg_id for range (inclusive).",
                },
                "from_seq": {
                    "type": "integer",
                    "description": "Start seq. Used by range_by_seq (inclusive start) and around (anchor).",
                },
                "to_seq": {
                    "type": "integer",
                    "description": "End seq for range_by_seq (inclusive).",
                },
                "from_date": {
                    "type": "string",
                    "description": (
                        "Start date (ISO 8601: 'YYYY-MM-DD' or full datetime). "
                        "Used by range_by_date (inclusive) and around (anchor)."
                    ),
                },
                "to_date": {
                    "type": "string",
                    "description": "End date (ISO 8601) for range_by_date (inclusive).",
                },
                "role_filter": {
                    "type": "string",
                    "enum": ["user", "assistant", "tool", "thinking"],
                    "description": (
                        "Keep only messages of this kind. 'thinking' selects "
                        "assistant turns with non-empty reasoning and renders "
                        "the thinking field instead of content."
                    ),
                },
                "agent_filter": {
                    "type": "string",
                    "description": (
                        "Keep only messages involving this agent — matches "
                        "speaker (source.name) OR addressee (source.target_agent) "
                        "OR delegate endpoints (source.from / source.to). "
                        "Combine with role_filter to get precise slices: "
                        "role_filter=user + agent_filter=claude gives every "
                        "user message addressed to claude; "
                        "role_filter=assistant + agent_filter=qwen gives every "
                        "qwen reply. Date / seq / msg_id scopes compose too."
                    ),
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    # ── Dispatch ───────────────────────────────────────────────────────

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._conversation_id:
            return "Error: no conversation context"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        action = arguments.get("action", "recent")
        role_filter = (arguments.get("role_filter") or "").strip()
        agent_filter = (arguments.get("agent_filter") or "").strip()
        if role_filter and role_filter not in _ROLE_FILTERS:
            return (f"Error: role_filter must be one of "
                    f"{sorted(_ROLE_FILTERS)}")

        if action == "count":
            return self._do_count(store, role_filter, agent_filter)
        if action == "read":
            return self._do_read(store, arguments, role_filter, agent_filter)
        if action == "search":
            return self._do_search(store, arguments, role_filter, agent_filter)
        if action == "range":
            return self._do_range(store, arguments, role_filter, agent_filter)
        if action == "range_by_seq":
            return self._do_range_by_seq(store, arguments, role_filter, agent_filter)
        if action == "range_by_date":
            return self._do_range_by_date(store, arguments, role_filter, agent_filter)
        if action == "around":
            return self._do_around(store, arguments, role_filter, agent_filter)
        return self._do_recent(store, arguments, role_filter, agent_filter)

    # ── Loader helper ─────────────────────────────────────────────────

    def _load_all(self, store) -> Optional[List]:
        return store.load(self._conversation_id, user_id=self._user_id)

    def _index_by_id(self, all_msgs: List) -> Dict[str, int]:
        return {_msg_id(m): i for i, m in enumerate(all_msgs) if _msg_id(m)}

    # ── Actions ───────────────────────────────────────────────────────

    def _do_count(self, store, role_filter: str, agent_filter: str) -> str:
        if not role_filter and not agent_filter:
            return f"Total messages in history: {store.message_count(self._conversation_id)}"
        msgs = self._load_all(store) or []
        filtered = _apply_filters(msgs, role_filter, agent_filter)
        scope = _scope_label(role_filter, agent_filter)
        return (f"Total messages in history: {len(msgs)} "
                f"({scope}: {len(filtered)})")

    def _do_read(self, store, arguments,
                 role_filter: str, agent_filter: str) -> str:
        idx = int(arguments.get("index", 0))
        all_msgs = self._load_all(store)
        if not all_msgs:
            return "No history found"
        if idx < 0 or idx >= len(all_msgs):
            return f"Error: index {idx} out of range (0-{len(all_msgs) - 1})"
        msg = all_msgs[idx]
        if (role_filter or agent_filter) and not _apply_filters(
                [msg], role_filter, agent_filter):
            return (f"Message at index {idx} does not match "
                    f"{_scope_label(role_filter, agent_filter)}")
        return self._format_message(msg, idx, role_filter=role_filter)

    def _do_search(self, store, arguments,
                   role_filter: str, agent_filter: str) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: search requires a query"
        all_msgs = self._load_all(store)
        if not all_msgs:
            return "No history found"
        # Collect every match first, then paginate — the offset/limit
        # pair lets the LLM page through large hit sets instead of being
        # silently cut off at 20 like the old impl did.
        hits = []  # list of (index, msg)
        query_lower = query.lower()
        for i, msg in enumerate(all_msgs):
            if (role_filter or agent_filter) and not _apply_filters(
                    [msg], role_filter, agent_filter):
                continue
            content = self._render_body(msg, role_filter)
            if query_lower in content.lower():
                hits.append((i, msg))
        if not hits:
            scope = _scope_label(role_filter, agent_filter)
            tag = f" ({scope})" if scope else ""
            return f"No messages matching '{query}'{tag}"
        page, offset, limit, total = _paginate(
            hits, arguments.get("offset"), arguments.get("limit"))
        lines = [
            self._format_message(m, i, preview=True, role_filter=role_filter)
            for (i, m) in page
        ]
        scope = _scope_label(role_filter, agent_filter)
        tag = f" [{scope}]" if scope else ""
        footer = _page_footer(offset, limit, len(page), total, "search")
        return (f"Found {total} match(es) for '{query}'{tag}:\n\n"
                + "\n\n".join(lines)
                + footer)

    def _do_range(self, store, arguments,
                  role_filter: str, agent_filter: str) -> str:
        from_id = arguments.get("from_msg_id", "")
        to_id = arguments.get("to_msg_id", "")
        if not from_id or not to_id:
            return "Error: range requires from_msg_id and to_msg_id"
        msgs = store.load_range_by_msg_id(
            self._conversation_id, from_id, to_id, user_id=self._user_id)
        if msgs is None:
            return "Error: conversation not found"
        return self._render_slice(
            store, msgs, f"Range {from_id}..{to_id}",
            role_filter, agent_filter,
            action="range",
            offset_arg=arguments.get("offset"),
            limit_arg=arguments.get("limit"))

    def _do_range_by_seq(self, store, arguments,
                         role_filter: str, agent_filter: str) -> str:
        try:
            from_seq = int(arguments.get("from_seq", 0))
            to_seq = int(arguments.get("to_seq", 0))
        except (TypeError, ValueError):
            return "Error: from_seq and to_seq must be integers"
        if from_seq <= 0 or to_seq <= 0 or to_seq < from_seq:
            return "Error: range_by_seq requires from_seq <= to_seq, both > 0"
        all_msgs = self._load_all(store) or []
        msgs = [m for m in all_msgs
                if from_seq <= _msg_seq(m) <= to_seq]
        return self._render_slice(
            store, msgs, f"Seq range {from_seq}..{to_seq}",
            role_filter, agent_filter,
            action="range_by_seq",
            offset_arg=arguments.get("offset"),
            limit_arg=arguments.get("limit"),
            all_msgs=all_msgs)

    def _do_range_by_date(self, store, arguments,
                          role_filter: str, agent_filter: str) -> str:
        from_ts = _parse_date(arguments.get("from_date", ""))
        to_ts = _parse_date(arguments.get("to_date", ""))
        if from_ts is None or to_ts is None or to_ts < from_ts:
            return ("Error: range_by_date requires from_date <= to_date "
                    "(ISO 8601 'YYYY-MM-DD' or full datetime)")
        all_msgs = self._load_all(store) or []
        msgs = [m for m in all_msgs if from_ts <= _msg_ts(m) <= to_ts]
        label = (f"Date range {arguments.get('from_date', '')}.."
                 f"{arguments.get('to_date', '')}")
        return self._render_slice(
            store, msgs, label, role_filter, agent_filter,
            action="range_by_date",
            offset_arg=arguments.get("offset"),
            limit_arg=arguments.get("limit"),
            all_msgs=all_msgs)

    def _do_around(self, store, arguments,
                   role_filter: str, agent_filter: str) -> str:
        """Anchor + signed limit. Anchor = msg_id | seq | date (exactly one)."""
        try:
            limit = int(arguments.get("limit", _DEFAULT_LIMIT))
        except (TypeError, ValueError):
            return "Error: limit must be an integer (positive or negative)"
        if limit == 0:
            return "Error: limit must be non-zero (sign selects direction)"
        # Cap magnitude — a single call can't dump more than _MAX_LIMIT msgs.
        if abs(limit) > _MAX_LIMIT:
            limit = _MAX_LIMIT if limit > 0 else -_MAX_LIMIT
        from_msg_id = arguments.get("from_msg_id", "")
        from_seq_raw = arguments.get("from_seq")
        from_date = arguments.get("from_date", "")
        anchors = [bool(from_msg_id), from_seq_raw is not None, bool(from_date)]
        if sum(anchors) != 1:
            return ("Error: around requires exactly one of "
                    "from_msg_id / from_seq / from_date")
        all_msgs = self._load_all(store) or []
        if not all_msgs:
            return "No history found"
        anchor_idx = -1
        if from_msg_id:
            for i, m in enumerate(all_msgs):
                if _msg_id(m) == from_msg_id:
                    anchor_idx = i
                    break
            if anchor_idx < 0:
                return f"Error: msg_id {from_msg_id} not found"
        elif from_seq_raw is not None:
            try:
                fs = int(from_seq_raw)
            except (TypeError, ValueError):
                return "Error: from_seq must be an integer"
            for i, m in enumerate(all_msgs):
                if _msg_seq(m) == fs:
                    anchor_idx = i
                    break
            if anchor_idx < 0:
                return f"Error: no message with seq={fs}"
        else:
            fts = _parse_date(from_date)
            if fts is None:
                return "Error: from_date must be ISO 8601"
            for i, m in enumerate(all_msgs):
                if _msg_ts(m) >= fts:
                    anchor_idx = i
                    break
            if anchor_idx < 0:
                return f"Error: no message at or after {from_date}"

        if limit > 0:
            window = all_msgs[anchor_idx:anchor_idx + limit]
        else:
            lo = max(0, anchor_idx + limit + 1)
            window = all_msgs[lo:anchor_idx + 1]
        direction = "forward" if limit > 0 else "backward"
        label = f"Around anchor [#{anchor_idx}] ({direction} {abs(limit)})"
        # around is anchor-relative: its "limit" already encodes the
        # window size + direction, so we don't paginate further — just
        # render everything the window holds. _render_slice still caps
        # at _MAX_LIMIT via its paginator, which matches the abs(limit)
        # we already clamped above.
        return self._render_slice(
            store, window, label, role_filter, agent_filter,
            action="around",
            offset_arg=0,
            limit_arg=_MAX_LIMIT,
            all_msgs=all_msgs)

    def _do_recent(self, store, arguments,
                   role_filter: str, agent_filter: str) -> str:
        # Cap and normalise — same _MAX_LIMIT ceiling as the other
        # actions so a single call can never dump more than 100 msgs.
        try:
            limit = int(arguments.get("limit", _DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _MAX_LIMIT))
        try:
            offset = max(0, int(arguments.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        if role_filter or agent_filter:
            all_msgs = self._load_all(store) or []
            filtered = _apply_filters(all_msgs, role_filter, agent_filter)
            total = len(filtered)
            end = total - offset
            start = max(0, end - limit)
            window = filtered[start:end]
            idx_by_id = self._index_by_id(all_msgs)
            lines = [
                self._format_message(
                    m, idx_by_id.get(_msg_id(m), -1),
                    role_filter=role_filter)
                for m in window
            ]
            scope = _scope_label(role_filter, agent_filter)
            header = (f"Messages ({scope}) {start}-"
                      f"{start + len(window) - 1} of {total}")
            if start > 0:
                header += (f". More older — repeat with "
                           f"offset={offset + limit}.")
            return header + "\n\n" + "\n\n".join(lines)
        # Fast path: use the store's paginated tail reader (no filters).
        page = store.load_page(
            self._conversation_id, limit=limit, offset=offset,
            user_id=self._user_id)
        if not page:
            return "No history found"
        msgs = page.get("messages", [])
        total = page.get("total_count", 0)
        start_idx = total - offset - len(msgs)
        lines = [self._format_message(m, start_idx + i)
                 for i, m in enumerate(msgs)]
        header = f"Messages {start_idx}-{start_idx + len(msgs) - 1} of {total}"
        if page.get("has_more"):
            header += (f". More older — repeat with "
                       f"offset={offset + limit}.")
        return header + "\n\n" + "\n\n".join(lines)

    # ── Rendering ─────────────────────────────────────────────────────

    def _render_slice(self, store, msgs: List, label: str,
                      role_filter: str, agent_filter: str,
                      action: str, offset_arg, limit_arg,
                      all_msgs: Optional[List] = None) -> str:
        if msgs is None:
            return "Error: conversation not found"
        if not msgs:
            scope = _scope_label(role_filter, agent_filter)
            tag = f" ({scope})" if scope else ""
            return f"No messages found for {label}{tag}"
        all_msgs = all_msgs if all_msgs is not None else (self._load_all(store) or [])
        idx_by_id = self._index_by_id(all_msgs)
        if role_filter or agent_filter:
            msgs = _apply_filters(msgs, role_filter, agent_filter)
            if not msgs:
                return (f"No messages matching "
                        f"{_scope_label(role_filter, agent_filter)} "
                        f"inside {label}")
        page, offset, limit, total = _paginate(msgs, offset_arg, limit_arg)
        lines = [
            self._format_message(
                m, idx_by_id.get(_msg_id(m), -1), role_filter=role_filter)
            for m in page
        ]
        scope = _scope_label(role_filter, agent_filter)
        tag = f" [{scope}]" if scope else ""
        footer = _page_footer(offset, limit, len(page), total, action)
        return (f"{label}{tag} ({total} messages total):\n\n"
                + "\n\n".join(lines)
                + footer)

    @staticmethod
    def _get_content(msg) -> str:
        if isinstance(msg, dict):
            c = msg.get("content", "")
        else:
            c = getattr(msg, "content", "")
        return c if isinstance(c, str) else str(c)

    @staticmethod
    def _render_body(msg, role_filter: str) -> str:
        """Pick which field to display — thinking vs. content."""
        if role_filter == "thinking":
            return _msg_thinking(msg) or ReadHistoryHandler._get_content(msg)
        return ReadHistoryHandler._get_content(msg)

    @staticmethod
    def _format_message(msg, index: int, preview: bool = False,
                        role_filter: str = "") -> str:
        role = _msg_role(msg) or "?"
        source = msg.get("source", {}) if isinstance(msg, dict) else getattr(msg, "source", {})
        agent = ""
        if isinstance(source, dict):
            agent = source.get("name", "")

        label = role
        if role_filter == "thinking" and role == "assistant":
            label = "assistant.thinking"
        header = f"[#{index}] {label}"
        if agent:
            header += f" ({agent})"

        body = ReadHistoryHandler._render_body(msg, role_filter)
        if preview:
            return f"{header}: {body[:200]}{'...' if len(body) > 200 else ''}"
        if len(body) > 2000:
            return f"{header}:\n{body[:2000]}\n... ({len(body)} chars total)"
        return f"{header}:\n{body}"
