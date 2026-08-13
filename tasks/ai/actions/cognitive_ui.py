"""AgentLoopTask actions for cognitive webchat panels.

Handles diary, knowledge graph, project graph/wiki and scratchpad requests.
"""

import json
import logging


logger = logging.getLogger(__name__)


def _resolve_project_graph(user_id, body):
    """Return the active relay-scoped graph and its live execution surface."""
    conv_id = body.get("conversation_id", "")
    agent_name = body.get("agent_name", "")
    relay_param = str(body.get("relay_id") or body.get("source") or "")
    if relay_param:
        from core.relay_bindings import get_default_local, get_linked_all
        if relay_param not in get_linked_all(conv_id):
            raise ValueError(
                f"Relay '{relay_param}' is not linked to this conversation")
        relay_id = relay_param
        from core.service_registry import ServiceRegistry
        service = ServiceRegistry.get_instance().resolve(
            relay_id, user_id=user_id, conv_id=conv_id)
        local = bool(get_default_local(
            conv_id, relay_id=relay_id, agent=agent_name))
    else:
        from core.project_context import resolve_active_project
        relay_id, service, local = resolve_active_project(
            user_id, conv_id, agent_name)
    if not relay_id:
        raise ValueError("No active relay project for this conversation")
    from core.project_graph import ProjectGraph
    return ProjectGraph.for_relay(user_id, relay_id), service, local, relay_id


def _resolve_project_wiki(user_id, body):
    """Return the wiki sharing the active relay project identity."""
    _graph, service, local, relay_id = _resolve_project_graph(user_id, body)
    from core.project_wiki import ProjectWiki
    return ProjectWiki.for_relay(user_id, relay_id), service, local, relay_id


