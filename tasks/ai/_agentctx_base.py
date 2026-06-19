"""Shared base for the agent_context split: per-call state bag + agent-md lookup."""
import logging
import time

logger = logging.getLogger(__name__)

_agent_md_cache = {}  # (agent_name, user_id, conversation_id) -> (result, timestamp)
_AGENT_MD_TTL = 30  # seconds


class _PACState:
    """Per-call mutable state bag for _prepare_agent_context (split for <=800 lines)."""
    pass

def _find_agent_md(agent_name, user_id, conversation_id=""):
    """Find {agent_name}.md (case-insensitive) in the relay filesystem root."""
    cache_key = (agent_name, user_id, conversation_id)
    cached = _agent_md_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _AGENT_MD_TTL:
        return cached[0]
    try:
        from core.handlers._fs_base import find_fs_service
        svc = find_fs_service(user_id, conversation_id=conversation_id)
        if not svc:
            _agent_md_cache[cache_key] = (None, time.time())
            return None
        entries = svc.list_dir(".")
        target = f"{agent_name}.md".lower()
        for e in entries:
            if e.name.lower() == target:
                data = svc.read_file(e.name)
                result = (e.name, data.decode("utf-8"))
                _agent_md_cache[cache_key] = (result, time.time())
                return result
        _agent_md_cache[cache_key] = (None, time.time())
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return None
