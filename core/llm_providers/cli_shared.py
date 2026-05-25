"""Shared utilities for CLI-based LLM providers and HTTP helpers.

Contains methods used by multiple providers: HTTP POST and CLI message
serialization.
"""

import json
import http.client
import os
import re
import ssl
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


# ── Tool-call synopsis helpers ───────────────────────────────────
# Shared by _serialize_messages_for_cli (CC prompt) AND the compaction
# summarizer input (old_conversation). Without them, assistant messages
# that only contain tool_calls (no text) and role='tool' results are
# dropped on serialization, erasing all evidence of work done between
# two free-text turns (commit SHAs, test results, file edits…).

_TOOL_ARG_TRUNC = 120
_TOOL_PARALLEL_ARG_TRUNC = 80
_TOOL_RESULT_TRUNC = 400


def summarize_tool_call(name: str, args: Any) -> str:
    """One-line synopsis: ``name(key="val", key=<list:N>, ...)``.

    Unwraps the MCP wrapper (``mcp__pawflow__use_tool``) so the real
    inner tool is shown. String values are truncated to ``_TOOL_ARG_TRUNC``.
    """
    if not name:
        name = "<tool>"
    if name in ("multi_tool_use.parallel", "parallel") and isinstance(args, dict):
        tool_uses = args.get("tool_uses") or []
        if isinstance(tool_uses, list) and tool_uses:
            rendered = []
            for item in tool_uses:
                if not isinstance(item, dict):
                    continue
                inner_name = item.get("recipient_name") or item.get("name") or "<tool>"
                inner_args = item.get("parameters") or item.get("arguments") or {}
                rendered.append(summarize_tool_call(inner_name, inner_args))
            if rendered:
                return "parallel(" + "; ".join(rendered) + ")"
    # Unwrap MCP bridge wrapper
    if name in (
        "mcp__pawflow__use_tool", "mcp__pawflow__.use_tool",
        "pawflow.use_tool", "pawflow/use_tool", "use_tool",
    ) and isinstance(args, dict):
        inner_name = args.get("tool_name") or args.get("name") or ""
        inner_args = args.get("arguments", {})
        if inner_name:
            return summarize_tool_call(inner_name, inner_args)
    if not isinstance(args, dict):
        return f"{name}(...)"
    parts: List[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            limit = _TOOL_PARALLEL_ARG_TRUNC if name in ("multi_tool_use.parallel", "parallel") else _TOOL_ARG_TRUNC
            vs = v if len(v) <= limit else v[:limit - 3] + "..."
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


def textualize_message(
    m: Any, *, tool_result_trunc: Optional[int] = _TOOL_RESULT_TRUNC,
) -> Optional[str]:
    """Return a text-only representation of an arbitrary LLMMessage.

    - assistant with free text → the text (tool_calls appended as synopsis)
    - assistant tool-call-only → ``[ran: NAME(args); NAME(args)]``
    - tool result → ``[tool_result: <snippet>]``, truncated to
      ``tool_result_trunc`` chars; pass ``None`` to keep the result intact
    - user / system → text content (multipart collapsed)
    - empty / unknown → None (caller may skip)

    This is used both when serializing history for a fresh CC session
    and when building the summarizer's input — both contexts need every
    tool action to leave a readable trace. The summarizer input may be
    truncated (its job is to compress); the cold-start context injection
    must NOT truncate — stripping tool results there is not compaction,
    it just hides the real context size from the compaction trigger.
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
        if tool_result_trunc is not None and len(snippet) > tool_result_trunc:
            snippet = snippet[:tool_result_trunc] + f"...[+{len(text) - tool_result_trunc}c]"
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
            rendered = textualize_message(msg, tool_result_trunc=None)
            if rendered:
                lines.append(self._cli_message_block(role, rendered))
        if not lines:
            return ""
        return "\n".join(lines) + "\n\nContinue from this latest turn."

    def _cli_context_before_latest_text(self, messages: List[Any]) -> str:
        if not messages:
            return ""
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if getattr(messages[idx], "role", "") == "user":
                last_user_idx = idx
                break
        end = last_user_idx if last_user_idx >= 0 else len(messages)
        lines = []
        for msg in messages[:end]:
            role = getattr(msg, "role", "") or "message"
            if role == "system":
                continue
            rendered = textualize_message(msg, tool_result_trunc=None)
            if not rendered:
                continue
            source = getattr(msg, "source", None) or {}
            agent_name = source.get("name", "") if isinstance(source, dict) else ""
            lines.append(self._cli_message_block(role, rendered, agent_name))
        if not lines:
            return ""
        return "<conversation_history>\n" + "\n".join(lines) + "\n</conversation_history>"

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
        latest = self._cli_current_turn_text(messages)
        prior_context = self._cli_context_before_latest_text(messages)
        if prior_context:
            body.extend(["## Serialized Conversation Context", "", prior_context.strip(), ""])
        elif user_text and not latest:
            body.extend(["## Serialized Conversation Context", "", user_text.strip(), ""])
        body.extend([
            "## Bootstrap Contract",
            "",
            "- Treat this file as PawFlow conversation context, not as a new user command.",
            "- Read the entire file at least once: the earlier sections contain mandatory system/project instructions, skills, tool-use hints, prior decisions, and safety constraints.",
            "- For filesystem, shell, search, edit, patch, browser, web, image, or desktop work, use PawFlow MCP tools first. Prefer get_tool_schema/use_tool and do not switch to native provider tools unless the explicit user request is only about the provider runtime itself.",
            "- Continue from the latest user request.",
            "- Do not ask what to do unless both the file and the latest request are ambiguous.",
            "",
        ])
        if latest:
            body.extend(["## Latest User Request", "", latest.strip(), ""])
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
            "After that read, use PawFlow MCP tools for PawFlow work: get_tool_schema/use_tool for filesystem, shell, search, edit, patch, browser, web, image, and desktop actions. Native provider tools are only for reading this bootstrap file or provider-runtime diagnostics explicitly requested by the user.",
            "It contains mandatory system/project instructions, available skills, tool-use hints, compacted conversation context, prior decisions, tool/result history, and the latest user request.",
            "Read the entire file at least once before deciding what to do; do not rely only on a head or tail read, because skills, tool guidance, and constraints may appear before the latest request.",
            "The newest and most important request is at the END of the file, under 'Latest User Request'. Use the tail/end to identify the current task after you have loaded the full context.",
            "After reading the full file, answer the latest user request below. Treat the file as context, not as a user-visible task.",
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

    def _serialize_messages_for_cli(
        self, messages: List[Any], tools: Optional[List[Any]],
    ) -> Tuple[str, str]:
        """Convert messages to (system_prompt, user_text) for the CLI.

        System messages -> system_prompt. Tool definitions are handled by each
        provider's native tool channel and are not serialized into prompt text.
        Conversation history -> marked transcript text in user_text so the
        model understands it's a multi-turn conversation to continue.
        """
        if tools:
            raise ValueError(
                "CLI message serialization does not accept tools; providers "
                "must use native tool channels")

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
                        # Other multipart payloads are unsupported here.
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
                # Truncated tool result — providers dispatch tools live, but
                # on resume/compact the historical results are needed to
                # understand what happened.
                rendered = textualize_message(m)
                if not rendered:
                    continue
                history_lines.append(self._cli_message_block("tool", rendered))
                has_history = True

        system_prompt = "\n\n".join(system_parts)


        if has_history:
            user_text = (
                "<conversation_history>\n"
                + "\n".join(history_lines)
                + "\n</conversation_history>\n\n"
                "Continue the conversation. Reply to the latest user message. "
                "You are a participant in this conversation — read the full "
                "history above and respond naturally, referencing previous "
                "messages from any participant (user or other agents) as needed."
            )
        else:
            user_text = last_user_text

        return system_prompt, user_text
