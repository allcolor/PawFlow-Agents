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

from core.docker_utils import docker_cmd as _docker_cmd, docker_popen, docker_rm

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

        Reads tokens from the LIVE service config (not cached attrs —
        tokens may have been added after the client was created).
        NO fallback to local ~/.claude/.

        Raises LLMClientError if no credentials configured.
        """
        from core.llm_client import LLMClientError

        # Read from live service config (may have been updated since client creation)
        access_token = getattr(self, 'claude_access_token', '') or ''
        refresh_token = getattr(self, 'claude_refresh_token', '') or ''
        expires_at = getattr(self, 'claude_expires_at', 0) or 0

        if not access_token:
            # Try live config from registry (tokens added after client init)
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
                    if getattr(sdef, "service_type", "") == "llmConnection":
                        cfg = getattr(sdef, "config", {}) or {}
                        if cfg.get("provider") == "claude-code" and cfg.get("claude_access_token"):
                            access_token = cfg["claude_access_token"]
                            refresh_token = cfg.get("claude_refresh_token", "")
                            expires_at = cfg.get("claude_expires_at", 0)
                            # Update self for next call
                            self.claude_access_token = access_token
                            self.claude_refresh_token = refresh_token
                            self.claude_expires_at = expires_at
                            break
            except Exception:
                pass

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

    def _recover_tokens(self, workdir: str):
        """Read back tokens from workdir after a run.

        Claude Code may have refreshed the access_token during the run.
        If tokens changed, update the service config so next run uses them.
        """
        creds_path = os.path.join(workdir, ".credentials.json")
        if not os.path.exists(creds_path):
            return
        try:
            with open(creds_path, "r", encoding="utf-8") as f:
                creds = json.load(f)
            oauth = creds.get("claudeAiOauth", {})
            new_access = oauth.get("accessToken", "")
            new_refresh = oauth.get("refreshToken", "")
            new_expires = oauth.get("expiresAt", 0)
            if not new_access:
                return

            # Check if tokens changed
            old_access = getattr(self, 'claude_access_token', '')
            if new_access == old_access:
                return

            # Update in-memory
            self.claude_access_token = new_access
            self.claude_refresh_token = new_refresh
            self.claude_expires_at = new_expires

            # Persist to service config
            from gui.services.global_service_registry import GlobalServiceRegistry
            for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
                cfg = getattr(sdef, "config", {}) or {}
                if cfg.get("provider") == "claude-code" and cfg.get("claude_access_token") == old_access:
                    cfg["claude_access_token"] = new_access
                    cfg["claude_refresh_token"] = new_refresh
                    cfg["claude_expires_at"] = new_expires
                    GlobalServiceRegistry.get_instance()._save_to_disk()
                    logger.info("Recovered refreshed Claude Code tokens for '%s'", sid)
                    break
        except Exception as e:
            logger.debug("Token recovery failed: %s", e)

    def _setup_mcp_config(self, workdir: str, user_id: str = "",
                          conversation_id: str = "",
                          agent_name: str = "") -> str:
        """Write MCP config to workdir and return the file path."""
        _containerize = getattr(self, 'containerize', False)

        if _containerize:
            mcp_bridge = "/opt/pawflow/mcp_bridge.py"
            python_bin = "python3"
        else:
            mcp_bridge = self._get_mcp_bridge_path()
            if not os.path.exists(mcp_bridge):
                return ""
            import sys as _sys
            python_bin = _sys.executable or "python"

        relay_url, relay_token = self._get_tool_relay_info()
        if not relay_url:
            logger.warning("No toolRelay service — MCP bridge will have no tools")

        # In Docker mode, replace localhost with host.docker.internal
        if _containerize and relay_url:
            relay_url = relay_url.replace("localhost", "host.docker.internal")
            relay_url = relay_url.replace("127.0.0.1", "host.docker.internal")

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
                          mcp_config_path: str = "",
                          workdir: str = "") -> list:
        """Build claude CLI command with bidirectional stream-json.

        --disallowedTools: blocks ALL built-in tools (filesystem is remote)
        --strict-mcp-config: ignores pre-existing MCP configs
        Only our pawflow MCP tools (get_tool_schema, use_tool) remain.
        If MCP fails, Claude Code has ZERO tools and stops.

        When containerize=True, wraps the command in docker run.
        """
        claude_args = [
            "-p",
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
            claude_args.extend(["--mcp-config", mcp_config_path])
        if session_id:
            claude_args.extend(["--resume", session_id])

        if not getattr(self, 'containerize', False):
            return [self.claude_binary] + claude_args

        # Docker mode: run Claude Code in a container
        image = getattr(self, 'docker_image', '') or "pawflow-claude-code:latest"
        cpu = getattr(self, 'docker_cpu_limit', '') or "2"
        mem = getattr(self, 'docker_memory_limit', '') or "2g"

        # Resolve host address for MCP bridge to connect back
        import platform
        if platform.system() == "Windows" or "microsoft" in platform.release().lower():
            host_addr = "host.docker.internal"
        else:
            host_addr = "host.docker.internal"  # works on Docker Desktop Linux too

        docker_run_args = [
            "--rm", "-i",
            "--cpus", cpu,
            "--memory", mem,
            "--name", f"pawflow-claude-{os.getpid()}",
            # Mount session dir for persistence (memories, CLAUDE.md)
            "-v", f"{workdir}:/workspace",
            # Environment — HOME must be /workspace so Claude Code
            # finds .credentials.json at $CLAUDE_CONFIG_DIR/
            "-e", "CLAUDE_CONFIG_DIR=/workspace",
            "-e", "HOME=/workspace",
            "-e", "NODE_OPTIONS=--max-old-space-size=1536",
            "-e", f"PAWFLOW_HOST={host_addr}",
            # Network: allow MCP bridge to reach host tool relay
            "--add-host", f"host.docker.internal:host-gateway",
            # Run as non-root: Claude Code refuses --dangerously-skip-permissions as root
            "--user", "1000:1000",
            # Security
            "--tmpfs", "/tmp:rw,nosuid,size=256m",
            "--security-opt", "no-new-privileges",
            image,
        ] + claude_args

        # Store args for docker_popen (used in _stream_claude_code)
        self._docker_run_args = docker_run_args
        return _docker_cmd() + ["run"] + docker_run_args

    # ── Process management ──────────────────────────────────────────

    def send_user_message(self, text: str):
        """Send a user message to the running Claude Code subprocess (preempt).

        Uses stream-json input format to inject a new user message while
        Claude Code is working — enables the same preempt behavior as
        other LLM providers.

        Sets _preempt_pending so _stream_claude_code knows to NOT break
        at the next result event — it must wait for the preempt's result too.
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
            self._preempt_pending = getattr(self, '_preempt_pending', 0) + 1
            logger.info("Sent preempt message to Claude Code (pending=%d): %.100s",
                        self._preempt_pending, text)
            return True
        except (OSError, BrokenPipeError) as e:
            logger.warning("Failed to send to Claude Code stdin: %s", e)
            return False

    def cancel_claude_code(self, force: bool = False):
        """Cancel Claude Code subprocess.

        force=False: graceful interrupt on stdin — Claude Code acknowledges
                     and responds with a summary, then the stream loop exits
                     normally on the result event. No kill.
        force=True: kill immediately.
        """
        proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            return

        if force:
            logger.info("FORCE KILLING Claude Code subprocess (pid=%d)", proc.pid)
            self._claude_proc = None
            try:
                proc.kill()
                # Docker mode: also force-remove the container
                if getattr(self, 'containerize', False):
                    container_name = f"pawflow-claude-{proc.pid}"
                    docker_rm(container_name)
            except OSError:
                pass
            return

        # Graceful interrupt: send interrupt message on stdin.
        # Claude Code will stop what it's doing, summarize, and send
        # a result event — the stream loop exits normally.
        logger.info("Interrupting Claude Code subprocess (pid=%d)", proc.pid)
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

    def _cleanup_proc(self, proc):
        """Clean up a Claude Code subprocess (and Docker container if applicable)."""
        self._claude_proc = None
        # Docker mode: kill container FIRST (proc.kill only kills wsl, not the container)
        if getattr(self, 'containerize', False):
            container_name = f"pawflow-claude-{proc.pid}"
            try:
                from core.docker_utils import docker_rm
                docker_rm(container_name)
            except Exception:
                pass
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
            proc.wait(timeout=3)
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
    def _extract_images(messages) -> list:
        """Extract images from messages and return as Anthropic content blocks.

        Removes image blocks from messages (so they don't bloat the text
        prompt) and returns them as content blocks for the stream-json
        message. This enables native vision in Claude Code.

        Returns list of {"type": "image", "source": {"type": "base64", ...}}
        """
        import base64 as _b64
        image_blocks = []

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
                            continue
                        except Exception as e:
                            logger.warning("Failed to extract image: %s", e)

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        image_blocks.append(block)
                        logger.info("Extracted image for vision: %s",
                                    source.get("media_type", "?"))
                        continue

                new_content.append(block)

            m.content = new_content

        return image_blocks

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

        # Extract images BEFORE serialization (they'll be sent as content blocks)
        image_blocks = self._extract_images(messages)

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
        _containerize = getattr(self, 'containerize', False)

        # In Docker mode, MCP config path is relative to /workspace
        _mcp_arg = mcp_path
        if _containerize and mcp_path:
            _mcp_arg = "/workspace/" + os.path.basename(mcp_path)

        cmd = self._build_claude_cmd(model, session_id,
                                     mcp_config_path=_mcp_arg,
                                     workdir=workdir)

        logger.info("claude-code stream: cwd=%s, containerize=%s, cmd=%s",
                     workdir, _containerize, " ".join(cmd[:8]) + "...")

        try:
            if _containerize:
                proc = docker_popen(
                    self._docker_run_args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
            else:
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
            _bin = "docker" if _containerize else self.claude_binary
            raise LLMClientError(
                f"Binary '{_bin}' not found. "
                + ("Install Docker Desktop." if _containerize
                   else "Install with: npm install -g @anthropic-ai/claude-code"))

        # Send initial message as stream-json (keep stdin open for preempt/interrupt)
        try:
            if image_blocks:
                # Multipart: text + images as content array (enables vision)
                content = [{"type": "text", "text": initial_text}] + image_blocks
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": content},
                })
            else:
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
        _current_msg_id: str = ""  # track message ID to detect incremental updates
        self._preempt_pending = 0  # reset at start of each stream

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
                logger.info("[claude-code] flush turn %d: text=%d chars, tc=%d, callback=%s",
                            _turn_count, len(text), len(tc), bool(turn_callback))
                try:
                    turn_callback(text, tc)
                except Exception as e:
                    logger.error("[claude-code] turn_callback error: %s", e,
                                 exc_info=True)
            elif text or tc:
                logger.warning("[claude-code] flush turn %d but NO turn_callback: text=%d, tc=%d",
                               _turn_count, len(text), len(tc))
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
                    # Capture AND persist session_id from init event immediately.
                    # Must be in ConversationStore before any preempt triggers
                    # _prepare_agent_context (which checks for session to skip compact).
                    sid = event.get("session_id", "")
                    if sid:
                        self._claude_session_id = sid
                        if conv_id:
                            try:
                                from core.conversation_store import ConversationStore
                                ConversationStore.instance().set_extra(
                                    conv_id,
                                    f"claude_session:{agent_name or 'default'}",
                                    sid)
                            except Exception:
                                pass
                    continue

                if etype == "assistant":
                    msg = event.get("message", {})
                    msg_id = msg.get("id", "")

                    # Claude Code sends INCREMENTAL updates for the same message:
                    # event 1: [thinking], event 2: [text], event 3: [tool_use]
                    # Each event has ONLY the new block, not all blocks.
                    # Same msg_id = same turn → just append (don't clear).
                    if msg_id and msg_id != _current_msg_id:
                        # New message — flush previous turn
                        if _turn_count > 0:
                            _flush_turn()
                        _turn_count += 1
                        _current_msg_id = msg_id
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
                            # Unwrap MCP wrapper for display:
                            # mcp__pawflow__use_tool(tool_name=X, arguments={...})
                            # → X({...})
                            _tc_name = block.get("name", "")
                            _tc_args = block.get("input", {})
                            if _tc_name == "mcp__pawflow__use_tool" and isinstance(_tc_args, dict):
                                _tc_name = _tc_args.get("tool_name", _tc_name)
                                _tc_args = _tc_args.get("arguments", _tc_args)
                            elif _tc_name == "mcp__pawflow__get_tool_schema":
                                _tc_args = _tc_args  # keep as-is
                            _pub("tool_call", {
                                "tool": _tc_name,
                                "arguments": _tc_args,
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "via": "claude-code",
                            })
                        elif btype == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                _turn_thinking = thinking
                                _pub("thinking_content", {
                                    "text": thinking,
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
                            # Resolve tool name from turn_tool_calls
                            _tr_name = tc_id
                            for _tc in _turn_tool_calls:
                                if _tc.get("id") == tc_id:
                                    _tr_name = _tc.get("name", tc_id)
                                    # Unwrap MCP wrapper name
                                    if _tr_name == "mcp__pawflow__use_tool":
                                        _tr_name = _tc.get("arguments", {}).get("tool_name", _tr_name)
                                    break
                            _pub("tool_result", {
                                "tool": _tr_name,
                                "result": result_str[:300],
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
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
                    _flush_turn()
                    # Check for API errors (auth failure, rate limit, etc.)
                    if event.get("is_error") or event.get("subtype") == "error_during_execution":
                        _err_text = event.get("result", "")
                        if "authentication" in _err_text.lower() or "401" in _err_text:
                            raise LLMClientError(f"Claude Code auth failed: {_err_text[:300]}")
                        # Other errors: log but continue (may have partial results)
                        logger.warning("[claude-code] result has is_error=True: %s", _err_text[:200])
                    result_text = event.get("result", "")
                    if not turn_callback and result_text and not content_parts:
                        content_parts.append(result_text)
                        if callback:
                            callback(result_text)
                    last_data = event
                    # Publish token stats for the webchat
                    _usage = event.get("usage", {})
                    # Total input = direct + cache_read + cache_creation
                    _total_in = (_usage.get("input_tokens", 0)
                                 + _usage.get("cache_read_input_tokens", 0)
                                 + _usage.get("cache_creation_input_tokens", 0))
                    _total_out = _usage.get("output_tokens", 0)
                    logger.info("[claude-code] result event keys: %s, usage=%s, model=%s",
                                list(event.keys()), _usage, event.get("model", "?"))
                    # model is in modelUsage keys, not at top level
                    _model_usage = event.get("modelUsage", {})
                    _result_model = (event.get("model")
                                     or (list(_model_usage.keys())[0] if _model_usage else "")
                                     or model)
                    if _total_in or _total_out:
                        # Get the msg_id of the last assistant message (from turn_callback)
                        _last_msg_id = ""
                        try:
                            _last_msg_id = getattr(self, '_last_turn_msg_id', "") or ""
                        except Exception:
                            pass
                        _pub("message_meta", {
                            "msg_id": _last_msg_id,
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
                    # If preempt messages were injected via stdin, don't break —
                    # Claude Code will process them and send another result.
                    _pending = getattr(self, '_preempt_pending', 0)
                    if _pending > 0:
                        self._preempt_pending = _pending - 1
                        logger.info("[claude-code] result event but preempt pending (%d) "
                                    "— continuing stream", _pending - 1)
                        # Reset turn state for the next message
                        _turn_count = 0
                        _current_msg_id = ""
                        continue
                    break

        finally:
            # Flush any pending turn (ensures last text is persisted even if interrupted)
            try:
                _flush_turn()
            except Exception:
                pass
            # Capture stderr BEFORE cleanup closes the pipes
            try:
                _stderr = proc.stderr.read() if proc.stderr and not proc.stderr.closed else ""
            except Exception:
                _stderr = ""
            self._cleanup_proc(proc)
            # Recover refreshed tokens from workdir (Claude Code may have refreshed them)
            self._recover_tokens(workdir)

        # Don't error on non-zero exit if we got a successful result
        # (process was killed after break on result event — that's expected)
        _got_result = bool(last_data.get("session_id") or last_data.get("result"))
        if proc.returncode and proc.returncode != 0 and not _got_result:
            if _stderr:
                logger.error("Claude CLI stderr: %.500s", _stderr)
            raise LLMClientError(
                f"Claude CLI stream exited with code {proc.returncode}"
                + (f": {_stderr[:200]}" if _stderr else ""))

        # If turn_callback handled all turns, don't return content
        # (prevents agent loop from persisting the same text again)
        full_content = "" if turn_callback else "".join(content_parts)

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
        _ti = (usage.get("input_tokens", 0)
               + usage.get("cache_read_input_tokens", 0)
               + usage.get("cache_creation_input_tokens", 0))
        _to = usage.get("output_tokens", 0)
        return LLMResponse(
            content=full_content,
            model=last_data.get("model", model),
            tokens_in=_ti,
            tokens_out=_to,
            total_tokens=_ti + _to,
            finish_reason="stop",
            raw=last_data,
        )
