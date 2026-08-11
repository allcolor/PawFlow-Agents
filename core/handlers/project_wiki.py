"""Manual control and query surface for the automatic project wiki."""

from __future__ import annotations

import json
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler


class ProjectWikiHandler(BaseFsHandler):
    """Inspect or repair the LLM-maintained wiki for the selected relay."""

    @property
    def name(self) -> str:
        return "project_wiki"

    @property
    def description(self) -> str:
        return (
            "Persistent LLM-maintained wiki for a relay project. The relay ID is "
            "the project ID, so pages are shared across conversations and agents. "
            "Source hashing, graph refresh and wiki updates run automatically in "
            "the background. Use this tool to query the wiki, inspect freshness, "
            "lint it, or manually recover/update it.\n\n"
            "Actions: status, pages, query, page, page_data, lint, refresh, "
            "upsert, delete, acknowledge.\n"
            "- status: counts, pending sources and stale pages.\n"
            "- query: ranked full-text lookup over generated Markdown pages.\n"
            "- page: read one page by slug.\n"
            "- lint: stale claims, orphan pages, broken links and missing sources.\n"
            "- refresh: rescan relay sources and update hashes.\n"
            "- upsert: create/replace a generated page with current source citations.\n"
            "- acknowledge: clear processed sources; refuses sources still cited "
            "by stale pages."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "pages", "query", "page", "page_data", "lint",
                        "refresh", "upsert", "delete", "acknowledge",
                    ],
                    "description": (
                        "status: freshness summary; pages: list index; query: ranked "
                        "search; page/page_data: read one page; lint: validate; refresh: "
                        "rescan relay sources; upsert: write sourced page; delete: remove "
                        "page; acknowledge: clear processed source markers"),
                },
                "source": {"type": "string",
                           "description": "Relay ID; omit for the active default relay."},
                "path": {"type": "string",
                         "description": "Project root for refresh, default '.'."},
                "question": {"type": "string", "description": "Query text."},
                "slug": {"type": "string", "description": "Wiki page slug."},
                "title": {"type": "string", "description": "Page title for upsert."},
                "summary": {"type": "string", "description": "One-line index summary."},
                "content": {"type": "string",
                            "description": "Markdown body without frontmatter."},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "Current relay-relative source paths."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25,
                          "description": "Maximum results for pages/query"},
                "local": {"type": "boolean",
                          "description": "Use the relay host surface."},
            },
            "required": ["action"],
        }

    def _project(self, arguments: Dict[str, Any]):
        source = str(arguments.get("source") or "")
        service = self._find_service(source)
        if service is None:
            return None, None, self._no_target_error(source)
        relay_id = str(getattr(service, "_service_id", "") or source)
        if not relay_id:
            return None, None, "Error: resolved relay has no service ID"
        from core.project_wiki import ProjectWiki
        return service, ProjectWiki.for_relay(self._user_id, relay_id), ""

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id required"
        action = str(arguments.get("action") or "")
        service, wiki, error = self._project(arguments)
        if error:
            return error
        try:
            if action == "status":
                return self._json(wiki.status())
            if action == "pages":
                return self._json(wiki.list_pages(
                    str(arguments.get("question") or "")))
            if action == "query":
                question = str(arguments.get("question") or "")
                if not question:
                    return "Error: question required for query"
                return self._json(wiki.query(
                    question, limit=int(arguments.get("limit", 8) or 8)))
            if action == "page":
                slug = str(arguments.get("slug") or arguments.get("question") or "")
                if not slug:
                    return "Error: slug required for page"
                try:
                    return wiki.get_page(slug)
                except KeyError:
                    return f"Error: wiki page not found: {slug}"
            if action == "page_data":
                slug = str(arguments.get("slug") or "")
                if not slug:
                    return "Error: slug required for page_data"
                try:
                    return self._json(wiki.get_page_data(slug))
                except KeyError:
                    return f"Error: wiki page not found: {slug}"
            if action == "lint":
                return self._json(wiki.lint())
            if action == "refresh":
                result = wiki.scan_from_relay(
                    service, str(arguments.get("path") or "."),
                    local=self._resolve_local(arguments))
                return self._json(result)
            if action == "upsert":
                result = wiki.upsert_page(
                    str(arguments.get("slug") or arguments.get("title") or ""),
                    str(arguments.get("title") or ""),
                    str(arguments.get("summary") or ""),
                    str(arguments.get("content") or ""),
                    arguments.get("sources") or [],
                )
                return self._json(result)
            if action == "delete":
                slug = str(arguments.get("slug") or "")
                if not slug:
                    return "Error: slug required for delete"
                return self._json({"slug": slug, "deleted": wiki.delete_page(slug)})
            if action == "acknowledge":
                paths = arguments.get("sources") or []
                if not paths:
                    return "Error: sources required for acknowledge"
                return self._json(wiki.acknowledge(paths))
            return f"Unknown action: {action}"
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Error: {exc}"
