"""A2A publication and named-target configuration actions."""

import json

from tasks.ai.actions._agentres_base import _UNHANDLED


_ACTIONS = {
    "a2a_get",
    "a2a_publication_configure",
    "a2a_publication_create_key",
    "a2a_publication_revoke_key",
    "a2a_publication_delete",
    "a2a_target_save",
    "a2a_target_delete",
}


def _reply(flowfile, payload, status=200):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if status != 200:
        flowfile.set_attribute("http.response.status", str(status))
    return [flowfile]


def _owner(store, conversation_id, user_id, flowfile):
    owner = store.resolve_owner(conversation_id) if conversation_id else ""
    if not owner:
        return "", _reply(flowfile, {"error": "Conversation not found"}, 404)
    if owner != user_id:
        return "", _reply(flowfile, {
            "error": "Only the conversation owner can configure A2A",
        }, 403)
    return owner, None


def _canonical_agent(conversation_id, agent_name):
    from core.conv_agent_config import get_all_agent_configs
    configs = get_all_agent_configs(conversation_id) or {}
    needle = str(agent_name or "").strip().lower()
    return next((name for name in configs
                 if isinstance(name, str) and name.lower() == needle), "")


def _publications(a2a_store, conversation_id):
    rows = []
    for publication in a2a_store.list_publications(conversation_id):
        item = dict(publication)
        item["keys"] = a2a_store.list_keys(publication["publication_id"])
        rows.append(item)
    return rows


def _local_choices(store, owner):
    from core.conv_agent_config import get_all_agent_configs
    result = []
    for conversation in store.list_conversations(user_id=owner):
        conversation_id = conversation.get("conversation_id", "")
        agents = sorted((get_all_agent_configs(conversation_id) or {}).keys(),
                        key=str.lower)
        if agents:
            result.append({
                "conversation_id": conversation_id,
                "title": conversation.get("title") or conversation_id[:12],
                "agents": agents,
            })
    return result


def _handle_agentres_k7(self, action, body, store, user_id, flowfile):
    if action not in _ACTIONS:
        return _UNHANDLED
    conversation_id = str(body.get("conversation_id") or "").strip()
    if not conversation_id:
        return _reply(flowfile, {"error": "conversation_id is required"}, 400)
    owner, denied = _owner(store, conversation_id, user_id, flowfile)
    if denied is not None:
        return denied

    from core.a2a_store import A2AStore
    a2a_store = A2AStore.instance()
    if action == "a2a_get":
        return _reply(flowfile, {
            "publications": _publications(a2a_store, conversation_id),
            "targets": a2a_store.list_targets(conversation_id),
            "local_choices": _local_choices(store, owner),
        })

    if action == "a2a_publication_configure":
        canonical = _canonical_agent(conversation_id, body.get("agent_name"))
        if not canonical:
            return _reply(flowfile, {
                "error": "agent_name must identify an agent attached to this conversation",
            }, 400)
        try:
            publication = a2a_store.configure_publication(
                owner, conversation_id, canonical,
                label=str(body.get("label") or canonical).strip(),
                description=str(body.get("description") or "").strip(),
                context_policy=str(body.get("context_policy") or "isolated"),
                enabled=bool(body.get("enabled", True)),
            )
            from services.a2a_server_endpoint import ensure_a2a_routes
            ensure_a2a_routes()
        except (ValueError, PermissionError) as exc:
            return _reply(flowfile, {"error": str(exc)}, 400)
        return _reply(flowfile, {"publication": publication})

    publication_id = str(body.get("publication_id") or "").strip()
    if action.startswith("a2a_publication_"):
        publication = a2a_store.get_publication(publication_id)
        if (not publication or publication["conversation_id"] != conversation_id
                or publication["owner_user_id"] != owner):
            return _reply(flowfile, {"error": "A2A publication not found"}, 404)
        if action == "a2a_publication_create_key":
            raw, key = a2a_store.create_key(
                publication_id, str(body.get("label") or "A2A client").strip())
            return _reply(flowfile, {
                "api_key": raw, "key": key,
                "warning": "This API key is shown only once.",
            })
        if action == "a2a_publication_revoke_key":
            return _reply(flowfile, {"revoked": a2a_store.revoke_key(
                publication_id, str(body.get("key_id") or ""))})
        if action == "a2a_publication_delete":
            return _reply(flowfile, {
                "deleted": a2a_store.delete_publication(publication_id)})

    if action == "a2a_target_save":
        kind = str(body.get("kind") or "").strip().lower()
        if kind == "local":
            target_conversation_id = str(
                body.get("target_conversation_id") or "").strip()
            try:
                from core.conversation_access import require_write
                require_write(target_conversation_id, user_id, store=store)
            except Exception:
                return _reply(flowfile, {"error": "Target conversation not found"}, 404)
            canonical = _canonical_agent(
                target_conversation_id, body.get("target_agent"))
            if not canonical:
                return _reply(flowfile, {
                    "error": "Target agent is not attached to the target conversation",
                }, 400)
            body = dict(body)
            body["target_agent"] = canonical
        elif kind == "remote":
            try:
                from core.a2a_client import discover
                discover(
                    str(body.get("agent_card_url") or ""),
                    user_id=user_id, conversation_id=conversation_id,
                    allow_private=bool(body.get("allow_private")),
                )
            except Exception as exc:
                return _reply(flowfile, {
                    "error": f"Could not validate the remote Agent Card: {exc}",
                }, 400)
        try:
            target = a2a_store.save_target(
                owner, conversation_id, str(body.get("alias") or ""), kind,
                target_conversation_id=body.get("target_conversation_id"),
                target_agent=body.get("target_agent"),
                agent_card_url=body.get("agent_card_url"),
                auth_secret=body.get("auth_secret"),
                allow_private=bool(body.get("allow_private")),
            )
        except (ValueError, PermissionError) as exc:
            return _reply(flowfile, {"error": str(exc)}, 400)
        return _reply(flowfile, {"target": target})

    if action == "a2a_target_delete":
        deleted = a2a_store.delete_target(
            owner, conversation_id, str(body.get("target_id") or ""))
        return _reply(flowfile, {"deleted": deleted})
    return _UNHANDLED
