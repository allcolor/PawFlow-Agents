"""Gemini ACP wire-protocol mixin for LLMGeminiMixin: process start, JSON-RPC
send/notify/request, session init/auth/new/load, stdout/stderr drains, MCP
settings, and content/token helpers. Also holds the ACP error classes.

Split out of gemini.py as a leaf mixin so the file stays <= 800 lines.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess  # nosec B404
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class _GeminiAcpProtocolError(Exception):
    """Raised when Gemini ACP returns an invalid JSON-RPC response."""


class _GeminiAcpCapacityError(_GeminiAcpProtocolError):
    """Raised when Gemini reports model capacity/quota exhaustion."""




class _GeminiAcpProtocolMixin:
    """Gemini ACP JSON-RPC wire protocol for LLMGeminiMixin."""

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
        from core import __version__
        return self._gemini_acp_request(proc, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {
                "name": "pawflow_gemini_acp",
                "title": "PawFlow Gemini ACP",
                "version": __version__,
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
                text_len = len(_GeminiAcpProtocolMixin._gemini_acp_content_text(content)) if content is not None else 0
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
            "allowMCPServers": ["pawflow"],
            "excludeTools": excluded_core_tools,
            "useWriteTodos": False,
            "modelConfigs": {
                "overrides": [
                    {
                        "match": {},
                        "modelConfig": {"generateContentConfig": generation_config},
                    }
                ],
                "customOverrides": [
                    {
                        "match": {},
                        "modelConfig": {"generateContentConfig": generation_config},
                    }
                ],
            },
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
            if ctype in ("image", "image_url", "image_ref"):
                return "[image]"
            if ctype == "content":
                return _GeminiAcpProtocolMixin._gemini_acp_content_text(content.get("content"))
            if ctype == "resource" and isinstance(content.get("resource"), dict):
                resource = content.get("resource") or {}
                return resource.get("text") or ""
            if "text" in content:
                return str(content.get("text") or "")
            return ""
        if isinstance(content, list):
            return "\n".join(
                part for part in (_GeminiAcpProtocolMixin._gemini_acp_content_text(p) for p in content)
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
                text = _GeminiAcpProtocolMixin._gemini_acp_content_text(item.get("content"))
                if text:
                    parts.append(text)
            elif item.get("type") == "diff":
                path = item.get("path") or ""
                parts.append(f"diff: {path}" if path else "diff")
            else:
                text = _GeminiAcpProtocolMixin._gemini_acp_content_text(item)
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
        if update.get("rawOutput") is not None:
            text = _GeminiAcpProtocolMixin._gemini_acp_content_text(update.get("rawOutput"))
            return text or "[non-text tool result]"
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
