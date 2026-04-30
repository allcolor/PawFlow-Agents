"""LLM provider mixin -- Gemini CLI via Agent Client Protocol (ACP).

Gemini's old PawFlow provider used headless ``gemini -p`` stream-json. That
transport could not carry user image attachments as native vision input. The
provider now speaks ACP over stdio (``gemini --acp``), which gives PawFlow a
structured prompt channel with native image blocks, session cancellation, MCP
servers, tool updates, and thought chunks.
"""

from __future__ import annotations

import ast
import base64
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT
from core.llm_providers.gemini_session import GeminiSessionMixin, _get_sessions_base

logger = logging.getLogger(__name__)


class _GeminiAcpProtocolError(Exception):
    """Raised when Gemini ACP returns an invalid JSON-RPC response."""


class _GeminiAcpCapacityError(_GeminiAcpProtocolError):
    """Raised when Gemini reports model capacity/quota exhaustion."""


class LLMGeminiMixin(GeminiSessionMixin):
    """Gemini ACP provider.

    The process speaks JSON-RPC over stdio. PawFlow starts one Gemini ACP
    process for the active turn, persists the ACP session id per conversation
    and agent, and sends images as ACP ``ContentBlock::Image`` values.
    """

    _GEMINI_PROVIDER = "gemini"
    _GEMINI_PAWFLOW_PREAMBLE = CLI_MCP_SYSTEM_PROMPT

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
    def _gemini_acp_capacity_error(error: Any) -> str:
        """Return a sanitized Gemini capacity error message, or empty string."""
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("error") or "")
        else:
            message = str(error or "")
        lowered = message.lower()
        if ("exhausted your capacity" in lowered
                or "quota will reset" in lowered
                or "no capacity available" in lowered):
            cooldown = ""
            match = re.search(r"after\s+([0-9.]+\s*[a-z]+)", message, re.IGNORECASE)
            if match:
                cooldown = f"; cooldown {match.group(1).strip()}"
            return "Gemini model capacity exhausted" + cooldown
        return ""

    @staticmethod
    def _gemini_acp_tool_name(update: dict) -> str:
        """Normalize Gemini ACP tool titles to PawFlow wrapper names."""
        name = str(update.get("title") or update.get("kind") or "tool")
        suffix = " (pawflow MCP Server)"
        if name.endswith(suffix):
            name = name[:-len(suffix)]
        if str(update.get("toolCallId") or "").startswith("mcp_pawflow_get_tool_schema"):
            return "get_tool_schema"
        if str(update.get("toolCallId") or "").startswith("mcp_pawflow_use_tool"):
            return "use_tool"
        return name or "tool"

    @staticmethod
    def _gemini_acp_is_pawflow_mcp_tool(update: dict, raw_name: str = "") -> bool:
        tc_id = str(update.get("toolCallId") or "")
        return tc_id.startswith("mcp_pawflow_") or raw_name in ("get_tool_schema", "use_tool")

    @staticmethod
    def _gemini_acp_display_tool_name(raw_name: str, result_text: str = "") -> str:
        """Prefer the inner PawFlow tool over Gemini's MCP wrapper name."""
        if raw_name not in ("use_tool", "mcp__pawflow__use_tool"):
            return raw_name
        patterns = (
            r'<tool_output\s+tool="([^"]+)"',
            r"tool_name['\"]?\s*[:=]\s*['\"]([^'\"}\s,]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, result_text or "")
            if match:
                return match.group(1)
        return raw_name

    @staticmethod
    def _gemini_acp_display_tool_call(raw_name: str, raw_args: Any,
                                      result_text: str = "") -> tuple[str, Any]:
        """Return the UI-facing PawFlow tool name and arguments."""
        try:
            from core.llm_client import unwrap_mcp_tool
            name, args = unwrap_mcp_tool(raw_name, raw_args or {})
        except Exception:
            name, args = raw_name, raw_args or {}
        if name in ("use_tool", "mcp__pawflow__use_tool"):
            display = LLMGeminiMixin._gemini_acp_display_tool_name(name, result_text)
            if display != name:
                return display, args
        return name, args

    @staticmethod
    def _gemini_acp_clean_thinking(text: str) -> str:
        """Drop ACP/MCP call serialization snippets from visible thinking."""
        if not text:
            return ""
        kept = []
        for line in str(text).splitlines():
            lowered = line.lower()
            if (("tool_name" in lowered and "arguments" in lowered)
                    or ("mcp_pawflow" in lowered and "arguments" in lowered)
                    or ('"name": "use_tool"' in lowered and '"args"' in lowered)
                    or ("'name': 'use_tool'" in lowered and "'args'" in lowered)
                    or (lowered.strip().startswith(("{", "["))
                        and "use_tool" in lowered)):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _gemini_acp_extract_tool_arguments_from_text(text: str) -> dict:
        if not text or "use_tool" not in text:
            return {}
        for match in re.finditer(r"\{", text):
            depth = 0
            end = -1
            for pos in range(match.start(), len(text)):
                char = text[pos]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = pos + 1
                        break
            if end <= match.start():
                continue
            candidate = text[match.start():end]
            if "tool_name" not in candidate and "use_tool" not in candidate:
                continue
            parsed = None
            for parser in (json.loads, ast.literal_eval):
                try:
                    parsed = parser(candidate)
                    break
                except Exception:
                    parsed = None
            found = LLMGeminiMixin._gemini_acp_extract_tool_arguments(parsed)
            if found:
                return found
        return {}

    @staticmethod
    def _gemini_acp_extract_tool_arguments(value: Any) -> dict:
        if isinstance(value, dict):
            if "tool_name" in value and "arguments" in value:
                return value
            name = str(value.get("name") or value.get("tool") or "")
            if name in ("use_tool", "mcp_pawflow_use_tool") and isinstance(value.get("args"), dict):
                return value.get("args") or {}
            for key in ("rawInput", "input", "arguments", "args"):
                found = LLMGeminiMixin._gemini_acp_extract_tool_arguments(value.get(key))
                if found:
                    return found
            for key in ("content", "items", "parts"):
                found = LLMGeminiMixin._gemini_acp_extract_tool_arguments(value.get(key))
                if found:
                    return found
            return {}
        if isinstance(value, list):
            for item in value:
                found = LLMGeminiMixin._gemini_acp_extract_tool_arguments(item)
                if found:
                    return found
            return {}
        if isinstance(value, str):
            return LLMGeminiMixin._gemini_acp_extract_tool_arguments_from_text(value)
        return {}

    @staticmethod
    def _gemini_acp_tool_arguments(update: dict) -> dict:
        for key in ("rawInput", "input", "arguments", "args"):
            value = update.get(key)
            if isinstance(value, dict):
                return value
        return LLMGeminiMixin._gemini_acp_extract_tool_arguments(update.get("content"))

    @staticmethod
    def _gemini_acp_history_paths(workdir: str) -> list:
        chats_dir = os.path.join(workdir or "", ".gemini", "tmp", "gemini", "chats")
        try:
            names = [n for n in os.listdir(chats_dir) if n.endswith(".jsonl")]
        except Exception:
            return []
        paths = [os.path.join(chats_dir, n) for n in names]
        try:
            paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception:
            paths.sort(reverse=True)
        return paths

    @staticmethod
    def _gemini_acp_history_tool_arguments(workdir: str, tool_call_id: str) -> dict:
        """Recover ACP tool args from Gemini's session JSONL on replayed events."""
        if not workdir or not tool_call_id:
            return {}
        for path in LLMGeminiMixin._gemini_acp_history_paths(workdir)[:8]:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if tool_call_id not in line:
                            continue
                        rec = json.loads(line)
                        for tc in rec.get("toolCalls") or []:
                            if str(tc.get("id") or "") == tool_call_id:
                                args = tc.get("args")
                                return args if isinstance(args, dict) else {}
            except Exception:
                logger.debug("[gemini-acp] history tool args lookup failed", exc_info=True)
        return {}

    @staticmethod
    def _gemini_acp_history_content_text(rec: dict) -> str:
        content = rec.get("content") if isinstance(rec, dict) else None
        if isinstance(content, str):
            return content
        parts = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content") or ""
                    if text:
                        parts.append(str(text))
        return "".join(parts)

    @classmethod
    def _gemini_acp_check_preempt_in_history(cls, workdir: str, sent_texts: list) -> str:
        if not sent_texts or not workdir:
            return "unknown"
        found_flags = [False] * len(sent_texts)
        preempt_positions = [-1] * len(sent_texts)
        last_assistant_pos = -1
        pos = 0
        paths = cls._gemini_acp_history_paths(workdir)
        if not paths:
            return "unknown"
        for path in reversed(paths[:8]):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rtype = rec.get("type") or ""
                        if rtype == "gemini":
                            last_assistant_pos = pos
                        elif rtype == "user":
                            text_blob = cls._gemini_acp_history_content_text(rec)
                            for idx, sent in enumerate(sent_texts):
                                if sent and sent in text_blob:
                                    found_flags[idx] = True
                                    preempt_positions[idx] = pos
                        pos += 1
            except OSError:
                return "unknown"
        if not any(found_flags):
            return "unread"
        for idx, hit_pos in enumerate(preempt_positions):
            if found_flags[idx] and hit_pos > last_assistant_pos:
                return "pending"
        if not all(found_flags):
            return "unread"
        return "done"

    def _gemini_acp_enqueue_live_tool_tc(self, conv_id: str, agent_name: str,
                                         tc_id: str, raw_name: str,
                                         raw_args: dict, update: dict) -> None:
        """Map Gemini ACP tool ids to the next PawFlow MCP relay request."""
        try:
            from core.llm_client import unwrap_mcp_tool
            from core.background_tool import (
                ANY_ARGS_HASH, ANY_TOOL, _args_hash, enqueue_cc_tc,
            )
            if raw_args:
                tc_name, tc_args = unwrap_mcp_tool(raw_name, raw_args)
                enqueue_cc_tc(conv_id, agent_name, tc_id, tc_name, _args_hash(tc_args))
            elif self._gemini_acp_is_pawflow_mcp_tool(update, raw_name):
                enqueue_cc_tc(conv_id, agent_name, tc_id, ANY_TOOL, ANY_ARGS_HASH)
        except Exception:
            logger.debug("[gemini-acp] enqueue background tc skipped", exc_info=True)

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
        system_prompt = (
            self._GEMINI_PAWFLOW_PREAMBLE
            + ("\n\n" + system_prompt if system_prompt else "")
        )
        return self._gemini_acp_build_stdin_with_system(system_prompt, user_text)

    def _gemini_acp_live_text(self, user_text: str) -> str:
        return user_text or ""

    def _gemini_acp_resume_text(self, messages) -> str:
        return self._gemini_acp_last_user_text(messages)

    @staticmethod
    def _gemini_acp_last_user_text(messages) -> str:
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    return getattr(msg, "text_content", "") or ""
                return content or ""
        return ""

    def _gemini_pool_popen(self, workdir: str, cmd: list,
                           container_name: str = "", user_id: str = "",
                           conversation_id: str = "", agent_name: str = "",
                           **popen_kwargs) -> tuple:
        """Launch gemini inside a pool container via docker exec."""
        env = self._gemini_env(workdir)
        from core.gemini_pool import GeminiPool
        from core.cli_workspace_mounts import build_cli_workspace_mount_args
        pool = GeminiPool.instance()
        workspace_mounts = [] if container_name else build_cli_workspace_mount_args(
            conversation_id, agent_name, user_id=user_id)
        container = container_name or pool.acquire(workspace_mount_args=workspace_mounts)
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
        """Live-preempt the active ACP prompt inside the warm Gemini session."""
        if not (text or attachments):
            return False
        active = getattr(self, "_gemini_acp_active", None)
        if not isinstance(active, dict):
            return False
        lock = self._gemini_acp_ensure_lock()
        with lock:
            entries = list(active.values())
            if not entries:
                return False
            entries = sorted(entries, key=lambda s: s.get("started_at", 0), reverse=True)
            for state in entries:
                proc = state.get("proc")
                session_id = state.get("session_id") or ""
                if not proc or proc.poll() is not None or not session_id:
                    continue
                prompt = self._gemini_acp_prompt_items(
                    self._gemini_acp_live_text(text or ""), [])
                req_id = self._gemini_acp_next_id()
                try:
                    self._gemini_acp_notify(proc, "session/cancel", {"sessionId": session_id})
                    self._gemini_acp_send(proc, {
                        "jsonrpc": "2.0",
                        "method": "session/prompt",
                        "id": req_id,
                        "params": {"sessionId": session_id, "prompt": prompt},
                    })
                    state["preempt_req_id"] = req_id
                    state["preempt_started_at"] = time.time()
                    state["preempt_text"] = text or ""
                    self._gemini_acp_preempt_pending = int(
                        getattr(self, "_gemini_acp_preempt_pending", 0) or 0) + 1
                    sent = list(getattr(self, "_gemini_acp_sent_preempt_texts", []) or [])
                    sent.append(text or "")
                    self._gemini_acp_sent_preempt_texts = sent
                    logger.info(
                        "[gemini-acp-live] preempted active session %s with prompt id=%s",
                        session_id[:12], req_id)
                    return True
                except Exception as exc:
                    logger.warning("[gemini-acp-live] live preempt failed: %s", exc)
        return False

    def cancel_gemini(self, force: bool = False):
        """Best-effort cancellation for the active Gemini ACP prompt."""
        active = getattr(self, "_gemini_acp_active", None)
        if not isinstance(active, dict):
            return
        lock = self._gemini_acp_ensure_lock()
        with lock:
            entries = list(active.values())
        for state in sorted(entries, key=lambda s: s.get("started_at", 0), reverse=True):
            proc = state.get("proc")
            session_id = state.get("session_id") or ""
            if not proc or proc.poll() is not None or not session_id:
                continue
            try:
                self._gemini_acp_notify(proc, "session/cancel", {"sessionId": session_id})
                logger.info("[gemini-acp] cancelled active session %s", session_id[:12])
                return
            except Exception:
                logger.debug("[gemini-acp] session/cancel failed", exc_info=True)

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
        model = (model or "").strip()
        effort = self._gemini_acp_effort(thinking_budget, self._cfg("effort", ""))

        image_blocks = self._gemini_acp_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        def _estimate_prompt_tokens(text: str) -> int:
            try:
                from core.token_counter import (
                    count_messages_tokens as _count_msgs,
                    resolve_token_multiplier as _resolve_mult,
                )
                mult = _resolve_mult(getattr(self, "_config_ref", None) or {})
                return _count_msgs([{"content": text or ""}], multiplier=mult)
            except Exception:
                fallback = int(len(text or "") / 3.5)
                logger.warning(
                    "[gemini-acp] count_messages_tokens failed, fell back to chars/3.5 -> %d",
                    fallback, exc_info=True)
                return fallback

        def _prompt_text_for_mode(mode: str) -> str:
            if str(mode or "").startswith("resume"):
                return self._gemini_acp_resume_text(messages)
            return self._gemini_acp_full_initial_text(messages)

        store = None
        session_id = ""
        legacy_session_cleared = False
        session_key = f"gemini_acp_session:{agent_name or 'default'}"
        pool_key = f"gemini_acp_pool_idx:{agent_name or 'default'}"
        session_version_key = f"gemini_acp_session_version:{agent_name or 'default'}"
        if conv_id and not is_ephemeral:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                session_id = store.get_extra(conv_id, session_key) or ""
                session_version = store.get_extra(conv_id, session_version_key) or ""
                if session_id and session_version != "2":
                    logger.info(
                        "[gemini-acp-live] clearing legacy stored session %s",
                        session_id[:12])
                    store.set_extra(conv_id, session_key, "")
                    store.set_extra(conv_id, pool_key, "")
                    store.set_extra(conv_id, session_version_key, "")
                    session_id = ""
                    legacy_session_cleared = True
            except Exception:
                logger.debug("[gemini-acp] failed to restore session id", exc_info=True)

        prompt_mode = "resume" if session_id else "cold"
        initial_text = _prompt_text_for_mode(prompt_mode)
        workdir = self._gemini_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._gemini_acp_container_dir(workdir)

        resume_pool_idx = -1
        if session_id and conv_id and store is not None:
            try:
                resume_pool_idx = int(store.get_extra(conv_id, pool_key) or -1)
            except Exception:
                logger.debug("[gemini-acp] failed to restore pool index", exc_info=True)

        svc_id = getattr(self, "_agent_service", "") or ""
        live_reg = None
        live_key = None
        live_session = None
        owns_live_lock = False
        is_reuse = False
        mcp_servers: list = []
        internal_token = ""
        proc = None
        container = None
        reuse_container = ""
        stderr_lines: queue.Queue[str] = queue.Queue(maxsize=200)

        if conv_id and not is_ephemeral:
            try:
                from core.gemini_live_registry import GeminiLiveRegistry
                live_reg = GeminiLiveRegistry.instance()
                live_reg.ensure_sweeper(
                    idle_ttl_seconds=int(getattr(self, "timeout", 1800) or 1800))
                live_key = (user_id, conv_id, agent_name or "default", svc_id)
                live_session = live_reg.get(live_key)
                if live_session is not None and legacy_session_cleared:
                    live_reg.evict(live_key, "legacy_session")
                    live_session = None
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
                        session_id = live_session.session_id or session_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        prompt_mode = "resume-live"
                        initial_text = _prompt_text_for_mode(prompt_mode)
                        logger.info(
                            "[gemini-acp-live] REUSE conv=%s agent=%s session=%s reuse=%d",
                            conv_id[:8] or "?", agent_name, session_id[:12],
                            live_session.reuse_count)
                    else:
                        reuse_container = live_session.container_name
                        container = reuse_container
                        internal_token = live_session.mcp_internal_token or ""
                        session_id = live_session.session_id or session_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.warning(
                            "[gemini-acp-live] process dead but container alive; restarting ACP in container=%s",
                            reuse_container)
            except Exception:
                logger.debug("[gemini-acp-live] lookup failed", exc_info=True)
                live_reg = None
                live_key = None

        if not is_reuse:
            if session_id:
                logger.info(
                    "[gemini-acp-live] stored session %s has no live process; loading in fresh ACP process",
                    session_id[:12])
            self._gemini_setup_credentials(workdir, pool_index=resume_pool_idx)
            if conv_id and store is not None and hasattr(self, "_current_pool_index"):
                try:
                    store.set_extra(conv_id, pool_key, self._current_pool_index)
                except Exception:
                    logger.debug("[gemini-acp] failed to persist pool index", exc_info=True)
            mcp_servers, internal_token = self._gemini_acp_mcp_servers(
                user_id=user_id, conversation_id=conv_id, agent_name=agent_name)
            self._gemini_acp_write_settings(
                workdir, model, effort, thinking_budget, temperature, max_tokens,
                mcp_servers=mcp_servers, mcp_cwd=container_dir)
        active_key = (user_id, conv_id, agent_name, time.time())
        text_parts: List[str] = []
        turn_text_parts: List[str] = []
        thinking_parts: List[str] = []
        stream_uniq = f"geminiacp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        completed_tool_ids = set()
        started_tool_ids = set()
        deferred_tool_ids = set()
        usage_meta: Dict[str, Any] = {}
        loaded_session_replay_barrier = False
        self._had_preempts_this_turn = False
        self._gemini_acp_preempt_pending = 0
        self._gemini_acp_sent_preempt_texts = []

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

        turn_failed = False
        opened_session_this_call = False
        try:
            if not is_reuse:
                proc, container = self._gemini_acp_start_process(
                    workdir, model, container_name=reuse_container,
                    user_id=user_id, conversation_id=conv_id,
                    agent_name=agent_name)
                self._gemini_acp_start_stderr_drain(proc, stderr_lines)
                logger.info("[gemini-acp] started ACP conv=%s agent=%s session=%s",
                            conv_id[:8] or "?", agent_name, session_id[:12] or "new")

                self._gemini_acp_start_stdout_drain(proc)
                init_result = self._gemini_acp_initialize(proc)
                self._gemini_acp_authenticate(proc)
                supports_load = bool(
                    ((init_result.get("agentCapabilities") or {}).get("loadSession")))
                if session_id and supports_load:
                    try:
                        self._gemini_acp_load_session(proc, session_id, container_dir, mcp_servers)
                        loaded_session_replay_barrier = True
                    except Exception as exc:
                        if not self._gemini_acp_stale_session_error(exc):
                            raise
                        logger.warning(
                            "[gemini-acp] stale session id %s; starting new session",
                            session_id[:12])
                        if conv_id and store is not None and not is_ephemeral:
                            try:
                                store.set_extra(conv_id, session_key, "")
                                store.set_extra(conv_id, session_version_key, "")
                            except Exception:
                                logger.debug("[gemini-acp] failed to clear stale session id", exc_info=True)
                        session_id = ""
                        prompt_mode = "cold-after-stale-session"
                        initial_text = _prompt_text_for_mode(prompt_mode)
                elif session_id and not supports_load:
                    session_id = ""
                    prompt_mode = "cold-no-load-session"
                    initial_text = _prompt_text_for_mode(prompt_mode)

                if not session_id:
                    logger.info("[gemini-acp] opening new session cwd=%s", container_dir)
                    result = self._gemini_acp_new_session(proc, container_dir, mcp_servers)
                    session_id = (result or {}).get("sessionId", "")
                    opened_session_this_call = True
                    logger.info("[gemini-acp] new session id=%s", session_id[:12] or "?")
            elif not session_id:
                raise LLMClientError("gemini ACP live session has no session id")
            if not session_id:
                raise LLMClientError("gemini ACP did not return a session id")

            prompt_tokens = _estimate_prompt_tokens(initial_text)
            logger.info(
                "[gemini-acp] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
                prompt_tokens, prompt_mode, len(messages), len(initial_text))
            prompt = self._gemini_acp_prompt_items(initial_text, image_blocks)
            active_state = {
                "proc": proc,
                "session_id": session_id,
                "workdir": workdir,
                "container_dir": container_dir,
                "started_at": time.time(),
            }
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if not isinstance(active, dict):
                    active = {}
                    self._gemini_acp_active = active
                active[active_key] = active_state

            if live_reg is not None and live_key is not None and not is_ephemeral:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=session_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=True,
                    )
                    logger.info(
                        "[gemini-acp-live] active conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, session_id[:12])
                except Exception:
                    logger.debug("[gemini-acp-live] active register failed", exc_info=True)

            logger.info(
                "[gemini-acp] sending prompt session=%s items=%d images=%d chars=%d",
                session_id[:12], len(prompt), len(image_blocks), len(initial_text))
            req_id = self._gemini_acp_next_id()
            self._gemini_acp_send(proc, {
                "jsonrpc": "2.0",
                "method": "session/prompt",
                "id": req_id,
                "params": {"sessionId": session_id, "prompt": prompt},
            })

            logger.info("[gemini-acp] prompt sent; waiting for ACP events")
            self._gemini_acp_log_stderr(stderr_lines)

            _prompt_activity_seen = False
            _preempt_prompt_active = False
            _skip_resume_replay = bool(loaded_session_replay_barrier and not is_reuse)
            _resume_replay_skipped = 0
            _last_acp_event_at = time.monotonic()
            _last_acp_event = "prompt_sent"
            while True:
                msg = self._gemini_acp_read_message(
                    proc, timeout_s=None, wait_log_s=15.0,
                    wait_context=lambda: (
                        f"session={session_id[:12]} req={req_id} "
                        f"last={_last_acp_event} "
                        f"idle={time.monotonic() - _last_acp_event_at:.1f}s"
                    ))
                _now_acp_event = time.monotonic()
                _gap_s = _now_acp_event - _last_acp_event_at
                if _gap_s >= 5.0:
                    logger.info(
                        "[gemini-acp][gap] %.1fs since %s before %s",
                        _gap_s, _last_acp_event,
                        self._gemini_acp_message_preview(msg))
                _last_acp_event_at = _now_acp_event
                _last_acp_event = self._gemini_acp_message_preview(msg)
                if msg is None:
                    raise _GeminiAcpProtocolError(
                        "gemini ACP exited before session/prompt completed")

                incoming_id = msg.get("id")
                if (incoming_id is not None
                        and incoming_id == active_state.get("preempt_req_id")
                        and incoming_id != req_id):
                    req_id = int(incoming_id)
                    active_state.pop("preempt_req_id", None)
                    _preempt_prompt_active = True
                    _prompt_activity_seen = True
                    logger.info("[gemini-acp-live] switched reader to preempt prompt id=%s", req_id)

                if incoming_id == req_id:
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    _prompt_activity_seen = True
                    if msg.get("error"):
                        capacity_message = self._gemini_acp_capacity_error(msg.get("error"))
                        if capacity_message:
                            raise _GeminiAcpCapacityError(
                                f"Gemini capacity exhausted: {capacity_message}")
                        raise _GeminiAcpProtocolError(
                            f"session/prompt failed: {msg.get('error')}")
                    result = msg.get("result") or {}
                    usage_meta = result.get("_meta") or result.get("meta") or {}
                    stop_reason = result.get("stopReason") or "end_turn"
                    if stop_reason in ("cancelled", "canceled"):
                        next_req_id = active_state.pop("preempt_req_id", None)
                        if next_req_id and next_req_id != req_id:
                            req_id = int(next_req_id)
                            _preempt_prompt_active = True
                            _prompt_activity_seen = False
                            logger.info(
                                "[gemini-acp-live] cancelled old prompt; waiting for preempt id=%s",
                                req_id)
                            continue
                        break
                    if _preempt_prompt_active:
                        sent = list(getattr(self, "_gemini_acp_sent_preempt_texts", []) or [])
                        pstatus = self._gemini_acp_check_preempt_in_history(workdir, sent)
                        if pstatus in ("done", "pending", "unknown"):
                            self._had_preempts_this_turn = True
                            logger.info(
                                "[gemini-acp-live] preempt prompt completed (history=%s, count=%d)",
                                pstatus, len(sent))
                        else:
                            logger.info(
                                "[gemini-acp-live] preempt prompt completed but history status=%s; pending rescue may retrigger",
                                pstatus)
                        self._gemini_acp_preempt_pending = 0
                        self._gemini_acp_sent_preempt_texts = []
                    if stop_reason not in ("end_turn", "stop", "max_tokens"):
                        logger.info("[gemini-acp] prompt stopped: %s", stop_reason)
                    break

                if "id" in msg and msg.get("method"):
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    req_method = msg.get("method", "")
                    req_params = msg.get("params", {}) or {}
                    logger.info("[gemini-acp] client request during prompt: %s", req_method)
                    if req_method == "session/request_permission":
                        _prompt_activity_seen = True
                        outcome = self._gemini_acp_permission_result(req_params)
                        self._gemini_acp_send(proc, {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "result": outcome,
                        })
                    else:
                        self._gemini_acp_send(proc, {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "client method not implemented"},
                        })
                    continue

                method = msg.get("method", "")
                params = msg.get("params", {}) or {}
                if method != "session/update":
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    logger.info("[gemini-acp] ignored ACP message during prompt: %s", method or "?")
                    continue
                update = params.get("update", {}) or {}
                kind = update.get("sessionUpdate") or ""
                if _skip_resume_replay:
                    if kind == "available_commands_update":
                        _skip_resume_replay = False
                        if _resume_replay_skipped:
                            logger.info(
                                "[gemini-acp] skipped %d replayed session/load update(s)",
                                _resume_replay_skipped)
                        continue
                    _resume_replay_skipped += 1
                    continue
                logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))

                if kind == "agent_message_chunk":
                    _prompt_activity_seen = True
                    delta = self._gemini_acp_content_text(update.get("content"))
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        if callback:
                            callback(delta)
                    continue

                if kind == "agent_thought_chunk":
                    _prompt_activity_seen = True
                    thought = self._gemini_acp_clean_thinking(
                        self._gemini_acp_content_text(update.get("content")))
                    if thought:
                        thinking_parts.append(thought)
                    continue

                _terminal_tool_statuses = ("completed", "failed", "cancelled", "canceled")

                def _emit_started_tool(
                    tc_id: str,
                    raw_name: str,
                    raw_input: dict,
                    update: dict,
                    result_text: str = "",
                    enqueue_live_mapping: bool = True,
                ) -> None:
                    stream_tc_names[tc_id] = raw_name
                    if enqueue_live_mapping:
                        self._gemini_acp_enqueue_live_tool_tc(
                            conv_id, agent_name, tc_id, raw_name, raw_input, update)
                    display_name, display_args = self._gemini_acp_display_tool_call(
                        raw_name, raw_input, result_text)
                    defer_wrapper = raw_name == "use_tool" and not raw_input and not result_text
                    if block_callback and not defer_wrapper:
                        block_callback("tool_use", {
                            "id": tc_id,
                            "name": display_name,
                            "arguments": display_args,
                            "thinking": "".join(thinking_parts).strip(),
                        })
                        thinking_parts.clear()
                        started_tool_ids.add(tc_id)
                    elif defer_wrapper:
                        deferred_tool_ids.add(tc_id)

                def _emit_finished_tool(
                    update: dict,
                    tc_id: str,
                    raw_name: str,
                    raw_input: dict,
                ) -> None:
                    result_text = self._gemini_acp_tool_result_text(update)
                    display_name, display_args = self._gemini_acp_display_tool_call(
                        stream_tc_names.get(tc_id) or raw_name, raw_input, result_text)
                    if tc_id not in started_tool_ids:
                        _emit_started_tool(
                            tc_id, raw_name, raw_input, update, result_text,
                            enqueue_live_mapping=False)
                        started_tool_ids.add(tc_id)
                        deferred_tool_ids.discard(tc_id)
                    completed_tool_ids.add(tc_id)
                    if block_callback:
                        block_callback("tool_result", {
                            "tc_id": tc_id,
                            "tool": display_name,
                            "result": result_text,
                        })

                if kind == "tool_call":
                    _prompt_activity_seen = True
                    if turn_text_parts:
                        _flush_text()
                    raw_id = update.get("toolCallId") or uuid.uuid4().hex[:8]
                    tc_id = f"{stream_uniq}:{raw_id}"
                    status = update.get("status") or ""
                    raw_name = self._gemini_acp_tool_name(update)
                    raw_input = self._gemini_acp_tool_arguments(update)
                    if not raw_input and raw_name == "use_tool":
                        raw_input = self._gemini_acp_history_tool_arguments(workdir, raw_id)
                    if status in _terminal_tool_statuses:
                        _emit_finished_tool(update, tc_id, raw_name, raw_input)
                    elif tc_id not in started_tool_ids:
                        _emit_started_tool(tc_id, raw_name, raw_input, update)
                    continue

                if kind == "tool_call_update":
                    _prompt_activity_seen = True
                    if turn_text_parts:
                        _flush_text()
                    raw_id = update.get("toolCallId") or ""
                    tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                    status = update.get("status") or ""
                    raw_name = self._gemini_acp_tool_name(update)
                    raw_input = self._gemini_acp_tool_arguments(update)
                    if not raw_input and raw_name == "use_tool" and raw_id:
                        raw_input = self._gemini_acp_history_tool_arguments(workdir, raw_id)
                    if tc_id and status in _terminal_tool_statuses:
                        _emit_finished_tool(update, tc_id, raw_name, raw_input)
                    elif tc_id and tc_id not in started_tool_ids:
                        _emit_started_tool(tc_id, raw_name, raw_input, update)
                    continue


            _flush_text()
            content = "".join(text_parts).strip()
            tokens_out = self._gemini_acp_output_tokens(usage_meta, content)
            if session_id and conv_id and store is not None and not is_ephemeral:
                try:
                    store.set_extra(conv_id, session_key, session_id)
                    store.set_extra(conv_id, session_version_key, "2")
                except Exception:
                    logger.debug("[gemini-acp] failed to persist session id", exc_info=True)
            return LLMResponse(
                content=content,
                model=model,
                tokens_in=max(0, int(prompt_tokens or 0)),
                tokens_out=max(0, int(tokens_out or 0)),
                finish_reason="stop",
                raw={"session_id": session_id, "tool_results": len(completed_tool_ids)},
                thinking="".join(thinking_parts).strip(),
            )
        except _GeminiAcpCapacityError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except _GeminiAcpProtocolError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except Exception:
            turn_failed = True
            raise
        finally:
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if isinstance(active, dict):
                    active.pop(active_key, None)
            try:
                self._gemini_recover_tokens(workdir)
            except Exception:
                logger.debug("[gemini-acp] token recovery failed", exc_info=True)

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
                and bool(session_id)
                and bool(container)
                and not is_ephemeral
            )
            if keep_alive:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=session_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=False,
                    )
                    logger.info(
                        "[gemini-acp-live] keep-alive conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, session_id[:12])
                except Exception:
                    logger.debug("[gemini-acp-live] register failed", exc_info=True)
                    keep_alive = False

            if not keep_alive:
                if turn_failed and opened_session_this_call and conv_id and store is not None and not is_ephemeral:
                    try:
                        if (store.get_extra(conv_id, session_key) or "") == session_id:
                            store.set_extra(conv_id, session_key, "")
                            store.set_extra(conv_id, session_version_key, "")
                    except Exception:
                        logger.debug("[gemini-acp] failed to clear failed fresh session", exc_info=True)
                if live_reg is not None and live_key is not None:
                    try:
                        live_reg.evict(live_key, "acp_teardown")
                    except Exception:
                        logger.debug("[gemini-acp-live] evict failed", exc_info=True)
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
                self._gemini_pool_release(container)
            self._gemini_acp_log_stderr(stderr_lines)
            if owns_live_lock and live_session is not None:
                try:
                    live_session.turn_lock.release()
                except Exception:
                    logger.debug("[gemini-acp-live] turn lock release failed", exc_info=True)

    def _gemini_acp_start_process(self, workdir: str, model: str,
                                  container_name: str = "", user_id: str = "",
                                  conversation_id: str = "", agent_name: str = ""):
        args = ["--debug", "--acp"]
        if model:
            args = ["--model", model, *args]
        try:
            return self._gemini_pool_popen(
                workdir,
                args,
                container_name=container_name,
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
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

    def _gemini_acp_request(self, proc, method: str, params: Optional[dict] = None,
                            timeout_s: float = 60.0) -> dict:
        req_id = self._gemini_acp_next_id()
        self._gemini_acp_send(proc, {
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id,
            "params": params or {},
        })
        while True:
            msg = self._gemini_acp_read_message(proc, timeout_s=timeout_s)
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
        }, timeout_s=30.0)

    def _gemini_acp_authenticate(self, proc) -> dict:
        api_key = getattr(self, "api_key", "") or ""
        method_id = "gemini-api-key" if api_key else "oauth-personal"
        params = {"methodId": method_id}
        if api_key:
            params["apiKey"] = api_key
        result = self._gemini_acp_request(
            proc, "authenticate", params, timeout_s=60.0)
        logger.info("[gemini-acp] authenticated via %s", method_id)
        return result

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
    def _gemini_acp_permission_result(params: dict) -> dict:
        """Approve only PawFlow MCP actions; deny Gemini built-in tools."""
        options = params.get("options") or []
        request_text = json.dumps(params, ensure_ascii=False).lower()
        allow_pawflow = "pawflow" in request_text or "mcp_pawflow" in request_text
        selected = None
        if allow_pawflow:
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                kind = str(opt.get("kind") or opt.get("optionId") or "").lower()
                if "allow" in kind or "proceed" in kind:
                    selected = opt.get("optionId")
                    break
        if selected:
            return {"outcome": {"outcome": "selected", "optionId": selected}}
        logger.info("[gemini-acp] denied non-PawFlow permission request")
        return {"outcome": {"outcome": "cancelled"}}

    @staticmethod
    def _gemini_acp_message_preview(msg: dict) -> str:
        try:
            method = msg.get("method") or ""
            msg_id = msg.get("id", "")
            if msg.get("error"):
                return f"id={msg_id} error={str(msg.get('error'))[:300]}"
            params = msg.get("params") or {}
            update = params.get("update") if isinstance(params, dict) else None
            if isinstance(update, dict):
                kind = update.get("sessionUpdate") or ""
                content = update.get("content")
                text_len = len(LLMGeminiMixin._gemini_acp_content_text(content)) if content is not None else 0
                keys = ",".join(sorted(str(k) for k in update.keys())[:8])
                tool_bits = []
                for key in ("toolCallId", "status", "kind", "title"):
                    if update.get(key):
                        tool_bits.append(f"{key}={str(update.get(key))[:120]}")
                suffix = f" {' '.join(tool_bits)}" if tool_bits else ""
                return f"method={method} id={msg_id} update={kind} text_len={text_len} keys={keys}{suffix}"
            result = msg.get("result")
            if isinstance(result, dict):
                return f"method={method} id={msg_id} result_keys={','.join(sorted(str(k) for k in result.keys())[:8])}"
            keys = ",".join(sorted(str(k) for k in msg.keys())[:8])
            return f"method={method} id={msg_id} keys={keys}"
        except Exception as exc:
            return f"unpreviewable: {exc}"


    @staticmethod
    def _gemini_acp_read_message(proc, timeout_s: Optional[float] = None,
                                 wait_log_s: float = 0.0,
                                 wait_context=None) -> Optional[dict]:
        stdout_q = getattr(proc, "_pawflow_gemini_acp_stdout", None)
        if stdout_q is None:
            raise _GeminiAcpProtocolError(
                "gemini ACP stdout drain was not initialized; refusing blocking readline")

        deadline = time.monotonic() + float(timeout_s) if timeout_s is not None else None
        wait_interval = float(wait_log_s or 0.0)
        next_wait_log = time.monotonic() + wait_interval if wait_interval > 0 else None
        while True:
            if proc.poll() is not None and stdout_q.empty():
                return None
            try:
                if deadline is None:
                    line = stdout_q.get(timeout=0.5)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    line = stdout_q.get(timeout=min(0.5, remaining))
            except queue.Empty:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise _GeminiAcpProtocolError(
                        f"gemini ACP timed out after {timeout_s:.0f}s waiting for stdout")
                if next_wait_log is not None and now >= next_wait_log:
                    try:
                        context = wait_context() if callable(wait_context) else (wait_context or "")
                    except Exception:
                        context = ""
                    suffix = f" ({context})" if context else ""
                    logger.info("[gemini-acp][wait] still waiting for stdout%s", suffix)
                    next_wait_log = now + wait_interval
                continue
            if line is None:
                return None
            line = str(line).strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[gemini-acp] ignored non-json stdout line: %s", line[:300])

    @staticmethod
    def _gemini_acp_start_stdout_drain(proc) -> None:
        sink: queue.Queue[Optional[str]] = queue.Queue(maxsize=10000)
        setattr(proc, "_pawflow_gemini_acp_stdout", sink)

        def _drain():
            try:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    sink.put(line)
            except Exception:
                pass
            finally:
                try:
                    sink.put_nowait(None)
                except queue.Full:
                    try:
                        sink.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        sink.put_nowait(None)
                    except queue.Full:
                        pass
        threading.Thread(target=_drain, daemon=True, name="gemini-acp-stdout").start()

    @staticmethod
    def _gemini_acp_start_stderr_drain(proc, sink: queue.Queue[str]) -> None:
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
                        if text and not text.startswith("__PF_GEMINI_PID="):
                            logger.info("[gemini-acp][stderr] %s", text[:1000])
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
                                   max_tokens: int,
                                   mcp_servers: Optional[list] = None,
                                   mcp_cwd: str = "") -> None:
        """Write Gemini settings for auth, model selection and thoughts."""
        gemini_home = os.path.join(workdir, ".gemini")
        os.makedirs(gemini_home, exist_ok=True)
        settings_path = os.path.join(gemini_home, "settings.json")
        model = (model or "").strip()
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

        # Gemini CLI exposes local core tools by default. In PawFlow those tools
        # point at the isolated CLI session directory, not the user's relay-backed
        # workspace, and they are slow/failing fallbacks. Keep only PawFlow MCP.
        excluded_core_tools = [
            "list_directory",
            "read_file",
            "read_many_files",
            "glob",
            "search_file_content",
            "write_file",
            "replace",
            "run_shell_command",
            "web_fetch",
            "google_web_search",
            "save_memory",
            "ReadFolder",
            "ReadFile",
            "GlobTool",
            "ShellTool",
            "WriteFileTool",
            "EditTool",
            "WebFetchTool",
            "WebSearchTool",
        ]
        settings: Dict[str, Any] = {
            "general": {"defaultApprovalMode": "auto_edit", "maxAttempts": 1},
            "security": {"auth": {}, "folderTrust": {"enabled": False}},
            "ui": {"inlineThinkingMode": "full", "loadingPhrases": "off"},
            "tools": {"exclude": excluded_core_tools},
            "mcp": {"allowed": ["pawflow"]},
            "useWriteTodos": False,
            "modelConfigs": {
                "overrides": [
                    {
                        "match": {},
                        "modelConfig": {"generateContentConfig": generation_config},
                    }
                ],
                # Legacy v1 compatibility for older Gemini CLI builds.
                "customOverrides": [
                    {
                        "match": {"model": model} if model else {},
                        "modelConfig": {"generateContentConfig": generation_config},
                    }
                ],
            },
            # Legacy v1 compatibility for older Gemini CLI builds.
            "autoAccept": True,
            "allowMCPServers": ["pawflow"],
            "excludeTools": excluded_core_tools,
        }
        if model:
            settings["model"] = {"name": model}
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
    def _gemini_acp_settings_mcp_servers(mcp_servers: list, mcp_cwd: str) -> dict:
        """Convert ACP MCP server definitions to Gemini settings.json format.

        Kept for regression coverage and possible future native CLI use. ACP
        runtime passes MCP servers through session/new so Gemini does not start
        the same PawFlow bridge twice during initialize and session creation.
        """
        result: Dict[str, Any] = {}
        for server in mcp_servers or []:
            name = server.get("name") or "pawflow"
            env = {}
            for item in server.get("env") or []:
                if isinstance(item, dict) and item.get("name"):
                    env[item.get("name")] = item.get("value", "")
            entry = {
                "type": "stdio",
                "command": server.get("command"),
                "args": server.get("args") or [],
                "cwd": mcp_cwd,
                "env": env,
                "timeout": 15000,
                "trust": True,
            }
            result[name] = {k: v for k, v in entry.items() if v is not None}
        return result

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
