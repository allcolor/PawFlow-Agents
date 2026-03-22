"""LLM provider mixin -- Claude Code CLI (subprocess-based)."""

import json
import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class LLMClaudeCodeMixin:
    """Claude Code CLI provider methods: complete and stream."""

    def _claude_code_env(self) -> dict:
        """Build environment for claude subprocess.

        Claude CLI uses its own auth (claude login). Just inherit env.
        """
        return os.environ.copy()

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

    def _complete_claude_code(
        self, messages, model, temperature, max_tokens, tools=None,
    ):
        """Run claude CLI in pipe mode and parse the response."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        cmd = [
            self.claude_binary, "-p",
            "--output-format", "json",
            "--model", model or "sonnet",
            "--max-turns", "1",
            # Disable all native Claude Code tools -- the model must only
            # respond with text (and optionally <tool_call> tags that we
            # parse ourselves).  Without this, Claude Code tries to execute
            # tools interactively (Read, Write, Bash...) which triggers
            # permission prompts and causes timeouts.
            "--allowedTools", "",
        ]
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
                env=self._claude_code_env(),
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

        cmd = [
            self.claude_binary, "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model or "sonnet",
            "--max-turns", "1",
        ]
        # Note: Claude CLI has no --max-tokens flag (only --max-budget-usd)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._claude_code_env(),
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
