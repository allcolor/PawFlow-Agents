"""AgentLoopTask actions — diary, knowledge graph, and project graph UI actions.

Handles action requests from the webchat UI panels (diary.js, knowledge_graph.js,
project_graph.js). Each action maps to the corresponding handler.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


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

    if action == "project_graph_report":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.project_graph import ProjectGraph
            pg = ProjectGraph.for_conversation(user_id, conv_id)
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
            from core.project_graph import ProjectGraph
            pg = ProjectGraph.for_conversation(user_id, conv_id)
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
            from core.project_graph import ProjectGraph
            pg = ProjectGraph.for_conversation(user_id, conv_id)
            node = pg.get_node(label)
            if node:
                flowfile.set_content(json.dumps(node, ensure_ascii=False).encode())
            else:
                flowfile.set_content(json.dumps({"error": f"Node '{label}' not found"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
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
