"""PawFlow Relay — standalone client and low-level relay thread.

Usage:
    Managed client: pawflow-relay server add ...; pawflow-relay start ...
    Legacy direct:  python -m pawflow_relay --dir /path --server https://...

PawCode, VS Code, and API frontends are PawFlow clients only; they do not own
relay lifecycle.
"""

from pawflow_relay.thread import RelayThread  # noqa: F401
from pawflow_relay.utils import generate_relay_id, find_free_port  # noqa: F401
