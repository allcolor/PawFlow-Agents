"""LLM provider mixin -- Gemini CLI via Agent Client Protocol (ACP).

Gemini's old PawFlow provider used headless ``gemini -p`` stream-json. That
transport could not carry user image attachments as native vision input. The
provider now speaks ACP over stdio (``gemini --acp``), which gives PawFlow a
structured prompt channel with native image blocks, session cancellation, MCP
servers, tool updates, and thought chunks.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.llm_providers.gemini_session import GeminiSessionMixin, _get_sessions_base

logger = logging.getLogger(__name__)


class _GeminiAcpProtocolError(Exception):
    """Raised when Gemini ACP returns an invalid JSON-RPC response."""


class LLMGeminiMixin(GeminiSessionMixin):
    """Gemini ACP provider.

    The process speaks JSON-RPC over stdio. PawFlow starts one Gemini ACP
    process for the active turn, persists the ACP session id per conversation
    and agent, and sends images as ACP ``ContentBlock::Image`` values.
    """

    _GEMINI_PROVIDER = "gemini"

    def _gemini_context_window(self, model: str) -> int:
        """Return Gemini's effective context window for ``model``."""
        runtime_windows = getattr(self, "_gemini_context_windows", None)
        if isinstance(runtime_windows, dict):
            for key in (model, (model or "").lower()):
                try:
                    value = int(runtime_windows.get(key, 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    return value

        cfg = getattr(self, "_config_ref", None) or {}
        try:
            value = int(cfg.get("max_context_size", 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            from core.llm_client import LLMClientError
            raise LLMClientError(
                "Gemini LLM service is missing required max_context_size")
        return value

    @staticmethod
    def _gemini_acp_effort(thinking_budget: int = 0,
                           configured_effort: str = "") -> str:
        """Map PawFlow service settings to Gemini thinking effort."""
        effort = (configured_effort or "").strip().lower()
        aliases = {
            "max": "high",
            "xhigh": "high",
            "extra": "high",
            "maximum": "high",
            "none": "minimal",
            "off": "minimal",
        }
        if effort:
            return aliases.get(effort, effort)
        try:
            budget = int(thinking_budget or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget >= 12000:
            return "high"
        if budget >= 4096:
            return "medium"
        if budget > 0:
            return "low"
        return "minimal"

    @staticmethod
    def _gemini_acp_budget(thinking_budget: int = 0, effort: str = "") -> int:
        """Return a Gemini 2.5 thinkingBudget for a PawFlow effort."""
        try:
            budget = int(thinking_budget or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget:
            return budget
        effort = (effort or "").lower()
        if effort == "high":
            return 16384
        if effort == "medium":
            return 8192
        if effort == "low":
            return 2048
        return 0

    @staticmethod
    def _gemini_acp_container_dir(workdir: str) -> str:
        """Return the session path as seen inside gemini_pool's namespace."""
        rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        parts = [part for part in rel.split("/") if part]
        if len(parts) < 3:
            raise ValueError(f"invalid gemini ACP workdir layout: {workdir}")
        # gemini_pool bind-mounts /cc_sessions/<user> over /cc_sessions.
        return "/cc_sessions/" + "/".join(parts[1:])

    @staticmethod
    def _gemini_acp_stale_session_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "session/load failed" in text and (
            "not found" in text or "unknown" in text or "no session" in text)

    @staticmethod
    def _gemini_acp_image_item(block: dict) -> Optional[dict]:
        """Convert a PawFlow/Claude-style image block to ACP image content."""
        if not isinstance(block, dict):
            return None
        source = block.get("source") or {}
        if source.get("type") != "base64":
            return None
        data = source.get("data") or ""
        if not data:
            return None
        return {
            "type": "image",
            "mimeType": source.get("media_type") or "image/png",
            "data": data,
        }

    @staticmethod
    def _gemini_acp_extract_images(messages, user_id: str,
                                   conversation_id: str) -> list:
        """Extract images from the last user message for ACP native vision."""
        if not user_id:
            raise ValueError(
                "_gemini_acp_extract_images: user_id is required to resolve image_ref attachments")
        if not conversation_id:
            raise ValueError(
                "_gemini_acp_extract_images: conversation_id is required to resolve image_ref attachments")
        image_blocks = []
        last_user_idx = -1
        for i, msg in enumerate(messages):
            if getattr(msg, "role", "") == "user" and isinstance(getattr(msg, "content", None), list):
                last_user_idx = i

        for idx, msg in enumerate(messages):
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                continue
            new_content = []
            for block in content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                btype = block.get("type", "")
                is_last_user = idx == last_user_idx

                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        if is_last_user:
                            try:
                                header, data_b64 = url.split(",", 1)
                                mime = header.split(":")[1].split(";")[0]
                                image_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime,
                                        "data": data_b64,
                                    },
                                })
                                logger.info("Extracted image for Gemini ACP vision: %s", mime)
                            except Exception as exc:
                                logger.warning("Failed to extract image: %s", exc)
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                if btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        if is_last_user:
                            image_blocks.append(block)
                            logger.info("Extracted image for Gemini ACP vision: %s",
                                        source.get("media_type", "?"))
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                if btype == "image_ref":
                    if is_last_user:
                        from core.file_store import FileStore
                        fid = block.get("file_id", "")
                        if not fid:
                            raise ValueError("image_ref block missing file_id - producer bug")
                        _fname, data, content_type = FileStore.instance().get_required(
                            fid, user_id=user_id, conversation_id=conversation_id)
                        image_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.get("mime_type", content_type),
                                "data": base64.b64encode(data).decode("ascii"),
                            },
                        })
                        logger.info("Loaded image from FileStore for Gemini ACP vision: %s (%d bytes)",
                                    fid, len(data))
                    else:
                        new_content.append({
                            "type": "text",
                            "text": f"[image: {block.get('filename', '?')}]",
                        })
                    continue

                new_content.append(block)
            msg.content = new_content

        return image_blocks

    @staticmethod
    def _gemini_acp_build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n" + system_prompt
            + "\n</system_instructions>\n\n" + user_text
        )

    def _gemini_acp_full_initial_text(self, messages) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        return self._gemini_acp_build_stdin_with_system(system_prompt, user_text)

    @staticmethod
    def _gemini_acp_last_user_text(messages) -> str:
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    return getattr(msg, "text_content", "") or ""
                return content or ""
        return ""

    def _gemini_pool_popen(self, workdir: str, cmd: list, **popen_kwargs) -> tuple:
        """Launch gemini inside a pool container via docker exec."""
        env = self._gemini_env(workdir)
        from core.gemini_pool import GeminiPool
        pool = GeminiPool.instance()
        container = pool.acquire()
        rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        session_dir = f"/cc_sessions/{rel}"
        extra = {}
        for key in (
            "GEMINI_API_KEY",
            "GEMINI_BASE_URL",
            "GOOGLE_API_KEY",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "NODE_TLS_REJECT_UNAUTHORIZED",
        ):
            if env.get(key):
                extra[key] = env[key]
        proc = pool.exec_gemini(
            container, session_dir, cmd,
            extra_env=extra or None,
            **popen_kwargs)
        return proc, container

    def _gemini_pool_release(self, container_name):
        if container_name:
            try:
                from core.gemini_pool import GeminiPool
                GeminiPool.instance().release(container_name)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

    def _gemini_send_user_message(self, text: str, attachments: list = None):
        """Cancel the active ACP prompt so PawFlow can reloop with new input."""
        active = getattr(self, "_gemini_acp_active", None)
        if not isinstance(active, dict):
            return False
        lock = self._gemini_acp_ensure_lock()
        with lock:
            entries = list(active.values())
        if not entries:
            return False
        cancelled = False
        for state in sorted(entries, key=lambda s: s.get("started_at", 0), reverse=True):
            proc = state.get("proc")
            session_id = state.get("session_id") or ""
            if not proc or proc.poll() is not None or not session_id:
                continue
            try:
                self._gemini_acp_notify(proc, "session/cancel", {"sessionId": session_id})
                cancelled = True
                logger.info("[gemini-acp] cancelled active session %s for preempt", session_id[:12])
                break
            except Exception as exc:
                logger.warning("[gemini-acp] session/cancel failed: %s", exc)
        return False if cancelled else False

    def cancel_gemini(self, force: bool = False):
        """Best-effort cancellation for the active Gemini ACP prompt."""
        self._gemini_send_user_message("", [])

    def _stream_gemini(
        self,
        messages,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 0,
        tools=None,
        callback=None,
        *,
        thinking_budget: int = 0,
        turn_callback=None,
        block_callback=None,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
    ):
        """Stream one Gemini ACP prompt into PawFlow callbacks."""
        from core.llm_client import LLMClientError, LLMResponse

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        is_ephemeral = bool(call_ephemeral_stream if call_ephemeral_stream is not None
                            else getattr(self, "_ephemeral_stream", False))
        model = model or "gemini-3-pro-preview"
        effort = self._gemini_acp_effort(thinking_budget, self._cfg("effort", ""))

        image_blocks = self._gemini_acp_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)
        full_context_text = self._gemini_acp_full_initial_text(messages)
        try:
            from core.token_counter import (
                count_messages_tokens as _count_msgs,
                resolve_token_multiplier as _resolve_mult,
            )
            mult = _resolve_mult(getattr(self, "_config_ref", None) or {})
            prompt_tokens = _count_msgs(
                [{"content": (m.content if hasattr(m, "content") else str(m))}
                 for m in messages],
                multiplier=mult)
        except Exception:
            prompt_tokens = int(len(full_context_text) / 3.5)
            logger.warning(
                "[gemini-acp] count_messages_tokens failed, fell back to chars/3.5 -> %d",
                prompt_tokens, exc_info=True)
        logger.info(
            "[gemini-acp] gauge: prompt_tokens=%d (msgs=%d, full_context=%d chars)",
            prompt_tokens, len(messages), len(full_context_text))

        store = None
        session_id = ""
        session_key = f"gemini_acp_session:{agent_name or 'default'}"
        pool_key = f"gemini_acp_pool_idx:{agent_name or 'default'}"
        if conv_id and not is_ephemeral:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                session_id = store.get_extra(conv_id, session_key) or ""
            except Exception:
                logger.debug("[gemini-acp] failed to restore session id", exc_info=True)

        initial_text = (self._gemini_acp_build_stdin_with_system(
            "", self._gemini_acp_last_user_text(messages)) if session_id else full_context_text)
        workdir = self._gemini_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._gemini_acp_container_dir(workdir)

        resume_pool_idx = -1
        if session_id and conv_id and store is not None:
            try:
                resume_pool_idx = int(store.get_extra(conv_id, pool_key) or -1)
            except Exception:
                logger.debug("[gemini-acp] failed to restore pool index", exc_info=True)

        self._gemini_setup_credentials(workdir, pool_index=resume_pool_idx)
        if conv_id and store is not None and hasattr(self, "_current_pool_index"):
            try:
                store.set_extra(conv_id, pool_key, self._current_pool_index)
            except Exception:
                logger.debug("[gemini-acp] failed to persist pool index", exc_info=True)
        self._gemini_acp_write_settings(workdir, model, effort, thinking_budget, temperature, max_tokens)
        mcp_servers, internal_token = self._gemini_acp_mcp_servers(
            user_id=user_id, conversation_id=conv_id, agent_name=agent_name)

        proc = None
        container = None
        stderr_lines: queue.Queue[str] = queue.Queue()
        active_key = (user_id, conv_id, agent_name, time.time())
        text_parts: List[str] = []
        turn_text_parts: List[str] = []
        thinking_parts: List[str] = []
        stream_uniq = f"geminiacp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        completed_tool_ids = set()
        usage_meta: Dict[str, Any] = {}

        def _flush_text():
            nonlocal turn_text_parts
            if not turn_text_parts:
                return
            text = "".join(turn_text_parts).strip()
            turn_text_parts = []
            if text and turn_callback:
                try:
                    if thinking_parts:
                        turn_callback(text, [], "".join(thinking_parts).strip())
                        thinking_parts.clear()
                    else:
                        turn_callback(text, [])
                except TypeError:
                    turn_callback(text, [])

        try:
            proc, container = self._gemini_acp_start_process(workdir)
            self._gemini_acp_start_stderr_drain(proc, stderr_lines)
            logger.info("[gemini-acp] started ACP conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, session_id[:12] or "new")

            init_result = self._gemini_acp_initialize(proc)
            supports_load = bool(
                ((init_result.get("agentCapabilities") or {}).get("loadSession")))
            if session_id and supports_load:
                try:
                    self._gemini_acp_load_session(proc, session_id, container_dir, mcp_servers)
                except Exception as exc:
                    if not self._gemini_acp_stale_session_error(exc):
                        raise
                    logger.warning(
                        "[gemini-acp] stale session id %s; starting new session",
                        session_id[:12])
                    if conv_id and store is not None and not is_ephemeral:
                        try:
                            store.set_extra(conv_id, session_key, "")
                        except Exception:
                            logger.debug("[gemini-acp] failed to clear stale session id", exc_info=True)
                    session_id = ""
                    initial_text = self._gemini_acp_full_initial_text(messages)
            elif session_id and not supports_load:
                session_id = ""
                initial_text = self._gemini_acp_full_initial_text(messages)

            if not session_id:
                result = self._gemini_acp_new_session(proc, container_dir, mcp_servers)
                session_id = (result or {}).get("sessionId", "")
            if session_id and conv_id and store is not None and not is_ephemeral:
                try:
                    store.set_extra(conv_id, session_key, session_id)
                except Exception:
                    logger.debug("[gemini-acp] failed to persist session id", exc_info=True)
            if not session_id:
                raise LLMClientError("gemini ACP did not return a session id")

            prompt = self._gemini_acp_prompt_items(initial_text, image_blocks)
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if not isinstance(active, dict):
                    active = {}
                    self._gemini_acp_active = active
                active[active_key] = {
                    "proc": proc,
                    "session_id": session_id,
                    "workdir": workdir,
                    "started_at": time.time(),
                }

            req_id = self._gemini_acp_next_id()
            self._gemini_acp_send(proc, {
                "jsonrpc": "2.0",
                "method": "session/prompt",
                "id": req_id,
                "params": {"sessionId": session_id, "prompt": prompt},
            })

            while True:
                msg = self._gemini_acp_read_message(proc)
                if msg is None:
                    raise _GeminiAcpProtocolError(
                        "gemini ACP exited before session/prompt completed")

                if msg.get("id") == req_id:
                    if msg.get("error"):
                        raise _GeminiAcpProtocolError(
                            f"session/prompt failed: {msg.get('error')}")
                    result = msg.get("result") or {}
                    usage_meta = result.get("_meta") or result.get("meta") or {}
                    stop_reason = result.get("stopReason") or "end_turn"
                    if stop_reason in ("cancelled", "canceled"):
                        break
                    if stop_reason not in ("end_turn", "stop", "max_tokens"):
                        logger.info("[gemini-acp] prompt stopped: %s", stop_reason)
                    break

                if "id" in msg and msg.get("method"):
                    self._gemini_acp_send(proc, {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {"code": -32601, "message": "client method not implemented"},
                    })
                    continue

                method = msg.get("method", "")
                params = msg.get("params", {}) or {}
                if method != "session/update":
                    continue
                update = params.get("update", {}) or {}
                kind = update.get("sessionUpdate") or ""

                if kind == "agent_message_chunk":
                    delta = self._gemini_acp_content_text(update.get("content"))
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        if callback:
                            callback(delta)
                    continue

                if kind == "agent_thought_chunk":
                    thought = self._gemini_acp_content_text(update.get("content"))
                    if thought:
                        thinking_parts.append(thought)
                    continue

                if kind == "tool_call":
                    if block_callback and turn_text_parts:
                        _flush_text()
                    raw_id = update.get("toolCallId") or uuid.uuid4().hex[:8]
                    tc_id = f"{stream_uniq}:{raw_id}"
                    raw_name = update.get("title") or update.get("kind") or "tool"
                    stream_tc_names[tc_id] = raw_name
                    raw_input = update.get("rawInput") or {}
                    if block_callback:
                        block_callback("tool_use", {
                            "id": tc_id,
                            "name": raw_name,
                            "arguments": raw_input,
                            "thinking": "".join(thinking_parts).strip(),
                        })
                        thinking_parts.clear()
                    continue

                if kind == "tool_call_update":
                    raw_id = update.get("toolCallId") or ""
                    tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                    status = update.get("status") or ""
                    if tc_id and status in ("completed", "failed", "cancelled", "canceled"):
                        result_text = self._gemini_acp_tool_result_text(update)
                        completed_tool_ids.add(tc_id)
                        if block_callback:
                            block_callback("tool_result", {
                                "tc_id": tc_id,
                                "tool": stream_tc_names.get(tc_id) or update.get("title") or "tool",
                                "result": result_text,
                            })
                    continue

            _flush_text()
            content = "".join(text_parts).strip()
            tokens_out = self._gemini_acp_output_tokens(usage_meta, content)
            return LLMResponse(
                content=content,
                model=model,
                tokens_in=max(0, int(prompt_tokens or 0)),
                tokens_out=max(0, int(tokens_out or 0)),
                finish_reason="stop",
                raw={"session_id": session_id, "tool_results": len(completed_tool_ids)},
                thinking="".join(thinking_parts).strip(),
            )
        except _GeminiAcpProtocolError as exc:
            raise LLMClientError(str(exc)) from exc
        finally:
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if isinstance(active, dict):
                    active.pop(active_key, None)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    logger.debug("[gemini-acp] terminate failed", exc_info=True)
            if internal_token:
                try:
                    from core.internal_auth import revoke_token
                    revoke_token(internal_token)
                except Exception:
                    logger.debug("[gemini-acp] internal token revoke failed", exc_info=True)
            try:
                self._gemini_recover_tokens(workdir)
            except Exception:
                logger.debug("[gemini-acp] token recovery failed", exc_info=True)
            self._gemini_pool_release(container)
            self._gemini_acp_log_stderr(stderr_lines)

    def _gemini_acp_start_process(self, workdir: str):
        try:
            return self._gemini_pool_popen(
                workdir,
                ["--yolo", "--acp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception:
            raise

    def _gemini_acp_ensure_lock(self):
        lock = getattr(self, "_gemini_acp_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._gemini_acp_lock = lock
        return lock

    def _gemini_acp_next_id(self) -> int:
        lock = self._gemini_acp_ensure_lock()
        with lock:
            value = int(getattr(self, "_gemini_acp_rpc_id", 0) or 0) + 1
            self._gemini_acp_rpc_id = value
            return value

    @staticmethod
    def _gemini_acp_send(proc, msg: Dict[str, Any]) -> None:
        if proc.stdin is None:
            raise _GeminiAcpProtocolError("gemini ACP stdin is closed")
        proc.stdin.write(json.dumps(msg, ensure_ascii=True) + "\n")
        proc.stdin.flush()

    def _gemini_acp_notify(self, proc, method: str, params: Optional[dict] = None) -> None:
        self._gemini_acp_send(proc, {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        })

    def _gemini_acp_request(self, proc, method: str, params: Optional[dict] = None) -> dict:
        req_id = self._gemini_acp_next_id()
        self._gemini_acp_send(proc, {
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id,
            "params": params or {},
        })
        while True:
            msg = self._gemini_acp_read_message(proc)
            if msg is None:
                raise _GeminiAcpProtocolError(
                    f"gemini ACP exited before response to {method}")
            if msg.get("id") != req_id:
                if "id" in msg and msg.get("method"):
                    self._gemini_acp_send(proc, {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {"code": -32601, "message": "client method not implemented"},
                    })
                continue
            if msg.get("error"):
                raise _GeminiAcpProtocolError(
                    f"{method} failed: {msg.get('error')}")
            return msg.get("result") or {}

    def _gemini_acp_initialize(self, proc) -> dict:
        return self._gemini_acp_request(proc, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {
                "name": "pawflow_gemini_acp",
                "title": "PawFlow Gemini ACP",
                "version": "1.0.0a1",
            },
        })

    def _gemini_acp_new_session(self, proc, container_dir: str, mcp_servers: list) -> dict:
        return self._gemini_acp_request(proc, "session/new", {
            "cwd": container_dir,
            "mcpServers": mcp_servers,
        })

    def _gemini_acp_load_session(self, proc, session_id: str,
                                 container_dir: str, mcp_servers: list) -> dict:
        return self._gemini_acp_request(proc, "session/load", {
            "sessionId": session_id,
            "cwd": container_dir,
            "mcpServers": mcp_servers,
        })

    @staticmethod
    def _gemini_acp_read_message(proc) -> Optional[dict]:
        if proc.stdout is None:
            return None
        while True:
            line = proc.stdout.readline()
            if line == "":
                return None
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[gemini-acp] ignored non-json stdout line: %s", line[:300])

    @staticmethod
    def _gemini_acp_start_stderr_drain(proc, sink: queue.Queue[str]) -> None:
        def _drain():
            try:
                if proc.stderr is None:
                    return
                for line in proc.stderr:
                    if line:
                        sink.put(line.rstrip("\n"))
            except Exception:
                pass
        threading.Thread(target=_drain, daemon=True, name="gemini-acp-stderr").start()

    @staticmethod
    def _gemini_acp_log_stderr(lines: queue.Queue[str]) -> None:
        buffered = []
        try:
            while len(buffered) < 20:
                buffered.append(lines.get_nowait())
        except queue.Empty:
            pass
        if buffered:
            logger.debug("[gemini-acp] stderr: %s", "\n".join(buffered[-20:]))

    def _gemini_acp_prompt_items(self, text: str, image_blocks: list) -> list:
        items = []
        if text:
            items.append({"type": "text", "text": text})
        for block in image_blocks or []:
            item = self._gemini_acp_image_item(block)
            if item:
                items.append(item)
        return items or [{"type": "text", "text": ""}]

    def _gemini_acp_mcp_servers(self, user_id: str = "",
                                conversation_id: str = "",
                                agent_name: str = "") -> tuple[list, str]:
        relay_url, relay_token = self._get_tool_relay_info()
        if relay_url:
            from core.docker_utils import get_host_ip
            host_ip = get_host_ip()
            relay_url = relay_url.replace("localhost", host_ip).replace("127.0.0.1", host_ip)
        else:
            logger.warning("No toolRelay service - Gemini ACP MCP bridge will have no tools")

        from core.internal_auth import mint_token
        internal_token = mint_token()
        server = {
            "name": "pawflow",
            "command": "/usr/bin/python3",
            "args": ["/opt/pawflow/mcp_bridge.py"],
            "env": [
                {"name": "PAWFLOW_TOOL_RELAY_URL", "value": relay_url or ""},
                {"name": "PAWFLOW_TOOL_RELAY_TOKEN", "value": relay_token or ""},
                {"name": "PAWFLOW_INTERNAL_TOKEN", "value": internal_token},
                {"name": "PAWFLOW_USER_ID", "value": user_id or ""},
                {"name": "PAWFLOW_CONVERSATION_ID", "value": conversation_id or ""},
                {"name": "PAWFLOW_AGENT_NAME", "value": agent_name or ""},
            ],
        }
        return [server], internal_token

    def _gemini_acp_write_settings(self, workdir: str, model: str, effort: str,
                                   thinking_budget: int, temperature: float,
                                   max_tokens: int) -> None:
        """Write Gemini settings for auth, model selection and thoughts."""
        gemini_home = os.path.join(workdir, ".gemini")
        os.makedirs(gemini_home, exist_ok=True)
        settings_path = os.path.join(gemini_home, "settings.json")
        model = model or "gemini-3-pro-preview"
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "thinkingConfig": {"includeThoughts": True},
        }
        effort = self._gemini_acp_effort(thinking_budget, effort)
        model_l = model.lower()
        if "2.5" in model_l:
            generation_config["thinkingConfig"]["thinkingBudget"] = self._gemini_acp_budget(
                thinking_budget, effort)
        else:
            generation_config["thinkingConfig"]["thinkingLevel"] = effort.upper()
        if max_tokens and max_tokens > 0:
            generation_config["maxOutputTokens"] = int(max_tokens)

        settings: Dict[str, Any] = {
            "security": {"auth": {}},
            "ui": {"inlineThinkingMode": "full"},
            "model": {"name": "pawflow-current"},
            "modelConfigs": {
                "aliases": {
                    "pawflow-current": {
                        "modelConfig": {
                            "model": model,
                            "generateContentConfig": generation_config,
                        }
                    }
                }
            },
        }
        api_key = getattr(self, "api_key", "")
        if callable(api_key):
            api_key = api_key()
        elif isinstance(api_key, property):
            api_key = ""
        if not api_key:
            settings["security"]["auth"]["selectedType"] = "oauth-personal"
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.chmod(settings_path, 0o600)
        logger.info("[gemini-acp] settings.json written: %s model=%s effort=%s",
                    settings_path, model, effort)

    @staticmethod
    def _gemini_acp_content_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            ctype = content.get("type")
            if ctype == "text":
                return content.get("text", "") or ""
            if ctype == "content":
                return LLMGeminiMixin._gemini_acp_content_text(content.get("content"))
            if ctype == "resource" and isinstance(content.get("resource"), dict):
                resource = content.get("resource") or {}
                return resource.get("text") or ""
            if "text" in content:
                return str(content.get("text") or "")
            return ""
        if isinstance(content, list):
            return "\n".join(
                part for part in (LLMGeminiMixin._gemini_acp_content_text(p) for p in content)
                if part)
        return str(content)

    @staticmethod
    def _gemini_acp_tool_result_text(update: dict) -> str:
        parts = []
        for item in update.get("content") or []:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            if item.get("type") == "content":
                text = LLMGeminiMixin._gemini_acp_content_text(item.get("content"))
                if text:
                    parts.append(text)
            elif item.get("type") == "diff":
                path = item.get("path") or ""
                parts.append(f"diff: {path}" if path else "diff")
            else:
                text = LLMGeminiMixin._gemini_acp_content_text(item)
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
        if update.get("rawOutput") is not None:
            return json.dumps(update.get("rawOutput"), ensure_ascii=False, default=str)
        return update.get("status") or ""

    @staticmethod
    def _gemini_acp_output_tokens(meta: dict, content: str) -> int:
        quota = (meta or {}).get("quota") or {}
        token_count = quota.get("token_count") or quota.get("tokenCount") or {}
        for key in ("candidatesTokenCount", "candidates_token_count", "outputTokens", "output_tokens"):
            try:
                value = int(token_count.get(key, 0) or 0)
            except (TypeError, ValueError, AttributeError):
                value = 0
            if value > 0:
                return value
        try:
            total = int(token_count.get("totalTokenCount", 0) or token_count.get("total_token_count", 0) or 0)
        except (TypeError, ValueError, AttributeError):
            total = 0
        if total > 0:
            return total
        return len(content or "") // 4
