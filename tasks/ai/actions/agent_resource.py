"""AgentLoopTask actions — agent resource"""

import logging
from tasks.ai.actions._agentres_base import (  # noqa: F401  (re-exported public surface)
    _UNHANDLED,
    _FLOW_TEMPLATES_TTL,
    _FLOW_TEMPLATES_CACHE,
    _FLOW_TEMPLATES_REFRESHING,
    _FLOW_TEMPLATES_LOCK,
    invalidate_flow_templates_cache,
    _safe_package_component,
    _has_pfp_install_records,
    _decode_skill_package_files,
    _scan_flow_templates,
    _flow_template_from_latest,
    _scan_all_flow_templates,
    _overlay_admin_view_all,
    _get_flow_templates_cached,
)
from tasks.ai.actions._agentres_k1 import _handle_agentres_k1
from tasks.ai.actions._agentres_k2 import _handle_agentres_k2
from tasks.ai.actions._agentres_k3 import _handle_agentres_k3
from tasks.ai.actions._agentres_k4 import _handle_agentres_k4
from tasks.ai.actions._agentres_k5 import _handle_agentres_k5

logger = logging.getLogger(__name__)


def _handle_agent_resource(self, action, body, store, user_id, flowfile):
    """Handle agent resource actions. Returns [flowfile] or None."""
    for _handler in (_handle_agentres_k1, _handle_agentres_k2, _handle_agentres_k3,
                     _handle_agentres_k4, _handle_agentres_k5):
        _res = _handler(self, action, body, store, user_id, flowfile)
        if _res is not _UNHANDLED:
            return _res
    return None
