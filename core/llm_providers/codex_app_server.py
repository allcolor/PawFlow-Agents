"""LLM provider mixin -- Codex app-server JSON-RPC.

This is the supported Codex provider surface for PawFlow. It uses
``codex app-server`` for Codex's richer client protocol: native image input,
MCP image results, and turn steering.
"""

import base64
import json
import logging
import os
import time
from urllib.parse import urlparse
from typing import Dict

from core.llm_providers.codex_session import (
    CodexSessionMixin, _get_sessions_base)
from core.llm_providers._codex_app_rpc import (  # noqa: F401
    _CodexAppRpcMixin, _CodexAppServerProtocolError)
from core.llm_providers._codex_app_stream import _CodexAppStreamMixin

logger = logging.getLogger(__name__)


class LLMCodexAppServerMixin(
        _CodexAppStreamMixin, _CodexAppRpcMixin, CodexSessionMixin):
    """Codex app-server provider.

    The app-server process speaks JSON-RPC over stdio. PawFlow starts one
    app-server process for the active turn, persists the Codex thread id in the
    conversation store, and uses ``turn/steer`` when a user message arrives while
    the turn is still running.
    """

    _CODEX_APP_PROVIDER = "codex-app-server"

    @staticmethod
    def _codex_app_valid_remote_url(value: str) -> bool:
        """Return True only for URL formats accepted by Codex image input."""
        if not isinstance(value, str):
            return False
        ref = value.strip()
        if ref.startswith("data:image/"):
            return True
        parsed = urlparse(ref)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

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
            if m.role == "user":
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
                    if not LLMCodexAppServerMixin._codex_app_valid_remote_url(url):
                        label = url or "image"
                        new_content.append({"type": "text", "text": f"[image: {label}]"})
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
                        fid = block.get("file_id", "")
                        fname = block.get("filename", "image") or "image"
                        if fid:
                            new_content.append({
                                "type": "text",
                                "text": f"Attached image: fs://filestore/{fid}/{fname}",
                            })
                        else:
                            new_content.append({"type": "text", "text": f"[image: {fname}]"})
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
                attachment_items = self._codex_app_attachment_items(
                    attachments,
                    user_id=target_user,
                    conversation_id=target_conv,
                    workdir=state.get("workdir", ""),
                    container_dir=state.get("container_dir", ""),
                )
                if attachment_items is None:
                    return False
                input_items.extend(attachment_items)
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

    def _codex_app_estimate_prompt_tokens(self, text: str) -> int:
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

    def _codex_app_append_final_reasoning(
            self, text: str, *, emitted_thinking_parts,
            thinking_parts) -> None:
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

    def _codex_app_send_native_tool_hint(
            self, native_name: str, *, proc, thread_id, turn_id,
            workdir, container_dir) -> None:
        if self._codex_app_native_tool_hint_sent or not proc or not thread_id or not turn_id:
            return
        self._codex_app_native_tool_hint_sent = True
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

    def _codex_app_hard_kill_for_context_compaction(
            self, reason: str, *, conv_id, store, is_ephemeral, agent_name,
            active_key, live_reg, live_key, proc, container,
            internal_token) -> None:
        """Make contextCompaction an immediate process/container barrier."""
        if self._codex_app_compact_hard_killed:
            return
        self._codex_app_compact_hard_killed = True
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
