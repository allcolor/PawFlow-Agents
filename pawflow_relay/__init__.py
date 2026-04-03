"""PawFlow Relay — orchestrator + standalone entry point.

Usage:
    Standalone: python -m pawflow_relay --dir /path --server https://...
    PawCode:    from pawflow_relay import RelayThread
    VS Code:    spawn `python -m pawflow_relay --dir ... --token ...`
"""

from pawflow_relay.thread import RelayThread  # noqa: F401
from pawflow_relay.utils import generate_relay_id, find_free_port  # noqa: F401
