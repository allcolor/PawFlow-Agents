"""Protection rules for PawFlow's mandatory system flow."""

REQUIRED_SYSTEM_FLOW_INSTANCE_ID = "pawflow-agent"


class RequiredSystemFlowError(RuntimeError):
    """Raised when ordinary flow controls target the mandatory runtime."""


def ensure_required_system_flow_action_allowed(
    instance_id: str,
    action: str,
    *,
    allow_required: bool = False,
) -> None:
    """Reject user-facing lifecycle mutations of the mandatory runtime."""
    if (str(instance_id or "") == REQUIRED_SYSTEM_FLOW_INSTANCE_ID
            and not allow_required):
        raise RequiredSystemFlowError(
            f"{REQUIRED_SYSTEM_FLOW_INSTANCE_ID} is a required system flow "
            f"and cannot be {action}; restart PawFlow or reset the installer "
            "to recover it"
        )
