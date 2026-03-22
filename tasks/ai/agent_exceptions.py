"""Agent exceptions — shared across agent loop mixins."""


class AgentCancelled(Exception):
    """Raised when agent generation is cancelled by user."""
    pass


class _InterruptComplete(Exception):
    """Internal: raised when interrupt-synthesis is done to break out of the loop."""
    pass
