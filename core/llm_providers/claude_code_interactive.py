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
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
from tools.cc_interactive_filters import is_hidden_native_tool, normalize_observed_tool


_POST_STOP_IDLE_DRAIN_SECONDS = 0.25


class _CCITurnCoordinator:
    def __init__(self, event_service, session_token: str, callback=None,
                 thinking_callback=None, block_callback=None,
                 turn_callback=None):
        self.event_service = event_service
        self.session_token = session_token
        self.callback = callback
        self.thinking_callback = thinking_callback
        self.block_callback = block_callback
        self.turn_callback = turn_callback
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_blocks: dict[int, dict] = {}
        self.tool_by_id: dict[str, dict] = {}
        self.pending_tool_results: dict[str, list[dict]] = {}
        self.emitted_tool_use_ids: set[str] = set()
        self.emitted_tool_result_ids: set[str] = set()
        self.usage = {}
        self.lifecycle_events: list[dict] = []
        self.current_block_type = None
        self._text_block_buf = ""
        self._thinking_block_buf = ""
        self._thinking_redacted = False
        self._thinking_start = 0.0
        self._thinking_end = 0.0
        self._request_stop_reasons: dict[str, str] = {}
        self._request_saw_model_content: dict[str, bool] = {}
        self._request_saw_tool_use: dict[str, bool] = {}
        self._saw_model_content = False
        self._stop_seen = False
        self._post_stop_last_event_at = 0.0

    def run(self, abort_event=None):
        from core.llm_client import LLMResponse

        done = False
        while not done:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("claude-code-interactive aborted")
            timeout = 0.05 if self._stop_seen else 0.25
            event = self.event_service.wait_event(self.session_token, timeout=timeout)
            if not event:
                if self._stop_seen:
                    idle_for = time.time() - self._post_stop_last_event_at
                    if idle_for >= _POST_STOP_IDLE_DRAIN_SECONDS:
                        done = self._finish_turn_if_ready()
                continue
            if self._stop_seen:
                self._post_stop_last_event_at = time.time()
            etype = event.get("type", "")
            if etype == "request_error":
                raise RuntimeError(event.get("error", "CC interactive proxy request failed"))
            if etype == "request_start":
                request_id = event.get("request_id", "") or ""
                path = event.get("path", "") or ""
                if request_id and path.startswith("/v1/messages") and not event.get("ignore_reason"):
                    self._request_saw_model_content.setdefault(request_id, False)
                    self._request_saw_tool_use.setdefault(request_id, False)
                continue
            if etype == "request_stop":
                continue
            if etype == "response_ignored":
                continue
            if etype == "tool_use":
                self._emit_observed_tool_use(event)
                continue
            if etype == "tool_result":
                self._emit_tool_result(event)
                continue
            if etype == "hook":
                self.lifecycle_events.append(event)
                hook_name = event.get("hook_event_name", "")
                if hook_name == "Stop":
                    self._stop_seen = True
                    self._post_stop_last_event_at = time.time()
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
            request_id = event.get("request_id", "") or ""
            if ptype == "content_block_start":
                self._saw_model_content = True
                if request_id:
                    self._request_saw_model_content[request_id] = True
                block = payload.get("content_block") or {}
                idx = int(payload.get("index", 0) or 0)
                block_type = block.get("type")
                self.current_block_type = block_type
                if block_type == "thinking":
                    thinking = (
                        block.get("thinking", "")
                        or block.get("text", "")
                        or block.get("reasoning_content", ""))
                    if thinking:
                        self._append_thinking(thinking)
                    elif block.get("signature"):
                        self._mark_redacted_thinking()
                elif block_type == "tool_use":
                    if request_id:
                        self._request_saw_tool_use[request_id] = True
                    block_state = {
                        "id": block.get("id") or f"cci_{uuid.uuid4().hex[:12]}",
                        "name": block.get("name", ""),
                        "json": "",
                        "emitted": False,
                        "hidden": False,
                    }
                    self.tool_blocks[idx] = block_state
                    self.tool_by_id[block_state["id"]] = block_state
                    tool_input = block.get("input")
                    if isinstance(tool_input, dict) and tool_input:
                        self.tool_blocks[idx]["json"] = json.dumps(tool_input, ensure_ascii=False)
                elif block_type == "text":
                    self._append_text(block.get("text", ""))
            elif ptype == "content_block_delta":
                self._saw_model_content = True
                if request_id:
                    self._request_saw_model_content[request_id] = True
                idx = int(payload.get("index", 0) or 0)
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                if dtype == "signature_delta":
                    if self.current_block_type == "thinking" or delta.get("signature"):
                        self._mark_redacted_thinking()
                    continue
                if dtype == "input_json_delta" and idx in self.tool_blocks:
                    self.tool_blocks[idx]["json"] += delta.get("partial_json", "")
                    continue
                thinking_text = (
                    delta.get("thinking", "")
                    or delta.get("reasoning_content", "")
                    or delta.get("reasoning", ""))
                if dtype == "thinking_delta" or (
                        self.current_block_type == "thinking" and thinking_text):
                    self._append_thinking(thinking_text or delta.get("text", ""))
                else:
                    self._append_text(delta.get("text", ""))
            elif ptype == "content_block_stop":
                idx = int(payload.get("index", 0) or 0)
                if idx in self.tool_blocks:
                    self._emit_tool_use(idx)
                self._flush_text_block()
                self._flush_thinking_block()
                self.current_block_type = None
            elif ptype == "message_delta":
                request_id = event.get("request_id", "") or ""
                delta = payload.get("delta") or {}
                stop_reason = delta.get("stop_reason") or payload.get("stop_reason") or ""
                if request_id and stop_reason:
                    self._request_stop_reasons[request_id] = str(stop_reason)
                usage = payload.get("usage") or {}
                if usage:
                    self.usage.update(usage)
            elif ptype == "message_stop":
                continue

        text = "".join(self.text_parts)
        return LLMResponse(
            content=text,
            tool_calls=[],
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

    def _finish_turn_if_ready(self) -> bool:
        if not self._stop_seen:
            return False
        self._flush_text_block()
        self._flush_thinking_block()
        self._emit_pending_tool_uses()
        return True

    def _append_text(self, text: str) -> None:
        if text:
            self._text_block_buf += text
            self.text_parts.append(text)
            if self.callback:
                self.callback(text)

    def _append_thinking(self, text: str) -> None:
        if text:
            self._thinking_block_buf += text
            if self.thinking_callback:
                self.thinking_callback(text)

    def _mark_redacted_thinking(self) -> None:
        self._thinking_redacted = True
        if self._thinking_start == 0.0:
            self._thinking_start = time.time()
        self._thinking_end = time.time()

    def _flush_text_block(self) -> None:
        if not self._text_block_buf:
            return
        text = self._text_block_buf
        self._text_block_buf = ""
        if self.turn_callback and text:
            self.turn_callback(text, [], "")

    def _flush_thinking_block(self) -> None:
        if not self._thinking_block_buf and not self._thinking_redacted:
            return
        thinking = self._thinking_block_buf
        synthesized = False
        if not thinking and self._thinking_redacted:
            duration = max(0.0, self._thinking_end - self._thinking_start)
            thinking = (
                f"[Thought for {duration:.1f}s - reasoning content redacted "
                "by the Anthropic API; the signature is preserved by Claude Code.]"
            )
            synthesized = True
            self.thinking_parts.append(thinking)
        elif len(thinking.strip()) <= 1:
            self._thinking_block_buf = ""
            self._thinking_redacted = False
            self._thinking_start = 0.0
            self._thinking_end = 0.0
            return
        else:
            self.thinking_parts.append(thinking)
        self._thinking_block_buf = ""
        self._thinking_redacted = False
        self._thinking_start = 0.0
        self._thinking_end = 0.0
        if synthesized and thinking and self.thinking_callback:
            self.thinking_callback(thinking)
        if self.turn_callback:
            self.turn_callback("", [], thinking)

    def _emit_tool_use(self, idx: int) -> None:
        block = self.tool_blocks.get(idx) or {}
        if not block or block.get("emitted"):
            return
        tool_id = block.get("id") or f"cci_{uuid.uuid4().hex[:12]}"
        raw = block.get("json", "") or "{}"
        try:
            args = json.loads(raw)
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        display_name, display_args = normalize_observed_tool(block.get("name", ""), args)
        block["display_name"] = display_name
        block["display_args"] = display_args
        block["hidden"] = (
            is_hidden_native_tool(block.get("name", ""), args)
            or is_hidden_native_tool(display_name, display_args)
        )
        block["emitted"] = True
        if tool_id in self.emitted_tool_use_ids:
            self._emit_pending_tool_results(tool_id)
            return
        self.emitted_tool_use_ids.add(tool_id)
        if self.block_callback and not block.get("hidden"):
            self.block_callback("tool_use", {
                "id": tool_id,
                "name": display_name,
                "arguments": display_args,
            })
        self._emit_pending_tool_results(tool_id)

    def _emit_pending_tool_uses(self) -> None:
        for idx in list(self.tool_blocks):
            self._emit_tool_use(idx)

    def _emit_tool_result(self, event: dict) -> None:
        tc_id = event.get("tool_use_id", "") or ""
        if not tc_id:
            return
        if tc_id in self.emitted_tool_result_ids:
            return
        block = self.tool_by_id.get(tc_id) or {}
        if not block.get("emitted"):
            self.pending_tool_results.setdefault(tc_id, []).append(dict(event))
            return
        self._emit_tool_result_now(event, block)

    def _emit_observed_tool_use(self, event: dict) -> None:
        tc_id = event.get("tool_use_id", "") or event.get("id", "") or ""
        if not tc_id:
            return
        block = self.tool_by_id.get(tc_id)
        if block is None:
            args = event.get("arguments") or {}
            block = {
                "id": tc_id,
                "name": event.get("name", ""),
                "json": json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False),
                "emitted": False,
                "hidden": is_hidden_native_tool(event.get("name", ""), args if isinstance(args, dict) else {}),
            }
            self.tool_by_id[tc_id] = block
        if block.get("emitted") or tc_id in self.emitted_tool_use_ids:
            block["emitted"] = True
            self._emit_pending_tool_results(tc_id)
            return
        block["emitted"] = True
        self.emitted_tool_use_ids.add(tc_id)
        try:
            args = json.loads(block.get("json", "") or "{}")
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        display_name, display_args = normalize_observed_tool(block.get("name", ""), args)
        block["display_name"] = display_name
        block["display_args"] = display_args
        block["hidden"] = (
            is_hidden_native_tool(block.get("name", ""), args)
            or is_hidden_native_tool(display_name, display_args)
        )
        if self.block_callback and not block.get("hidden"):
            self.block_callback("tool_use", {
                "id": tc_id,
                "name": display_name,
                "arguments": display_args,
            })
        self._emit_pending_tool_results(tc_id)

    def _emit_pending_tool_results(self, tc_id: str) -> None:
        if not tc_id:
            return
        block = self.tool_by_id.get(tc_id) or {}
        if not block.get("emitted"):
            return
        pending = self.pending_tool_results.pop(tc_id, [])
        for event in pending:
            self._emit_tool_result_now(event, block)

    def _emit_tool_result_now(self, event: dict, block: dict) -> None:
        tc_id = event.get("tool_use_id", "") or ""
        if not tc_id or tc_id in self.emitted_tool_result_ids:
            return
        self.emitted_tool_result_ids.add(tc_id)
        if self.block_callback and not block.get("hidden"):
            display_name = block.get("display_name") or block.get("name", "")
            self.block_callback("tool_result", {
                "tc_id": tc_id,
                "tool": display_name,
                "result": event.get("content", "") or "(no output)",
            })


