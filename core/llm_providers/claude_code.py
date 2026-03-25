"""LLM provider mixin -- Claude Code CLI (subprocess-based)."""

import json
import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class LLMClaudeCodeMixin:
    """Claude Code CLI provider methods: complete and stream."""

    def _claude_code_env(self, relay_service: str = "",
                         user_id: str = "", conversation_id: str = "",
                         agent_name: str = "") -> dict:
        """Build environment for claude subprocess.

        Claude CLI uses its own auth (claude login). Passes PawFlow
        context to the MCP bridge via env vars.
        """
        env = os.environ.copy()
        env["PAWFLOW_RELAY_SERVICE"] = relay_service or ""
        env["PAWFLOW_USER_ID"] = user_id or ""
        env["PAWFLOW_CONVERSATION_ID"] = conversation_id or ""
        env["PAWFLOW_AGENT_NAME"] = agent_name or ""
        return env

    @staticmethod
    def _build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text into a single stdin payload.

        Always passes everything via stdin to avoid Windows command-line
        length limits (CreateProcess: 32,767 chars).
        """
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n"
            + system_prompt
            + "\n</system_instructions>\n\n"
            + user_text
        )

    def _get_mcp_bridge_path(self) -> str:
        """Path to the MCP bridge script."""
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "mcp_bridge.py")

    def _build_claude_cmd(self, model: str, output_format: str = "json",
                          session_id: str = "") -> list:
        """Build claude CLI command with MCP bridge."""
        cmd = [
            self.claude_binary, "-p",
            "--output-format", output_format,
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "100",
        ]
        # MCP bridge for PawFlow tools
        mcp_bridge = self._get_mcp_bridge_path()
        if os.path.exists(mcp_bridge):
            cmd.extend(["--mcp-server", f"python {mcp_bridge}"])
        # Session resume for context continuity
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    def _complete_claude_code(
        self, messages, model, temperature, max_tokens, tools=None,
    ):
        """Run claude CLI in pipe mode and parse the response."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        # Check for session_id (set by agent context for resume)
        session_id = getattr(self, '_claude_session_id', "")
        relay_svc = getattr(self, '_relay_service_id', "")
        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        cmd = self._build_claude_cmd(model, "json", session_id)
        # Note: Claude CLI has no --max-tokens flag (only --max-budget-usd)

        logger.debug("claude-code cmd: %s", " ".join(cmd[:6]) + "...")
        logger.debug("claude-code input length: %d chars", len(stdin_text))

        try:
            result = subprocess.run(
                cmd,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._claude_code_env(relay_svc, user_id, conv_id, agent_name),
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code"
            )
        except subprocess.TimeoutExpired:
            raise LLMClientError(
                f"Claude CLI timed out after {self.timeout}s"
            )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMClientError(
                f"Claude CLI exited with code {result.returncode}: {stderr[:500]}"
            )

        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            raise LLMClientError("Claude CLI returned empty output")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Sometimes output is plain text, not JSON
            content = stdout
            clean, tc = self._extract_tool_calls(content)
            return LLMResponse(
                content=clean, model=model, tool_calls=tc,
                finish_reason="stop" if not tc else "tool_use",
            )

        content = data.get("result", data.get("content", ""))
        if isinstance(content, list):
            # Handle content blocks format
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )

        clean, tc = self._extract_tool_calls(content)

        # Extract session_id for resume on next call
        new_session = data.get("session_id", "")
        if new_session:
            self._claude_session_id = new_session

        return LLMResponse(
            content=clean,
            model=data.get("model", model),
            tokens_in=data.get("usage", {}).get("input_tokens", 0),
            tokens_out=data.get("usage", {}).get("output_tokens", 0),
            total_tokens=(
                data.get("usage", {}).get("input_tokens", 0)
                + data.get("usage", {}).get("output_tokens", 0)
            ),
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=data,
        )

    def _stream_claude_code(
        self, messages, model, temperature, max_tokens, tools, callback,
    ):
        """Stream from claude CLI using stream-json output format."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        session_id = getattr(self, '_claude_session_id', "")
        relay_svc = getattr(self, '_relay_service_id', "")
        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        cmd = self._build_claude_cmd(model, "stream-json", session_id)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._claude_code_env(relay_svc, user_id, conv_id, agent_name),
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code"
            )

        # Send input and close stdin
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except BrokenPipeError:
            # CLI process died before reading input -- capture stderr
            stderr = ""
            try:
                stderr = proc.stderr.read().strip()
            except Exception:
                pass
            proc.wait()
            raise LLMClientError(
                f"Claude CLI pipe broken (exit {proc.returncode}): {stderr[:500]}"
            )

        # Read streaming output line by line
        content_parts: List[str] = []
        last_data: dict = {}
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                if etype == "assistant":
                    # Content message
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                content_parts.append(text)
                                if callback:
                                    callback(text)
                    last_data = msg
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        content_parts.append(text)
                        if callback:
                            callback(text)
                elif etype == "result":
                    # Final result
                    result_text = event.get("result", "")
                    if result_text and not content_parts:
                        content_parts.append(result_text)
                        if callback:
                            callback(result_text)
                    last_data = event
        finally:
            proc.stdout.close()
            proc.stderr.close()
            proc.wait(timeout=5)

        if proc.returncode and proc.returncode != 0:
            raise LLMClientError(f"Claude CLI stream exited with code {proc.returncode}")

        full_content = "".join(content_parts)
        clean, tc = self._extract_tool_calls(full_content)

        # Extract session_id for resume on next call
        new_session = last_data.get("session_id", "")
        if new_session:
            self._claude_session_id = new_session

        usage = last_data.get("usage", {})
        return LLMResponse(
            content=clean,
            model=last_data.get("model", model),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=last_data,
        )
