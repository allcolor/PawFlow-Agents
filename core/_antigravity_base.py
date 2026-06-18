"""Antigravity CLI interactive sessions.

This pool starts the real ``agy`` CLI in tmux with Gemini OAuth/MCP config and
a transparent observer proxy for ``daily-cloudcode-pa.googleapis.com``. The
same tmux/proxy foundation is used by both the diagnostics observer action and
the ``antigravity-interactive`` LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
import logging
import threading
import time


if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)
# Shared dataclass + constant for the antigravity observer pool split.


ANTIGRAVITY_BACKEND_HOST = "daily-cloudcode-pa.googleapis.com"


@dataclass
class AntigravityObserverSession:
    key: tuple[str, str, str, str]
    name: str
    workdir: str
    container_workdir: str
    log_path: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    initial_context_loaded: bool = False
    last_error: str = ""
    emitted_tool_use_ids: set = field(default_factory=set)
    emitted_tool_result_ids: set = field(default_factory=set)
    manual_live_tool_calls: dict = field(default_factory=dict)
    manual_ingest_enabled: bool = False
    manual_ingest_suspended: bool = False
    manual_ingest_offset: int = 0
    manual_ingest_stop: threading.Event = field(default_factory=threading.Event)
    manual_ingest_thread: Optional[threading.Thread] = None
    manual_ingest_seen_requests: set = field(default_factory=set)
    injected_prompt_hashes: dict = field(default_factory=dict)
    pending_injected_prompt_ignores: list[float] = field(default_factory=list)
    active_submit_lock: threading.Lock = field(default_factory=threading.Lock)
    active_submit_hash: str = ""
    active_submit_at: float = 0.0

    @property
    def agent_name(self) -> str:
        return self.key[2]

    @property
    def service_id(self) -> str:
        return self.key[3]
