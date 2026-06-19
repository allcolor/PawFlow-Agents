"""Shared sentinels + per-call state for the Claude Code provider.

Extracted so the facade and the streaming sub-mixins can both import these
without a circular dependency (<=800-line split).
"""


# Sentinel pushed onto the per-session event queue when the reader daemon
# exits (proc stdout EOF). Module-level so the SAME object identity holds
# across turns for a reused session.
_CC_READER_EOF = object()


class _CC401Retry(Exception):
    """Internal signal: OAuth 401 mid-stream, credentials refreshed, retry the call."""


class _CCStreamState:
    """Per-call mutable state for the Claude Code streaming turn, shared across
    the streaming sub-mixin methods. A plain attribute bag by design."""
