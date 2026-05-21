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
import shutil
import subprocess  # nosec B404
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
        """Return Codex app-server's effective context budget for `model`."""
        cfg = getattr(self, "_config_ref", None) or {}
        try:
            configured = int(cfg.get("max_context_size", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        runtime_windows = getattr(self, "_codex_context_windows", None)
        real = 0
        if isinstance(runtime_windows, dict):
            for key in (model, (model or "").lower()):
                try:
                    value = int(runtime_windows.get(key, 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    real = value
                    break

        from core.context_window import effective_context_window
        value = effective_context_window(configured, real, fallback=0)
        if value <= 0:
            from core.llm_client import LLMClientError
            raise LLMClientError(
                "Codex app-server LLM service is missing required max_context_size")
        return value

    def _codex_pool_popen(self, workdir: str, cmd: list,
                          container_name: str = "", user_id: str = "",
                          conversation_id: str = "", agent_name: str = "",
                          **popen_kwargs) -> tuple:
        """Launch codex inside a pool container via docker exec."""
        _env = self._codex_env(workdir)
        from core.codex_pool import CodexPool
        from core.cli_workspace_mounts import (
            build_cli_workspace_mount_args, build_skill_mount_args,
        )
        pool = CodexPool.instance()
        workspace_mounts = [] if container_name else (
            build_cli_workspace_mount_args(
                conversation_id, agent_name, user_id=user_id)
            + build_skill_mount_args(
                conversation_id, agent_name, user_id=user_id))
        container = container_name or pool.acquire(workspace_mount_args=workspace_mounts)
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
                        if not user_id:
                            raise ValueError(
                                "_codex_app_extract_images: user_id is required "
                                "to resolve image_ref attachments")
                        if not conversation_id:
                            raise ValueError(
                                "_codex_app_extract_images: conversation_id is "
                                "required to resolve image_ref attachments")
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
        if not isinstance(payload, dict):
            return ""
        content = payload.get("content") if isinstance(payload, dict) else None
        direct_text = (payload.get("text") or payload.get("message")
                       or payload.get("output_text") or "")
        if isinstance(direct_text, str) and direct_text:
            return direct_text
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
    def _codex_app_rollout_line_count(cls, jsonl_path: str) -> int:
        if not jsonl_path:
            return 0
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    @classmethod
    def _codex_app_check_preempt_in_rollout(cls, jsonl_path: str, sent_texts: list) -> str:
        """Return done/unread/unknown for steered user texts in Codex JSONL.

        For app-server, seeing the steered user message in the rollout after
        the `turn/steer` write is the provider-level receipt proof. The model
        may or may not visibly answer that preempt, but PawFlow must not run a
        rescue turn once Codex has recorded the user item in the active rollout.
        """
        if not sent_texts or not jsonl_path:
            return "unknown"
        sent_records = []
        for item in sent_texts:
            if isinstance(item, dict):
                sent_records.append({
                    "text": str(item.get("text") or ""),
                    "after_line": max(0, int(item.get("after_line") or 0)),
                })
            else:
                sent_records.append({"text": str(item or ""), "after_line": 0})
        found_flags = [False] * len(sent_records)
        preempt_positions = [-1] * len(sent_records)
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
                    if role != "user":
                        continue
                    text_blob = cls._codex_app_payload_text(payload)
                    if not text_blob:
                        continue
                    for idx, sent_record in enumerate(sent_records):
                        if i < sent_record["after_line"]:
                            continue
                        sent = sent_record["text"]
                        if sent and sent in text_blob:
                            found_flags[idx] = True
                            preempt_positions[idx] = i
        except OSError:
            return "unknown"
        if not any(found_flags):
            return "unread"
        if not all(found_flags):
            return "unread"
        return "done"

    def _codex_app_full_initial_text(self, messages, workdir: str,
                                     container_dir: str) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        system_prompt = (
            self._CODEX_PAWFLOW_PREAMBLE
            + ("\n" + system_prompt if system_prompt else "")
        )
        prompt = self._build_cli_initial_context_prompt(
            messages,
            system_prompt=system_prompt,
            user_text=user_text,
            workdir=workdir,
            provider_workdir=container_dir,
        )
        return prompt

    def _codex_app_resume_text(self, messages) -> str:
        return self._codex_app_last_user_text(messages)

    def _codex_app_abort_active(self, force: bool = True) -> None:
        """Abort active app-server turns by killing their stdio process."""
        active = getattr(self, "_codex_app_active", None)
        if not isinstance(active, dict):
            return
        lock = getattr(self, "_codex_app_lock", None)
        entries = []
        if lock is None:
            entries = list(active.values())
        else:
            with lock:
                entries = list(active.values())
        for state in entries:
            proc = state.get("proc") if isinstance(state, dict) else None
            if not proc:
                continue
            try:
                if proc.poll() is None:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
            except Exception:
                logger.debug("[codex-app] abort active proc failed", exc_info=True)

    def cancel_codex(self, force: bool = True) -> None:
        """Force-stop hook used by AgentLoopTask for Codex app-server."""
        try:
            self.abort()
        except Exception:
            logger.debug("[codex-app] abort flag failed", exc_info=True)
        self._codex_app_abort_active(force=force)

    def _codex_app_send_user_message(
        self, text: str, attachments: list = None, *, user_id: str = "",
        conversation_id: str = "", agent_name: str = ""):
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
        started = time.monotonic()
        timings: Dict[str, float] = {}

        def mark(name: str, t0: float) -> None:
            timings[name] = timings.get(name, 0.0) + ((time.monotonic() - t0) * 1000.0)

        with lock:
            t0 = time.monotonic()
            target_user = user_id or getattr(self, "_user_id", "") or ""
            target_conv = conversation_id or getattr(self, "_conversation_id", "") or ""
            target_agent = agent_name or getattr(self, "_agent_name", "") or ""
            entries = []
            for key, candidate in active.items():
                if not isinstance(key, tuple) or len(key) < 3:
                    continue
                key_user, key_conv, key_agent = key[:3]
                if target_user and key_user != target_user:
                    continue
                if target_conv and key_conv != target_conv:
                    continue
                if target_agent and key_agent != target_agent:
                    continue
                entries.append(candidate)
            mark("find_active", t0)
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
            t0 = time.monotonic()
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
            mark("input_items", t0)
            try:
                t0 = time.monotonic()
                rollout = self._codex_app_rollout_path(state.get("workdir", ""), thread_id)
                after_line = self._codex_app_rollout_line_count(rollout)
                mark("rollout_count", t0)
                t0 = time.monotonic()
                self._codex_app_send(proc, {
                    "method": "turn/steer",
                    "id": self._codex_app_next_id(),
                    "params": {
                        "threadId": thread_id,
                        "expectedTurnId": turn_id,
                        "input": input_items,
                    },
                })
                mark("send", t0)
                self._codex_app_preempt_pending = int(
                    getattr(self, "_codex_app_preempt_pending", 0) or 0) + 1
                sent = list(getattr(self, "_codex_app_sent_preempt_texts", []) or [])
                sent.append({"text": text or "", "after_line": after_line})
                self._codex_app_sent_preempt_texts = sent
                logger.info(
                    "[codex-app] steered active turn %s (pending=%d)",
                    turn_id[:12], self._codex_app_preempt_pending)
                total_ms = (time.monotonic() - started) * 1000.0
                if total_ms >= 100.0:
                    logger.info(
                        "[codex-app] steer timing total_ms=%.1f find_active=%.1f "
                        "input_items=%.1f rollout_count=%.1f send=%.1f",
                        total_ms,
                        timings.get("find_active", 0.0),
                        timings.get("input_items", 0.0),
                        timings.get("rollout_count", 0.0),
                        timings.get("send", 0.0))
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
        thinking_callback=None,
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
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

        self._codex_app_turn_completed_for_callback = False

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

        workdir = self._codex_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._codex_app_container_dir(workdir)

        if is_ephemeral:
            prompt_mode = "ephemeral"
            initial_text = self._codex_app_resume_text(messages)
        elif thread_id:
            prompt_mode = "resume"
            initial_text = self._codex_app_resume_text(messages)
        else:
            prompt_mode = "cold"
            initial_text = self._codex_app_full_initial_text(messages, workdir, container_dir)
        prompt_tokens = _estimate_prompt_tokens(initial_text)
        logger.info(
            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
            prompt_tokens, prompt_mode, len(messages), len(initial_text))

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
        internal_token = ""  # nosec B105
        proc = None
        container = None
        stderr_lines: queue.Queue[str] = queue.Queue(maxsize=200)
        _first_event_timer = None
        _first_event_done = None

        if conv_id and not is_ephemeral:
            try:
                from core.codex_live_registry import CodexLiveRegistry
                live_reg = CodexLiveRegistry.instance()
                _idle_ttl = getattr(self, "timeout", None)
                live_reg.ensure_sweeper(
                    idle_ttl_seconds=int(_idle_ttl) if _idle_ttl else None)
                live_key = (user_id, conv_id, agent_name or "default", svc_id,
                            int(resume_pool_idx))
                live_session = live_reg.get(live_key)
                if live_session is None:
                    compatible = live_reg.get_compatible(
                        user_id, conv_id, agent_name or "default", svc_id)
                    if compatible is not None:
                        live_key, live_session = compatible
                        try:
                            resume_pool_idx = int(live_key[4])
                        except Exception:
                            resume_pool_idx = -1
                        logger.info(
                            "[codex-app-live] restored live key conv=%s agent=%s service=%s pool_idx=%s thread=%s",
                            conv_id[:8] or "?", agent_name or "default", svc_id or "default",
                            int(resume_pool_idx), (live_session.session_id or thread_id)[:12] or "new")
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
        turn_text_is_final = False
        final_text_parts: List[str] = []
        thinking_parts: List[str] = []
        live_thinking_parts: List[str] = []
        emitted_thinking_parts: List[str] = []
        last_thinking_emit = 0.0
        stream_uniq = f"codexapp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        stream_tc_started: Dict[str, float] = {}
        completed_tool_ids = set()
        self._had_preempts_this_turn = False
        self._codex_app_preempt_pending = 0
        self._codex_app_sent_preempt_texts = []
        native_tool_hint_sent = False

        def _flush_text():
            nonlocal turn_text_parts, turn_text_is_final
            if not turn_text_parts:
                return
            if not turn_text_is_final:
                logger.warning(
                    "[codex-app] dropping non-final assistant delta text; waiting for completed item (delta_len=%d)",
                    len("".join(turn_text_parts)),
                )
                turn_text_parts = []
                return
            text = "".join(turn_text_parts).strip()
            turn_text_parts = []
            turn_text_is_final = False
            if text and turn_callback:
                try:
                    if thinking_parts:
                        turn_callback(text, [], "".join(thinking_parts).strip())
                        thinking_parts.clear()
                    else:
                        turn_callback(text, [])
                except TypeError:
                    turn_callback(text, [])

        def _flush_live_thinking(force: bool = False) -> None:
            nonlocal last_thinking_emit
            if not live_thinking_parts:
                return
            now = time.time()
            text = "".join(live_thinking_parts)
            if not text.strip():
                live_thinking_parts.clear()
                return
            if not force:
                if len(text) < 160:
                    return
                if now - last_thinking_emit < 4.0:
                    return
            live_thinking_parts.clear()
            last_thinking_emit = now
            if thinking_callback:
                thinking_callback(text)

        def _append_final_reasoning(text: str) -> None:
            text = (text or "").strip()
            if not text:
                return
            emitted = "".join(emitted_thinking_parts).strip()
            if emitted and (text in emitted or emitted in text):
                return
            existing = "".join(thinking_parts).strip()
            if existing and (text in existing or existing in text):
                return
            thinking_parts.append(text)

        def _send_native_tool_hint(native_name: str) -> None:
            nonlocal native_tool_hint_sent
            if native_tool_hint_sent or not proc or not thread_id or not turn_id:
                return
            native_tool_hint_sent = True
            hint = (
                "PawFlow internal efficiency hint: when you need filesystem, "
                "search, shell, edit, or patch operations, prefer PawFlow MCP "
                "tools through get_tool_schema/use_tool. Native Codex tools "
                "work here, but they are less efficient and less integrated "
                "with PawFlow progress, cancellation, and relay-aware tooling."
            )
            try:
                self._codex_app_send(proc, {
                    "method": "turn/steer",
                    "id": self._codex_app_next_id(),
                    "params": {
                        "threadId": thread_id,
                        "expectedTurnId": turn_id,
                        "input": self._codex_app_input_items(
                            hint, [], workdir, container_dir),
                    },
                })
                logger.info(
                    "[codex-app] steered native-tool MCP hint after %s",
                    native_name)
            except Exception as exc:
                logger.debug("[codex-app] native-tool hint steer failed: %s", exc)

        turn_failed = False
        compact_hard_killed = False

        def _hard_kill_for_context_compaction(reason: str) -> None:
            """Make contextCompaction an immediate process/container barrier."""
            nonlocal compact_hard_killed
            if compact_hard_killed:
                return
            compact_hard_killed = True
            logger.warning(
                "[codex-app] contextCompaction hard-kill — %s", reason)
            if conv_id and store is not None and not is_ephemeral:
                try:
                    store.set_extra(
                        conv_id,
                        f"codex_app_server_thread:{agent_name or 'default'}",
                        "")
                    store.set_extra(
                        conv_id,
                        f"codex_app_pool_idx:{agent_name or 'default'}",
                        "")
                except Exception:
                    logger.debug(
                        "[codex-app] compact session invalidation failed",
                        exc_info=True)
            try:
                lock = self._codex_app_ensure_lock()
                with lock:
                    active = getattr(self, "_codex_app_active", None)
                    if isinstance(active, dict):
                        active.pop(active_key, None)
            except Exception:
                logger.debug("[codex-app] compact active cleanup failed", exc_info=True)
            if live_reg is not None and live_key is not None:
                try:
                    live_reg.evict(live_key, "context_compaction")
                except Exception:
                    logger.debug("[codex-app-live] compact evict failed", exc_info=True)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    logger.debug("[codex-app] compact proc kill failed", exc_info=True)
            if container:
                self._codex_pool_release(container)
            if internal_token:
                try:
                    from core.internal_auth import revoke_token
                    revoke_token(internal_token)
                except Exception:
                    logger.debug("[codex-app] compact token revoke failed", exc_info=True)

        try:
            thread_key = f"codex_app_server_thread:{agent_name or 'default'}"
            _phase_t0 = time.monotonic()
            if not is_reuse:
                proc, container = self._codex_pool_popen(
                    workdir,
                    ["app-server"],
                    container_name=reuse_container,
                    user_id=user_id,
                    conversation_id=conv_id,
                    agent_name=agent_name,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                self._codex_app_start_stderr_drain(proc, stderr_lines)
                _spawn_ms = (time.monotonic() - _phase_t0) * 1000.0
                logger.info("[codex-app] started app-server conv=%s agent=%s thread=%s",
                            conv_id[:8] or "?", agent_name, thread_id[:12] or "new")

                _phase_t0 = time.monotonic()
                self._codex_app_initialize(proc)
                _init_ms = (time.monotonic() - _phase_t0) * 1000.0
                if thread_id:
                    try:
                        _phase_t0 = time.monotonic()
                        thread = self._codex_app_resume_thread(proc, thread_id, model)
                        _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
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
                        initial_text = self._codex_app_full_initial_text(
                            messages, workdir, container_dir)
                        prompt_tokens = _estimate_prompt_tokens(initial_text)
                        logger.info(
                            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
                            prompt_tokens, prompt_mode, len(messages), len(initial_text))
                        _phase_t0 = time.monotonic()
                        thread = self._codex_app_start_thread(proc, model, container_dir)
                        _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
                else:
                    _phase_t0 = time.monotonic()
                    thread = self._codex_app_start_thread(proc, model, container_dir)
                    _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
                if max(_spawn_ms, _init_ms, _thread_ms) >= 500.0:
                    logger.info(
                        "[codex-app] startup timing conv=%s agent=%s spawn=%.1fms "
                        "initialize=%.1fms thread=%.1fms mode=%s",
                        conv_id[:8] or "?", agent_name, _spawn_ms, _init_ms,
                        _thread_ms, prompt_mode)
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
            _phase_t0 = time.monotonic()
            turn = self._codex_app_start_turn(
                proc, thread_id, input_items, model, container_dir,
                effort, reasoning_summary)
            _turn_start_ms = (time.monotonic() - _phase_t0) * 1000.0
            if _turn_start_ms >= 500.0:
                logger.info(
                    "[codex-app] turn/start timing conv=%s agent=%s mode=%s "
                    "prompt_tokens=%d input_chars=%d ms=%.1f",
                    conv_id[:8] or "?", agent_name, prompt_mode,
                    int(prompt_tokens or 0), len(initial_text or ""),
                    _turn_start_ms)
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
                    "user_id": user_id,
                    "conversation_id": conv_id,
                    "agent_name": agent_name,
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

            _turn_wait_started = time.monotonic()
            _first_event_seen = False
            _last_event_at = _turn_wait_started
            _first_event_done = threading.Event()

            def _warn_slow_first_event() -> None:
                if _first_event_done.is_set():
                    return
                logger.warning(
                    "[codex-app] waiting %.1fs for first event after turn/start "
                    "conv=%s agent=%s mode=%s prompt_tokens=%d input_chars=%d "
                    "thread=%s turn=%s",
                    time.monotonic() - _turn_wait_started,
                    conv_id[:8] or "?", agent_name, prompt_mode,
                    int(prompt_tokens or 0), len(initial_text or ""),
                    thread_id[:12], turn_id[:12])
                self._codex_app_log_stderr(stderr_lines)

            _first_event_timer = threading.Timer(10.0, _warn_slow_first_event)
            _first_event_timer.daemon = True
            _first_event_timer.start()
            while True:
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                msg = self._codex_app_read_message(proc)
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                if msg is None:
                    break
                _now_evt = time.monotonic()
                if "id" in msg:
                    # Late response to turn/steer or server request resolution.
                    if msg.get("error"):
                        raise _CodexAppServerProtocolError(str(msg.get("error")))
                    continue
                method = msg.get("method", "")
                params = msg.get("params", {}) or {}
                is_useful_stream_event = (
                    method.startswith("item/")
                    or method in ("turn/completed", "turn/failed")
                )
                if is_useful_stream_event:
                    if not _first_event_seen:
                        _first_event_seen = True
                        _first_event_done.set()
                        _first_event_timer.cancel()
                        _first_ms = (_now_evt - _turn_wait_started) * 1000.0
                        if _first_ms >= 1000.0:
                            logger.info(
                                "[codex-app] first useful stream event after turn/start %.1fms "
                                "method=%s conv=%s agent=%s mode=%s prompt_tokens=%d "
                                "input_chars=%d thread=%s turn=%s",
                                _first_ms, method, conv_id[:8] or "?", agent_name,
                                prompt_mode, int(prompt_tokens or 0),
                                len(initial_text or ""), thread_id[:12], turn_id[:12])
                    elif (_now_evt - _last_event_at) >= 10.0:
                        logger.info(
                            "[codex-app] stream event gap %.1fms method=%s conv=%s agent=%s turn=%s",
                            (_now_evt - _last_event_at) * 1000.0, method,
                            conv_id[:8] or "?", agent_name, turn_id[:12])
                    _last_event_at = _now_evt

                if method == "item/agentMessage/delta":
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        turn_text_is_final = False
                        if callback:
                            callback(delta)
                    continue

                if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        thinking_parts.append(delta)
                        live_thinking_parts.append(delta)
                        _flush_live_thinking()
                    continue

                if method == "item/completed":
                    item = params.get("item", {}) or {}
                    if (item.get("type") in ("message", "agentMessage")
                            and item.get("role", "assistant") == "assistant"):
                        final_text = self._codex_app_payload_text(item).strip()
                        if final_text:
                            delta_text = "".join(turn_text_parts).strip()
                            if delta_text and delta_text != final_text:
                                logger.warning(
                                    "[codex-app] assistant delta/final mismatch; "
                                    "using completed item as source of truth "
                                    "delta_len=%d final_len=%d",
                                    len(delta_text), len(final_text))
                            turn_text_parts = [final_text]
                            turn_text_is_final = True
                            final_text_parts.append(final_text)
                        continue
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
                    if item.get("type") == "contextCompaction":
                        from core.llm_client import CCCompactDetected
                        logger.warning(
                            "[codex-app] contextCompaction detected — handing compaction to PawFlow")
                        _hard_kill_for_context_compaction("item/started")
                        raise CCCompactDetected(
                            "Codex app-server contextCompaction detected")
                    if item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall"):
                        _flush_live_thinking(force=True)
                        if block_callback and turn_text_parts:
                            _flush_text()
                        tc_id = f"{stream_uniq}:{item.get('id') or uuid.uuid4().hex[:8]}"
                        native_name = self._codex_app_native_tool_name(item)
                        _send_native_tool_hint(native_name)
                        stream_tc_names[tc_id] = native_name
                        if block_callback:
                            block_callback("tool_use", {
                                "id": tc_id,
                                "name": native_name,
                                "arguments": self._codex_app_native_tool_args(item),
                                "thinking": "".join(thinking_parts).strip(),
                            })
                            thinking_parts.clear()
                        continue
                    if item.get("type") == "mcpToolCall":
                        _flush_live_thinking(force=True)
                        if block_callback and turn_text_parts:
                            _flush_text()
                        tc_id = f"{stream_uniq}:{item.get('id') or uuid.uuid4().hex[:8]}"
                        raw_name = item.get("tool") or "use_tool"
                        raw_args = item.get("arguments") or {}
                        stream_tc_names[tc_id] = raw_name
                        stream_tc_started[tc_id] = time.perf_counter()
                        logger.info(
                            "[codex-app] timing mcpToolCall started tc_id=%s raw_id=%s tool=%s conv=%s agent=%s",
                            tc_id, item.get("id") or "", raw_name,
                            conv_id[:8] or "?", agent_name or "")
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
                    if item.get("type") == "contextCompaction":
                        from core.llm_client import CCCompactDetected
                        logger.warning(
                            "[codex-app] contextCompaction completed before interception — compacting PawFlow context")
                        _hard_kill_for_context_compaction("item/completed")
                        raise CCCompactDetected(
                            "Codex app-server contextCompaction completed")
                    if item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall"):
                        raw_id = item.get("id") or ""
                        tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                        if not tc_id:
                            continue
                        native_name = stream_tc_names.get(tc_id) or self._codex_app_native_tool_name(item)
                        completed_tool_ids.add(tc_id)
                        if block_callback:
                            block_callback("tool_result", {
                                "tc_id": tc_id,
                                "tool": native_name,
                                "result": self._codex_app_native_tool_result(item),
                            })
                        continue
                    if item.get("type") == "mcpToolCall":
                        raw_id = item.get("id") or ""
                        tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                        if not tc_id:
                            continue
                        raw_name = stream_tc_names.get(tc_id) or item.get("tool") or ""
                        result_str = self._codex_app_result_text(item)
                        completed_tool_ids.add(tc_id)
                        started = stream_tc_started.pop(tc_id, 0.0)
                        provider_ms = ((time.perf_counter() - started) * 1000
                                       if started else 0.0)
                        logger.info(
                            "[codex-app] timing mcpToolCall completed tc_id=%s raw_id=%s tool=%s provider_ms=%.1f result_len=%d",
                            tc_id, raw_id, raw_name, provider_ms,
                            len(result_str))
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
                                "[codex-app] turn completed; rollout shows %d preempt(s) received by provider",
                                len(sent))
                        else:
                            logger.info(
                                "[codex-app] turn completed; %d preempt(s) not proven received in rollout (status=%s) - pending rescue will retrigger",
                                len(sent), pstatus)
                        self._codex_app_preempt_pending = 0
                        self._codex_app_sent_preempt_texts = []
                    self._codex_app_turn_completed_for_callback = True
                    break

                if method == "error":
                    raise LLMClientError(f"codex app-server error: {params.get('error') or params}")

            _flush_text()
            _first_event_done.set()
            _first_event_timer.cancel()
            _flush_live_thinking(force=True)
            content = "".join(final_text_parts).strip() or "".join(text_parts).strip()
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
            if _first_event_done is not None:
                _first_event_done.set()
            if _first_event_timer is not None:
                try:
                    _first_event_timer.cancel()
                except Exception:
                    logger.debug("[codex-app] first-event timer cancel failed", exc_info=True)
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

            if not keep_alive and not compact_hard_killed:
                if live_reg is not None and live_key is not None:
                    try:
                        live_reg.evict(live_key, "app_server_teardown")
                    except Exception:
                        logger.debug("[codex-app-live] evict failed", exc_info=True)
                if proc is not None:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=2.0)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=2.0)
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
            if is_ephemeral and workdir:
                try:
                    shutil.rmtree(workdir, ignore_errors=True)
                    if os.path.isdir(workdir):
                        stale = f"{workdir}.stale-{uuid.uuid4().hex[:8]}"
                        try:
                            os.replace(workdir, stale)
                            shutil.rmtree(stale, ignore_errors=True)
                        except OSError:
                            logger.debug(
                                "[codex-app] deferred ephemeral workdir cleanup: %s",
                                workdir)
                    else:
                        logger.debug("[codex-app] deleted ephemeral workdir: %s", workdir)
                except Exception:
                    logger.debug("[codex-app] ephemeral workdir cleanup failed: %s",
                                 workdir, exc_info=True)

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
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()
        req_id = self._codex_app_next_id()
        self._codex_app_send(proc, {"method": method, "id": req_id,
                                    "params": params or {}})
        while True:
            if getattr(self, "_abort", None) and self._abort.is_set():
                raise AgentCancelled()
            msg = self._codex_app_read_message(proc)
            if getattr(self, "_abort", None) and self._abort.is_set():
                raise AgentCancelled()
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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

    @staticmethod
    def _codex_app_native_tool_name(item: dict) -> str:
        item_type = item.get("type") or "nativeToolCall"
        if item_type == "commandExecution":
            return "codex_native_commandExecution"
        if item_type == "fileChange":
            return "codex_native_fileChange"
        if item_type == "dynamicToolCall":
            return f"codex_native_{item.get('tool') or 'dynamicToolCall'}"
        return f"codex_native_{item_type}"

    @staticmethod
    def _codex_app_native_tool_args(item: dict) -> dict:
        item_type = item.get("type") or ""
        if item_type == "commandExecution":
            return {
                "command": item.get("command") or "",
                "cwd": item.get("cwd") or "",
                "source": item.get("source") or "",
            }
        if item_type == "fileChange":
            return {"changes": item.get("changes") or []}
        if item_type == "dynamicToolCall":
            args = item.get("arguments")
            return args if isinstance(args, dict) else {"arguments": args}
        return {"item": item}

    @staticmethod
    def _codex_app_native_tool_result(item: dict) -> str:
        item_type = item.get("type") or ""
        if item_type == "commandExecution":
            output = item.get("aggregatedOutput") or ""
            prefix = "status=%s exit_code=%s" % (
                item.get("status") or "", item.get("exitCode"))
            return prefix + (("\n" + output) if output else "")
        if item_type == "fileChange":
            return json.dumps({
                "status": item.get("status"),
                "changes": item.get("changes") or [],
            }, ensure_ascii=False, default=str)
        if item_type == "dynamicToolCall":
            return json.dumps({
                "status": item.get("status"),
                "success": item.get("success"),
                "contentItems": item.get("contentItems"),
            }, ensure_ascii=False, default=str)
        return json.dumps(item, ensure_ascii=False, default=str)
