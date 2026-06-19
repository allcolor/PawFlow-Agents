"""Pure helpers shared by the agent-compaction mixins.

Extracted from tasks/ai/agent_compaction.py for the <=800-line rule. These are
stateless module functions (no ``self``); the compaction mixins call them as
module globals.
"""
import logging
from typing import Any, Dict, List

from core.llm_client import LLMMessage

logger = logging.getLogger(__name__)


def _is_synthetic_compact_msg(m: LLMMessage) -> bool:
    source = getattr(m, 'source', None) or {}
    source_type = source.get("type") if isinstance(source, dict) else ""
    return source_type in {"context", "private_compaction"}

def _collect_recent_files(msgs: List[LLMMessage], limit: int = 5) -> List[Dict]:
    """Walk msgs, pull the last `limit` file tool_call args.

    Each entry: {path, offset?, limit?, service?}. Keeps the most
    recent read/edit/write per path (dedup by path). Used to
    inject a "you were editing X" hint in the compact output so
    the agent doesn't lose track of the files it was working on.
    Inspired by CC's postCompact file-restore attachment.
    """
    out: Dict[str, Dict] = {}  # path -> info (overwritten = most recent)
    _FILE_TOOLS = {"read", "edit", "write"}
    for m in msgs:
        if m.role != "assistant" or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            _name = (getattr(tc, "name", "") or "").lower()
            if _name not in _FILE_TOOLS:
                continue
            _args = tc.arguments if isinstance(tc.arguments, dict) else {}
            _path = _args.get("path") or _args.get("file_path") or ""
            if not _path:
                continue
            entry = {"path": _path, "tool": _name}
            if _name == "read":
                _off = _args.get("offset")
                _lim = _args.get("limit")
                if _off:
                    entry["offset"] = int(_off)
                if _lim:
                    entry["limit"] = int(_lim)
            _svc = _args.get("source") or _args.get("filesystem") or ""
            if _svc:
                entry["service"] = _svc
            out[_path] = entry  # overwrite → keep latest
    return list(out.values())[-limit:]

def _format_files_note(files: List[Dict]) -> str:
    if not files:
        return ""
    lines = [
        "\n\n[Files you were working with (state lost after compact). "
        "Re-read them now with the exact same parameters to restore "
        "your working view before continuing:]"
    ]
    for fi in files:
        _p = fi.get("path", "")
        _call = f"  - read(path={_p!r}"
        params = []
        if "offset" in fi:
            params.append(f"offset={fi['offset']}")
        if "limit" in fi:
            params.append(f"limit={fi['limit']}")
        if params:
            _call += ", " + ", ".join(params)
        if "service" in fi:
            _call += f", source={fi['service']!r}"
        _call += ")"
        lines.append(_call)
    return "\n".join(lines)

def _clone_with_content(m: LLMMessage, content: Any) -> LLMMessage:
    return LLMMessage(
        role=m.role,
        content=content,
        tool_call_id=getattr(m, 'tool_call_id', None),
        tool_calls=getattr(m, 'tool_calls', None),
        timestamp=getattr(m, 'timestamp', 0.0),
        seq=getattr(m, 'seq', 0),
        source=getattr(m, 'source', None),
        conversation_id=getattr(m, 'conversation_id', None),
    )

def _is_independent_summary(m: LLMMessage) -> bool:
    source = getattr(m, 'source', None) or {}
    source_type = source.get("type") if isinstance(source, dict) else ""
    return source_type == "independent_compaction"
