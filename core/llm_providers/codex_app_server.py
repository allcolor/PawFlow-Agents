"""LLM provider mixin -- Codex app-server JSON-RPC.

This is the supported Codex provider surface for PawFlow. It uses
``codex app-server`` for Codex's richer client protocol: native image input,
MCP image results, and turn steering.
"""

import base64
import json
import logging
import mimetypes
import os
import queue
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.llm_providers.codex_session import CodexSessionMixin, _get_sessions_base

logger = logging.getLogger(__name__)


class _CodexAppServerProtocolError(Exception):
    """Raised when codex app-server returns an invalid JSON-RPC response."""


class LLMCodexAppServerMixin(CodexSessionMixin):
    """Codex app-server provider.

    The app-server process speaks JSON-RPC over stdio. PawFlow starts one
    app-server process for the active turn, persists the Codex thread id in the
    conversation store, and uses ``turn/steer`` when a user message arrives while
    the turn is still running.
    """

    _CODEX_APP_PROVIDER = "codex-app-server"

    @staticmethod
    def _codex_app_effort(thinking_budget: int = 0, configured_effort: str = "") -> str:
        """Map PawFlow thinking budget/config to app-server effort."""
        effort = (configured_effort or "").strip().lower()
        aliases = {"max": "xhigh", "extra": "xhigh", "maximum": "xhigh"}
        if effort:
            return aliases.get(effort, effort)
        try:
            budget = int(thinking_budget or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget >= 20000:
            return "xhigh"
        if budget >= 10000:
            return "high"
        if budget >= 5000:
            return "medium"
        return "low"

    @staticmethod
    def _codex_app_reasoning_summary(effort: str) -> str:
        """Request visible reasoning summaries when reasoning is enabled."""
        return "none" if effort == "low" else "auto"

    def _codex_app_context_window(self, model: str) -> int:
        """Return Codex app-server's effective context window for `model`."""
        runtime_windows = getattr(self, "_codex_context_windows", None)
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
                "Codex app-server LLM service is missing required max_context_size")
        return value

    def _codex_pool_popen(self, workdir: str, cmd: list,
                          container_name: str = "", **popen_kwargs) -> tuple:
        """Launch codex inside a pool container via docker exec."""
        _env = self._codex_env(workdir)
        from core.codex_pool import CodexPool
        pool = CodexPool.instance()
        container = container_name or pool.acquire()
        _rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        _session_dir = f"/cc_sessions/{_rel}"
        _extra = {}
        if _env.get("CODEX_API_KEY"):
            _extra["CODEX_API_KEY"] = _env["CODEX_API_KEY"]
        if _env.get("OPENAI_API_KEY"):
            _extra["OPENAI_API_KEY"] = _env["OPENAI_API_KEY"]
        if _env.get("OPENAI_BASE_URL"):
            _extra["OPENAI_BASE_URL"] = _env["OPENAI_BASE_URL"]
        if _env.get("NODE_TLS_REJECT_UNAUTHORIZED"):
            _extra["NODE_TLS_REJECT_UNAUTHORIZED"] = _env["NODE_TLS_REJECT_UNAUTHORIZED"]
        proc = pool.exec_codex(
            container, _session_dir, cmd,
            extra_env=_extra or None,
            **popen_kwargs)
        return proc, container

    def _codex_pool_release(self, container_name):
        """Release a pool container slot."""
        if container_name:
            try:
                from core.codex_pool import CodexPool
                CodexPool.instance().release(container_name)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

    @staticmethod
    def _codex_app_extract_images(messages, user_id: str, conversation_id: str) -> list:
        """Extract images from the last user message for native vision."""
        if not user_id:
            raise ValueError(
                "_codex_app_extract_images: user_id is required to resolve image_ref attachments")
        if not conversation_id:
            raise ValueError(
                "_codex_app_extract_images: conversation_id is required to resolve image_ref attachments")
        image_blocks = []
        last_user_idx = -1
        for i, m in enumerate(messages):
            if m.role == "user" and isinstance(m.content, list):
                last_user_idx = i

        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            new_content = []
            for block in m.content:
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
                                logger.info("Extracted image for vision: %s (%d chars b64)",
                                            mime, len(data_b64))
                            except Exception as e:
                                logger.warning("Failed to extract image: %s", e)
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        if is_last_user:
                            image_blocks.append(block)
                            logger.info("Extracted image for vision: %s",
                                        source.get("media_type", "?"))
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image_ref":
                    if is_last_user:
                        from core.file_store import FileStore
                        fid = block.get("file_id", "")
                        if not fid:
                            raise ValueError("image_ref block missing file_id — producer bug")
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
                        logger.info("Loaded image from FileStore for vision: %s (%d bytes)",
                                    fid, len(data))
                    else:
                        new_content.append({"type": "text", "text": f"[image: {block.get('filename', '?')}]"})
                    continue

                new_content.append(block)
            m.content = new_content

        return image_blocks

    @staticmethod
    def _codex_app_build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text for app-server text input."""
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n" + system_prompt
            + "\n</system_instructions>\n\n" + user_text
        )

    @staticmethod
    def _codex_app_container_dir(workdir: str) -> str:
        """Return the session path as seen inside codex_pool's mount namespace."""
        rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        parts = [part for part in rel.split("/") if part]
        if len(parts) < 3:
            raise ValueError(f"invalid codex app-server workdir layout: {workdir}")
        # codex_pool bind-mounts /cc_sessions/<user> over /cc_sessions, so the
        # process namespace starts at the conversation segment.
        return "/cc_sessions/" + "/".join(parts[1:])

    @staticmethod
    def _codex_app_missing_rollout_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "thread/resume failed" in text and "no rollout found" in text

    @staticmethod
    def _codex_app_rollout_path(workdir: str, thread_id: str) -> str:
        if not workdir or not thread_id:
            return ""
        sessions_dir = os.path.join(workdir, ".codex", "sessions")
        matches = []
        try:
            for root, _dirs, files in os.walk(sessions_dir):
                for name in files:
                    if name.endswith(".jsonl") and thread_id in name:
                        matches.append(os.path.join(root, name))
        except Exception:
            return ""
        if not matches:
            return ""
        try:
            return max(matches, key=os.path.getmtime)
        except Exception:
            return matches[-1]

    @staticmethod
    def _codex_app_payload_text(payload: dict) -> str:
        content = payload.get("content") if isinstance(payload, dict) else None
        if isinstance(content, str):
            return content
        parts = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("input_text") or part.get("output_text") or ""
                    if text:
                        parts.append(str(text))
        return "".join(parts)

    @classmethod
    def _codex_app_check_preempt_in_rollout(cls, jsonl_path: str, sent_texts: list) -> str:
        """Return done/pending/unread/unknown for steered user texts in Codex JSONL."""
        if not sent_texts or not jsonl_path:
            return "unknown"
        last_assistant_pos = -1
        found_flags = [False] * len(sent_texts)
        preempt_positions = [-1] * len(sent_texts)
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = entry.get("payload") if isinstance(entry, dict) else None
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "message":
                        continue
                    role = payload.get("role") or ""
                    if role == "assistant":
                        last_assistant_pos = i
                        continue
                    if role != "user":
                        continue
                    text_blob = cls._codex_app_payload_text(payload)
                    if not text_blob:
                        continue
                    for idx, sent in enumerate(sent_texts):
                        if sent and sent in text_blob:
                            found_flags[idx] = True
                            preempt_positions[idx] = i
        except OSError:
            return "unknown"
        if not any(found_flags):
            return "unread"
        for idx, pos in enumerate(preempt_positions):
            if found_flags[idx] and pos > last_assistant_pos:
                return "pending"
        if not all(found_flags):
            return "unread"
        return "done"

    def _codex_app_full_initial_text(self, messages) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        system_prompt = (
            self._CODEX_PAWFLOW_PREAMBLE
            + ("\n" + system_prompt if system_prompt else "")
        )
        return self._codex_app_build_stdin_with_system(system_prompt, user_text)

    def _codex_app_resume_text(self, messages) -> str:
        system_prompt = ""
        for msg in messages:
            if getattr(msg, "role", "") == "system":
                content = getattr(msg, "content", "")
                system_prompt = (
                    getattr(msg, "text_content", "")
                    if isinstance(content, list) else (content or ""))
                break
        system_prompt = (
            self._CODEX_PAWFLOW_PREAMBLE
            + ("\n" + system_prompt if system_prompt else "")
        )
        return self._codex_app_build_stdin_with_system(
            system_prompt, self._codex_app_last_user_text(messages))

    def _codex_app_send_user_message(self, text: str, attachments: list = None):
        """Preempt/steer entrypoint for active app-server turns.

        Unlike ``codex exec``, app-server can append input to an in-flight turn
        with ``turn/steer``. If no compatible active turn exists we return False
        and PawFlow's pending-message path will trigger the next normal turn.
        """
        active = getattr(self, "_codex_app_active", None)
        if not isinstance(active, dict):
            return False
        lock = getattr(self, "_codex_app_lock", None)
        if lock is None:
            return False
        with lock:
            # Prefer the most recent active turn. LLMClient instances are shared,
            # so keep this best-effort rather than relying on mutable self scope.
            entries = list(active.values())
            if not entries:
                return False
            state = sorted(entries, key=lambda s: s.get("started_at", 0))[-1]
            proc = state.get("proc")
            if not proc or proc.poll() is not None:
                return False
            thread_id = state.get("thread_id") or ""
            turn_id = state.get("turn_id") or ""
            if not thread_id or not turn_id:
                return False
            input_items = self._codex_app_input_items(
                text or "", [], state.get("workdir", ""), state.get("container_dir", ""),
            )
            if attachments:
                # Attachment shapes vary by UI path. Text steering is the main
                # contract; image steering is handled when attachments already
                # carry an app-server-compatible path or URL.
                input_items.extend(self._codex_app_attachment_items(attachments))
            if not input_items:
                return False
            try:
                self._codex_app_send(proc, {
                    "method": "turn/steer",
                    "id": self._codex_app_next_id(),
                    "params": {
                        "threadId": thread_id,
                        "expectedTurnId": turn_id,
                        "input": input_items,
                    },
                })
                self._codex_app_preempt_pending = int(
                    getattr(self, "_codex_app_preempt_pending", 0) or 0) + 1
                sent = list(getattr(self, "_codex_app_sent_preempt_texts", []) or [])
                sent.append(text or "")
                self._codex_app_sent_preempt_texts = sent
                logger.info(
                    "[codex-app] steered active turn %s (pending=%d)",
                    turn_id[:12], self._codex_app_preempt_pending)
                return True
            except Exception as exc:
                logger.warning("[codex-app] turn/steer failed: %s", exc)
                return False

    def _stream_codex_app_server(
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
        """Stream one Codex app-server turn into PawFlow callbacks."""
        from core.llm_client import LLMClientError, LLMResponse

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        is_ephemeral = bool(call_ephemeral_stream if call_ephemeral_stream is not None
                            else getattr(self, "_ephemeral_stream", False))
        model = (model or "").strip()
        effort = self._codex_app_effort(
            thinking_budget, self._cfg("effort", ""))
        reasoning_summary = self._codex_app_reasoning_summary(effort)

        # Mutates message content to remove image blocks from text history.
        image_blocks = self._codex_app_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        store = None
        thread_id = ""
        if conv_id and not is_ephemeral:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                thread_id = store.get_extra(
                    conv_id, f"codex_app_server_thread:{agent_name or 'default'}") or ""
            except Exception:
                logger.debug("[codex-app] failed to restore thread id", exc_info=True)

        def _estimate_prompt_tokens(text: str) -> int:
            try:
                from core.token_counter import (
                    count_messages_tokens as _count_msgs,
                    resolve_token_multiplier as _resolve_mult,
                )
                _mult = _resolve_mult(getattr(self, "_config_ref", None) or {})
                return _count_msgs([{"content": text or ""}], multiplier=_mult)
            except Exception:
                fallback = int(len(text or "") / 3.5)
                logger.warning(
                    "[codex-app] count_messages_tokens failed, fell back to chars/3.5 -> %d",
                    fallback, exc_info=True)
                return fallback

        if thread_id:
            prompt_mode = "resume"
            initial_text = self._codex_app_resume_text(messages)
        else:
            prompt_mode = "cold"
            initial_text = self._codex_app_full_initial_text(messages)
        prompt_tokens = _estimate_prompt_tokens(initial_text)
        logger.info(
            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
            prompt_tokens, prompt_mode, len(messages), len(initial_text))

        workdir = self._codex_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._codex_app_container_dir(workdir)

        resume_pool_idx = -1
        if thread_id and conv_id and store is not None:
            try:
                resume_pool_idx = int(store.get_extra(
                    conv_id, f"codex_app_pool_idx:{agent_name or 'default'}") or -1)
            except Exception:
                logger.debug("[codex-app] failed to restore pool index", exc_info=True)

        svc_id = getattr(self, "_agent_service", "") or ""
        live_reg = None
        live_key = None
        live_session = None
        owns_live_lock = False
        is_reuse = False
        reuse_container = ""
        internal_token = ""
        proc = None
        container = None
        stderr_lines: queue.Queue[str] = queue.Queue(maxsize=200)

        if conv_id and not is_ephemeral:
            try:
                from core.codex_live_registry import CodexLiveRegistry
                live_reg = CodexLiveRegistry.instance()
                live_reg.ensure_sweeper(
                    idle_ttl_seconds=int(getattr(self, "timeout", 1800) or 1800))
                live_key = (user_id, conv_id, agent_name or "default", svc_id,
                            int(resume_pool_idx))
                live_session = live_reg.get(live_key)
                if live_session is None:
                    logger.info(
                        "[codex-app-live] cold-start conv=%s agent=%s service=%s pool_idx=%s thread=%s",
                        conv_id[:8] or "?", agent_name or "default", svc_id or "default",
                        int(resume_pool_idx), thread_id[:12] or "new")
                if live_session is not None and not live_session.is_container_alive():
                    live_reg.evict(live_key, "dead_container")
                    live_session = None
                if live_session is not None:
                    live_session.turn_lock.acquire()
                    owns_live_lock = True
                    if live_session.is_process_alive():
                        live_reg.touch(live_key)
                        is_reuse = True
                        proc = live_session.proc
                        container = live_session.container_name
                        internal_token = live_session.mcp_internal_token or ""
                        thread_id = live_session.session_id or thread_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.info(
                            "[codex-app-live] REUSE conv=%s agent=%s session=%s reuse=%d",
                            conv_id[:8] or "?", agent_name, thread_id[:12],
                            live_session.reuse_count)
                    else:
                        reuse_container = live_session.container_name or ""
                        container = reuse_container
                        thread_id = live_session.session_id or thread_id
                        internal_token = live_session.mcp_internal_token or ""
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.warning(
                            "[codex-app-live] process dead but container alive; "
                            "restarting app-server in container=%s conv=%s agent=%s session=%s",
                            reuse_container, conv_id[:8] or "?", agent_name,
                            thread_id[:12] or "new")
            except Exception:
                logger.debug("[codex-app-live] lookup failed", exc_info=True)
                live_reg = None
                live_key = None

        if not is_reuse:
            self._codex_setup_credentials(workdir, pool_index=resume_pool_idx)
            if conv_id and store is not None and hasattr(self, "_current_pool_index"):
                try:
                    store.set_extra(conv_id, f"codex_app_pool_idx:{agent_name or 'default'}",
                                    self._current_pool_index)
                except Exception:
                    logger.debug("[codex-app] failed to persist pool index", exc_info=True)
            if live_reg is not None and conv_id and not is_ephemeral:
                try:
                    live_key = (user_id, conv_id, agent_name or "default", svc_id,
                                int(getattr(self, "_current_pool_index", resume_pool_idx)))
                except Exception:
                    live_key = None
            _, internal_token = self._codex_setup_mcp_config(
                workdir, user_id=user_id, conversation_id=conv_id, agent_name=agent_name)

        active_key = (user_id, conv_id, agent_name, time.time())
        text_parts: List[str] = []
        turn_text_parts: List[str] = []
        thinking_parts: List[str] = []
        stream_uniq = f"codexapp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        completed_tool_ids = set()
        self._had_preempts_this_turn = False
        self._codex_app_preempt_pending = 0
        self._codex_app_sent_preempt_texts = []

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

        def _append_final_reasoning(text: str) -> None:
            text = (text or "").strip()
            if not text:
                return
            existing = "".join(thinking_parts).strip()
            if existing and (text in existing or existing in text):
                return
            thinking_parts.append(text)

        turn_failed = False
        try:
            thread_key = f"codex_app_server_thread:{agent_name or 'default'}"
            if not is_reuse:
                proc, container = self._codex_pool_popen(
                    workdir,
                    ["app-server"],
                    container_name=reuse_container,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                self._codex_app_start_stderr_drain(proc, stderr_lines)
                logger.info("[codex-app] started app-server conv=%s agent=%s thread=%s",
                            conv_id[:8] or "?", agent_name, thread_id[:12] or "new")

                self._codex_app_initialize(proc)
                if thread_id:
                    try:
                        thread = self._codex_app_resume_thread(proc, thread_id, model)
                    except Exception as exc:
                        if not self._codex_app_missing_rollout_error(exc):
                            raise
                        logger.warning(
                            "[codex-app] stale thread id %s; starting a new thread", thread_id[:12])
                        if conv_id and store is not None and not is_ephemeral:
                            try:
                                store.set_extra(conv_id, thread_key, "")
                            except Exception:
                                logger.debug("[codex-app] failed to clear stale thread id", exc_info=True)
                        thread_id = ""
                        prompt_mode = "cold-after-stale-resume"
                        initial_text = self._codex_app_full_initial_text(messages)
                        prompt_tokens = _estimate_prompt_tokens(initial_text)
                        logger.info(
                            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
                            prompt_tokens, prompt_mode, len(messages), len(initial_text))
                        thread = self._codex_app_start_thread(proc, model, container_dir)
                else:
                    thread = self._codex_app_start_thread(proc, model, container_dir)
                thread_id = (thread or {}).get("id", "") or thread_id
                if thread_id and conv_id and store is not None and not is_ephemeral:
                    try:
                        store.set_extra(conv_id, thread_key, thread_id)
                    except Exception:
                        logger.debug("[codex-app] failed to persist thread id", exc_info=True)
            elif not thread_id:
                raise LLMClientError("codex app-server live session has no thread id")
            if not thread_id:
                raise LLMClientError("codex app-server did not return a thread id")

            input_items = self._codex_app_input_items(
                initial_text, image_blocks, workdir, container_dir)
            turn = self._codex_app_start_turn(
                proc, thread_id, input_items, model, container_dir,
                effort, reasoning_summary)
            turn_id = (turn or {}).get("id", "")

            lock = self._codex_app_ensure_lock()
            with lock:
                active = getattr(self, "_codex_app_active", None)
                if not isinstance(active, dict):
                    active = {}
                    self._codex_app_active = active
                active[active_key] = {
                    "proc": proc,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "workdir": workdir,
                    "container_dir": container_dir,
                    "started_at": time.time(),
                }

            # Surface LIVE while the app-server turn is running, not only
            # after the turn completes. Preempt already uses _codex_app_active;
            # the UI badge comes from CodexLiveRegistry.status().
            if live_reg is not None and live_key is not None and not is_ephemeral:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=thread_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=True,
                    )
                    logger.info(
                        "[codex-app-live] active conv=%s agent=%s session=%s turn=%s",
                        conv_id[:8] or "?", agent_name, thread_id[:12],
                        turn_id[:12])
                except Exception:
                    logger.debug("[codex-app-live] active register failed", exc_info=True)

            while True:
                msg = self._codex_app_read_message(proc)
                if msg is None:
                    break
                if "id" in msg:
                    # Late response to turn/steer or server request resolution.
                    if msg.get("error"):
                        raise _CodexAppServerProtocolError(str(msg.get("error")))
                    continue
                method = msg.get("method", "")
                params = msg.get("params", {}) or {}

                if method == "item/agentMessage/delta":
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        if callback:
                            callback(delta)
                    continue

                if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        thinking_parts.append(delta)
                    continue

                if method == "item/completed":
                    item = params.get("item", {}) or {}
                    if item.get("type") == "reasoning":
                        summary = item.get("summary") or []
                        content = item.get("content") or []
                        if isinstance(summary, (str, dict)):
                            summary = [summary]
                        if isinstance(content, (str, dict)):
                            content = [content]
                        for part in list(summary) + list(content):
                            if isinstance(part, dict):
                                text = part.get("text") or part.get("summary") or ""
                            else:
                                text = str(part or "")
                            _append_final_reasoning(text)
                        continue

                if method == "item/started":
                    item = params.get("item", {}) or {}
                    if item.get("type") == "mcpToolCall":
                        if block_callback and turn_text_parts:
                            _flush_text()
                        tc_id = f"{stream_uniq}:{item.get('id') or uuid.uuid4().hex[:8]}"
                        raw_name = item.get("tool") or "use_tool"
                        raw_args = item.get("arguments") or {}
                        stream_tc_names[tc_id] = raw_name
                        try:
                            from core.llm_client import unwrap_mcp_tool
                            from core.background_tool import enqueue_cc_tc, _args_hash
                            tc_name, tc_args = unwrap_mcp_tool(raw_name, raw_args)
                            enqueue_cc_tc(conv_id, agent_name, tc_id, tc_name, _args_hash(tc_args))
                        except Exception:
                            logger.debug("[codex-app] enqueue background tc skipped", exc_info=True)
                        if block_callback:
                            block_callback("tool_use", {
                                "id": tc_id,
                                "name": raw_name,
                                "arguments": raw_args,
                                "thinking": "".join(thinking_parts).strip(),
                            })
                            thinking_parts.clear()
                        continue

                if method == "item/completed":
                    item = params.get("item", {}) or {}
                    if item.get("type") == "mcpToolCall":
                        raw_id = item.get("id") or ""
                        tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                        if not tc_id:
                            continue
                        raw_name = stream_tc_names.get(tc_id) or item.get("tool") or ""
                        result_str = self._codex_app_result_text(item)
                        completed_tool_ids.add(tc_id)
                        if block_callback:
                            block_callback("tool_result", {
                                "tc_id": tc_id,
                                "tool": raw_name,
                                "result": result_str,
                            })
                        continue

                if method == "turn/completed":
                    turn = params.get("turn", {}) or {}
                    status = turn.get("status") or ""
                    err = turn.get("error")
                    if status in ("failed", "error") or err:
                        raise LLMClientError(
                            f"codex app-server turn failed: {err or status}")
                    if getattr(self, "_codex_app_preempt_pending", 0) > 0:
                        sent = list(getattr(self, "_codex_app_sent_preempt_texts", []) or [])
                        rollout = self._codex_app_rollout_path(workdir, thread_id)
                        pstatus = self._codex_app_check_preempt_in_rollout(rollout, sent)
                        deadline = time.time() + 3.0
                        while pstatus in ("unread", "unknown") and time.time() < deadline:
                            time.sleep(0.1)
                            pstatus = self._codex_app_check_preempt_in_rollout(rollout, sent)
                        if pstatus == "done":
                            self._had_preempts_this_turn = True
                            logger.info(
                                "[codex-app] turn completed; rollout shows %d preempt(s) answered inline",
                                len(sent))
                        else:
                            logger.info(
                                "[codex-app] turn completed; %d preempt(s) not proven answered in rollout (status=%s) - pending rescue will retrigger",
                                len(sent), pstatus)
                        self._codex_app_preempt_pending = 0
                        self._codex_app_sent_preempt_texts = []
                    break

                if method == "error":
                    raise LLMClientError(f"codex app-server error: {params.get('error') or params}")

            _flush_text()
            content = "".join(text_parts).strip()
            return LLMResponse(
                content=content,
                model=model,
                tokens_in=max(0, int(prompt_tokens or 0)),
                tokens_out=max(0, len(content) // 4),
                finish_reason="stop",
                raw={"thread_id": thread_id, "turn_id": turn_id,
                     "tool_results": len(completed_tool_ids)},
                thinking="".join(thinking_parts).strip(),
            )
        except _CodexAppServerProtocolError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except Exception:
            turn_failed = True
            raise
        finally:
            lock = self._codex_app_ensure_lock()
            with lock:
                active = getattr(self, "_codex_app_active", None)
                if isinstance(active, dict):
                    active.pop(active_key, None)
            try:
                self._codex_recover_tokens(workdir)
            except Exception:
                logger.debug("[codex-app] token recovery failed", exc_info=True)

            proc_alive = False
            if proc is not None:
                try:
                    proc_alive = proc.poll() is None
                except Exception:
                    proc_alive = False
            keep_alive = (
                not turn_failed
                and proc_alive
                and live_reg is not None
                and live_key is not None
                and bool(thread_id)
                and bool(container)
                and not is_ephemeral
            )
            if keep_alive:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=thread_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=False,
                    )
                    logger.info(
                        "[codex-app-live] keep-alive conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, thread_id[:12])
                except Exception:
                    logger.debug("[codex-app-live] register failed", exc_info=True)
                    keep_alive = False

            if not keep_alive:
                if live_reg is not None and live_key is not None:
                    try:
                        live_reg.evict(live_key, "app_server_teardown")
                    except Exception:
                        logger.debug("[codex-app-live] evict failed", exc_info=True)
                if proc is not None:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                    except Exception:
                        logger.debug("[codex-app] terminate failed", exc_info=True)
                if internal_token:
                    try:
                        from core.internal_auth import revoke_token
                        revoke_token(internal_token)
                    except Exception:
                        logger.debug("[codex-app] internal token revoke failed", exc_info=True)
                self._codex_pool_release(container)
            self._codex_app_log_stderr(stderr_lines)
            if owns_live_lock and live_session is not None:
                try:
                    live_session.turn_lock.release()
                except Exception:
                    logger.debug("[codex-app-live] turn lock release failed", exc_info=True)

    def _codex_app_ensure_lock(self):
        lock = getattr(self, "_codex_app_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._codex_app_lock = lock
        return lock

    def _codex_app_next_id(self) -> int:
        lock = self._codex_app_ensure_lock()
        with lock:
            value = int(getattr(self, "_codex_app_rpc_id", 0) or 0) + 1
            self._codex_app_rpc_id = value
            return value

    @staticmethod
    def _codex_app_send(proc, msg: Dict[str, Any]) -> None:
        if proc.stdin is None:
            raise _CodexAppServerProtocolError("codex app-server stdin is closed")
        proc.stdin.write(json.dumps(msg, ensure_ascii=True) + "\n")
        proc.stdin.flush()

    def _codex_app_request(self, proc, method: str, params: Optional[dict] = None) -> dict:
        req_id = self._codex_app_next_id()
        self._codex_app_send(proc, {"method": method, "id": req_id,
                                    "params": params or {}})
        while True:
            msg = self._codex_app_read_message(proc)
            if msg is None:
                raise _CodexAppServerProtocolError(
                    f"codex app-server exited before response to {method}")
            if msg.get("id") != req_id:
                continue
            if msg.get("error"):
                raise _CodexAppServerProtocolError(
                    f"{method} failed: {msg.get('error')}")
            return msg.get("result") or {}

    def _codex_app_initialize(self, proc) -> None:
        self._codex_app_request(proc, "initialize", {
            "clientInfo": {
                "name": "pawflow_codex_app_server",
                "title": "PawFlow Codex App Server",
                "version": "1.0.0a1",
            },
            "capabilities": {"experimentalApi": True},
        })
        self._codex_app_send(proc, {"method": "initialized", "params": {}})

    def _codex_app_start_thread(self, proc, model: str, container_dir: str) -> dict:
        params = {
            "cwd": container_dir,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "serviceName": "pawflow_codex_app_server",
        }
        model = (model or "").strip()
        if model:
            params["model"] = model
        result = self._codex_app_request(proc, "thread/start", params)
        return result.get("thread") or {}

    def _codex_app_resume_thread(self, proc, thread_id: str, model: str) -> dict:
        params = {"threadId": thread_id}
        model = (model or "").strip()
        if model:
            params["model"] = model
        result = self._codex_app_request(proc, "thread/resume", params)
        return result.get("thread") or {"id": thread_id}

    def _codex_app_start_turn(self, proc, thread_id: str, input_items: list,
                              model: str, container_dir: str,
                              effort: str, reasoning_summary: str) -> dict:
        params = {
            "threadId": thread_id,
            "input": input_items,
            "cwd": container_dir,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "effort": effort,
        }
        model = (model or "").strip()
        if model:
            params["model"] = model
        if reasoning_summary != "none":
            params["summary"] = reasoning_summary
        result = self._codex_app_request(proc, "turn/start", params)
        return result.get("turn") or {}

    @staticmethod
    def _codex_app_read_message(proc) -> Optional[dict]:
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
                logger.debug("[codex-app] ignored non-json stdout line: %s", line[:300])

    @staticmethod
    def _codex_app_start_stderr_drain(proc, sink: queue.Queue[str]) -> None:
        def _drain():
            try:
                if proc.stderr is None:
                    return
                for line in proc.stderr:
                    if line:
                        text = line.rstrip("\n")
                        try:
                            sink.put_nowait(text)
                        except queue.Full:
                            try:
                                sink.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                sink.put_nowait(text)
                            except queue.Full:
                                pass
            except Exception:
                pass
        threading.Thread(target=_drain, daemon=True, name="codex-app-stderr").start()

    @staticmethod
    def _codex_app_log_stderr(lines: queue.Queue[str]) -> None:
        buffered = []
        try:
            while len(buffered) < 20:
                buffered.append(lines.get_nowait())
        except queue.Empty:
            pass
        if buffered:
            logger.debug("[codex-app] stderr: %s", "\n".join(buffered[-20:]))

    @staticmethod
    def _codex_app_last_user_text(messages) -> str:
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    return getattr(msg, "text_content", "") or ""
                return content or ""
        return ""

    def _codex_app_input_items(self, text: str, image_blocks: list,
                               workdir: str, container_dir: str) -> list:
        items = []
        if text:
            items.append({"type": "text", "text": text})
        for block in image_blocks or []:
            item = self._codex_app_image_item(block, workdir, container_dir)
            if item:
                items.append(item)
        return items or [{"type": "text", "text": ""}]

    @staticmethod
    def _codex_app_attachment_items(attachments: list) -> list:
        items = []
        for attachment in attachments or []:
            if not isinstance(attachment, dict):
                continue
            url = attachment.get("url") or attachment.get("image_url") or ""
            path = attachment.get("path") or ""
            if url:
                items.append({"type": "image", "url": url})
            elif path:
                items.append({"type": "localImage", "path": path})
        return items

    @staticmethod
    def _codex_app_image_item(block: dict, workdir: str, container_dir: str) -> Optional[dict]:
        if not isinstance(block, dict):
            return None
        source = block.get("source") or {}
        if source.get("type") != "base64":
            return None
        data_b64 = source.get("data") or ""
        if not data_b64:
            return None
        mime = source.get("media_type") or "image/png"
        ext = mimetypes.guess_extension(mime) or ".png"
        if ext == ".jpe":
            ext = ".jpg"
        vision_dir = os.path.join(workdir, ".pawflow_vision")
        os.makedirs(vision_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        host_path = os.path.join(vision_dir, filename)
        with open(host_path, "wb") as f:
            f.write(base64.b64decode(data_b64))
        rel_name = f".pawflow_vision/{filename}"
        return {"type": "localImage", "path": f"{container_dir}/{rel_name}"}

    @staticmethod
    def _codex_app_result_text(item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        if item.get("error"):
            return str(item.get("error"))
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            parts = []
            for part in result["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            if parts:
                return "\n".join(p for p in parts if p)
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, default=str)
        if result is not None:
            return str(result)
        if item.get("status"):
            return str(item.get("status"))
        return ""
