"""conversation_search — full-text search across the user's past conversations.

Learning loop P4 (``docs/LEARNING_LOOP_PLAN.md``). ``recall`` searches what an
agent chose to remember; this searches what was actually said.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Per-hit budget for the summarize path. 50 hits x this stays well inside a
# summarizer's context while giving it real text to work from.
_SUMMARY_EXCERPT_CHARS = 900

_SUMMARY_PROMPT = (
    "Below are excerpts from past conversations, retrieved for the query "
    "shown first. Answer in at most 8 lines: what these conversations "
    "established about the query, and which conversation to open for the "
    "detail. Cite conversation ids. If the excerpts do not answer the query, "
    "say so instead of guessing.\n\n"
)


class ConversationSearchHandler(ToolHandler):
    """Search the raw text of the user's past conversations."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "conversation_search"

    @property
    def description(self) -> str:
        return (
            "Search the full text of your past conversations with this user.\n\n"
            "Use this when you suspect a topic was already discussed or a "
            "problem already solved, and no memory covers it — 'we fixed this "
            "before, where?'. `recall` only finds facts an agent decided to "
            "store at the time; this finds what was actually said, whether or "
            "not anyone extracted it. `read_history` stays the right tool for "
            "the CURRENT conversation.\n\n"
            "Key parameters:\n"
            "- query (required): words to look for. Supports quoted phrases "
            "(\"exact phrase\") and OR; invalid syntax falls back to matching "
            "all the words.\n"
            "- agent: restrict to messages produced by one agent.\n"
            "- limit: max hits (default 10, max 50).\n"
            "- include_current: search the current conversation too "
            "(default false — it is already in your context).\n"
            "- summarize: have the summarizer LLM synthesize the hits into an "
            "answer instead of returning excerpts.\n\n"
            "Read-only. Encrypted conversations are never indexed and so never "
            "appear in results."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words or quoted phrase to search for",
                },
                "agent": {
                    "type": "string",
                    "description": "Only messages from this agent",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits to return (default 10, max 50)",
                },
                "include_current": {
                    "type": "boolean",
                    "description": (
                        "Include the current conversation (default false)"),
                },
                "summarize": {
                    "type": "boolean",
                    "description": (
                        "Summarize the hits with the summarizer LLM instead "
                        "of returning excerpts"),
                },
            },
            "required": ["query"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "Error: query is required"

        from core.conversation_index import ConversationIndex, FTSUnavailable
        try:
            index = ConversationIndex.for_user(self._user_id)
        except FTSUnavailable as exc:
            return (f"Error: conversation search needs SQLite FTS5, which this "
                    f"server's Python does not provide ({exc}).")

        try:
            refresh = index.refresh()
        except Exception as exc:
            logger.warning("conversation index refresh failed", exc_info=True)
            return f"Error refreshing the conversation index: {exc}"

        exclude = "" if arguments.get("include_current") else self._conversation_id
        try:
            hits = index.search(
                query,
                agent=str(arguments.get("agent") or ""),
                limit=arguments.get("limit"),
                exclude_conversation=exclude,
            )
        except Exception as exc:
            logger.warning("conversation search failed", exc_info=True)
            return f"Error searching conversations: {exc}"

        if not hits:
            return self._no_hits(query, index, refresh)

        if arguments.get("summarize"):
            return self._summarize(query, hits)
        return self._render(query, hits, refresh)

    # -- Rendering -----------------------------------------------------

    def _no_hits(self, query: str, index, refresh: Dict[str, int]) -> str:
        stats = index.stats()
        out = (f"No match for {query!r} in {stats['messages']} messages across "
               f"{stats['conversations']} conversations.")
        if refresh.get("skipped_encrypted"):
            out += (f" {refresh['skipped_encrypted']} encrypted conversation(s) "
                    f"are never indexed.")
        return out

    @staticmethod
    def _when(ts: Any) -> str:
        try:
            value = float(ts or 0)
        except (TypeError, ValueError):
            return ""
        if value <= 0:
            return ""
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")

    def _render(self, query: str, hits: List[Dict[str, Any]],
                refresh: Dict[str, int]) -> str:
        lines = [f"{len(hits)} hit(s) for {query!r}:"]
        for hit in hits:
            cid = str(hit.get("conversation_id") or "")
            title = str(hit.get("title") or "").strip() or "(untitled)"
            who = str(hit.get("agent") or "").strip() or str(hit.get("role") or "")
            when = self._when(hit.get("ts"))
            head = f"\n- [{cid[:8]}] {title}"
            if when:
                head += f" — {when}"
            if who:
                head += f" — {who}"
            lines.append(head)
            snippet = " ".join(str(hit.get("snippet") or "").split())
            if snippet:
                lines.append(f"  {snippet}")
        lines.append(
            "\nOpen a conversation with its full id to read the surrounding "
            "messages; matched terms are shown in [brackets].")
        if refresh.get("skipped_encrypted"):
            lines.append(
                f"{refresh['skipped_encrypted']} encrypted conversation(s) were "
                f"not searched — encrypted content is never indexed.")
        return "\n".join(lines)

    # -- Optional summarization ----------------------------------------

    def _summarize(self, query: str, hits: List[Dict[str, Any]]) -> str:
        if not self._conversation_id:
            # Every LLM call is billed and traced against a conversation;
            # there is no anonymous one to bill this to.
            return ("Error: summarize=true needs a conversation context and "
                    "this call has none. Re-run without summarize to get the "
                    "excerpts.")
        client = self._summarizer_client()
        if client is None:
            return ("Error: summarize=true needs a summarizer_service, and "
                    "none is configured. Re-run without summarize to get the "
                    "excerpts.")
        excerpts = []
        for hit in hits:
            cid = str(hit.get("conversation_id") or "")
            # The whole matched message, not the 16-token snippet the excerpt
            # renderer shows: a summarizer given only "… [word] …" can do
            # nothing but paraphrase the query back.
            text = " ".join(str(hit.get("content")
                                or hit.get("snippet") or "").split())
            excerpts.append(f"[{cid[:8]}] {text[:_SUMMARY_EXCERPT_CHARS]}")
        prompt = (f"{_SUMMARY_PROMPT}QUERY: {query}\n\nEXCERPTS:\n"
                  + "\n".join(excerpts))
        try:
            from core.llm_client import LLMMessage
            resp = client.complete(
                messages=[LLMMessage(role="user", content=prompt,
                                     conversation_id=self._conversation_id)],
                temperature=0.2,
                max_tokens=800,
                call_user_id=self._user_id,
                call_conversation_id=self._conversation_id,
                call_agent_name=self._agent_name or "conversation_search",
                call_event_cid="",
                call_ephemeral_stream=True,
            )
        except Exception as exc:
            logger.warning("conversation search summarize failed", exc_info=True)
            return (f"Error summarizing hits: {exc}\n\n"
                    + self._render(query, hits, {}))
        summary = str(getattr(resp, "content", "") or "").strip()
        if not summary:
            return self._render(query, hits, {})
        ids = sorted({str(h.get("conversation_id") or "")[:8] for h in hits})
        return (f"Summary of {len(hits)} hit(s) for {query!r} "
                f"(conversations: {', '.join(ids)}):\n{summary}")

    def _summarizer_client(self):
        try:
            from core.expression import resolve_value
            from core.service_registry import ServiceRegistry

            svc_id = resolve_value("summarizer_service", owner=self._user_id,
                                   conversation_id=self._conversation_id) or ""
            if not svc_id:
                return None
            svc = ServiceRegistry.get_instance().resolve(
                svc_id, user_id=self._user_id, conv_id=self._conversation_id)
            return svc if svc is not None and hasattr(svc, "complete") else None
        except Exception:
            logger.debug("summarizer resolution failed", exc_info=True)
            return None
