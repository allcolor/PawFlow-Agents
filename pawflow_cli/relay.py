"""Relay — re-exported from pawflow_relay package.

PawCode CLI imports from here; the actual implementation lives in
pawflow_relay/ so it can also be used standalone and by VS Code.
"""

from pawflow_relay.thread import RelayThread  # noqa: F401
from pawflow_relay.utils import (  # noqa: F401
    generate_relay_id,
    find_free_port,
    api_call as _api_call_impl,
)

# Legacy compatibility: _api_call with token holder pattern
# PawCode's app.py doesn't use _api_call directly (it uses AgentAPIClient),
# but the auth module does for check_session.
_token_holder = {"token": "", "on_refresh": None}  # nosec B105


def _api_call(server_url, method, path, body=None, session_token="", gateway_cookie=""):  # nosec B107
    """Legacy wrapper that routes through pawflow_relay.utils.api_call."""
    return _api_call_impl(
        server_url, method, path, body=body,
        session_token=session_token,
        gateway_cookie=gateway_cookie,
        on_token_refresh=_token_holder.get("on_refresh"),
    )
