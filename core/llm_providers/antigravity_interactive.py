"""LLM provider mixin -- Antigravity CLI interactive via observed SSE.

The provider runs the real ``agy`` CLI in tmux, injects prompts through tmux,
and assembles output from the Antigravity observer proxy JSONL. This mirrors
Claude Code interactive's boundary: tmux is the input transport, while model
events are taken from the provider network stream rather than terminal text.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
import time
import uuid

from core.agent_prompt_policy import append_cli_mcp_system_prompt
from core.antigravity_observer_pool import AntigravityObserverPool
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin


logger = logging.getLogger(__name__)


def _env_seconds(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.environ.get(name, "")
        if raw.strip():
            try:
                return max(0.0, float(raw))
            except ValueError:
                return default
    return default


_POST_DONE_IDLE_DRAIN_SECONDS = _env_seconds(
    ("PAWFLOW_AGI_POST_DONE_IDLE_DRAIN_SECONDS",), 2.5)
_NO_DONE_IDLE_DRAIN_SECONDS = _env_seconds(
    ("PAWFLOW_AGI_NO_DONE_IDLE_DRAIN_SECONDS",), 8.0)
_NO_PROXY_EVENT_TIMEOUT_SECONDS = _env_seconds(
    ("PAWFLOW_AGI_NO_PROXY_EVENT_TIMEOUT_SECONDS",), 300.0)


class _AntigravityLogTail:
    def __init__(self, path: str, offset: int = 0):
        self.path = path
        self.offset = max(0, int(offset or 0))
        self._partial = ""
        self._pending: list[dict] = []

    def wait_event(self, timeout: float = 0.25) -> dict:
        deadline = time.time() + max(0.0, timeout)
        while True:
            event = self._read_one()
            if event:
                return event
            if time.time() >= deadline:
                return {}
            time.sleep(0.05)

    def _read_one(self) -> dict:
        if self._pending:
            return self._pending.pop(0)
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.offset)
                data = fh.read()
                self.offset = fh.tell()
        except OSError:
            return {}
        if not data:
            return {}
        data = self._partial + data
        lines = data.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._partial = lines.pop()
        else:
            self._partial = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self._pending.append(event)
        if self._pending:
            return self._pending.pop(0)
        return {}


class _AntigravityTurnCoordinator:
    def __init__(self, log_path: str, offset: int = 0, callback=None,
                 thinking_callback=None, block_callback=None,
                 turn_callback=None, touch_callback=None,
                 emitted_tool_use_ids=None, emitted_tool_result_ids=None):
        self.tail = _AntigravityLogTail(log_path, offset)
        self.callback = callback
        self.thinking_callback = thinking_callback
        self.block_callback = block_callback
        self.turn_callback = turn_callback
        self.touch_callback = touch_callback
        self.emitted_tool_use_ids = emitted_tool_use_ids if emitted_tool_use_ids is not None else set()
        self.emitted_tool_result_ids = emitted_tool_result_ids if emitted_tool_result_ids is not None else set()
        self.text_parts: list[str] = []
        self._text_block_buf = ""
        self.thinking_parts: list[str] = []
        self.turn_tool_calls: list[dict] = []
        self.tool_by_id: dict[str, dict] = {}
        self.usage: dict = {}
        self._saw_proxy_event = False
        self._done_seen = False
        self._done_at = 0.0
        self._last_event_at = 0.0
        self._awaiting_tool_followup = False
        self._turn_callback_sent = False

    def run(self, abort_event=None):
        from core.llm_client import LLMResponse

        started_at = time.time()
        while True:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("antigravity-interactive aborted")
            event = self.tail.wait_event(timeout=0.05 if self._done_seen else 0.25)
            if not event:
                if not self._saw_proxy_event:
                    waited = time.time() - started_at
                    if waited >= _NO_PROXY_EVENT_TIMEOUT_SECONDS:
                        raise RuntimeError(
                            "Antigravity interactive produced no observed proxy "
                            "events after tmux prompt submit")
                if self._done_seen and time.time() - self._done_at >= _POST_DONE_IDLE_DRAIN_SECONDS:
                    self._emit_turn_callback()
                    break
                if (not self._done_seen and self._last_event_at
                        and time.time() - self._last_event_at >= _NO_DONE_IDLE_DRAIN_SECONDS):
                    self._emit_turn_callback()
                    break
                continue
            if self.touch_callback:
                self.touch_callback()
            if not self._handle_event(event):
                continue

        text = "".join(self.text_parts)
        thinking = "".join(self.thinking_parts)
        return LLMResponse(
            content=text,
            tool_calls=[],
            tokens_in=int(self.usage.get("input_tokens", 0) or 0),
            tokens_out=int(self.usage.get("output_tokens", 0) or 0),
            total_tokens=(int(self.usage.get("input_tokens", 0) or 0)
                          + int(self.usage.get("output_tokens", 0) or 0)),
            thinking=thinking,
            raw={"provider": "antigravity-interactive", "usage": self.usage},
        )

    def _handle_event(self, event: dict) -> bool:
        etype = event.get("type", "")
        if etype not in {"ag_text_delta", "ag_model_delta"}:
            return False
        self._saw_proxy_event = True
        self._last_event_at = time.time()
        if event.get("usage") and isinstance(event.get("usage"), dict):
            self.usage.update(event["usage"])
        thinking = event.get("thinking", "") or "".join(event.get("thinking_texts") or [])
        if thinking:
            self._flush_text_block()
            self.thinking_parts.append(thinking)
            if self.thinking_callback:
                self.thinking_callback(thinking)
            if self.block_callback:
                self.block_callback("thinking_content", {"text": thinking})
        text = event.get("text", "") or "".join(event.get("texts") or [])
        if text:
            self.text_parts.append(text)
            self._text_block_buf += text
            if self._awaiting_tool_followup:
                self._awaiting_tool_followup = False
            if self.callback:
                self.callback(text)
        tool_calls = event.get("tool_calls") or []
        if tool_calls:
            self._flush_text_block()
        for tc in tool_calls:
            if isinstance(tc, dict):
                self._emit_tool_use(tc)
        tool_results = event.get("tool_results") or []
        if tool_results:
            self._flush_text_block()
        for tr in tool_results:
            if isinstance(tr, dict):
                self._emit_tool_result(tr)
        if event.get("done") or event.get("finish_reason"):
            self._flush_text_block()
        if text:
            if not tool_calls and self._awaiting_tool_followup:
                self._awaiting_tool_followup = False
        if event.get("done") or (event.get("finish_reason") and not self._awaiting_tool_followup):
            self._done_seen = True
            self._done_at = time.time()
        return True

    def _flush_text_block(self) -> None:
        if not self.block_callback:
            return
        text = self._text_block_buf
        self._text_block_buf = ""
        if text.strip():
            self.block_callback("text", {"text": text})

    def _emit_tool_use(self, tc: dict) -> None:
        tc_id = str(tc.get("id") or tc.get("tool_call_id") or f"ag_{uuid.uuid4().hex[:12]}")
        if tc_id in self.emitted_tool_use_ids:
            return
        self.emitted_tool_use_ids.add(tc_id)
        name = str(tc.get("name") or tc.get("tool") or "")
        args = tc.get("arguments") or tc.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        entry = {"id": tc_id, "name": name, "arguments": args}
        self.tool_by_id[tc_id] = entry
        self.turn_tool_calls.append(dict(entry))
        self._awaiting_tool_followup = True
        if self.block_callback:
            self.block_callback("tool_use", entry)

    def _emit_tool_result(self, tr: dict) -> None:
        raw_tc_id = str(tr.get("tool_use_id") or tr.get("tool_call_id") or tr.get("id") or "")
        name = str(tr.get("name") or tr.get("tool") or "")
        tc_id = raw_tc_id
        if not tc_id or tc_id not in self.tool_by_id:
            matches = [
                tc for tc in self.turn_tool_calls
                if not tc.get("result") and (not name or tc.get("name") == name)
            ]
            if len(matches) == 1:
                tc_id = str(matches[0].get("id") or "")
        if not tc_id:
            return
        dedupe_id = raw_tc_id if raw_tc_id and raw_tc_id in self.tool_by_id else tc_id
        if dedupe_id in self.emitted_tool_result_ids:
            return
        self.emitted_tool_result_ids.add(dedupe_id)
        self.emitted_tool_result_ids.add(tc_id)
        result = tr.get("content") or tr.get("result") or tr.get("response") or "(no output)"
        for tc in self.turn_tool_calls:
            if tc.get("id") == tc_id:
                tc["result"] = result
                break
        if self.block_callback:
            tool = (self.tool_by_id.get(tc_id) or {}).get("name", "") or name
            self.block_callback("tool_result", {"tc_id": tc_id, "tool": tool, "result": result})

    def _emit_turn_callback(self) -> None:
        if self._turn_callback_sent or not self.turn_callback:
            return
        # Antigravity SSE text events are token/chunk deltas, not semantic
        # message blocks. With live block_callback, flush buffered text only at
        # semantic boundaries and do not duplicate it at the final turn callback.
        if self.block_callback:
            self._flush_text_block()
            text = ""
            thinking = ""
            tool_calls = []
        else:
            text = "".join(self.text_parts).strip()
            thinking = "".join(self.thinking_parts)
            tool_calls = [dict(tc) for tc in self.turn_tool_calls]
        if thinking and tool_calls:
            tool_calls[0]["thinking"] = thinking
        if text or thinking or tool_calls:
            self.turn_callback(text, tool_calls, thinking)
        self._turn_callback_sent = True


class LLMAntigravityInteractiveMixin(ClaudeCodeSessionMixin):
    """Antigravity CLI interactive provider."""

    def _stream_antigravity_interactive(
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
            raise LLMClientError("antigravity-interactive requires user_id, conversation_id and agent_name")

        pool = AntigravityObserverPool.instance()
        state = pool.ensure_started(self, model or "", user_id, conversation_id, agent_name)
        pool.touch(state)
        self._agi_active_user_id = user_id
        self._agi_active_conversation_id = conversation_id
        self._agi_active_agent_name = agent_name
        self._agi_active_service_id = getattr(state, "service_id", "") or getattr(self, "_agent_service", "") or ""
        prompt = self._agi_prompt(
            messages, tools, state.workdir, state.container_workdir,
            user_id, conversation_id,
            initial_context=not state.initial_context_loaded,
            agent_name=agent_name)
        offset = self._agi_log_offset(state.log_path)
        logger.info(
            "[antigravity-interactive] prompt submit conv=%s agent=%s service=%s initial=%s prompt_bytes=%d log_offset=%d log=%s",
            conversation_id[:8], agent_name, self._agi_active_service_id,
            not state.initial_context_loaded, len(prompt.encode("utf-8")),
            offset, state.log_path)
        pool.suspend_manual_ingest(state)
        try:
            if not pool.send_text(state, prompt):
                detail = getattr(state, "last_error", "") or "unknown tmux error"
                raise LLMClientError(
                    "Failed to paste prompt into Antigravity interactive tmux session: "
                    f"{detail}")
            state.initial_context_loaded = True
            coord = _AntigravityTurnCoordinator(
                state.log_path, offset=offset, callback=callback,
                thinking_callback=thinking_callback, block_callback=block_callback,
                turn_callback=turn_callback, touch_callback=lambda: pool.touch(state),
                emitted_tool_use_ids=state.emitted_tool_use_ids,
                emitted_tool_result_ids=state.emitted_tool_result_ids)
            response = coord.run(getattr(self, "_abort", None))
        finally:
            pool.resume_manual_ingest(state)
        response.model = model or self.default_model
        logger.info(
            "[antigravity-interactive] response complete conv=%s agent=%s text=%d thinking=%d tokens_in=%d tokens_out=%d",
            conversation_id[:8], agent_name, len(response.content or ""),
            len(response.thinking or ""), response.tokens_in, response.tokens_out)
        return response

    def _agi_prompt(self, messages, tools, workdir: str, container_workdir: str,
                    user_id: str, conversation_id: str,
                    initial_context: bool = False, agent_name: str = "") -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        if tools:
            system_prompt = append_cli_mcp_system_prompt(system_prompt)
        image_lines = self._agi_materialize_images(
            messages, workdir, container_workdir, user_id, conversation_id)
        parts = []
        if initial_context:
            parts.append(self._build_cli_initial_context_prompt(
                messages,
                system_prompt=system_prompt,
                user_text=user_text,
                workdir=workdir,
                provider_workdir=container_workdir,
                rel_path=".pawflow_ag/initial_context.md",
            ))
        if image_lines:
            parts.append("Attachments:\n" + "\n".join(image_lines))
        if not initial_context:
            catchup = self._agi_catchup_context(conversation_id, agent_name)
            if catchup:
                parts.append(catchup)
            current = self._agi_live_text(messages) or user_text
            if current:
                parts.append(current)
        return "\n\n".join(parts).strip() + "\n"

    def _agi_catchup_context(self, conversation_id: str, agent_name: str = "") -> str:
        agent = agent_name or getattr(self, "_agi_active_agent_name", "") or getattr(self, "_agent_name", "") or ""
        if not conversation_id or not agent:
            return ""
        builder = getattr(self, "_build_catchup_context", None)
        if builder is None:
            return ""
        return builder(conversation_id, agent) or ""

    @staticmethod
    def _agi_log_offset(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _agi_live_text(self, messages) -> str:
        from core.llm_providers.cli_shared import textualize_message

        for msg in reversed(messages or []):
            if getattr(msg, "role", "") != "user":
                continue
            rendered = textualize_message(msg)
            return rendered.strip() if isinstance(rendered, str) else ""
        return ""

    def _agi_materialize_images(self, messages, workdir: str, container_workdir: str,
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

    def _agi_session_state(self, *, user_id: str = "", conversation_id: str = "",
                           agent_name: str = ""):
        pool = AntigravityObserverPool.instance()
        uid = (user_id or getattr(self, "_agi_active_user_id", "")
               or getattr(self, "_user_id", "") or "")
        cid = (conversation_id or getattr(self, "_agi_active_conversation_id", "")
               or getattr(self, "_conversation_id", "") or "")
        agent = (agent_name or getattr(self, "_agi_active_agent_name", "")
                 or getattr(self, "_agent_name", "") or "")
        service_id = (getattr(self, "_agi_active_service_id", "")
                      or getattr(self, "_agent_service", "") or "")
        if not uid or not cid or not agent:
            return None
        return pool.find_session(uid, cid, agent, service_id)

    def _agi_send_user_message(self, text: str, attachments: list = None, **kwargs):
        state = self._agi_session_state(
            user_id=kwargs.get("user_id") or "",
            conversation_id=kwargs.get("conversation_id") or "",
            agent_name=kwargs.get("agent_name") or "",
        )
        if not state:
            return False
        prompt = self._agi_preempt_prompt(
            text, attachments or [], state, kwargs.get("user_id") or "",
            kwargs.get("conversation_id") or "", kwargs.get("agent_name") or "")
        return AntigravityObserverPool.instance().send_interrupt(state, prompt)

    def interrupt_antigravity_interactive(
        self, text: str, *, callback=None, thinking_callback=None,
        turn_callback=None, block_callback=None, user_id: str = "",
        conversation_id: str = "", agent_name: str = "", model: str = "",
    ):
        from core.llm_client import LLMClientError

        state = self._agi_session_state(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        if not state:
            raise LLMClientError("No active Antigravity interactive session for interrupt")
        pool = AntigravityObserverPool.instance()
        pool.touch(state)
        offset = self._agi_log_offset(state.log_path)
        pool.suspend_manual_ingest(state)
        try:
            if not pool.send_interrupt(state, text):
                detail = getattr(state, "last_error", "") or "unknown tmux error"
                raise LLMClientError(
                    "Failed to send interrupt to Antigravity interactive tmux session: "
                    f"{detail}")
            coord = _AntigravityTurnCoordinator(
                state.log_path, offset=offset, callback=callback,
                thinking_callback=thinking_callback, block_callback=block_callback,
                turn_callback=turn_callback, touch_callback=lambda: pool.touch(state),
                emitted_tool_use_ids=state.emitted_tool_use_ids,
                emitted_tool_result_ids=state.emitted_tool_result_ids)
            response = coord.run(getattr(self, "_abort", None))
        finally:
            pool.resume_manual_ingest(state)
        response.model = model or self.default_model
        return response

    def _agi_preempt_prompt(self, text: str, attachments: list, state,
                            user_id: str, conversation_id: str,
                            agent_name: str = "") -> str:
        catchup = self._agi_catchup_context(conversation_id, agent_name)
        if not attachments:
            return "\n\n".join(part for part in (catchup, text) if part).strip()
        from core.llm_client import LLMMessage

        parts = []
        if (text or "").strip():
            parts.append({"type": "text", "text": text})
        for att in attachments:
            block = self._agi_attachment_block(att, user_id, conversation_id) if isinstance(att, dict) else None
            if block:
                parts.append(block)
        if len(parts) <= 1:
            return text
        msg = LLMMessage(role="user", content=parts, conversation_id=conversation_id)
        return self._agi_prompt(
            [msg], None, state.workdir, state.container_workdir,
            user_id, conversation_id, initial_context=False, agent_name=agent_name)

    @staticmethod
    def _agi_attachment_block(att: dict, user_id: str, conversation_id: str):
        mime = str(att.get("mime_type") or "")
        filename = att.get("filename") or "image"
        file_id = att.get("file_id") or ""
        url = att.get("url") or ""
        if not file_id and isinstance(url, str) and url.startswith("fs://filestore/"):
            file_id = url[len("fs://filestore/"):].split("/", 1)[0]
        if file_id:
            from core.file_store import FileStore
            stored_name, _data, stored_mime = FileStore.instance().get_required(
                file_id, user_id=user_id, conversation_id=conversation_id)
            mime = mime or stored_mime or "application/octet-stream"
            filename = filename or stored_name
            if not str(mime).startswith("image/"):
                return None
            return {"type": "image_ref", "file_id": file_id,
                    "filename": filename, "mime_type": mime}
        data = att.get("data") or att.get("dataUrl") or ""
        if isinstance(data, str) and str(mime).startswith("image/"):
            if not data.startswith("data:"):
                data = f"data:{mime};base64,{data}"
            return {"type": "image", "data": data, "mime_type": mime}
        return None

    def cancel_antigravity_interactive(self, force: bool = False):
        if not force:
            return False
        state = self._agi_session_state()
        if not state:
            return False
        return AntigravityObserverPool.instance().force_stop(state)

