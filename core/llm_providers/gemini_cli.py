"""LLM provider mixin -- Gemini CLI (subprocess-based)."""

import json
import logging
import os
import subprocess
import tempfile
from typing import List

logger = logging.getLogger(__name__)


class LLMGeminiCliMixin:
    """Gemini CLI provider methods: complete and stream."""

    def _gemini_cli_env(self) -> dict:
        """Build environment for gemini subprocess."""
        env = os.environ.copy()
        if self.api_key:
            env["GEMINI_API_KEY"] = self.api_key
        return env

    def _complete_gemini_cli(
        self, messages, model, temperature, max_tokens, tools=None,
    ):
        """Run gemini CLI in prompt mode and parse the response."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        env = self._gemini_cli_env()

        cmd = [
            self.gemini_binary, "-p",
            "--output-format", "json",
            "-m", model or "gemini-2.5-flash",
        ]

        # System prompt via temp file (gemini uses GEMINI_SYSTEM_MD env var)
        sys_file = None
        try:
            if system_prompt:
                sys_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8",
                )
                sys_file.write(system_prompt)
                sys_file.close()
                env["GEMINI_SYSTEM_MD"] = sys_file.name

            logger.debug("gemini-cli cmd: %s", " ".join(cmd[:6]) + "...")

            try:
                result = subprocess.run(
                    cmd, input=user_text, capture_output=True,
                    text=True, timeout=self.timeout, env=env,
                    encoding="utf-8",
                )
            except FileNotFoundError:
                raise LLMClientError(
                    f"Gemini CLI binary '{self.gemini_binary}' not found. "
                    f"Install with: npm install -g @google/gemini-cli"
                )
            except subprocess.TimeoutExpired:
                raise LLMClientError(f"Gemini CLI timed out after {self.timeout}s")
        finally:
            if sys_file:
                try:
                    os.unlink(sys_file.name)
                except OSError:
                    pass

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMClientError(
                f"Gemini CLI exited with code {result.returncode}: {stderr[:500]}"
            )

        stdout = result.stdout.strip()
        if not stdout:
            raise LLMClientError("Gemini CLI returned empty output")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            clean, tc = self._extract_tool_calls(stdout)
            return LLMResponse(
                content=clean, model=model,
                finish_reason="stop" if not tc else "tool_use", tool_calls=tc,
            )

        content = data.get("response", data.get("result", ""))
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )

        clean, tc = self._extract_tool_calls(content)

        # Gemini stats format: {"stats": {"models": {"model_name": {"inputTokens": N, ...}}}}
        stats = data.get("stats", {})
        model_stats = {}
        for _mname, mdata in stats.get("models", {}).items():
            model_stats = mdata
            break
        tokens_in = model_stats.get("inputTokens", 0)
        tokens_out = model_stats.get("outputTokens", 0)

        return LLMResponse(
            content=clean,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=data,
        )

    def _stream_gemini_cli(
        self, messages, model, temperature, max_tokens, tools, callback,
    ):
        """Stream from gemini CLI using stream-json output format."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        env = self._gemini_cli_env()

        cmd = [
            self.gemini_binary, "-p",
            "--output-format", "stream-json",
            "-m", model or "gemini-2.5-flash",
        ]

        sys_file = None
        try:
            if system_prompt:
                sys_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8",
                )
                sys_file.write(system_prompt)
                sys_file.close()
                env["GEMINI_SYSTEM_MD"] = sys_file.name

            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, env=env,
                    encoding="utf-8",
                )
            except FileNotFoundError:
                raise LLMClientError(
                    f"Gemini CLI binary '{self.gemini_binary}' not found. "
                    f"Install with: npm install -g @google/gemini-cli"
                )

            proc.stdin.write(user_text)
            proc.stdin.close()

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
                    if etype in ("message", "assistant"):
                        msg = event.get("message", event)
                        for block in msg.get("content", []):
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    content_parts.append(text)
                                    if callback:
                                        callback(text)
                        last_data = event
                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            content_parts.append(text)
                            if callback:
                                callback(text)
                    elif etype == "result":
                        result_text = event.get("response", event.get("result", ""))
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
                raise LLMClientError(f"Gemini CLI stream exited with code {proc.returncode}")
        finally:
            if sys_file:
                try:
                    os.unlink(sys_file.name)
                except OSError:
                    pass

        full_content = "".join(content_parts)
        clean, tc = self._extract_tool_calls(full_content)

        stats = last_data.get("stats", {})
        model_stats = {}
        for _mname, mdata in stats.get("models", {}).items():
            model_stats = mdata
            break
        tokens_in = model_stats.get("inputTokens", 0)
        tokens_out = model_stats.get("outputTokens", 0)

        return LLMResponse(
            content=clean,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=last_data,
        )
