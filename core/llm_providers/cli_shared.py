"""Shared utilities for CLI-based LLM providers and HTTP helpers.

Contains methods used by multiple providers: HTTP POST, tool prompt
rendering, CLI message serialization, and tool call extraction.
"""

import json
import http.client
import logging
import os
import re
import ssl
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

logger = logging.getLogger(__name__)


# ── Tool-call synopsis helpers ───────────────────────────────────
# Shared by _serialize_messages_for_cli (CC prompt) AND the compaction
# summarizer input (old_conversation). Without them, assistant messages
# that only contain tool_calls (no text) and role='tool' results are
# dropped on serialization, erasing all evidence of work done between
# two free-text turns (commit SHAs, test results, file edits…).

_TOOL_ARG_TRUNC = 120
_TOOL_RESULT_TRUNC = 400


def summarize_tool_call(name: str, args: Any) -> str:
    """One-line synopsis: ``name(key="val", key=<list:N>, ...)``.

    Unwraps the MCP wrapper (``mcp__pawflow__use_tool``) so the real
    inner tool is shown. String values are truncated to ``_TOOL_ARG_TRUNC``.
    """
    if not name:
        name = "<tool>"
    # Unwrap MCP bridge wrapper
    if name in ("mcp__pawflow__use_tool", "use_tool") and isinstance(args, dict):
        inner_name = args.get("tool_name") or args.get("name") or ""
        inner_args = args.get("arguments", {})
        if inner_name:
            return summarize_tool_call(inner_name, inner_args)
    if not isinstance(args, dict):
        return f"{name}(...)"
    parts: List[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            vs = v if len(v) <= _TOOL_ARG_TRUNC else v[:_TOOL_ARG_TRUNC - 3] + "..."
            # escape double quotes in value
            vs = vs.replace('"', '\\"')
            parts.append(f'{k}="{vs}"')
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}=<list:{len(v)}>")
        elif isinstance(v, dict):
            parts.append(f"{k}=<dict:{len(v)}>")
        elif v is None:
            parts.append(f"{k}=None")
        else:
            parts.append(f"{k}={v}")
    return f"{name}({', '.join(parts)})"


def textualize_message(m: Any) -> Optional[str]:
    """Return a text-only representation of an arbitrary LLMMessage.

    - assistant with free text → the text (tool_calls appended as synopsis)
    - assistant tool-call-only → ``[ran: NAME(args); NAME(args)]``
    - tool result → ``[tool_result: <snippet>]`` truncated to 400 chars
    - user / system → text content (multipart collapsed)
    - empty / unknown → None (caller may skip)

    This is used both when serializing history for a fresh CC session
    and when building the summarizer's input — both contexts need every
    tool action to leave a readable trace.
    """
    role = getattr(m, "role", "")
    content = getattr(m, "content", "")
    text = m.text_content if isinstance(content, list) else (content or "")
    tool_calls = getattr(m, "tool_calls", None) or []

    if role == "assistant":
        body = text.strip() if isinstance(text, str) else ""
        if tool_calls:
            synopsis = "; ".join(
                summarize_tool_call(
                    getattr(tc, "name", "") or "",
                    getattr(tc, "arguments", {}) or {},
                )
                for tc in tool_calls
            )
            if body:
                return f"{body}\n[ran: {synopsis}]"
            return f"[ran: {synopsis}]"
        return body or None

    if role == "tool":
        if not isinstance(text, str):
            text = str(text)
        snippet = text.strip()
        if not snippet:
            return None
        if len(snippet) > _TOOL_RESULT_TRUNC:
            snippet = snippet[:_TOOL_RESULT_TRUNC] + f"...[+{len(text) - _TOOL_RESULT_TRUNC}c]"
        return f"[tool_result: {snippet}]"

    if role in ("user", "system"):
        return text.strip() if isinstance(text, str) and text.strip() else None

    return None


