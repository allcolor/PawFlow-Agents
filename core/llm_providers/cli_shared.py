"""Shared utilities for CLI-based LLM providers and HTTP helpers.

Contains methods used by multiple providers: HTTP POST, tool prompt
rendering, CLI message serialization, and tool call extraction.
"""

import json
import http.client
import logging
import re
import ssl
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

logger = logging.getLogger(__name__)


class LLMCliSharedMixin:
    """Methods shared across CLI and HTTP providers."""

    @staticmethod
    def _clean_control_chars(text: str) -> str:
        """Remove control characters that break JSON parsing on some APIs."""
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    def _http_post(self, path: str, body: dict, headers: dict) -> dict:
        """Send POST and return parsed JSON."""
        parsed = urlparse(self.base_url or "https://api.openai.com")
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme

        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            raw_json = json.dumps(body)
            # Strip control characters that some LLM APIs can't parse
            json_body = self._clean_control_chars(raw_json).encode("utf-8")
            headers["Content-Length"] = str(len(json_body))
            full_path = (parsed.path.rstrip("/") + "/" + path.lstrip("/")).replace("//", "/")
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()
            response_body = response.read().decode("utf-8")
            if response.status >= 400:
                from core.llm_client import LLMClientError
                raise LLMClientError(f"LLM API error {response.status}: {response_body[:500]}")
            return json.loads(response_body)
        finally:
            conn.close()

    def _build_tool_prompt(self, tools: List[Any]) -> str:
        """Render tool definitions as text for the system prompt."""
        if not tools:
            return ""
        lines = ["<available_tools>"]
        for t in tools:
            lines.append(f"## {t.name}")
            lines.append(t.description)
            lines.append(f"Parameters: {json.dumps(t.parameters)}")
            lines.append("")
        lines.append("</available_tools>")
        lines.append("")
        lines.append("<tool_use_instructions>")
        lines.append("To use a tool, you MUST output this EXACT XML format (multiple calls allowed):")
        lines.append('<tool_call>{"name": "tool_name", "arguments": {"param": "value"}}</tool_call>')
        lines.append("")
        lines.append("CRITICAL RULES:")
        lines.append("- NEVER describe what you would do. EMIT the <tool_call> tag directly.")
        lines.append("- Do NOT wrap tool calls in markdown code blocks.")
        lines.append("- After emitting <tool_call> tags, the system executes them and returns results in the next turn.")
        lines.append("- You may include text before or after <tool_call> tags.")
        lines.append("- If you need more information or another turn to complete your work, output: [NEED_MORE]")
        lines.append("- When no tool is needed and your answer is complete, respond with plain text (no tags).")
        lines.append("")
        lines.append("EXAMPLE \u2014 correct:")
        lines.append('Let me check that for you.')
        lines.append('<tool_call>{"name": "fetch_http", "arguments": {"url": "https://api.example.com/data"}}</tool_call>')
        lines.append("")
        lines.append("EXAMPLE \u2014 WRONG (never do this):")
        lines.append('I would use the fetch_http tool to retrieve the data from the API.')
        lines.append("</tool_use_instructions>")
        return "\n".join(lines)

    def _serialize_messages_for_cli(
        self, messages: List[Any], tools: Optional[List[Any]],
    ) -> Tuple[str, str]:
        """Convert messages to (system_prompt, user_text) for the CLI.

        System messages + tool definitions -> system_prompt.
        Conversation history -> structured XML in user_text so the model
        understands it's a multi-turn conversation to continue.
        """
        system_parts: List[str] = []
        history_lines: List[str] = []
        last_user_text = ""
        has_history = False

        for m in messages:
            text = m.text_content if isinstance(m.content, list) else (m.content or "")
            if m.role == "system":
                system_parts.append(text)
            elif m.role == "user":
                last_user_text = text
                history_lines.append(f"<message role=\"user\">\n{text}\n</message>")
            elif m.role == "assistant":
                # Include source/agent identity if available
                source = getattr(m, "source", None) or {}
                agent_name = source.get("name", "") if isinstance(source, dict) else ""
                svc = source.get("llm_service", "") if isinstance(source, dict) else ""
                attr = ' role="assistant"'
                if agent_name:
                    attr += f' agent="{agent_name}"'
                if svc:
                    attr += f' service="{svc}"'

                assistant_text = text
                if m.tool_calls:
                    tc_strs = []
                    for tc in m.tool_calls:
                        tc_strs.append(
                            f'<tool_call>{json.dumps({"name": tc.name, "arguments": tc.arguments})}</tool_call>'
                        )
                    assistant_text = (assistant_text + "\n" + "\n".join(tc_strs)).strip()
                history_lines.append(f"<message{attr}>\n{assistant_text}\n</message>")
                has_history = True
            elif m.role == "tool":
                name = m.tool_call_id or "unknown"
                history_lines.append(
                    f"<message role=\"tool\" name=\"{name}\">\n{text}\n</message>"
                )

        # Build system prompt
        tool_prompt = self._build_tool_prompt(tools) if tools else ""
        if tool_prompt:
            system_parts.append(tool_prompt)
        system_prompt = "\n\n".join(system_parts)

        # Build user text
        if has_history:
            # Multi-turn: wrap in conversation tags with clear instruction
            user_text = (
                "<conversation_history>\n"
                + "\n".join(history_lines)
                + "\n</conversation_history>\n\n"
                "Continue the conversation. Reply to the latest user message. "
                "You are a participant in this conversation \u2014 read the full "
                "history above and respond naturally, referencing previous "
                "messages from any participant (user or other agents) as needed."
            )
        else:
            user_text = last_user_text

        return system_prompt, user_text

    def _extract_tool_calls(self, text: str) -> Tuple[str, list]:
        """Extract <tool_call> tags from response text.

        Returns (clean_text, tool_calls) where clean_text has tags removed.
        """
        from core.llm_client import LLMToolCall
        tool_calls = []
        for match in self.TOOL_CALL_RE.finditer(text):
            try:
                data = json.loads(match.group(1))
                tool_calls.append(LLMToolCall(
                    id=f"cc_{uuid4().hex[:12]}",
                    name=data.get("name", ""),
                    arguments=data.get("arguments", {}),
                ))
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Failed to parse tool_call: %s", match.group(1)[:200])
        clean = self.TOOL_CALL_RE.sub("", text).strip()
        return clean, tool_calls

