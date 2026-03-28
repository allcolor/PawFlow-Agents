"""AgentLoopTask mixin — Claude Code file-context offloading.

Extracted from tasks/ai/agent_compaction.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
from typing import List

from core.llm_client import LLMMessage
from tasks.ai.agent_compaction import _select_recent_messages

logger = logging.getLogger(__name__)


class AgentCCContextMixin:
    """Claude Code context preparation mixin extracted from AgentCompactionMixin."""

    def _prepare_cc_file_context(
        self,
        messages: List[LLMMessage],
        max_recent: int = 50,
    ) -> List[LLMMessage]:
        """Prepare context for Claude Code by offloading old messages to FileStore.

        Instead of sending all messages as the API prompt (which hits "Prompt too long"),
        writes old messages to a JSONL file in FileStore and returns a short context:
          [0] system prompt (original)
          [1] user: "Conversation history is in file {file_id}. Read it."
          [2] assistant: "Understood."
          [3..N] recent messages (last ~50)

        Claude Code reads the JSONL file via read MCP tool — no prompt size limit.
        """
        if not messages:
            return messages

        system_msg = messages[0] if messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        # If few enough messages, no need to offload
        if len(messages) <= max_recent + start_idx + 5:
            return messages

        # Split: old messages → file, recent messages → prompt
        split = _select_recent_messages(messages, start_idx,
                                         min_conversation=25, max_total=max_recent)
        if split <= start_idx:
            return messages

        old_messages = messages[start_idx:split]
        recent_messages = messages[split:]

        # Serialize old messages to JSONL
        serialized = self._serialize_messages(old_messages)
        jsonl_lines = []
        for entry in serialized:
            jsonl_lines.append(json.dumps(entry, ensure_ascii=False))
        jsonl_content = "\n".join(jsonl_lines)

        # Write to FileStore — fallback to direct messages if store fails
        from core.file_store import FileStore
        try:
            file_id = FileStore.instance().store(
                "conversation_history.jsonl",
                jsonl_content.encode("utf-8"),
                "application/jsonl",
                category="context",
            )
        except Exception as e:
            logger.error("[cc-context] FileStore write failed: %s — sending messages directly", e)
            return messages

        logger.info("[cc-context] offloaded %d old messages (%d chars) to FileStore %s, "
                    "keeping %d recent messages in prompt",
                    len(old_messages), len(jsonl_content), file_id, len(recent_messages))

        # Tag: the agent loop checks if CC actually read this file
        self._cc_context_file_id = file_id

        # Build compact context
        result: List[LLMMessage] = []
        if system_msg:
            result.append(system_msg)
        result.append(LLMMessage(
            role="user",
            content=(
                f"[Conversation context — {len(old_messages)} earlier messages offloaded]\n\n"
                f"Your conversation history ({len(old_messages)} messages) is stored in "
                f"FileStore file '{file_id}' (JSONL format, one message per line with "
                f"role/content/tool_calls/tool_call_id fields).\n\n"
                f"Read it with: mcp__pawflow__use_tool(tool_name='read', "
                f"arguments={{path: '{file_id}', source: 'filestore'}}) to understand the full context.\n"
                f"The file may be large — use offset/limit arguments to paginate.\n\n"
                f"The {len(recent_messages)} most recent messages are below in the prompt. "
                f"Continue from where you left off."
            ),
        ))
        result.append(LLMMessage(
            role="assistant",
            content="Understood. I'll read the conversation history file to get full context, "
                    "then continue from the recent messages.",
        ))
        result.extend(recent_messages)
        return result
