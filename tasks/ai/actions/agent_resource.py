"""AgentLoopTask actions — agent resource (dispatcher facade).

The handlers were split into _agentres_k1..k5 for the <=800-line rule; this
module dispatches by action across them and carries the authorization table
for all five. They address conversation-scoped agents, skills, prompts, MCPs,
tasks, hooks and themes by conversation_id alone, so before the table any
authenticated user who knew an id could read, replace or delete them.

``test_conversation_sharing`` fails if a handled action is missing from the
table -- the inventory is the point, not the individual rows.
"""

import logging
from tasks.ai.actions._conv_base import _authorize_conversation_action
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
from tasks.ai.actions._agentres_k6 import _handle_agentres_k6

logger = logging.getLogger(__name__)


# What each action needs on the conversation it names.
#
# A conversation-scoped agent, skill, prompt, MCP or task is filed under the
# conversation's OWNER -- ResourceStore._map_scope resolves that owner from the
# id alone, deliberately, because a collaborator's turn must resolve the
# conversation's own resources. The store answers "whose directory", never
# "may you". That second question had no asker here: this cluster took a
# conversation_id straight from the request body, so any authenticated user
# who knew an id could list, read, overwrite and delete the resources the
# conversation runs on -- and swap the agent it answers with.
#
# read  -- shows the conversation's resources or configuration
# write -- mutates them, or drives the conversation with them
_ACTION_ROLES = {
    # _agentres_k1
    "set_agent_nickname": "write",
    "create_agent": "write",
    "list_agents": "read",
    "agent_disable": "write",
    "agent_enable": "write",
    "agent_promote": "write",
    "select_agent": "write",
    "delete_agent": "write",
    "create_skill": "write",
    "add_skill": "write",
    "update_skill": "write",
    "modify_skill": "write",
    "set_llm_service": "write",
    "delete_skill": "write",
    # _agentres_k2
    "assign_skill": "write",
    "unassign_skill": "write",
    "agent_msg": "write",       # posts into the conversation and runs an agent
    "resume_agent": "write",
    "run_skill": "write",
    "list_agent_skills": "read",
    "list_skills": "read",
    "import_skill_marketplace": "write",
    # _agentres_k3
    "list_resources": "read",
    "list_chat_themes": "read",
    "apply_chat_theme": "write",
    "create_chat_theme": "write",
    "delete_chat_theme": "write",
    "get_tool_mcp_filters": "read",
    "update_tool_mcp_filters": "write",
    "get_resource_detail": "read",
    # _agentres_k4
    "update_resource": "write",
    "create_resource": "write",
    "get_conversation_hooks": "read",
    "update_conversation_hooks": "write",
    "delete_resource": "write",
    "copy_resource_scope": "write",
    # _agentres_k5
    "activate_resource": "write",
    "deactivate_resource": "write",
    "add_agent_to_conv": "write",
    "get_agent_conv_config": "read",
    "update_agent_conv_config": "write",
    "remove_agent_from_conv": "write",
    "list_repo_agents": "read",
    # _agentres_k6 — publication delegates the owner's tool authority, so all
    # mutations are write-gated and then additionally owner-only in the handler.
    "mcp_server_get": "read",
    "mcp_server_configure": "write",
    "mcp_server_create_key": "write",
    "mcp_server_revoke_key": "write",
    "mcp_server_disconnect_client": "write",
    "mcp_server_set_enabled": "write",
    "mcp_server_delete": "write",
}

# `pfp_*` is one action per package operation, dispatched on the suffix.
# Installing a package into conversation scope adds tasks, agents and tools
# the conversation then runs; the rest only look.
_PFP_ACTION_ROLES = {
    "install": "write",
    "update": "write",
    "uninstall": "write",
    "dev_load": "write",
    "dev_unload": "write",
    "reload_tasks": "write",
    "registry_add": "write",
    "registry_remove": "write",
    "depot_add": "write",
    "depot_delete": "write",
    "depot_list": "read",
    "list_installed": "read",
    "inspect": "read",
    "registry_list": "read",
    "search": "read",
    "export": "read",
    "build": "read",
    "key_create": "read",
    "error": "read",
}

# Handled here but not scoped to an existing conversation. Listed so the
# completeness test can tell "reviewed, needs no conversation check" from
# "forgotten".
_UNSCOPED_ACTIONS = frozenset({
    "check_files",                  # FileStore existence by file id
    "reload_disk",                  # re-reads the requester's own services
    "delete_voice_clone",           # the requester's own voice cache
    "rename_voice_clone",           # idem
    "search_skill_marketplace",     # a public catalog
    "resolve_skill_import_source",  # resolves a ref, touches no conversation
    "share_resource",               # checks its own target_conversation_id
    "create_conversation",          # there is nothing to authorize against yet
})


def _authorize_action(action, body, store, user_id, flowfile):
    """Gate one action against the conversation it names.

    Returns a response to send back, or None to continue dispatching. An
    action with no conversation_id falls through: the handler owns that error,
    and answering 404 here would turn its 400 into a lie.
    """
    if action.startswith("pfp_"):
        # A package operation reaches the conversation only when `scope` aims
        # it there -- otherwise it installs, lists or removes the requester's
        # own user-scoped packages and the conversation_id is incidental,
        # attached by the UI to every call it makes. Gating those on the
        # conversation would refuse a user their own packages whenever the
        # conversation is not theirs, or not yet saved.
        scope = str(body.get("scope", "") or "").strip()
        if not scope and action in {"pfp_dev_load", "pfp_dev_unload"}:
            scope = "conversation"
        if scope not in ("conversation", "conv"):
            return None
        role = _PFP_ACTION_ROLES.get(action[4:])
    else:
        role = _ACTION_ROLES.get(action)
    if not role:
        return None
    conv_id = str(body.get("conversation_id", "") or "").strip()
    if not conv_id:
        return None
    return _authorize_conversation_action(role, conv_id, user_id, store,
                                          flowfile)


def _handle_agent_resource(self, action, body, store, user_id, flowfile):
    """Handle agent resource actions. Returns [flowfile] or None."""
    _denied = _authorize_action(action, body, store, user_id, flowfile)
    if _denied is not None:
        return _denied
    for _handler in (_handle_agentres_k1, _handle_agentres_k2, _handle_agentres_k3,
                     _handle_agentres_k4, _handle_agentres_k5,
                     _handle_agentres_k6):
        _res = _handler(self, action, body, store, user_id, flowfile)
        if _res is not _UNHANDLED:
            return _res
    return None