class LLMCliSharedMixin:
    """Methods shared across CLI and HTTP providers."""

    @staticmethod
    def _cli_escape_text(text: str, *, quote: bool = False) -> str:
        return escape(str(text or ""), quote=quote)

    def _cli_message_block(self, role: str, rendered: str,
                           agent_name: str = "") -> str:
        attr = f' role="{self._cli_escape_text(role or "message", quote=True)}"'
        if agent_name:
            attr += f' agent="{self._cli_escape_text(agent_name, quote=True)}"'
        return (
            f"<message{attr}>\n"
            f"{self._cli_escape_text(rendered, quote=False)}\n"
            "</message>"
        )

    def _cli_current_turn_text(self, messages: List[Any]) -> str:
        if not messages:
            return ""
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if getattr(messages[idx], "role", "") == "user":
                last_user_idx = idx
                break
        start = last_user_idx if last_user_idx >= 0 else max(0, len(messages) - 3)
        lines = []
        for msg in messages[start:]:
            role = getattr(msg, "role", "") or "message"
            if role == "system":
                continue
            rendered = textualize_message(msg)
            if rendered:
                lines.append(self._cli_message_block(role, rendered))
        if not lines:
            return ""
        return "\n".join(lines) + "\n\nContinue from this latest turn."

    def _build_cli_initial_context_prompt(
        self,
        messages: List[Any],
        *,
        system_prompt: str,
        user_text: str,
        workdir: str,
        provider_workdir: str,
        rel_path: str = ".pawflow_cli/initial_context.md",
    ) -> str:
        """Write full cold-start context to a session file and return bootstrap text."""
        rel = Path(rel_path)
        host_path = Path(workdir) / rel
        host_path.parent.mkdir(parents=True, exist_ok=True)
        body = ["# PawFlow Initial Context", ""]
        if system_prompt:
            body.extend(["## System Instructions", "", system_prompt.strip(), ""])
        if user_text:
            body.extend(["## Serialized Conversation Context", "", user_text.strip(), ""])
        latest = self._cli_current_turn_text(messages)
        if latest:
            body.extend(["## Latest User Request", "", latest.strip(), ""])
        body.extend([
            "## Bootstrap Contract",
            "",
            "- Treat this file as PawFlow conversation context, not as a new user command.",
            "- Continue from the latest user request.",
            "- Do not ask what to do unless both the file and the latest request are ambiguous.",
            "",
        ])
        host_path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        provider_path = os.path.join(provider_workdir, rel.as_posix()).replace("\\", "/")
        prompt = [
            "PawFlow cold-session bootstrap.",
            "",
            "You must first read this initial context file before answering.",
            f"Path: {provider_path}",
            "If your CLI supports file mentions, this is the same file:",
            f"@{provider_path}",
            "",
            "Use your local filesystem/file-read capability if the file mention is not expanded automatically.",
            "It contains system instructions, project instructions, compacted conversation context, prior decisions, tool/result history, and the latest user request.",
            "After reading it, answer the latest user request below. Treat the file as context, not as a user-visible task.",
        ]
        if latest:
            prompt.extend(["", "Latest turn to answer now:", latest.strip()])
        return "\n".join(prompt).strip() + "\n"

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

        import re as _re
        _b64_pattern = _re.compile(r'data:[^;]+;base64,[A-Za-z0-9+/=]{100,}')

        for m in messages:
            text = m.text_content if isinstance(m.content, list) else (m.content or "")
            if m.role == "system":
                system_parts.append(text)
            elif m.role == "user":
                if isinstance(m.content, list):
                    _text_parts = []
                    for p in m.content:
                        if not isinstance(p, dict):
                            continue
                        pt = p.get("type", "")
                        if pt == "text":
                            _text_parts.append(p.get("text", ""))
                        elif pt == "image_ref":
                            _text_parts.append(f"[image: {p.get('filename', '?')}]")
                        elif pt == "file_ref":
                            _text_parts.append(
                                f"[attached file: {p.get('filename', '?')} ({p.get('mime_type', '?')}) "
                                f"— read via: read(path='{p.get('file_id', '?')}', source='filestore')]")
                        # skip image_url, image, document (legacy inline)
                    text = "\n".join(p for p in _text_parts if p.strip())
                else:
                    text = text or ""
                # Safety: strip any remaining base64 data URIs from string content
                text = _b64_pattern.sub('[image]', text)
                last_user_text = text
                if text.strip():
                    history_lines.append(self._cli_message_block("user", text))
            elif m.role == "assistant":
                # Keep tool-call-only messages as a synopsis so CC sees the
                # full trail of work (commits, tests, edits) after compaction
                # — dropping them erased the evidence between two free-text
                # turns and made CC rediscover its own work on every resume.
                rendered = textualize_message(m)
                if not rendered:
                    continue
                source = getattr(m, "source", None) or {}
                agent_name = source.get("name", "") if isinstance(source, dict) else ""
                history_lines.append(self._cli_message_block("assistant", rendered, agent_name))
                has_history = True
            elif m.role == "tool":
                # Truncated tool result — CC dispatches its own tools live,
                # but on resume/compact the historical results are needed
                # to understand what happened.
                rendered = textualize_message(m)
                if not rendered:
                    continue
                history_lines.append(self._cli_message_block("tool", rendered))
                has_history = True

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