def _handle_cognitive_ui(self, action, body, store, user_id, flowfile):
    """Handle diary/KG/project_graph UI actions. Returns [flowfile] or None."""

    # ── Diary ──────────────────────────────────────────────────────

    if action == "diary_list":
        try:
            from core.agent_diary import AgentDiary
            agent = body.get("agent_name", "")
            limit = int(body.get("limit", 50) or 50)
            entry_type = body.get("type", "")
            entries = AgentDiary.instance().read(
                user_id, agent, limit=limit, entry_type=entry_type)
            flowfile.set_content(json.dumps({
                "entries": entries, "count": len(entries),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "diary_add":
        text = body.get("text", "").strip()
        if not text:
            flowfile.set_content(json.dumps({"error": "Missing text"}).encode())
            return [flowfile]
        agent = body.get("agent_name", "")
        if not agent:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        entry_type = body.get("type", "observation")
        tags = body.get("tags", [])
        try:
            from core.agent_diary import AgentDiary
            record = AgentDiary.instance().write(
                user_id, agent, text,
                entry_type=entry_type, tags=tags)
            flowfile.set_content(json.dumps(record, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Knowledge Graph ────────────────────────────────────────────

    if action == "kg_list":
        try:
            from core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph.for_user(user_id)
            entity = body.get("entity", "")
            limit = int(body.get("limit", 100) or 100)
            entries = kg.timeline(entity=entity, limit=limit)
            flowfile.set_content(json.dumps({
                "triples": entries, "count": len(entries),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "kg_add_triple":
        subject = body.get("subject", "").strip()
        predicate = body.get("predicate", "").strip()
        obj = body.get("object", "").strip()
        if not subject or not predicate or not obj:
            flowfile.set_content(json.dumps(
                {"error": "subject, predicate, and object are required"}).encode())
            return [flowfile]
        confidence = body.get("confidence", "EXTRACTED")
        try:
            from core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph.for_user(user_id)
            result = kg.add_triple(
                subject=subject, predicate=predicate, obj=obj,
                confidence=confidence,
                source=body.get("source", "user"),
            )
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "kg_invalidate_triple":
        subject = body.get("subject", "").strip()
        predicate = body.get("predicate", "").strip()
        obj = body.get("object", "").strip()
        if not subject or not predicate or not obj:
            flowfile.set_content(json.dumps(
                {"error": "subject, predicate, and object are required"}).encode())
            return [flowfile]
        try:
            from core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph.for_user(user_id)
            count = kg.invalidate(subject=subject, predicate=predicate, obj=obj)
            flowfile.set_content(json.dumps({"invalidated": count}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "kg_get_stats":
        try:
            from core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph.for_user(user_id)
            stats = kg.stats()
            flowfile.set_content(json.dumps(stats, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Project Graph ──────────────────────────────────────────────

    if action == "project_graph_build":
        # Synchronous build path for the UI 'Build' button. Goes
        # through the same ProjectGraph.build_from_relay logic the
        # agent's project_graph(action='build') tool uses, but skips
        # the agent loop — the panel needs the actual result, not the
        # async 'accepted' ack that call_tool returns.
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            pg, svc, local, relay_id = _resolve_project_graph(user_id, body)
            if svc is None:
                flowfile.set_content(json.dumps({
                    "error": f"Relay '{relay_id}' is not connected.",
                }).encode())
                return [flowfile]
            path = body.get("path", ".") or "."
            result = pg.build_from_relay(svc, path, local=local)
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            logger.exception("project_graph_build failed")
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "project_graph_report":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            pg, _svc, _local, _relay_id = _resolve_project_graph(user_id, body)
            if not pg.has_graph():
                flowfile.set_content(json.dumps({
                    "report": "No project graph built yet.",
                    "has_graph": False,
                }).encode())
            else:
                flowfile.set_content(json.dumps({
                    "report": pg.get_report(),
                    "has_graph": True,
                    "nodes": len(pg.nodes),
                    "edges": len(pg.edges),
                }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "project_graph_query":
        conv_id = body.get("conversation_id", "")
        question = body.get("question", "")
        if not conv_id or not question:
            flowfile.set_content(json.dumps(
                {"error": "conversation_id and question are required"}).encode())
            return [flowfile]
        try:
            pg, _svc, _local, _relay_id = _resolve_project_graph(user_id, body)
            results = pg.query(question, depth=int(body.get("depth", 3) or 3))
            flowfile.set_content(json.dumps({
                "edges": results, "count": len(results),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "project_graph_node":
        conv_id = body.get("conversation_id", "")
        label = body.get("label", "")
        if not conv_id or not label:
            flowfile.set_content(json.dumps(
                {"error": "conversation_id and label are required"}).encode())
            return [flowfile]
        try:
            pg, _svc, _local, _relay_id = _resolve_project_graph(user_id, body)
            node = pg.get_node(label)
            if node:
                flowfile.set_content(json.dumps(node, ensure_ascii=False).encode())
            else:
                flowfile.set_content(json.dumps({"error": f"Node '{label}' not found"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Project Wiki ───────────────────────────────────────────────

    if action.startswith("project_wiki_"):
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps(
                {"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            wiki, service, local, relay_id = _resolve_project_wiki(user_id, body)
            subaction = action.removeprefix("project_wiki_")
            if subaction == "status":
                result = wiki.status()
            elif subaction == "pages":
                result = {"pages": wiki.list_pages(
                    str(body.get("query") or "")), "relay_id": relay_id}
            elif subaction == "query":
                result = {"pages": wiki.query(
                    str(body.get("query") or ""),
                    limit=int(body.get("limit", 25) or 25)),
                    "relay_id": relay_id}
            elif subaction == "page":
                result = wiki.get_page_data(str(body.get("slug") or ""))
            elif subaction == "save":
                result = wiki.upsert_page(
                    str(body.get("slug") or body.get("title") or ""),
                    str(body.get("title") or ""),
                    str(body.get("summary") or ""),
                    str(body.get("content") or ""),
                    body.get("sources") or [])
            elif subaction == "delete":
                slug = str(body.get("slug") or "")
                result = {"slug": slug, "deleted": wiki.delete_page(slug)}
            elif subaction == "lint":
                result = wiki.lint()
            elif subaction == "refresh":
                if service is None:
                    raise ValueError(f"Relay '{relay_id}' is not connected")
                result = wiki.scan_from_relay(
                    service, str(body.get("path") or "."), local=local)
            else:
                raise ValueError(f"Unknown project wiki UI action: {subaction}")
            flowfile.set_content(json.dumps(
                result, ensure_ascii=False).encode())
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            flowfile.set_content(json.dumps({"error": str(exc)}).encode())
        return [flowfile]

    # ── Scratchpad ─────────────────────────────────────────────────

    if action.startswith("scratchpad_"):
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        if not conv_id or not agent:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and agent_name are required",
            }).encode())
            return [flowfile]
        try:
            from core.scratchpad_store import ScratchpadStore
            scratchpad = ScratchpadStore.instance()
            scope = (user_id, conv_id, agent)
            subaction = action.removeprefix("scratchpad_")
            if subaction == "list":
                result = scratchpad.list_page(
                    *scope, query=str(body.get("query") or ""),
                    limit=int(body.get("limit", 50) or 50),
                    offset=int(body.get("offset", 0) or 0))
            elif subaction == "get":
                result = scratchpad.get(
                    *scope, str(body.get("note_id") or ""))
                if result is None:
                    raise ValueError("scratchpad note not found")
            elif subaction == "save":
                note_id = str(body.get("note_id") or "")
                if note_id:
                    changes = {key: body[key] for key in (
                        "topic", "content", "tags", "ttl_hours") if key in body}
                    result = scratchpad.update(*scope, note_id, **changes)
                else:
                    result = scratchpad.create(
                        *scope, topic=body.get("topic", ""),
                        content=body.get("content", ""),
                        tags=body.get("tags"),
                        ttl_hours=body.get("ttl_hours"))
            elif subaction == "delete":
                note_id = str(body.get("note_id") or "")
                result = {"note_id": note_id,
                          "deleted": scratchpad.delete(*scope, note_id)}
            elif subaction == "clear":
                result = {"deleted": scratchpad.clear(*scope)}
            else:
                raise ValueError(f"Unknown scratchpad UI action: {subaction}")
            flowfile.set_content(json.dumps(
                result, ensure_ascii=False).encode())
        except (TypeError, ValueError) as exc:
            flowfile.set_content(json.dumps({"error": str(exc)}).encode())
        return [flowfile]

    # ── Learn ──────────────────────────────────────────────────────

    if action == "learn":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        limit = int(body.get("limit", 50) or 50)
        try:
            from core.handlers.learn import LearnHandler
            handler = LearnHandler()
            handler.set_user_id(user_id)
            handler.set_agent_name(body.get("agent_name", ""))
            handler.set_conversation_id(conv_id)
            result = handler.execute({"limit": limit})
            flowfile.set_content(json.dumps({"result": result}, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return None  # Not our action
