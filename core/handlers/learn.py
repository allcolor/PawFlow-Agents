"""Learn handler — extract user-centric insights from conversation messages.

Analyzes raw user messages (not summaries) to extract:
- Implicit preferences and patterns
- Frustrations and repeated requests (things the agent missed)
- Mood and communication style
- Corrections and clarifications (higher-value facts)
- Goals and priorities
"""

import json
import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

_LEARN_PROMPT = """Analyze these user messages from a conversation and extract key insights about the user.
Focus on what the user REVEALS about themselves through how they communicate, not just what they say.

Extract 5-15 structured facts, prioritizing:
1. REPEATED requests or corrections — things the user had to say twice mean the agent missed it. These are HIGH VALUE.
2. Frustrations — tone shifts, "no", "stop", "I said", "not that" indicate what went wrong.
3. Implicit preferences — communication style, level of detail expected, what they approve vs reject.
4. Explicit preferences — stated likes/dislikes, rules, constraints.
5. Goals and priorities — what they're working toward, what matters to them.
6. Mood/energy — are they patient, rushed, playful, serious, tired?

Return a JSON array of objects with keys:
- "text": The insight (concise, self-contained, 1-2 sentences max)
- "category": One of "preferences", "facts", "discoveries", "advice", "events"
- "confidence": "high" (explicitly stated or clearly repeated) or "inferred" (deduced from patterns)
- "tags": 1-3 relevant tags (e.g., "communication-style", "frustration", "correction", "priority")

Example:
[
  {"text": "User gets frustrated when the agent adds features beyond what was asked", "category": "preferences", "confidence": "high", "tags": ["frustration", "scope"]},
  {"text": "User prefers terse responses without trailing summaries", "category": "preferences", "confidence": "high", "tags": ["communication-style"]},
  {"text": "User had to repeat 3 times that backward compatibility is not wanted", "category": "discoveries", "confidence": "high", "tags": ["correction", "priority"]}
]

IMPORTANT: Focus on the USER, not the project. We want to understand the person.

USER MESSAGES:
"""


class LearnHandler(ToolHandler):
    """Extract user-centric insights from conversation messages."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "learn"

    @property
    def description(self) -> str:
        return (
            "Analyze user messages from the current conversation to extract insights "
            "about the user — their preferences, frustrations, communication style, "
            "repeated corrections, and goals.\n\n"
            "This is different from auto-extract (which works on summaries). Learn works "
            "on RAW user messages and captures what the user reveals through HOW they "
            "communicate, not just what they say. Repeated requests, corrections, and "
            "tone shifts are high-value signals that summaries lose.\n\n"
            "Key parameters:\n"
            "- limit: Max user messages to analyze (default: 50, max: 200). Uses the "
            "most recent messages.\n\n"
            "Results are stored as memories with source='learn' and tagged appropriately. "
            "Use this at the end of a long conversation, or when the user asks you to "
            "learn from the interaction. Can also be triggered via /learn in the webchat."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max user messages to analyze (default: 50)",
                },
            },
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
        if not self._conversation_id:
            return "Error: conversation_id not set — learn needs a conversation context"

        limit = min(int(arguments.get("limit", 50) or 50), 200)

        # 1. Load user messages from conversation
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            page = store.load_page(self._conversation_id, limit=limit * 3)
            if not page or not page.get("messages"):
                return "No messages found in this conversation."

            user_messages = []
            for m in page["messages"]:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    text = m["content"].strip()
                    # Skip system injections and very short messages
                    if text and not text.startswith("[System:") and len(text) > 5:
                        user_messages.append(text)

            # Take most recent N
            user_messages = user_messages[-limit:]
            if len(user_messages) < 3:
                return "Not enough user messages to analyze (need at least 3)."

        except Exception as e:
            return f"Error loading messages: {e}"

        # 2. Get summarizer client for LLM call
        try:
            _sum_client, _, _ = self._get_summarizer_client(self._user_id)
            if not _sum_client:
                return "Error: no summarizer_service configured — learn requires an LLM"
        except Exception:
            return "Error: summarizer_service not available"

        # 3. Build prompt with user messages
        numbered = "\n".join(f"[{i+1}] {msg[:500]}" for i, msg in enumerate(user_messages))
        prompt = _LEARN_PROMPT + numbered

        # 4. Call LLM
        try:
            from core.llm_client import LLMMessage
            resp = _sum_client.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=2000,
            )
            content = resp.content.strip()
        except Exception as e:
            return f"Error calling LLM: {e}"

        # 5. Parse JSON response
        import re
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if not match:
            return f"LLM returned unparseable response. Raw:\n{content[:500]}"

        try:
            facts = json.loads(match.group())
        except json.JSONDecodeError:
            return f"LLM returned invalid JSON. Raw:\n{content[:500]}"

        if not isinstance(facts, list) or not facts:
            return "LLM extracted no insights."

        # 6. Store as memories
        from core.memory_store import MemoryStore
        ms = MemoryStore.instance()
        stored = 0
        results = []
        for fact in facts[:15]:
            text = fact.get("text", "").strip()
            if not text or len(text) < 10:
                continue
            category = fact.get("category", "discoveries")
            if category not in ("facts", "events", "discoveries", "preferences", "advice"):
                category = "discoveries"
            tags = fact.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tags = [str(t).lower().strip() for t in tags if t][:5]
            if "learn" not in tags:
                tags.append("learn")
            confidence = fact.get("confidence", "inferred")
            if confidence == "high":
                tags.append("high-confidence")

            try:
                entry = ms.remember(
                    self._user_id, text, tags,
                    source="learn",
                    agent=self._agent_name,
                    category=category,
                )
                stored += 1
                results.append(f"- [{entry.id}] ({category}) {text[:100]}")
            except Exception:
                pass

        if not stored:
            return "LLM extracted insights but none could be stored."

        header = f"Learned {stored} insight(s) from {len(user_messages)} user messages:"
        return header + "\n" + "\n".join(results)

    def _get_summarizer_client(self, user_id: str):
        """Resolve summarizer service."""
        try:
            from core.service_registry import ServiceRegistry
            from core.expression import resolve_value

            svc_id = resolve_value("claude_code_llm_service", owner=user_id) or ""
            if not svc_id:
                return None, 0, ""

            svc = ServiceRegistry.get_instance().resolve(svc_id, user_id=user_id)
            if svc and hasattr(svc, 'complete'):
                return svc, 0, svc_id
        except Exception:
            pass
        return None, 0, ""
