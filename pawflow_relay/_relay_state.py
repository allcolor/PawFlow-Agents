"""Per-connection mutable state for a WS relay worker.

Lifted out of worker.py so the action handlers (and their tests) can import
the state shape without pulling in the whole worker module. A fresh instance
is created per ``_ws_connect`` call, so nothing leaks across connections; the
defaults mirror the old lazy-init values exactly (None for handles/ports, a
fresh dict for each WS-session map).
"""
import subprocess  # nosec B404 - used only for Optional[subprocess.Popen] annotations
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RelayWorkerState:
    """Long-lived process/session handles read and mutated by the handlers."""
    # code-server
    code_server_proc: Optional[subprocess.Popen] = None
    code_server_port: Optional[int] = None
    code_server_base_path: str = ""
    cs_ws_sessions: Dict[str, dict] = field(default_factory=dict)
    # desktop (containerized)
    desktop_procs: Optional[List[subprocess.Popen]] = None
    desktop_essential_procs: Optional[List[subprocess.Popen]] = None
    desktop_vnc_port: Optional[int] = None
    desktop_novnc_port: Optional[int] = None
    desktop_display: Optional[str] = None
    desktop_watchdog_stop: Optional[threading.Event] = None
    desktop_watchdog_thread: Optional[threading.Thread] = None
    desktop_ws_sessions: Dict[str, dict] = field(default_factory=dict)
    # local desktop (host screen)
    local_desktop_procs: Optional[List[subprocess.Popen]] = None
    local_desktop_vnc_port: Optional[int] = None
    local_desktop_novnc_port: Optional[int] = None
