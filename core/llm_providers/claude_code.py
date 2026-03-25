"""LLM provider mixin -- Claude Code CLI (subprocess-based).

Uses --input-format stream-json + --output-format stream-json for
bidirectional streaming. This enables:
- Real-time output streaming (tool calls, text, thinking)
- Preempt: send new user messages on stdin while Claude Code works
- Interrupt: send interrupt signal on stdin to stop gracefully
"""

import json
import logging
import os
import subprocess
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

# Base directory for per-session Claude Code workdirs
_SESSIONS_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "claude_sessions",
)


class LLMClaudeCodeMixin:
    """Claude Code CLI provider using bidirectional stream-json.

    Claude Code uses MCP natively for tool access. PawFlow tools are
    exposed via the MCP bridge (tools/mcp_bridge.py) configured with
    --mcp-config.

    Each session runs in its own working directory under
    data/claude_sessions/<conversation_id>/<agent_name>/ so Claude Code
    doesn't read/modify the PawFlow server code.

    stdin (stream-json input):
        {"type": "user", "content": "initial prompt"}
        {"type": "user", "content": "follow-up while working"}  # preempt

    stdout (stream-json output):
        {"type": "system", "subtype": "init", ...}
        {"type": "assistant", "message": {...}}
        {"type": "user", "message": {...}}  # tool results
        {"type": "result", ...}
    """

    def _get_session_workdir(self, conversation_id: str,
                             agent_name: str = "") -> str:
        """Get or create a dedicated working directory for this session."""
        cid = conversation_id or "default"
        agent = agent_name or "default"
        workdir = os.path.join(_SESSIONS_BASE, cid, agent)
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def _claude_code_env(self, workdir: str = "") -> dict:
        """Build environment for claude subprocess.

        Sets CLAUDE_CONFIG_DIR to the session workdir so Claude Code
        reads credentials from our managed .credentials.json instead
        of the user's ~/.claude/.credentials.json.
        """
        env = os.environ.copy()
        if workdir:
            env["CLAUDE_CONFIG_DIR"] = workdir
        return env

    # Cached tool relay info (shared across all claude-code agents)
    _tool_relay_cache: Optional[tuple] = None

    @classmethod
    def _get_tool_relay_info(cls) -> tuple:
        """Get the shared tool relay service (created once, reused by all agents).

        Returns (url, token). Creates the service on first call.
        """
        if cls._tool_relay_cache:
            return cls._tool_relay_cache
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()

            # Check if a live tool relay already exists (from this server run)
            for sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") == "toolRelay":
                    svc = greg.get_live_instance(sid)
                    if svc:
                        cfg = getattr(sdef, "config", {}) or {}
                        port = int(cfg.get("port", 0))
                        token = cfg.get("token", "")
                        if port and token:
                            cls._tool_relay_cache = (
                                f"wss://localhost:{port}/ws/tools", token)
                            return cls._tool_relay_cache
                    # Stale from previous run — remove it
                    try:
                        greg.uninstall(sid)
                    except Exception:
                        pass

            # Create fresh tool relay with dynamic port
            import uuid
            import socket as _sock
            with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                _s.bind(("", 0))
                free_port = _s.getsockname()[1]
            token = uuid.uuid4().hex
            service_id = "_tool_relay"
            greg.install(service_id, "toolRelay", {
                "port": free_port,
                "path": "/ws/tools",
                "token": token,
                "_service_id": service_id,
            }, description="Auto-created tool relay for Claude Code MCP bridge")
            svc = greg.get_live_instance(service_id)
            if svc:
                logger.info("Tool relay created: port=%d", free_port)
                cls._tool_relay_cache = (
                    f"wss://localhost:{free_port}/ws/tools", token)
                return cls._tool_relay_cache
        except Exception as e:
            logger.error("Failed to get/create tool relay: %s", e)
        return "", ""

    def _setup_credentials(self, workdir: str):
        """Write .credentials.json in session workdir for Claude Code auth.

        Reads tokens from the service config (stored in PawFlow secrets).
        NO fallback to local ~/.claude/ — each service must have its own
        credentials configured via the admin panel login flow.

        Raises LLMClientError if no credentials configured.
        """
        from core.llm_client import LLMClientError

        access_token = getattr(self, 'claude_access_token', '') or ''
        refresh_token = getattr(self, 'claude_refresh_token', '') or ''
        expires_at = getattr(self, 'claude_expires_at', 0) or 0

        if not access_token:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Go to Admin → Services → claude_code_llm_service → Login "
                "to authenticate with your Claude subscription.")

        creds = {
            "claudeAiOauth": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresAt": int(expires_at),
                "scopes": [
                    "org:create_api_key",
                    "user:profile",
                    "user:inference",
                    "user:sessions:claude_code",
                    "user:mcp_servers",
                    "user:file_upload",
                ],
            }
        }
        creds_path = os.path.join(workdir, ".credentials.json")
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(creds, f)

    def _setup_mcp_config(self, workdir: str, user_id: str = "",
                          conversation_id: str = "",
                          agent_name: str = "") -> str:
        """Write MCP config to workdir and return the file path."""
        mcp_bridge = self._get_mcp_bridge_path()
        if not os.path.exists(mcp_bridge):
            return ""
        import sys as _sys
        python_bin = _sys.executable or "python"

        relay_url, relay_token = self._get_tool_relay_info()
        if not relay_url:
            logger.warning("No toolRelay service — MCP bridge will have no tools")

        config = {
            "mcpServers": {
                "pawflow": {
                    "command": python_bin,
                    "args": [mcp_bridge],
                    "env": {
                        "PAWFLOW_TOOL_RELAY_URL": relay_url,
                        "PAWFLOW_TOOL_RELAY_TOKEN": relay_token,
                        "PAWFLOW_USER_ID": user_id or "",
                        "PAWFLOW_CONVERSATION_ID": conversation_id or "",
                        "PAWFLOW_AGENT_NAME": agent_name or "",
                    },
                }
            }
        }

        mcp_path = os.path.join(workdir, ".mcp.json")
        with open(mcp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        logger.info("MCP config written: %s (relay=%s)", mcp_path, relay_url)
        return mcp_path

    def _get_mcp_bridge_path(self) -> str:
        """Path to the MCP bridge script (tools/mcp_bridge.py at project root)."""
        project_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(project_root, "tools", "mcp_bridge.py")

    # All built-in Claude Code tools that must be disabled
    # (server filesystem ≠ user's filesystem — everything goes through MCP)
    _DISALLOWED_BUILTIN_TOOLS = (
        "Bash,Edit,Read,Write,Glob,Grep,NotebookEdit,WebFetch,WebSearch,"
        "Task,Agent,ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,"
        "EnterPlanMode,ExitPlanMode,EnterWorktree,ExitWorktree,"
        "RemoteTrigger,Skill,TaskOutput,TaskStop,TodoWrite,"
        "CronCreate,CronDelete,CronList,AskUserQuestion"
    )

    def _build_claude_cmd(self, model: str,
                          session_id: str = "",
                          mcp_config_path: str = "") -> list:
        """Build claude CLI command with bidirectional stream-json.

        --disallowedTools: blocks ALL built-in tools (filesystem is remote)
        --strict-mcp-config: ignores pre-existing MCP configs
        Only our pawflow MCP tools (get_tool_schema, use_tool) remain.
        If MCP fails, Claude Code has ZERO tools and stops.
        """
        cmd = [
            self.claude_binary, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "1000",
            "--verbose",
            "--strict-mcp-config",
            "--disallowedTools", self._DISALLOWED_BUILTIN_TOOLS,
        ]
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    # ── Process management ──────────────────────────────────────────

    def send_user_message(self, text: str):
        """Send a user message to the running Claude Code subprocess (preempt).

        Uses stream-json input format to inject a new user message while
        Claude Code is working — enables the same preempt behavior as
        other LLM providers.
        """
        proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            logger.warning("No running Claude Code process to send message to")
            return False
        try:
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": text},
            })
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
            logger.info("Sent preempt message to Claude Code: %.100s", text)
            return True
        except (OSError, BrokenPipeError) as e:
            logger.warning("Failed to send to Claude Code stdin: %s", e)
            return False

    def cancel_claude_code(self):
        """Graceful interrupt then kill after timeout.

        1. Send [Request interrupted by user] on stdin — Claude Code
           finishes current turn then stops.
        2. If still running after 5s, kill the process.
        """
        proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            return
        logger.info("Interrupting Claude Code subprocess (pid=%d)", proc.pid)

        # Graceful: send interrupt message
        try:
            if proc.stdin and not proc.stdin.closed:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user",
                                "content": "[Request interrupted by user]"},
                })
                proc.stdin.write(msg + "\n")
                proc.stdin.flush()
        except (OSError, BrokenPipeError):
            pass

        # Wait up to 5s for graceful shutdown, then kill
        def _kill_after_timeout():
            try:
                proc.wait(timeout=5)
            except Exception:
                logger.info("Claude Code did not stop in 5s — killing pid=%d", proc.pid)
                try:
                    proc.kill()
                except OSError:
                    pass

        import threading as _th
        _th.Thread(target=_kill_after_timeout, daemon=True,
                   name="claude-code-kill-timeout").start()

    def _cleanup_proc(self, proc):
        """Clean up a Claude Code subprocess."""
        self._claude_proc = None
        for stream in (proc.stdout, proc.stdin, proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    # ── Non-streaming (complete) ────────────────────────────────────

    def _complete_claude_code(
        self, messages, model, temperature, max_tokens, tools=None,
    ):
        """Run claude CLI and parse the response (non-streaming)."""
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        session_id = getattr(self, '_claude_session_id', "")
        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        workdir = self._get_session_workdir(conv_id, agent_name)
        mcp_path = self._setup_mcp_config(workdir, user_id, conv_id, agent_name)

        # For complete mode, use text input / json output (simpler)
        cmd = [
            self.claude_binary, "-p",
            "--output-format", "json",
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "1000",
            "--verbose",
        ]
        if mcp_path:
            cmd.extend(["--mcp-config", mcp_path])

        logger.debug("claude-code complete: cwd=%s, input=%d chars", workdir, len(stdin_text))

        try:
            result = subprocess.run(
                cmd, input=stdin_text, capture_output=True, text=True,
                timeout=self.timeout, cwd=workdir,
                env=self._claude_code_env(workdir), encoding="utf-8",
            )
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code")
        except subprocess.TimeoutExpired:
            raise LLMClientError(f"Claude CLI timed out after {self.timeout}s")

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMClientError(
                f"Claude CLI exited with code {result.returncode}: {stderr[:500]}")

        stdout = result.stdout.strip()
        if not stdout:
            raise LLMClientError("Claude CLI returned empty output")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return LLMResponse(content=stdout, model=model, finish_reason="stop")

        content = data.get("result", data.get("content", ""))
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text")

        new_session = data.get("session_id", "")
        if new_session:
            self._claude_session_id = new_session

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            tokens_in=data.get("usage", {}).get("input_tokens", 0),
            tokens_out=data.get("usage", {}).get("output_tokens", 0),
            total_tokens=(data.get("usage", {}).get("input_tokens", 0)
                          + data.get("usage", {}).get("output_tokens", 0)),
            finish_reason="stop",
            raw=data,
        )

    # ── Streaming ───────────────────────────────────────────────────

    @staticmethod
    def _externalize_attachments(messages):
        """Replace inline images/attachments with FileStore links IN-PLACE.

        Modifies message content blocks: replaces image blocks with text
        links. Must run BEFORE _serialize_messages_for_cli to prevent
        base64 data from bloating the prompt.
        """
        import base64 as _b64
        from core.file_store import FileStore
        store = FileStore.instance()

        for m in messages:
            if not isinstance(m.content, list):
                continue
            new_content = []
            for block in m.content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                btype = block.get("type", "")

                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        try:
                            header, data_b64 = url.split(",", 1)
                            mime = header.split(":")[1].split(";")[0]
                            data = _b64.b64decode(data_b64)
                            ext = mime.split("/")[-1].split("+")[0]
                            fname = f"image.{ext}"
                            file_id = store.store(fname, data, content_type=mime)
                            new_content.append({
                                "type": "text",
                                "text": (f"[Image attached: fs://filestore/{file_id}/{fname} "
                                         f"({len(data)} bytes) — use show_file tool with "
                                         f"file_id='{file_id}' to view, or filesystem "
                                         f"read_file with path=fs://filestore/{file_id}/{fname}]"),
                            })
                            logger.info("Externalized image: %s (%d bytes)", file_id, len(data))
                            continue
                        except Exception as e:
                            logger.warning("Failed to externalize image: %s", e)

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        try:
                            data = _b64.b64decode(source.get("data", ""))
                            mime = source.get("media_type", "image/png")
                            ext = mime.split("/")[-1].split("+")[0]
                            fname = f"image.{ext}"
                            file_id = store.store(fname, data, content_type=mime)
                            new_content.append({
                                "type": "text",
                                "text": (f"[Image attached: fs://filestore/{file_id}/{fname} "
                                         f"({len(data)} bytes) — use show_file tool with "
                                         f"file_id='{file_id}' to view, or filesystem "
                                         f"read_file with path=fs://filestore/{file_id}/{fname}]"),
                            })
                            logger.info("Externalized image: %s (%d bytes)", file_id, len(data))
                            continue
                        except Exception as e:
                            logger.warning("Failed to externalize image: %s", e)

                new_content.append(block)

            m.content = new_content

    @staticmethod
    def _build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text for text-mode input."""
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n" + system_prompt
            + "\n</system_instructions>\n\n" + user_text
        )

    def _stream_claude_code(
        self, messages, model, temperature, max_tokens, tools, callback,
        turn_callback=None,
    ):
        """Stream from claude CLI using bidirectional stream-json.

        Input: JSON lines on stdin (user messages, can preempt anytime)
        Output: JSON lines on stdout (events: assistant, user, result, etc.)

        turn_callback(text, tool_calls): called at each turn boundary so
        the agent loop can persist intermediate messages. Each Claude Code
        assistant turn = one message in the conversation.

        Claude Code uses MCP for tool calls — tools param is ignored.
        """
        from core.llm_client import LLMClientError, LLMResponse

        # Replace inline images with FileStore links BEFORE serialization
        self._externalize_attachments(messages)

        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)

        initial_text = self._build_stdin_with_system(system_prompt, user_text)

        session_id = getattr(self, '_claude_session_id', "")
        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        # Restore session_id from conversation store if not in memory
        if not session_id and conv_id:
            try:
                from core.conversation_store import ConversationStore
                session_id = ConversationStore.instance().get_extra(
                    conv_id, f"claude_session:{agent_name or 'default'}") or ""
                if session_id:
                    self._claude_session_id = session_id
                    logger.info("Restored claude session: %s", session_id)
            except Exception:
                pass

        logger.info("claude-code stream: conv_id='%s' user='%s' agent='%s' session='%s'",
                     conv_id, user_id, agent_name, session_id[:12] if session_id else "new")

        workdir = self._get_session_workdir(conv_id, agent_name)
        self._setup_credentials(workdir)
        mcp_path = self._setup_mcp_config(workdir, user_id, conv_id, agent_name)
        cmd = self._build_claude_cmd(model, session_id, mcp_config_path=mcp_path)

        logger.info("claude-code stream: cwd=%s, cmd=%s", workdir, " ".join(cmd[:8]) + "...")

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=workdir,
                env=self._claude_code_env(workdir),
                encoding="utf-8",
            )
            self._claude_proc = proc
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code")

        # Send initial message as stream-json (keep stdin open for preempt/interrupt)
        try:
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": initial_text},
            })
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
        except BrokenPipeError:
            stderr = ""
            try:
                stderr = proc.stderr.read().strip()
            except Exception:
                pass
            proc.wait()
            raise LLMClientError(
                f"Claude CLI pipe broken (exit {proc.returncode}): {stderr[:500]}")

        # SSE publisher for webchat visibility
        def _pub(event_type, data):
            if not conv_id:
                return
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conv_id, event_type, data)
            except Exception:
                pass

        # Read streaming output — accumulate per turn
        content_parts: List[str] = []  # final result text
        last_data: dict = {}
        _turn_count = 0

        # Per-turn accumulator
        _turn_text_parts: List[str] = []
        _turn_tool_calls: list = []
        _turn_thinking: str = ""
        _tool_results: dict = {}  # tool_use_id → result text

        def _flush_turn():
            """Emit the accumulated turn via turn_callback."""
            nonlocal _turn_text_parts, _turn_tool_calls, _turn_thinking, content_parts
            text = "".join(_turn_text_parts).strip()
            tc = _turn_tool_calls[:]
            thinking = _turn_thinking
            # Attach results to tool calls
            for t in tc:
                t["result"] = _tool_results.pop(t.get("id", ""), None)
            # Attach thinking
            for t in tc:
                t["thinking"] = thinking
                thinking = ""  # only first tc gets thinking
            _turn_text_parts = []
            _turn_tool_calls = []
            _turn_thinking = ""
            if (text or tc) and turn_callback:
                try:
                    turn_callback(text, tc)
                except Exception as e:
                    logger.error("[claude-code] turn_callback error: %s", e,
                                 exc_info=True)
                # Tell webchat to finalize current streaming element
                _pub("turn_complete", {
                    "agent_name": agent_name,
                    "turn": _turn_count,
                })
                # Clear content_parts — intermediate turns are persisted
                # by turn_callback. Only the LAST turn stays in content_parts
                content_parts.clear()

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                logger.info("[claude-code] %s %.200s", etype, json.dumps(event))

                if etype == "system":
                    # Capture session_id from init event (for --resume on next call)
                    sid = event.get("session_id", "")
                    if sid:
                        self._claude_session_id = sid
                    continue

                if etype == "assistant":
                    # New assistant turn — flush previous turn first
                    if _turn_count > 0:
                        _flush_turn()

                    _turn_count += 1
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        btype = block.get("type", "")
                        if btype == "text":
                            text = block.get("text", "")
                            if text:
                                _turn_text_parts.append(text)
                                content_parts.append(text)
                                if callback:
                                    callback(text)
                        elif btype == "tool_use":
                            _turn_tool_calls.append({
                                "name": block.get("name", ""),
                                "arguments": block.get("input", {}),
                                "id": block.get("id", ""),
                            })
                            _pub("tool_call", {
                                "tool": block.get("name", ""),
                                "arguments": block.get("input", {}),
                                "agent_name": agent_name,
                                "via": "claude-code",
                            })
                        elif btype == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                _turn_thinking = thinking
                                _pub("narration", {
                                    "text": thinking[:300],
                                    "agent_name": agent_name,
                                })
                    # Update turn count on status
                    _pub("heartbeat", {
                        "agent_name": agent_name,
                        "status": f"turn {_turn_count}",
                        "iteration": _turn_count,
                    })
                    last_data = msg

                elif etype == "user":
                    # Tool results — capture for persistence + forward to webchat
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "tool_result":
                            tc_id = block.get("tool_use_id", "")
                            result_text = block.get("content", "")
                            if isinstance(result_text, list):
                                # Content blocks format
                                result_text = " ".join(
                                    b.get("text", "") for b in result_text
                                    if isinstance(b, dict))
                            result_str = str(result_text) if result_text else "(no output)"
                            # Store for turn_callback persistence
                            if tc_id:
                                _tool_results[tc_id] = result_str
                            _pub("tool_result", {
                                "tool": tc_id,
                                "result": result_str[:300],
                                "agent_name": agent_name,
                                "via": "claude-code",
                            })

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        _turn_text_parts.append(text)
                        content_parts.append(text)
                        if callback:
                            callback(text)

                elif etype == "result":
                    # Final result — flush last turn and exit the loop
                    # (with stream-json input, Claude Code stays alive
                    #  waiting for more messages — we must break here)
                    _flush_turn()
                    result_text = event.get("result", "")
                    if result_text and not content_parts:
                        content_parts.append(result_text)
                        if callback:
                            callback(result_text)
                    last_data = event
                    # Publish token stats for the webchat
                    _usage = event.get("usage", {})
                    _total_in = _usage.get("input_tokens", 0)
                    _total_out = _usage.get("output_tokens", 0)
                    _result_model = event.get("model", model)
                    if _total_in or _total_out:
                        _pub("message_meta", {
                            "agent_name": agent_name,
                            "source": {
                                "type": "agent", "name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "provider": "claude-code",
                                "model": _result_model,
                                "tokens_in": _total_in,
                                "tokens_out": _total_out,
                            },
                            "model": _result_model,
                            "provider": "claude-code",
                            "tokens_in": _total_in,
                            "tokens_out": _total_out,
                            "num_turns": event.get("num_turns", _turn_count),
                            "duration_ms": event.get("duration_ms", 0),
                        })
                    break

        finally:
            self._cleanup_proc(proc)
            _stderr = ""

        if proc.returncode and proc.returncode != 0:
            if _stderr:
                logger.error("Claude CLI stderr: %.500s", _stderr)
            raise LLMClientError(
                f"Claude CLI stream exited with code {proc.returncode}"
                + (f": {_stderr[:200]}" if _stderr else ""))

        full_content = "".join(content_parts)

        new_session = last_data.get("session_id", "")
        if new_session:
            self._claude_session_id = new_session
            # Persist session_id in conversation store for resume after restart
            if conv_id:
                try:
                    from core.conversation_store import ConversationStore
                    ConversationStore.instance().set_extra(
                        conv_id, f"claude_session:{agent_name or 'default'}",
                        new_session)
                except Exception:
                    pass

        usage = last_data.get("usage", {})
        return LLMResponse(
            content=full_content,
            model=last_data.get("model", model),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            finish_reason="stop",
            raw=last_data,
        )
