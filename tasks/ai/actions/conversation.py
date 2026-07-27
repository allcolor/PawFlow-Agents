"""AgentLoopTask actions — conversation (dispatcher facade).

The handlers were split into
_conv_core/_conv_ops/_conv_tags_export/_conv_import/_conv_sharing for the
<=800-line rule; this module dispatches by action across them.
"""
from tasks.ai.actions._conv_base import _UNHANDLED
from tasks.ai.actions._conv_core import _handle_conv_core
from tasks.ai.actions._conv_ops import _handle_conv_ops
from tasks.ai.actions._conv_tags_export import _handle_conv_tags_export
from tasks.ai.actions._conv_import import _handle_conv_import
from tasks.ai.actions._conv_sharing import _handle_conv_sharing


def _handle_conversation(self, action, body, store, user_id, flowfile):
    """Handle conversation actions. Returns [flowfile] or None."""
    for _handler in (_handle_conv_core, _handle_conv_ops,
                     _handle_conv_tags_export, _handle_conv_import,
                     _handle_conv_sharing):
        _res = _handler(self, action, body, store, user_id, flowfile)
        if _res is not _UNHANDLED:
            return _res
    return None
