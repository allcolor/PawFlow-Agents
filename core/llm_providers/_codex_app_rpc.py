"""Codex app-server provider — JSON-RPC transport + item helpers.

Extracted from core/llm_providers/codex_app_server.py for the <=800-line
rule (invariant 2: composed back via MRO into LLMCodexAppServerMixin).
"""
import base64
import json
import logging
import mimetypes
import os
import queue
import threading
import uuid
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class _CodexAppServerProtocolError(Exception):
    """Raised when codex app-server returns an invalid JSON-RPC response."""


class _CodexAppRpcMixin:
    """JSON-RPC transport + Codex item/content helpers (MRO mixin)."""

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

    def _codex_app_request(self, proc, method: str, params: Optional[dict] = None,
                           stderr_lines: Optional[queue.Queue[str]] = None) -> dict:
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
                stderr_preview = self._codex_app_stderr_preview(stderr_lines)
                detail = f"codex app-server exited before response to {method}"
                if stderr_preview:
                    detail += f"; stderr:\n{stderr_preview}"
                raise _CodexAppServerProtocolError(
                    detail)
            if msg.get("id") != req_id:
                continue
            if msg.get("error"):
                raise _CodexAppServerProtocolError(
                    f"{method} failed: {msg.get('error')}")
            return msg.get("result") or {}

    def _codex_app_initialize(self, proc, stderr_lines: Optional[queue.Queue[str]] = None) -> None:
        from core import __version__
        self._codex_app_request(proc, "initialize", {
            "clientInfo": {
                "name": "pawflow_codex_app_server",
                "title": "PawFlow Codex App Server",
                "version": __version__,
            },
            "capabilities": {"experimentalApi": True},
        }, stderr_lines=stderr_lines)
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
    def _codex_app_stderr_preview(lines: Optional[queue.Queue[str]]) -> str:
        if lines is None:
            return ""
        buffered = []
        try:
            while len(buffered) < 20:
                buffered.append(lines.get_nowait())
        except queue.Empty:
            pass
        return "\n".join(buffered[-20:])

    @staticmethod
    def _codex_app_log_stderr(lines: queue.Queue[str]) -> None:
        preview = _CodexAppRpcMixin._codex_app_stderr_preview(lines)
        if preview:
            logger.warning("[codex-app] stderr: %s", preview)

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

    def _codex_app_attachment_items(
        self, attachments: list, *, user_id: str = "", conversation_id: str = "",
        workdir: str = "", container_dir: str = ""
    ) -> Optional[list]:
        items = []
        for attachment in attachments or []:
            if not isinstance(attachment, dict):
                continue
            url = attachment.get("url") or attachment.get("image_url") or ""
            path = attachment.get("path") or ""
            file_id = attachment.get("file_id") or ""
            if file_id:
                if not user_id or not conversation_id:
                    logger.warning(
                        "[codex-app] cannot steer FileStore attachment %s without user/conversation scope",
                        file_id)
                    return None
                try:
                    from core.file_store import FileStore
                    filename, data, content_type = FileStore.instance().get_required(
                        file_id, user_id=user_id, conversation_id=conversation_id)
                    items.append({
                        "type": "text",
                        "text": f"Attached image: fs://filestore/{file_id}/{filename}",
                    })
                    mime = (
                        attachment.get("mime_type")
                        or attachment.get("content_type")
                        or content_type
                        or mimetypes.guess_type(filename)[0]
                        or "image/png"
                    )
                    item = self._codex_app_image_item({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    }, workdir, container_dir)
                except Exception as exc:
                    logger.warning(
                        "[codex-app] failed to resolve steered FileStore attachment %s: %s",
                        file_id, exc)
                    return None
                if not item:
                    return None
                items.append(item)
                logger.info(
                    "[codex-app] loaded steered FileStore attachment: %s (%d bytes)",
                    file_id, len(data))
            elif url and self._codex_app_valid_remote_url(str(url)):
                items.append({"type": "image", "url": url})
            elif url:
                items.append({"type": "text", "text": f"Attached image: {url}"})
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
    def _codex_app_image_ref_from_args(args) -> str:
        if not isinstance(args, dict):
            return ""
        direct = str(args.get("path") or "")
        if direct.startswith("fs://"):
            return direct
        nested = args.get("arguments")
        if isinstance(nested, dict):
            path = str(nested.get("path") or "")
            if path.startswith("fs://"):
                return path
        return ""

    @staticmethod
    def _codex_app_mcp_content_text(content, image_ref: str = "") -> str:
        """Return transcript-safe text for MCP content blocks.

        Image blocks are already delivered to Codex as multimodal MCP content.
        PawFlow's transcript keeps only text plus a short FileStore reference;
        base64 image bytes never become counted context payload.
        """
        if not isinstance(content, list):
            return ""
        parts = []
        image_count = 0
        for part in content:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            ptype = part.get("type") or ""
            if ptype == "text":
                text = part.get("text") or ""
                if text:
                    parts.append(str(text))
                continue
            if ptype in ("image", "image_url"):
                image_count += 1
                continue
            text = part.get("text") or part.get("input_text") or part.get("output_text") or ""
            if text:
                parts.append(str(text))
        text = "\n".join(p for p in parts if p)
        if image_count and image_ref and image_ref not in text:
            ref_line = f"Image reference: {image_ref}"
            text = f"{text}\n{ref_line}" if text else ref_line
        if text:
            return text
        if image_count:
            return f"Image reference: {image_ref}" if image_ref else f"[image sent to vision: {image_count}]"
        return ""

    @staticmethod
    def _codex_app_result_text(item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        if item.get("error"):
            return str(item.get("error"))
        result = item.get("result")
        if isinstance(result, str):
            raw = result.strip()
            if raw.startswith(("{", "[")):
                try:
                    result = json.loads(raw)
                except Exception:
                    return result
            else:
                return result
        image_ref = _CodexAppRpcMixin._codex_app_image_ref_from_args(
            item.get("arguments") or {})
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            return _CodexAppRpcMixin._codex_app_mcp_content_text(
                result["content"], image_ref=image_ref)
        if isinstance(result, list):
            text = _CodexAppRpcMixin._codex_app_mcp_content_text(
                result, image_ref=image_ref)
            if text:
                return text
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
            content_items = item.get("contentItems")
            image_ref = _CodexAppRpcMixin._codex_app_image_ref_from_args(
                item.get("arguments") or {})
            if isinstance(content_items, list):
                text = _CodexAppRpcMixin._codex_app_mcp_content_text(
                    content_items, image_ref=image_ref)
                if text:
                    return text
                if any(isinstance(p, dict) and p.get("type") in ("image", "image_url")
                       for p in content_items):
                    return "[image sent to vision: 1]"
            return json.dumps({
                "status": item.get("status"),
                "success": item.get("success"),
                "contentItems": content_items,
            }, ensure_ascii=False, default=str)
        return json.dumps(item, ensure_ascii=False, default=str)