class LLMClaudeCodeInteractiveMixin(ClaudeCodeSessionMixin):
    """Claude Code interactive provider using a transparent MITM proxy."""

    def _stream_claude_code_interactive(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_callback=None, turn_callback=None,
        block_callback=None, *, call_user_id=None, call_conversation_id=None,
        call_agent_name=None, call_event_cid=None, call_ephemeral_stream=None,
    ):
        from core.llm_client import LLMClientError

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conversation_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or ""
        if not user_id or not conversation_id or not agent_name:
            raise LLMClientError("claude-code-interactive requires user_id, conversation_id and agent_name")

        pool = InteractiveClaudeCodePool.instance()
        state = pool.ensure_started(self, model or "", user_id, conversation_id, agent_name)
        self._cci_active_user_id = user_id
        self._cci_active_conversation_id = conversation_id
        self._cci_active_agent_name = agent_name
        self._cci_active_service_id = getattr(self, "_agent_service", "") or ""
        prompt = self._cci_prompt(
            messages, tools, state.workdir, state.container_workdir,
            user_id, conversation_id,
            initial_context=not state.initial_context_loaded)
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service
        _, _, event_service = get_or_create_cc_interactive_event_service()
        event_service.drain_session(state.session_token)
        if not pool.send_text(state, prompt):
            detail = getattr(state, "last_error", "") or "unknown tmux error"
            raise LLMClientError(
                "Failed to paste prompt into Claude Code interactive tmux session: "
                f"{detail}")
        state.initial_context_loaded = True

        coord = _CCITurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback, block_callback=block_callback,
            turn_callback=turn_callback)
        response = coord.run(getattr(self, "_abort", None))
        response.model = model or self.default_model
        return response

    def interrupt_claude_code_interactive(
        self, text: str, *, callback=None, thinking_callback=None,
        turn_callback=None, block_callback=None, user_id: str = "",
        conversation_id: str = "", agent_name: str = "", model: str = "",
    ):
        from core.llm_client import LLMClientError
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service

        state = self._cci_session_state(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        if not state:
            raise LLMClientError("No active Claude Code interactive session for interrupt")

        _, _, event_service = get_or_create_cc_interactive_event_service()
        event_service.drain_session(state.session_token)
        if not InteractiveClaudeCodePool.instance().send_interrupt(state, text):
            detail = getattr(state, "last_error", "") or "unknown tmux error"
            raise LLMClientError(
                "Failed to send interrupt to Claude Code interactive tmux session: "
                f"{detail}")

        coord = _CCITurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback, block_callback=block_callback,
            turn_callback=turn_callback)
        response = coord.run(getattr(self, "_abort", None))
        response.model = model or self.default_model
        return response

    def _cci_prompt(self, messages, tools, workdir: str, container_workdir: str,
                    user_id: str, conversation_id: str,
                    initial_context: bool = False) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        if tools:
            tool_mode = (
                "Use PawFlow MCP tools for shell, filesystem, browser, and "
                "project work. Claude Code built-in tools are disabled."
            )
            system_prompt = (system_prompt + "\n\n" + tool_mode).strip()
        image_lines = self._cci_materialize_images(
            messages, workdir, container_workdir, user_id, conversation_id)
        parts = []
        if initial_context:
            prompt = self._build_cli_initial_context_prompt(
                messages,
                system_prompt=system_prompt,
                user_text=user_text,
                workdir=workdir,
                provider_workdir=container_workdir,
                rel_path=".pawflow_cci/initial_context.md",
            )
            parts.append(prompt)
        elif system_prompt:
            parts.append("<system_instructions>\n" + system_prompt + "\n</system_instructions>")
        if image_lines:
            parts.append("Attachments:\n" + "\n".join(image_lines))
        if not initial_context:
            current = self._cci_live_text(messages) or user_text
            if current:
                parts.append(current)
        return "\n\n".join(parts).strip() + "\n"

    def _cci_live_text(self, messages) -> str:
        """Return only the latest user text for an already-live tmux session."""
        from core.llm_providers.cli_shared import textualize_message

        for msg in reversed(messages or []):
            if getattr(msg, "role", "") != "user":
                continue
            rendered = textualize_message(msg)
            return rendered.strip() if isinstance(rendered, str) else ""
        return ""

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

    def _cci_session_state(self, *, user_id: str = "", conversation_id: str = "",
                           agent_name: str = ""):
        pool = InteractiveClaudeCodePool.instance()
        uid = (user_id or getattr(self, "_cci_active_user_id", "")
               or getattr(self, "_user_id", "") or "")
        cid = (conversation_id or getattr(self, "_cci_active_conversation_id", "")
               or getattr(self, "_conversation_id", "") or "")
        agent = (agent_name or getattr(self, "_cci_active_agent_name", "")
                 or getattr(self, "_agent_name", "") or "")
        service_id = (getattr(self, "_cci_active_service_id", "")
                      or getattr(self, "_agent_service", "") or "")
        if not uid or not cid or not agent:
            return None
        return pool.find_session(uid, cid, agent, service_id)

    def _cci_send_user_message(self, text: str, attachments: list = None, **kwargs):
        state = self._cci_session_state(
            user_id=kwargs.get("user_id") or "",
            conversation_id=kwargs.get("conversation_id") or "",
            agent_name=kwargs.get("agent_name") or "",
        )
        if not state:
            return False
        return InteractiveClaudeCodePool.instance().send_interrupt(state, text)

    def cancel_claude_code_interactive(self, force: bool = False):
        if not force:
            return False
        state = self._cci_session_state()
        if not state:
            return False
        return InteractiveClaudeCodePool.instance().force_stop(state)
