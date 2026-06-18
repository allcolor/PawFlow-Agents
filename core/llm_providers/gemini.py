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
import re
import time
from typing import Any, Optional

from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT
from core.llm_providers.gemini_session import (
    GeminiSessionMixin, _get_sessions_base)
from core.llm_providers._gemini_acp import (  # noqa: F401 -- re-exported
    _GeminiAcpCapacityError, _GeminiAcpProtocolError, _GeminiAcpProtocolMixin)
from core.llm_providers._gemini_stream import _GeminiStreamMixin

logger = logging.getLogger(__name__)


class LLMGeminiMixin(_GeminiStreamMixin, _GeminiAcpProtocolMixin, GeminiSessionMixin):
    """Gemini ACP provider.

    The process speaks JSON-RPC over stdio. PawFlow starts one Gemini ACP
    process for the active turn, persists the ACP session id per conversation
    and agent, and sends images as ACP ``ContentBlock::Image`` values.
    """

    _GEMINI_PROVIDER = "gemini"
    _GEMINI_PAWFLOW_PREAMBLE = CLI_MCP_SYSTEM_PROMPT

    def _gemini_context_window(self, model: str) -> int:
        """Return Gemini's effective context budget for ``model``."""
        cfg = getattr(self, "_config_ref", None) or {}
        try:
            configured = int(cfg.get("max_context_size", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        runtime_windows = getattr(self, "_gemini_context_windows", None)
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
        wrapper_names = ("use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool")
        if raw_name not in wrapper_names:
            return raw_name
        for match in re.finditer(r'<tool_output\s+tool="([^"]+)"', result_text or ""):
            candidate = match.group(1)
            if candidate not in wrapper_names:
                return candidate
        match = re.search(
            r"tool_name['\"]?\s*[:=]\s*['\"]([^'\"}\s,]+)", result_text or "")
        if match:
            candidate = match.group(1)
            if candidate not in wrapper_names:
                return candidate
        return raw_name

    @staticmethod
    def _gemini_acp_clean_tool_result_text(result_text: str) -> str:
        """Drop Gemini/PawFlow wrapper tags from persisted tool results."""
        text = str(result_text or "")
        wrapper_names = ("use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool")
        for _ in range(3):
            match = re.match(
                r'\s*<tool_output\s+tool="([^"]+)">\n?(.*?)\n?</tool_output>',
                text,
                flags=re.DOTALL,
            )
            if not match or match.group(1) not in wrapper_names:
                break
            text = match.group(2).strip()
        return text

    @staticmethod
    def _gemini_acp_display_tool_call(raw_name: str, raw_args: Any,
                                      result_text: str = "") -> tuple[str, Any]:
        """Return the UI-facing PawFlow tool name and arguments."""
        try:
            from core.llm_client import unwrap_mcp_tool
            name, args = unwrap_mcp_tool(raw_name, raw_args or {})
        except Exception:
            name, args = raw_name, raw_args or {}
        if name in ("use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool"):
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
            if getattr(msg, "role", "") == "user":
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
                        new_content.append({
                            "type": "text",
                            "text": f"Attached image: fs://filestore/{fid}/{_fname}",
                        })
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
                        fid = block.get("file_id", "")
                        fname = block.get("filename", "image") or "image"
                        new_content.append({
                            "type": "text",
                            "text": (f"Attached image: fs://filestore/{fid}/{fname}"
                                     if fid else f"[image: {fname}]"),
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

    def _gemini_acp_full_initial_text(self, messages, workdir: str,
                                      container_dir: str) -> str:
        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        system_prompt = (
            self._GEMINI_PAWFLOW_PREAMBLE
            + ("\n\n" + system_prompt if system_prompt else "")
        )
        prompt = self._build_cli_initial_context_prompt(
            messages,
            system_prompt=system_prompt,
            user_text=user_text,
            workdir=workdir,
            provider_workdir=container_dir,
        )
        return prompt

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
        from core.cli_workspace_mounts import (
            build_cli_workspace_mount_args, build_skill_mount_args,
        )
        pool = GeminiPool.instance()
        workspace_mounts = [] if container_name else (
            build_cli_workspace_mount_args(
                conversation_id, agent_name, user_id=user_id)
            + build_skill_mount_args(
                conversation_id, agent_name, user_id=user_id))
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

