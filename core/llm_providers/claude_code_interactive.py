"""LLM provider mixin -- Claude Code interactive via MITM-observed SSE.

This provider deliberately does not read Claude Code transcripts or tmux
output. tmux is only the input transport; output is assembled from the local
MITM proxy's copy of Anthropic SSE events.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
import uuid

from core.agent_prompt_policy import append_cli_mcp_system_prompt
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin

logger = logging.getLogger(__name__)

from core.llm_providers._cci_turn import (  # noqa: E402,F401 -- re-exported for back-compat
    _CCITurnCoordinator,
    _loads_tolerant,
    _event_tool_args,
    _env_seconds,
    _POST_STOP_IDLE_DRAIN_SECONDS,
    _NO_PROXY_EVENT_TIMEOUT_SECONDS,
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

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conversation_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or ""
        if not user_id or not conversation_id or not agent_name:
            raise LLMClientError("claude-code-interactive requires user_id, conversation_id and agent_name")

        pool = InteractiveClaudeCodePool.instance()
        state = pool.ensure_started(self, model or "", user_id, conversation_id, agent_name)
        pool.touch(state)
        self._cci_active_user_id = user_id
        self._cci_active_conversation_id = conversation_id
        self._cci_active_agent_name = agent_name
        self._cci_active_service_id = getattr(self, "_agent_service", "") or ""
        self._had_preempts_this_turn = False
        prompt = self._cci_prompt(
            messages, tools, state.workdir, state.container_workdir,
            user_id, conversation_id,
            initial_context=not state.initial_context_loaded,
            agent_name=agent_name)
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
            turn_callback=turn_callback, touch_callback=lambda: pool.touch(state),
            emitted_tool_use_ids=state.emitted_tool_use_ids,
            emitted_tool_result_ids=state.emitted_tool_result_ids)
        response = coord.run(getattr(self, "_abort", None))
        # Prefer the model resolved on the wire (message_start); fall back to
        # the configured alias (e.g. "best") then the provider default.
        response.model = response.model or model or self.default_model
        return response

    def interrupt_claude_code_interactive(
        self, text: str, *, callback=None, thinking_callback=None,
        turn_callback=None, block_callback=None, user_id: str = "",
        conversation_id: str = "", agent_name: str = "", model: str = "",
    ):
        from core.llm_client import LLMClientError, LLMResponse
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service

        state = self._cci_session_state(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        if not state:
            # No live session to interrupt. This happens when the interrupt
            # lands on a compact boundary: the provider compact already
            # invalidated (killed) the CCI session before the interrupt ran.
            # The session being gone is exactly what the interrupt wants, so
            # treat it as a completed no-op (force stop is never an error) and
            # let the agent loop drain any queued message.
            logger.info(
                "[cci-interrupt] no active session for %s/%s \u2014 already "
                "stopped (compact boundary), treating interrupt as no-op",
                conversation_id[:8], agent_name)
            return LLMResponse(content="", model=model or self.default_model)

        pool = InteractiveClaudeCodePool.instance()
        pool.touch(state)
        _, _, event_service = get_or_create_cc_interactive_event_service()
        event_service.drain_session(state.session_token)
        if not pool.send_interrupt(state, text):
            detail = getattr(state, "last_error", "") or "unknown tmux error"
            raise LLMClientError(
                "Failed to send interrupt to Claude Code interactive tmux session: "
                f"{detail}")

        coord = _CCITurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback, block_callback=block_callback,
            turn_callback=turn_callback, touch_callback=lambda: pool.touch(state),
            emitted_tool_use_ids=state.emitted_tool_use_ids,
            emitted_tool_result_ids=state.emitted_tool_result_ids)
        response = coord.run(getattr(self, "_abort", None))
        # Prefer the model resolved on the wire (message_start); fall back to
        # the configured alias (e.g. "best") then the provider default.
        response.model = response.model or model or self.default_model
        return response

    def _cci_prompt(self, messages, tools, workdir: str, container_workdir: str,
                    user_id: str, conversation_id: str,
                    initial_context: bool = False, agent_name: str = "") -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        if tools:
            system_prompt = append_cli_mcp_system_prompt(system_prompt)
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
        if image_lines:
            parts.append("Attachments:\n" + "\n".join(image_lines))
        if not initial_context:
            catchup = self._cci_catchup_context(conversation_id, agent_name)
            if catchup:
                parts.append(catchup)
            current = self._cci_live_text(messages) or user_text
            if current:
                parts.append(current)
        return "\n\n".join(parts).strip() + "\n"

    def _cci_catchup_context(self, conversation_id: str, agent_name: str = "") -> str:
        agent = agent_name or getattr(self, "_cci_active_agent_name", "") or getattr(self, "_agent_name", "") or ""
        if not conversation_id or not agent:
            return ""
        builder = getattr(self, "_build_catchup_context", None)
        if builder is None:
            return ""
        return builder(conversation_id, agent) or ""

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
                lines.append(
                    f"fs://filestore/{file_id}/{filename} -> "
                    f"@{container_workdir}/.pawflow_vision/{name}")
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
        user_id = kwargs.get("user_id") or ""
        conversation_id = kwargs.get("conversation_id") or ""
        agent_name = kwargs.get("agent_name") or ""
        prompt = self._cci_preempt_prompt(
            text, attachments or [], state, user_id, conversation_id, agent_name)
        ok = InteractiveClaudeCodePool.instance().send_interrupt(state, prompt)
        if ok:
            self._had_preempts_this_turn = True
        return ok

    def _cci_preempt_prompt(self, text: str, attachments: list,
                            state, user_id: str, conversation_id: str,
                            agent_name: str = "") -> str:
        catchup = self._cci_catchup_context(conversation_id, agent_name)
        if not attachments:
            return "\n\n".join(part for part in (catchup, text) if part).strip()
        from core.llm_client import LLMMessage

        parts = []
        if (text or "").strip():
            parts.append({"type": "text", "text": text})
        for att in attachments:
            if not isinstance(att, dict):
                continue
            block = self._cci_attachment_block(att, user_id, conversation_id)
            if block:
                parts.append(block)
        if len(parts) <= 1:
            return text
        msg = LLMMessage(role="user", content=parts,
                         conversation_id=conversation_id)
        return self._cci_prompt(
            [msg], None, state.workdir, state.container_workdir,
            user_id, conversation_id, initial_context=False,
            agent_name=agent_name)

    @staticmethod
    def _cci_attachment_block(att: dict, user_id: str, conversation_id: str):
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
            return {
                "type": "image_ref",
                "file_id": file_id,
                "filename": filename,
                "mime_type": mime,
            }
        data = att.get("data") or att.get("dataUrl") or ""
        if isinstance(data, str) and str(mime).startswith("image/"):
            if not data.startswith("data:"):
                data = f"data:{mime};base64,{data}"
            return {"type": "image", "data": data, "mime_type": mime}
        return None

    def cancel_claude_code_interactive(self, force: bool = False):
        if not force:
            return False
        state = self._cci_session_state()
        if not state:
            return False
        return InteractiveClaudeCodePool.instance().force_stop(state)
