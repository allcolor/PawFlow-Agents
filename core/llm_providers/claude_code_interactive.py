"""LLM provider mixin -- Claude Code interactive via MITM-observed SSE.

This provider deliberately does not read Claude Code transcripts or tmux
output. tmux is only the input transport; output is assembled from the local
MITM proxy's copy of Anthropic SSE events.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import time
import uuid

from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.llm_providers.cli_shared import textualize_message
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin


class _CCITurnCoordinator:
    def __init__(self, event_service, session_token: str, callback=None,
                 thinking_callback=None, block_callback=None):
        self.event_service = event_service
        self.session_token = session_token
        self.callback = callback
        self.thinking_callback = thinking_callback
        self.block_callback = block_callback
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_blocks: dict[int, dict] = {}
        self.usage = {}
        self.lifecycle_events: list[dict] = []

    def run(self, abort_event=None):
        from core.llm_client import LLMResponse, LLMToolCall

        done = False
        while not done:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("claude-code-interactive aborted")
            event = self.event_service.wait_event(self.session_token, timeout=0.25)
            if not event:
                continue
            etype = event.get("type", "")
            if etype == "request_error":
                raise RuntimeError(event.get("error", "CC interactive proxy request failed"))
            if etype == "hook":
                self.lifecycle_events.append(event)
                hook_name = event.get("hook_event_name", "")
                if hook_name == "Stop":
                    done = True
                elif hook_name == "StopFailure":
                    info = event.get("input") or {}
                    detail = info.get("error") or "Claude Code interactive turn failed"
                    raise RuntimeError(str(detail))
                continue
            if etype != "sse":
                continue
            name = event.get("event", "")
            payload = event.get("payload") or {}
            ptype = payload.get("type") or name
            if ptype == "content_block_start":
                block = payload.get("content_block") or {}
                idx = int(payload.get("index", 0) or 0)
                if block.get("type") == "tool_use":
                    self.tool_blocks[idx] = {
                        "id": block.get("id") or f"cci_{uuid.uuid4().hex[:12]}",
                        "name": block.get("name", ""),
                        "json": "",
                    }
            elif ptype == "content_block_delta":
                idx = int(payload.get("index", 0) or 0)
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self.text_parts.append(text)
                        if self.callback:
                            self.callback(text)
                elif dtype == "thinking_delta":
                    text = delta.get("thinking", "")
                    if text:
                        self.thinking_parts.append(text)
                        if self.thinking_callback:
                            self.thinking_callback(text)
                elif dtype == "input_json_delta" and idx in self.tool_blocks:
                    self.tool_blocks[idx]["json"] += delta.get("partial_json", "")
            elif ptype == "message_delta":
                usage = payload.get("usage") or {}
                if usage:
                    self.usage.update(usage)
            elif ptype == "message_stop":
                done = True

        tool_calls = []
        for block in self.tool_blocks.values():
            raw = block.get("json", "") or "{}"
            try:
                args = json.loads(raw)
            except Exception:
                args = {}
            tool_calls.append(LLMToolCall(
                id=block.get("id") or f"cci_{uuid.uuid4().hex[:12]}",
                name=block.get("name", ""),
                arguments=args,
            ))
        text = "".join(self.text_parts)
        if self.block_callback:
            self.block_callback(text, tool_calls)
        return LLMResponse(
            content=text,
            tool_calls=tool_calls,
            tokens_in=int(self.usage.get("input_tokens", 0) or 0),
            tokens_out=int(self.usage.get("output_tokens", 0) or 0),
            total_tokens=(int(self.usage.get("input_tokens", 0) or 0)
                          + int(self.usage.get("output_tokens", 0) or 0)),
            thinking="".join(self.thinking_parts),
            raw={
                "provider": "claude-code-interactive",
                "usage": self.usage,
                "lifecycle_events": self.lifecycle_events,
            },
        )


class LLMClaudeCodeInteractiveMixin(ClaudeCodeSessionMixin):
    """Claude Code interactive provider using a transparent MITM proxy."""

    def _stream_claude_code_interactive(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_callback=None, turn_callback=None,
        block_callback=None, *, call_user_id=None, call_conversation_id=None,
        call_agent_name=None, call_event_cid=None, call_ephemeral_stream=None,
    ):
        from core.llm_client import LLMClientError

        if not self._cci_enabled():
            raise LLMClientError(
                "claude-code-interactive is experimental; set "
                "PAWFLOW_CC_INTERACTIVE_ENABLED=1 or config.experimental=true")

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conversation_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or ""
        if not user_id or not conversation_id or not agent_name:
            raise LLMClientError("claude-code-interactive requires user_id, conversation_id and agent_name")

        pool = InteractiveClaudeCodePool.instance()
        state = pool.ensure_started(self, model or "", user_id, conversation_id, agent_name)
        prompt = self._cci_prompt(
            messages, tools, state.workdir, state.container_workdir,
            user_id, conversation_id,
            initial_context=not state.initial_context_loaded)
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service
        _, _, event_service = get_or_create_cc_interactive_event_service()
        event_service.drain_session(state.session_token)
        if not pool.send_text(state, prompt):
            raise LLMClientError("Failed to paste prompt into Claude Code interactive tmux session")
        state.initial_context_loaded = True

        coord = _CCITurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback, block_callback=block_callback)
        response = coord.run(getattr(self, "_abort", None))
        response.model = model or self.default_model
        if turn_callback:
            turn_callback(response.content, response.tool_calls)
        return response

    def _cci_enabled(self) -> bool:
        import os
        raw = self._cfg("experimental", "")
        if isinstance(raw, bool) and raw:
            return True
        if str(raw).strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return os.environ.get("PAWFLOW_CC_INTERACTIVE_ENABLED", "").strip() in {"1", "true", "yes", "on"}

    def _cci_prompt(self, messages, tools, workdir: str, container_workdir: str,
                    user_id: str, conversation_id: str,
                    initial_context: bool = False) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        image_lines = self._cci_materialize_images(
            messages, workdir, container_workdir, user_id, conversation_id)
        parts = []
        if initial_context:
            context_path = self._cci_write_initial_context(
                system_prompt, user_text, workdir, container_workdir)
            parts.append(
                "Read this PawFlow initial context file before answering:\n"
                f"@{context_path}\n\n"
                "It contains the compacted conversation summary/context and "
                "the latest user request. After reading it, continue the "
                "conversation and answer the latest user request.")
        elif system_prompt:
            parts.append("<system_instructions>\n" + system_prompt + "\n</system_instructions>")
        if image_lines:
            parts.append("Attachments:\n" + "\n".join(image_lines))
        if not initial_context:
            current = self._cci_current_turn_text(messages) or user_text
            if current:
                parts.append(current)
        return "\n\n".join(parts).strip() + "\n"

    def _cci_write_initial_context(self, system_prompt: str, user_text: str,
                                   workdir: str, container_workdir: str) -> str:
        rel = Path(".pawflow_cci") / "initial_context.md"
        path = Path(workdir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = ["# PawFlow Initial Context", ""]
        if system_prompt:
            body.extend(["## System Instructions", "", system_prompt.strip(), ""])
        if user_text:
            body.extend(["## Compacted Conversation Context", "", user_text.strip(), ""])
        path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        return f"{container_workdir}/{rel.as_posix()}"

    def _cci_current_turn_text(self, messages) -> str:
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
                lines.append(f"<message role=\"{role}\">\n{rendered}\n</message>")
        if not lines:
            return ""
        return "\n".join(lines) + "\n\nContinue from this latest turn."

    def _cci_materialize_images(self, messages, workdir: str, container_workdir: str,
                                user_id: str, conversation_id: str) -> list[str]:
        if not messages:
            return []
        last = None
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                last = msg
                break
        if last is None or not isinstance(getattr(last, "content", None), list):
            return []
        out_dir = Path(workdir) / ".pawflow_vision"
        out_dir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for block in last.content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "image_ref":
                file_id = block.get("file_id", "")
                if not file_id:
                    continue
                from core.file_store import FileStore
                filename, data, mime = FileStore.instance().get_required(
                    file_id, user_id=user_id, conversation_id=conversation_id)
                suffix = Path(filename).suffix or mimetypes.guess_extension(mime or "") or ".png"
                name = f"{file_id}{suffix}"
                (out_dir / name).write_bytes(data)
                lines.append(f"@{container_workdir}/.pawflow_vision/{name}")
            elif btype in {"image_url", "image"}:
                url = ""
                if btype == "image_url":
                    image_url = block.get("image_url") or {}
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                else:
                    url = block.get("data", "") or block.get("url", "")
                if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
                    continue
                meta, b64 = url.split(",", 1)
                mime = meta.split(";", 1)[0].replace("data:", "") or "image/png"
                suffix = mimetypes.guess_extension(mime) or ".png"
                name = f"inline_{uuid.uuid4().hex[:12]}{suffix}"
                (out_dir / name).write_bytes(base64.b64decode(b64))
                lines.append(f"@{container_workdir}/.pawflow_vision/{name}")
        return lines

    def _cci_send_user_message(self, text: str, attachments: list = None, **kwargs):
        pool = InteractiveClaudeCodePool.instance()
        user_id = kwargs.get("user_id") or getattr(self, "_user_id", "") or ""
        conversation_id = kwargs.get("conversation_id") or getattr(self, "_conversation_id", "") or ""
        agent_name = kwargs.get("agent_name") or getattr(self, "_agent_name", "") or ""
        service_id = getattr(self, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        state = pool._sessions.get(key)
        if not state:
            return False
        return pool.send_interrupt(state, text)

    def cancel_claude_code_interactive(self, force: bool = False):
        if not force:
            return False
        pool = InteractiveClaudeCodePool.instance()
        key = (
            getattr(self, "_user_id", "") or "",
            getattr(self, "_conversation_id", "") or "",
            getattr(self, "_agent_name", "") or "",
            getattr(self, "_agent_service", "") or "",
        )
        state = pool._sessions.get(key)
        if not state:
            return False
        return pool.force_stop(state)
