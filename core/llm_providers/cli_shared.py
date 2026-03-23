"""Shared utilities for CLI-based LLM providers and HTTP helpers.

Contains methods used by multiple providers: HTTP POST, tool prompt
rendering, CLI message serialization, tool call extraction, and OAuth
token refresh.
"""

import json
import http.client
import logging
import re
import ssl
import time
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

    def _refresh_oauth_token(self) -> None:
        """Refresh the OAuth access token using the refresh_token.

        Thread-safe: only one refresh happens at a time. Updates
        self.api_key and self.token_expires_at in place.
        """
        if not self.refresh_token or not self._token_lock:
            return
        with self._token_lock:
            # Double-check after acquiring lock (another thread may have refreshed)
            if self.token_expires_at and time.time() * 1000 < self.token_expires_at - 60_000:
                return  # still valid

            logger.info("Refreshing OAuth token via %s", self.token_url)
            parsed = urlparse(self.token_url)
            body = json.dumps({
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }).encode("utf-8")

            try:
                if parsed.scheme == "https":
                    ctx = ssl.create_default_context()
                    conn = http.client.HTTPSConnection(
                        parsed.hostname, parsed.port, timeout=30, context=ctx,
                    )
                else:
                    conn = http.client.HTTPConnection(
                        parsed.hostname, parsed.port, timeout=30,
                    )
                conn.request("POST", parsed.path or "/v1/oauth/token", body=body, headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                })
                resp = conn.getresponse()
                resp_body = resp.read().decode("utf-8")
                conn.close()

                if resp.status >= 400:
                    logger.error("OAuth refresh failed (%d): %s", resp.status, resp_body[:300])
                    return

                data = json.loads(resp_body)
                new_token = data.get("access_token", data.get("accessToken", ""))
                new_refresh = data.get("refresh_token", data.get("refreshToken", ""))
                expires_at = data.get("expires_at", data.get("expiresAt", 0))
                # Some endpoints return expires_in (seconds) instead of expires_at
                if not expires_at and data.get("expires_in"):
                    expires_at = time.time() * 1000 + data["expires_in"] * 1000

                if new_token:
                    self.api_key = new_token
                    self.token_expires_at = float(expires_at)
                    logger.info("OAuth token refreshed, expires at %s",
                                time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(expires_at / 1000)) if expires_at else "unknown")
                    # Update refresh token if rotated
                    if new_refresh:
                        self.refresh_token = new_refresh
                    # Persist updated tokens back to service config
                    self._persist_refreshed_tokens()
                else:
                    logger.error("OAuth refresh returned no access_token: %s", resp_body[:300])
            except Exception as e:
                logger.error("OAuth token refresh error: %s", e)

    def _persist_refreshed_tokens(self) -> None:
        """Persist refreshed tokens back to secrets/params/config.

        Detects ${secrets.global.*} and ${global.*} expressions in the
        service config and updates the underlying store accordingly.
        Best-effort -- if it fails, tokens are still valid in memory.
        """
        try:
            self._update_secret_or_config("api_key", self.api_key)
            self._update_secret_or_config("refresh_token", self.refresh_token)
            self._update_secret_or_config("token_expires_at", str(int(self.token_expires_at)))
        except Exception as e:
            logger.debug("Failed to persist refreshed tokens: %s", e)

    def _update_secret_or_config(self, field: str, value: str) -> None:
        """Update a value behind an expression reference, or direct config.

        Detects expression patterns in the service config and writes
        to the appropriate backing store:
        - ${secrets.global.KEY}       -> config/global_secrets.json (encrypted)
        - ${secrets.user.KEY}         -> config/users/<user>/secrets.json (encrypted)
        - ${global.KEY} / ${user.KEY} -> config/global_parameters.json or user params (plaintext)
        - raw value                   -> service config directly
        """
        if not value:
            return
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            registry = GlobalServiceRegistry.get_instance()
            for svc_id, svc in registry._services.items():
                if not (hasattr(svc, '_client') and svc._client is self):
                    continue
                raw_val = str(svc.config.get(field, ""))
                if "${" in raw_val:
                    self._write_expression_value(raw_val, value)
                    logger.debug("Updated expression '%s' for field '%s'", raw_val, field)
                else:
                    svc.config[field] = value
                    registry.save()
                    logger.debug("Updated config field '%s' for service '%s'", field, svc_id)
                return
        except Exception as e:
            logger.debug("Failed to update %s: %s", field, e)

    @staticmethod
    def _write_expression_value(expression: str, value: str) -> None:
        """Write a value to the backing store referenced by an expression.

        Supports: ${secrets.global.KEY}, ${secrets.user.KEY},
                  ${global.KEY}, ${user.KEY}
        """
        from pathlib import Path

        m = re.search(r'\$\{([^}]+)\}', expression)
        if not m:
            return
        ref = m.group(1)  # e.g. "secrets.global.claude_api_key"
        parts = ref.split(".")

        if parts[0] == "secrets" and len(parts) >= 3:
            # ${secrets.global.KEY} or ${secrets.user.KEY}
            scope = parts[1]  # "global" or username
            key = ".".join(parts[2:])
            if scope == "global":
                path = Path("config/global_secrets.json")
            else:
                path = Path("config/users") / scope / "secrets.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            from core.secrets import get_secrets_manager
            existing[key] = get_secrets_manager().encrypt(value)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        elif parts[0] == "global" and len(parts) >= 2:
            # ${global.KEY}
            key = ".".join(parts[1:])
            path = Path("config/global_parameters.json")
            existing = {}
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            existing[key] = value
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        elif parts[0] == "user" and len(parts) >= 2:
            # ${user.KEY} -- needs owner context, best-effort
            logger.warning("Cannot auto-update user-scoped expression '%s' \u2014 "
                           "user context not available during token refresh", expression)
        else:
            logger.debug("Unknown expression pattern '%s', skipping", expression)
