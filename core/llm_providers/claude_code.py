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
import time
from typing import List

from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin, _SESSIONS_BASE

logger = logging.getLogger(__name__)


class LLMClaudeCodeMixin(ClaudeCodeSessionMixin):
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

    # Session/workdir methods inherited from ClaudeCodeSessionMixin:
    # _get_session_workdir, _claude_code_env, _setup_credentials,
    # _recover_tokens, _setup_mcp_config, _build_claude_cmd,
    # _get_tool_relay_info, _get_mcp_bridge_path, _DISALLOWED_BUILTIN_TOOLS

    # ── Process management ──────────────────────────────────────────

    def send_user_message(self, text: str, attachments: list = None):
        """Send a user message to the running Claude Code subprocess (preempt).

        Uses stream-json input format to inject a new user message while
        Claude Code is working — enables the same preempt behavior as
        other LLM providers.

        Args:
            text: User message text.
            attachments: Optional list of attachment dicts with base64 image data.

        Sets _preempt_pending so _stream_claude_code knows to NOT break
        at the next result event — it must wait for the preempt's result too.
        """
        proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            logger.warning("No running Claude Code process to send message to")
            return False
        try:
            # Multi-agent catch-up: inject messages from other agents before user msg
            conv_id = getattr(self, '_conversation_id', "")
            agent_name = getattr(self, '_agent_name', "")
            if conv_id and agent_name:
                catchup = self._build_catchup_context(conv_id, agent_name)
                if catchup:
                    text = catchup + "\n\n" + text

            # Build content: text + optional images
            if attachments:
                content = []
                if text:
                    content.append({"type": "text", "text": text})
                for att in attachments:
                    if isinstance(att, dict) and att.get("data"):
                        mime = att.get("mime_type", "image/png")
                        content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": att["data"],
                            },
                        })
            else:
                content = text

            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": content},
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
                # Pool mode: just kill the exec process, release the slot
                # (don't kill the container — other sessions may be using it)
                _pool_name = getattr(self, '_pool_container_name', None)
                if _pool_name:
                    from core.claude_code_pool import ClaudeCodePool
                    ClaudeCodePool.instance().release(_pool_name)
                    self._pool_container_name = None
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

    def _cleanup_proc(self, proc) -> str:
        """Clean up a Claude Code subprocess. Returns captured stderr."""
        self._claude_proc = None
        # Pool mode: release slot (don't kill the container)
        _pool_name = getattr(self, '_pool_container_name', None)
        if _pool_name:
            self._pool_release(_pool_name)
            self._pool_container_name = None
        # Kill process FIRST so pipes become readable (no more blocking)
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        # NOW read stderr (process is dead, read won't block)
        stderr = ""
        try:
            if proc.stderr and not proc.stderr.closed:
                stderr = proc.stderr.read() or ""
        except Exception:
            pass
        # Close all streams
        for stream in (proc.stdout, proc.stdin, proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        return stderr

    # ── Non-streaming (complete) ────────────────────────────────────

    def _pool_popen(self, workdir: str, cmd: list, **popen_kwargs) -> tuple:
        """Launch claude in a pool container or locally.

        Returns (proc, pool_container_name). Caller must release pool_container
        when done (if not None).
        """
        _containerize = getattr(self, 'containerize', False)
        if _containerize:
            from core.claude_code_pool import ClaudeCodePool
            pool = ClaudeCodePool.instance()
            container = pool.acquire()
            _rel = os.path.relpath(workdir, _SESSIONS_BASE).replace("\\", "/")
            _session_dir = f"/cc_sessions/{_rel}"
            proc = pool.exec_claude(
                container, _session_dir, cmd[1:],  # skip 'claude' binary
                **popen_kwargs)
            return proc, container
        else:
            proc = subprocess.Popen(
                cmd, cwd=workdir, env=self._claude_code_env(workdir),
                **popen_kwargs)
            return proc, None

    def _pool_release(self, container_name):
        """Release a pool container slot."""
        if container_name:
            try:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(container_name)
            except Exception:
                pass

    def _complete_claude_code(
        self, messages, model, temperature, max_tokens, tools=None,
    ):
        """Run claude CLI in simple prompt mode (no MCP, no tools).

        Used for summarization, narration, and other non-interactive calls.
        """
        from core.llm_client import LLMClientError, LLMResponse

        system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")
        user_id = getattr(self, '_user_id', "")
        if not conv_id or not agent_name or not user_id:
            raise ValueError(f"BUG: CC provider requires conv_id={conv_id!r}, agent_name={agent_name!r}, user_id={user_id!r}")
        workdir = self._get_session_workdir(conv_id, agent_name, user_id)
        self._setup_credentials(workdir)

        cmd = [
            self.claude_binary, "-p",
            "--output-format", "json",
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "1",
        ]

        _containerize = getattr(self, 'containerize', False)
        logger.info("claude-code complete: cwd=%s, containerize=%s, input=%d chars",
                     workdir, _containerize, len(stdin_text))

        _pool_container = None
        try:
            proc, _pool_container = self._pool_popen(workdir, cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8")
            stdout, stderr_out = proc.communicate(
                input=stdin_text, timeout=self.timeout)
            result = subprocess.CompletedProcess(
                args=cmd, returncode=proc.returncode,
                stdout=stdout, stderr=stderr_out)
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code")
        except subprocess.TimeoutExpired:
            raise LLMClientError(f"Claude CLI timed out after {self.timeout}s")
        finally:
            self._pool_release(_pool_container)

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
        """Extract images from the LAST user message only.

        Removes image blocks from ALL messages (so they don't bloat the text
        prompt). Only returns image blocks from the LAST user message as
        content blocks for the stream-json message (native vision).

        Older images are replaced with a placeholder text.
        """
        import base64 as _b64
        image_blocks = []

        # Find the last user message index
        _last_user_idx = -1
        for i, m in enumerate(messages):
            if m.role == "user" and isinstance(m.content, list):
                _last_user_idx = i

        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            new_content = []
            for block in m.content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                btype = block.get("type", "")

                _is_last_user = (idx == _last_user_idx)

                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        if _is_last_user:
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
                        # Replace with placeholder (both old and current — current goes to image_blocks)
                        new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        if _is_last_user:
                            image_blocks.append(block)
                            logger.info("Extracted image for vision: %s",
                                        source.get("media_type", "?"))
                        new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image_ref":
                    # Image stored in FileStore — load for vision on last user message only
                    if _is_last_user:
                        try:
                            from core.file_store import FileStore
                            import base64 as _b64
                            entry = FileStore.instance().get(block["file_id"])
                            if entry:
                                _fname, _data, _ct = entry
                                _data_b64 = _b64.b64encode(_data).decode("ascii")
                                image_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": block.get("mime_type", _ct),
                                        "data": _data_b64,
                                    },
                                })
                                logger.info("Loaded image from FileStore for vision: %s (%d bytes)",
                                            block["file_id"], len(_data))
                        except Exception as e:
                            logger.warning("Failed to load image from FileStore: %s", e)
                    new_content.append({"type": "text", "text": f"[image: {block.get('filename', '?')}]"})
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

    def _build_catchup_context(self, conv_id: str, agent_name: str) -> str:
        """Build catch-up text from messages other agents sent since our last turn.

        Reads agent's context.jsonl, finds messages after the last known index
        (tracked in self._cc_catchup_idx). Returns formatted text block or "".
        Also updates self._cc_catchup_idx so the same messages aren't sent twice
        (shared between initial, preempt, and inter-turn catch-up).
        """
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            ctx_data = store.load_agent_context(conv_id, agent_name)
            if not ctx_data:
                return ""

            # Initialize tracking index: find last own message as baseline
            if not hasattr(self, '_cc_catchup_idx') or self._cc_catchup_idx == 0:
                last_own = -1
                for i, m in enumerate(ctx_data):
                    src = m.get("source") or {}
                    if src.get("type") == "agent" and src.get("name") == agent_name:
                        last_own = i
                self._cc_catchup_idx = (last_own + 1) if last_own >= 0 else len(ctx_data)

            # Collect messages since last check
            new_msgs = ctx_data[self._cc_catchup_idx:]
            self._cc_catchup_idx = len(ctx_data)

            if not new_msgs:
                return ""

            # Format as XML block
            lines = ["<catch_up_context>",
                     "New messages from other participants since your last response:"]
            count = 0
            for m in new_msgs:
                content = m.get("content", "")
                if not content or not isinstance(content, str):
                    continue
                role = m.get("role", "user")
                lines.append(f"<message role=\"{role}\">\n{content}\n</message>")
                count += 1
            lines.append("</catch_up_context>")

            if count == 0:
                return ""

            logger.info("[claude-code] catch-up: %d messages for %s", count, agent_name)
            return "\n".join(lines)
        except Exception as e:
            logger.warning("[claude-code] catch-up failed: %s", e)
            return ""

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
        logger.debug("[claude-code] prompt: system=%d user=%d images=%d msgs=%d",
                     len(system_prompt), len(user_text), len(image_blocks), len(messages))

        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        # Always load session_id from the store for THIS conversation
        # (never from self — the client is shared across conversations)
        session_id = ""
        if conv_id:
            try:
                from core.conversation_store import ConversationStore
                session_id = ConversationStore.instance().get_extra(
                    conv_id, f"claude_session:{agent_name or 'default'}") or ""
                if session_id:
                    logger.info("Restored claude session: %s", session_id)
            except Exception:
                pass

        logger.info("claude-code stream: conv_id='%s' user='%s' agent='%s' session='%s'",
                     conv_id, user_id, agent_name, session_id[:12] if session_id else "new")

        workdir = self._get_session_workdir(conv_id, agent_name, user_id)
        # Resume with same credential that created the session (approach 3)
        _resume_pool_idx = -1
        if session_id and conv_id:
            try:
                _resume_pool_idx = int(ConversationStore.instance().get_extra(
                    conv_id, f"claude_pool_idx:{agent_name or 'default'}") or -1)
            except Exception:
                pass
        self._setup_credentials(workdir, pool_index=_resume_pool_idx)
        # Store pool index for this session
        if conv_id and hasattr(self, '_current_pool_index'):
            try:
                ConversationStore.instance().set_extra(
                    conv_id, f"claude_pool_idx:{agent_name or 'default'}",
                    self._current_pool_index)
            except Exception:
                pass
        mcp_path = self._setup_mcp_config(workdir, user_id, conv_id, agent_name)
        _containerize = getattr(self, 'containerize', False)

        # In pool mode, MCP config path is inside /cc_sessions/...
        _mcp_arg = mcp_path
        if _containerize and mcp_path:
            # Pool: symlink /workspace → /cc_sessions/<session_dir>
            # CC sees /workspace just like the old per-container model
            _mcp_arg = f"/workspace/{os.path.basename(mcp_path)}"
            _container_workdir = "/workspace"
        else:
            _container_workdir = workdir

        cmd = self._build_claude_cmd(model, session_id,
                                     mcp_config_path=_mcp_arg,
                                     workdir=workdir)

        logger.info("claude-code stream: cwd=%s, containerize=%s, cmd=%s",
                     workdir, _containerize, " ".join(str(c) for c in cmd[:20]))
        if session_id:
            # Verify session file exists at expected path
            _expected_session_file = os.path.join(workdir, "projects", "-workspace", f"{session_id}.jsonl")
            _exists = os.path.exists(_expected_session_file)
            _size = os.path.getsize(_expected_session_file) if _exists else 0
            logger.info("claude-code RESUME: session_id=%s file_exists=%s file_size=%d path=%s",
                         session_id, _exists, _size, _expected_session_file)

        # Track pool container for cleanup
        self._pool_container_name = None

        try:
            proc, self._pool_container_name = self._pool_popen(
                workdir, cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Ephemeral streams (btw) don't register proc — they don't
            # need preempt/cancel and must not overwrite the main agent's proc.
            if not getattr(self, '_ephemeral_stream', False):
                self._claude_proc = proc
        except FileNotFoundError:
            _bin = "docker" if _containerize else self.claude_binary
            if self._pool_container_name:
                ClaudeCodePool.instance().release(self._pool_container_name)
                self._pool_container_name = None
            raise LLMClientError(
                f"Binary '{_bin}' not found. "
                + ("Install Docker Desktop." if _containerize
                   else "Install with: npm install -g @anthropic-ai/claude-code"))

        # Multi-agent catch-up: when resuming a session, inject messages
        # from other agents that CC hasn't seen (arrived after CC's last turn)
        catchup_text = ""
        if session_id and conv_id and agent_name:
            catchup_text = self._build_catchup_context(conv_id, agent_name)

        # Send initial message as stream-json (keep stdin open for preempt/interrupt)
        try:
            # Prepend catch-up context to the initial message
            if catchup_text:
                initial_text = catchup_text + "\n\n" + initial_text

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
        # For task sub-conversations, publish to parent conv so webchat sees them
        _event_cid = getattr(self, '_event_cid', '') or conv_id
        # Extract task_id from sub-conv ID so frontend can group task events
        _task_id = ''
        if '::task::' in conv_id:
            _task_id = conv_id.split('::task::')[-1].split('::')[0]
        _agent_ctx = getattr(self, '_agent_ctx', {}) or {}

        def _pub(event_type, data):
            if not _event_cid:
                return
            if _task_id:
                data['task_id'] = _task_id
                data['task_iteration'] = _agent_ctx.get("_task_iteration", 0)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    _event_cid, event_type, data)
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

        def _inject_catchup():
            """Check for new messages from other agents and inject via stdin."""
            if not conv_id or not agent_name:
                return
            catchup = self._build_catchup_context(conv_id, agent_name)
            if not catchup:
                return
            _p = getattr(self, '_claude_proc', None)
            if _p and _p.poll() is None:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": catchup},
                })
                _p.stdin.write(msg + "\n")
                _p.stdin.flush()
                self._preempt_pending = getattr(self, '_preempt_pending', 0) + 1

        def _flush_turn():
            """Emit the accumulated turn via turn_callback."""
            nonlocal _turn_text_parts, _turn_tool_calls, _turn_thinking, content_parts
            text = "".join(_turn_text_parts).strip()
            # Drop phantom tool calls: empty inner args + no result (never executed)
            # For MCP wrapped calls, check the inner arguments, not the wrapper
            def _has_real_args(t):
                tid = t.get("id", "")
                result = _tool_results.get(tid, "")
                # Drop phantom tool calls with empty/ignored results
                if result and "no command provided" in str(result):
                    return False
                args = t.get("arguments", {})
                if not args or args == {}:
                    return False
                # MCP wrapper: check inner arguments
                if t.get("name") == "mcp__pawflow__use_tool" and isinstance(args, dict):
                    inner = args.get("arguments", {})
                    if not inner or inner == {}:
                        return False
                    # bash with empty/whitespace command
                    inner_tool = args.get("tool_name", "")
                    if inner_tool == "bash" and isinstance(inner, dict) and not str(inner.get("command", "")).strip():
                        return False
                    # Any tool where all string values are empty
                    if isinstance(inner, dict) and inner and all(
                            not str(v).strip() for v in inner.values()):
                        return False
                # Non-MCP bash with empty command
                if t.get("name") == "bash" and isinstance(args, dict) and not str(args.get("command", "")).strip():
                    return False
                return True
            tc = [t for t in _turn_tool_calls if _has_real_args(t)]
            _dropped = len(_turn_tool_calls) - len(tc)
            if _dropped:
                _dropped_tcs = [t for t in _turn_tool_calls if not _has_real_args(t)]
                logger.warning("[CC-DROPPED] %d phantom tool call(s): %s", _dropped,
                             json.dumps(_dropped_tcs, default=str, ensure_ascii=False)[:3000])
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

        # Stall watchdog: if CC emits a system event (init or compact_boundary)
        # but produces no assistant response within _STALL_TIMEOUT seconds,
        # kill the process. The retry loop in stream_chat will relaunch
        # a fresh CC process with the same session.
        _STALL_TIMEOUT = 120  # seconds
        _stall_start_time = 0.0  # time.monotonic() when stall watch begins
        _got_assistant = False   # set True on first assistant event
        _last_tool_result_time = 0.0  # monotonic time of last tool_result with no pending tools
        _pending_tool_ids = set()     # tool_use ids awaiting results
        _emitted_sse_tcs = set()      # tool_use ids for which we sent a SSE tool_call

        # Phantom tool call detector: if CC emits too many empty/phantom
        # tool calls in a short window, it likely lost context after a bad
        # internal compact. Trigger a PawFlow compact to recover.
        _PHANTOM_WINDOW = 300   # 5 minutes
        _PHANTOM_THRESHOLD = 10
        _phantom_timestamps: list = []  # monotonic timestamps of phantom detections

        _watchdog_stop = threading.Event()

        def _record_phantom(tool_name: str, block_id: str):
            """Record a phantom tool call. If threshold exceeded, trigger compact."""
            now = time.monotonic()
            _phantom_timestamps.append(now)
            # Prune entries outside window
            cutoff = now - _PHANTOM_WINDOW
            while _phantom_timestamps and _phantom_timestamps[0] < cutoff:
                _phantom_timestamps.pop(0)
            count = len(_phantom_timestamps)
            if count >= _PHANTOM_THRESHOLD:
                logger.warning(
                    "[claude-code] %d phantom tool calls in %ds window "
                    "(latest: %s id=%s) — triggering PawFlow compact",
                    count, _PHANTOM_WINDOW, tool_name, block_id)
                try:
                    proc.kill()
                except OSError:
                    pass
                from core.llm_client import CCCompactDetected
                raise CCCompactDetected(
                    f"Too many phantom tool calls ({count} in {_PHANTOM_WINDOW}s)")

        self._stall_killed = False  # set by watchdog — retry must be unconditional

        def _stall_watchdog():
            pass  # _stall_killed is on self
            while not _watchdog_stop.is_set():
                if _stall_start_time and not _got_assistant:
                    elapsed = time.monotonic() - _stall_start_time
                    if elapsed >= _STALL_TIMEOUT:
                        logger.warning(
                            "[claude-code] Stall detected (%.0fs with no assistant "
                            "response) — killing process for retry", elapsed)
                        self._stall_killed = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                # Tool result stall: all tools resolved but no assistant response
                if _last_tool_result_time and not _pending_tool_ids:
                    elapsed = time.monotonic() - _last_tool_result_time
                    if elapsed >= _STALL_TIMEOUT:
                        logger.warning(
                            "[claude-code] Tool-result stall (%.0fs since last "
                            "tool_result, no pending tools, no assistant) "
                            "— killing for retry", elapsed)
                        self._stall_killed = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                # Debug: log watchdog state every 30s
                if not hasattr(_stall_watchdog, '_dbg_count'):
                    _stall_watchdog._dbg_count = 0
                _stall_watchdog._dbg_count += 1
                if _stall_watchdog._dbg_count % 3 == 0:  # every 30s
                    logger.debug(
                        '[claude-code] watchdog state: stall_start=%.1f got_assistant=%s '
                        'last_tr=%.1f pending=%s',
                        _stall_start_time, _got_assistant,
                        _last_tool_result_time, _pending_tool_ids)
                _watchdog_stop.wait(10)  # check every 10s

        _watchdog_thread = threading.Thread(target=_stall_watchdog, daemon=True)
        _watchdog_thread.start()

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
                _parent_tc_id = event.get("parent_tool_use_id") or ""
                logger.info("[claude-code] %s %.200s", etype, json.dumps(event))

                if etype == "system":
                    # Capture AND persist session_id from init event immediately.
                    # Must be in ConversationStore before any preempt triggers
                    # _prepare_agent_context (which checks for session to skip compact).
                    sid = event.get("session_id", "")
                    if sid and conv_id:
                        if session_id and sid != session_id:
                            logger.warning(
                                "[claude-code] SESSION MISMATCH: sent --resume %s but CC returned %s "
                                "(resume FAILED — CC created new session)",
                                session_id[:12], sid[:12])
                        elif session_id and sid == session_id:
                            logger.info("[claude-code] RESUME OK: session %s reused", sid[:12])
                        else:
                            logger.info("[claude-code] NEW session: %s", sid[:12])
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().set_extra(
                                conv_id,
                                f"claude_session:{agent_name or 'default'}",
                                sid)
                        except Exception:
                            pass
                    # compact_boundary → kill CC + PawFlow compact; init → arm stall watchdog
                    subtype = event.get("subtype", "")
                    if subtype == "compact_boundary" or (
                            subtype == "status" and event.get("status") == "compacting"):
                        logger.warning("[claude-code] CC compacting detected (subtype=%s) "
                                       "— killing CC, PawFlow will compact", subtype)
                        proc.kill()
                        from core.llm_client import CCCompactDetected
                        raise CCCompactDetected("CC auto-compact detected")
                    if subtype == "init":
                        _stall_start_time = time.monotonic()
                        _got_assistant = False
                        logger.info("[claude-code] init — stall watchdog armed (%.0fs timeout)",
                                    _STALL_TIMEOUT)
                    continue

                if etype == "assistant":
                    # Got a response — stall watchdog disarmed
                    _got_assistant = True
                    _last_tool_result_time = 0.0

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
                            _inject_catchup()
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
                            logger.info("[CC-RAW-TOOL] block=%s", json.dumps(block, default=str, ensure_ascii=False))
                            _block_id = block.get("id", "")
                            _block_entry = {
                                "name": block.get("name", ""),
                                "arguments": block.get("input", {}),
                                "id": _block_id,
                            }
                            # Dedup: Claude Code may send the same tool_use block
                            # multiple times for the same msg_id (incremental updates).
                            # First time: input={} (empty), later: input={real args}.
                            # Replace by id instead of blindly appending.
                            _existing_idx = None
                            for _i, _tc in enumerate(_turn_tool_calls):
                                if _tc.get("id") == _block_id:
                                    _existing_idx = _i
                                    break
                            if _existing_idx is not None:
                                _turn_tool_calls[_existing_idx] = _block_entry
                            else:
                                _turn_tool_calls.append(_block_entry)
                            _pending_tool_ids.add(_block_id)
                            # Unwrap MCP wrapper for display:
                            # mcp__pawflow__use_tool(tool_name=X, arguments={...})
                            # → X({...})
                            _tc_name = block.get("name", "")
                            _tc_args = block.get("input", {})
                            if _tc_name == "mcp__pawflow__use_tool" and isinstance(_tc_args, dict):
                                _tc_name = _tc_args.get("tool_name", _tc_name)
                                _tc_args = _tc_args.get("arguments", _tc_args)
                            elif _tc_name == "mcp__pawflow__get_tool_schema":
                                _tc_name = "get_tool_schema"
                            # Don't emit SSE for empty-arg tool calls — likely
                            # an incremental update that will be followed by the
                            # real one with actual arguments.
                            if not _tc_args or _tc_args == {} or _tc_args == "{}":
                                logger.warning("[claude-code] skipping SSE for empty tool_use %s (id=%s) — awaiting args",
                                             _tc_name, _block_id)
                                continue
                            # Skip bash with empty/missing/whitespace command
                            if _tc_name == "bash" and isinstance(_tc_args, dict) and not str(_tc_args.get("command", "")).strip():
                                logger.warning("[claude-code] skipping SSE for bash with empty command (id=%s)", _block_id)
                                _record_phantom(_tc_name, _block_id)
                                continue
                            # Skip any tool where ALL string values are empty
                            if isinstance(_tc_args, dict) and _tc_args and all(
                                    not str(v).strip() for v in _tc_args.values()):
                                logger.warning("[claude-code] skipping SSE for %s with all-empty args (id=%s)", _tc_name, _block_id)
                                _record_phantom(_tc_name, _block_id)
                                continue
                            # Skip meta tools from SSE
                            if _tc_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                                continue
                            _tc_event = {
                                "tool": _tc_name,
                                "arguments": _tc_args,
                                "tc_id": _block_id,
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "via": "claude-code",
                                "ts": time.time(),
                            }
                            if _parent_tc_id:
                                _tc_event["parent_tc_id"] = _parent_tc_id
                            _pub("tool_call", _tc_event)
                            _emitted_sse_tcs.add(_block_id)
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
                            logger.info("[CC-RAW-RESULT] block=%s", json.dumps(block, default=str, ensure_ascii=False)[:2000])
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
                                _pending_tool_ids.discard(tc_id)
                                if not _pending_tool_ids:
                                    _last_tool_result_time = time.monotonic()
                            # Resolve tool name from turn_tool_calls
                            _tr_name = tc_id
                            for _tc in _turn_tool_calls:
                                if _tc.get("id") == tc_id:
                                    _tr_name = _tc.get("name", tc_id)
                                    # Unwrap MCP wrapper name
                                    if _tr_name == "mcp__pawflow__use_tool":
                                        _tr_name = _tc.get("arguments", {}).get("tool_name", _tr_name)
                                    break
                            # Skip meta tool results from SSE
                            if _tr_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                                continue
                            # Skip empty/phantom tool results from SSE
                            if not result_str or result_str == "" or "no command provided" in result_str:
                                continue
                            _tr_event = {
                                "tool": _tr_name,
                                "result": result_str[:300],
                                "tc_id": tc_id,
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "via": "claude-code",
                            }
                            if _parent_tc_id:
                                _tr_event["parent_tc_id"] = _parent_tc_id
                            _pub("tool_result", _tr_event)

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
                        _errors = event.get("errors", [])
                        if _errors:
                            _err_text = _err_text or "; ".join(
                                e.get("message", str(e)) if isinstance(e, dict) else str(e)
                                for e in _errors)
                            logger.error("[claude-code] errors: %s", _errors)
                        if "authentication" in _err_text.lower() or "401" in _err_text:
                            raise LLMClientError(f"Claude Code auth failed: {_err_text[:300]}")
                        if event.get("subtype") == "error_during_execution":
                            # Include the error code/text so LLMClient retry loop can match it
                            raise LLMClientError(f"Claude Code error: {_err_text[:300]}")
                        # is_error without error_during_execution: API error (500, 429, etc.)
                        # Raise so it reaches the retry loop in LLMClient
                        if _err_text:
                            raise LLMClientError(f"Claude Code API error: {_err_text[:300]}")
                        logger.warning("[claude-code] result has is_error=True but no details")
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
                    # result = CC is done with ALL pending work (including
                    # any preempted messages). Always break — CC processes
                    # all preempts in the same session before emitting result.
                    _pending = getattr(self, '_preempt_pending', 0)
                    if _pending > 0:
                        logger.info("[claude-code] result event, clearing %d preempt(s)", _pending)
                        self._preempt_pending = 0
                    break

        finally:
            # Stop compact stall watchdog
            _watchdog_stop.set()
            # Flush any pending turn (ensures last text is persisted even if interrupted)
            try:
                _flush_turn()
            except Exception:
                pass
            # Cleanup process — _cleanup_proc captures stderr internally
            _stderr = self._cleanup_proc(proc)
            # Recover refreshed tokens from workdir (Claude Code may have refreshed them)
            self._recover_tokens(workdir)

        # Don't error on non-zero exit if we got a successful result
        # (process was killed after break on result event — that's expected)
        _got_result = bool(last_data.get("session_id") or last_data.get("result"))
        _was_compact_stall = (proc.returncode == -9 and _stall_start_time > 0 and not _got_assistant)
        if proc.returncode and proc.returncode != 0 and not _got_result:
            if _stderr:
                logger.error("Claude CLI stderr: %.500s", _stderr)
            _reason = "compact_stall" if _was_compact_stall else ""
            raise LLMClientError(
                f"Claude CLI stream exited with code {proc.returncode}"
                + (f" ({_reason})" if _reason else "")
                + (f": {_stderr[:200]}" if _stderr else ""))

        # If turn_callback handled all turns, don't return content
        # (prevents agent loop from persisting the same text again)
        full_content = "" if turn_callback else "".join(content_parts)

        new_session = last_data.get("session_id", "")
        if new_session:
            # Persist session_id in conversation store (NOT on self — client is shared)
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
