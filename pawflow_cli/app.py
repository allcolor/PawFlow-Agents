"""PawCode — Terminal frontend for PawFlow."""

import atexit
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pawflow_cli.auth import authenticate
from pawflow_cli.relay import RelayThread
from pawflow_cli.api import AgentAPIClient, SSEClient
from pawflow_cli.ui.renderer import TerminalRenderer
from pawflow_cli.config import load_config, save_config

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.completion import WordCompleter
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

_COMMANDS = [
    "/new", "/conv", "/resume", "/history", "/delete", "/export",
    "/agent", "/msg", "/message", "/btw", "/stop", "/interrupt",
    "/compact", "/rebuild", "/restart", "/summary", "/context",
    "/memory", "/skill", "/task", "/service",
    "/resources", "/activate", "/deactivate",
    "/tools", "/call", "/model", "/llm",
    "/files", "/upload", "/paste", "/view", "/prompt",
    "/clear-files", "/detach",
    "/add-secret", "/secrets", "/add-variable", "/variables",
    "/schedules", "/cost", "/copy", "/clear", "/login", "/quit", "/exit",
    "/help", "/run", "/diff", "/watch", "/multi",
]
_completer = WordCompleter(_COMMANDS, sentence=True) if HAS_PROMPT_TOOLKIT else None


class PawCode:
    """Main CLI application."""

    def __init__(self, server_url: str, directory: str, allow_exec: bool = True):
        self.server_url = server_url
        self.directory = str(Path(directory).resolve())
        self.allow_exec = allow_exec

        self.renderer = TerminalRenderer()
        self.api: AgentAPIClient = None
        self.sse: SSEClient = None
        self.relay: RelayThread = None

        self.conversation_id = None
        self.selected_agent = ""
        self.username = ""
        self.session_token = ""
        self._sending = False
        self._running = True
        self._last_history = []
        self._status_text = ""  # shown in bottom toolbar (thinking verb, etc.)
        self._status_tick = 0  # for fade animation
        self._pending_attachments = []  # queued files/images to send with next message
        self._last_responses = []  # last N agent responses for /copy
        self._approval_queue = queue.Queue()  # background → main thread approval requests
        self._approval_response = queue.Queue()  # main thread → background approval responses

    def start(self):
        """Initialize auth, relay, and start the main loop."""
        # Wire status callback for bottom toolbar
        self.renderer.set_status_callback(self._update_status)

        # Authenticate
        self.renderer.print_banner(self.directory)

        auth = authenticate(self.server_url)
        self.session_token = auth["token"]
        self.username = auth["username"]

        self.renderer.print_system(f"Authenticated as {self.username}")

        # API client
        self.api = AgentAPIClient(self.server_url, self.session_token)

        # Start relay
        self.renderer.print_system(f"Mounting {self.directory} as filesystem relay...")
        self.relay = RelayThread(
            self.server_url, self.session_token, self.username,
            self.directory, self.allow_exec,
        )
        self.relay.start()
        self.renderer.print_system(f"Relay '{self.relay.relay_id}' connected on port {self.relay.port}")

        # Cleanup on exit
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Resume last conversation with sliding window
        config = load_config()
        last_cid = config.get("last_conversation_id")
        if last_cid:
            try:
                data = self.api.send_action("load_history",
                                             conversation_id=last_cid, limit=50, offset=0)
                if not data.get("error"):
                    self.conversation_id = last_cid
                    total = data.get("message_count", 0)
                    has_more = data.get("has_more", False)
                    messages = data.get("messages", [])
                    more_hint = " — /history for older" if has_more else ""
                    self.renderer.print_system(
                        f"Resumed {last_cid[:8]} (showing {len(messages)} of {total}{more_hint})")
                    self._display_history(messages, len(messages))
                    # Auto-summary for long conversations
                    if total > 100 and messages:
                        for m in reversed(messages):
                            if m.get("type") in ("assistant", "agent_response"):
                                source = m.get("source", {})
                                agent = source.get("name", "assistant") if isinstance(source, dict) else "assistant"
                                preview = m.get("content", "")[:200]
                                if preview:
                                    self.renderer.print_system(f"Last ({agent}): {preview}...")
                                break
                    self._ensure_sse()
            except Exception:
                pass

        self.renderer.print_system("Ready. Type /help for commands, /quit to exit.\n")

        # Main loop
        self._main_loop()

    _SPINNERS = ["◐", "◓", "◑", "◒"]
    _FADE_COLORS = ["#e94560", "#c73e54", "#a53848", "#c73e54"]

    def _get_toolbar(self):
        """Bottom toolbar content — animated spinner + fade for thinking status."""
        from prompt_toolkit.formatted_text import HTML
        if self._status_text:
            self._status_tick += 1
            spinner = self._SPINNERS[self._status_tick % len(self._SPINNERS)]
            color = self._FADE_COLORS[self._status_tick % len(self._FADE_COLORS)]
            # Cycle verb every 4 ticks (~2s) for variety
            if self._status_tick % 4 == 0:
                from pawflow_cli.ui.renderer import _random_verb
                # Update the verb in status text if it contains ✶
                parts = self._status_text.split("✶ ", 1)
                if len(parts) == 2:
                    rest = parts[1].split("...", 1)
                    after = rest[1] if len(rest) > 1 else ""
                    self._status_text = f"{parts[0]}✶ {_random_verb()}...{after}"
            text = self._status_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return HTML(f'<style bg="#16213e" fg="{color}"> {spinner} {text} </style>')
        # Idle — show PawCode + pending attachment count
        attach = f" | 📎 {len(self._pending_attachments)} file(s)" if self._pending_attachments else ""
        return HTML(f'<style bg="#0f1629" fg="#555"> PawCode{attach} </style>')

    def _main_loop(self):
        """Input loop — always available. SSE events render in background."""
        from pawflow_cli.config import HISTORY_FILE, ensure_config_dir
        ensure_config_dir()

        # Start background event consumer
        self._event_thread = threading.Thread(target=self._event_consumer,
                                               daemon=True, name="pawcode-events")
        self._event_thread.start()

        if HAS_PROMPT_TOOLKIT:
            from prompt_toolkit.patch_stdout import patch_stdout
            from prompt_toolkit.formatted_text import HTML

            # Custom key bindings
            bindings = KeyBindings()

            @bindings.add('enter')
            def _enter(event):
                """Enter: send message (unless empty or in middle of multiline with Shift)."""
                buf = event.current_buffer
                text = buf.text.strip()
                if text:
                    buf.validate_and_handle()
                else:
                    # Empty — just accept (no-op, prompt stays)
                    buf.validate_and_handle()

            @bindings.add('escape', 'enter')
            def _newline(event):
                """Alt+Enter (Escape then Enter): insert newline."""
                event.current_buffer.insert_text('\n')

            # Support Shift+Enter and Ctrl+Enter on modern terminals
            # (Windows Terminal, iTerm2, Kitty send CSI u sequences)
            try:
                from prompt_toolkit.input import vt100_parser
                # \x1b[13;2u = Shift+Enter, \x1b[13;5u = Ctrl+Enter
                bindings.add(Keys.Vt100MouseEvent)  # dummy to test availability
            except Exception:
                pass
            # Register raw escape sequences for Shift+Enter / Ctrl+Enter
            @bindings.add('escape', '[', '1', '3', ';', '2', 'u')
            def _shift_enter(event):
                event.current_buffer.insert_text('\n')
            @bindings.add('escape', '[', '1', '3', ';', '5', 'u')
            def _ctrl_enter(event):
                event.current_buffer.insert_text('\n')

            @bindings.add('c-v')
            def _ctrl_v(event):
                """Ctrl+V: check clipboard for image first, then paste text."""
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grabclipboard()
                    if img is not None:
                        # Clipboard has an image — queue it
                        import io, base64
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        self._pending_attachments.append({
                            "filename": "clipboard.png",
                            "mime_type": "image/png",
                            "data": b64,
                        })
                        n = len(self._pending_attachments)
                        self.renderer.print_system(
                            f"📎 clipboard image ({len(buf.getvalue()):,} bytes) — {n} file(s) queued")
                        return
                except ImportError:
                    pass
                except Exception:
                    pass
                # No image — do normal paste
                event.current_buffer.paste_clipboard_data(
                    event.app.clipboard.get_data())

            session = PromptSession(
                history=FileHistory(str(HISTORY_FILE)),
                multiline=True,
                prompt_continuation="  ",
                enable_history_search=True,
                bottom_toolbar=self._get_toolbar,
                refresh_interval=0.5,
                key_bindings=bindings,
                completer=_completer,
            )

            # patch_stdout(raw=True) preserves ANSI codes from Rich
            with patch_stdout(raw=True):
                # Re-create Rich Console to write through the patched stdout
                self.renderer.init_patched_console()
                # Filter [FSRelay] from stderr AFTER patch_stdout has wrapped it
                _real_stderr_write = sys.stderr.write
                def _filter_relay(s):
                    if isinstance(s, str) and "[FSRelay]" in s:
                        return len(s)
                    return _real_stderr_write(s)
                sys.stderr.write = _filter_relay
                while self._running:
                    try:
                        text = session.prompt("❯ ")
                    except (EOFError, KeyboardInterrupt):
                        self._running = False
                        break
                    text = text.strip()
                    if not text:
                        continue
                    try:
                        self._handle_input(text)
                    except Exception as e:
                        self.renderer.print_error(f"Unexpected error: {e}")
        else:
            while self._running:
                try:
                    text = input("❯ ")
                except (EOFError, KeyboardInterrupt):
                    self._running = False
                    break
                text = text.strip()
                if not text:
                    continue
                try:
                    self._handle_input(text)
                except Exception as e:
                    self.renderer.print_error(f"Unexpected error: {e}")

    def _handle_input(self, text: str):
        """Process a single input line."""
        # Check for pending approval — single char y/n/s/a responds to it
        if text.lower() in ("y", "n", "s", "a") and not self._approval_queue.empty():
            approval = self._approval_queue.get_nowait()
            result = approval["result_map"].get(text.lower(), "denied")
            self._approval_response.put(result)
            self.renderer.print_system(f"Approval: {result}")
            return
        # Detect dragged file path (file exists on disk)
        clean = text.strip().strip('"').strip("'")
        if not text.startswith("/") and os.path.isfile(clean):
            self.renderer.print_system(f"File detected: {clean}")
            self._upload_file(clean)
            return
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._send_message(text)

    def _send_message(self, text: str):
        """Send a message to the agent (non-blocking — events rendered by background thread)."""
        # Erase the raw prompt line, replace with styled Panel
        sys.stdout.write("\033[A\033[2K")
        sys.stdout.flush()
        # Show attachment count in user message if any
        attach_info = f" [📎 {len(self._pending_attachments)} file(s)]" if self._pending_attachments else ""
        self.renderer.print_user_message(text + attach_info)
        try:
            attachments = self._pending_attachments if self._pending_attachments else None
            resp = self.api.send_message(
                message=text,
                conversation_id=self.conversation_id,
                target_agent=self.selected_agent,
                attachments=attachments,
            )
            self._pending_attachments = []  # clear after send
            if resp.get("error"):
                self.renderer.print_error(resp["error"])
                return

            cid = resp.get("conversation_id")
            if cid:
                self.conversation_id = cid
                save_config({"last_conversation_id": cid})

            # Connect SSE if not connected
            self._ensure_sse()

        except PermissionError:
            self.renderer.print_error("Session expired. Run /login to re-authenticate.")
        except Exception as e:
            self.renderer.print_error(f"Send error: {e}")

    def _upload_file(self, file_path: str):
        """Queue a local file as pending attachment (sent with next message)."""
        import base64
        import mimetypes
        path = Path(file_path)
        if not path.is_file():
            self.renderer.print_error(f"File not found: {file_path}")
            return
        if path.stat().st_size > 10 * 1024 * 1024:
            self.renderer.print_error(f"File too large (max 10MB): {path.name}")
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        self._pending_attachments.append({
            "filename": path.name,
            "mime_type": mime,
            "data": b64,
        })
        n = len(self._pending_attachments)
        self.renderer.print_system(f"📎 {path.name} ({len(data):,} bytes) — {n} file(s) queued. Type message + Enter to send.")

    def _paste_clipboard_image(self):
        """Queue clipboard image as pending attachment."""
        import base64
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
            if img is None:
                self.renderer.print_error("No image in clipboard")
                return
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            self._pending_attachments.append({
                "filename": "clipboard.png",
                "mime_type": "image/png",
                "data": b64,
            })
            n = len(self._pending_attachments)
            self.renderer.print_system(f"📎 clipboard image ({len(buf.getvalue()):,} bytes) — {n} file(s) queued. Type message + Enter to send.")
        except ImportError:
            self.renderer.print_error("Install Pillow for clipboard support: pip install Pillow")
        except Exception as e:
            self.renderer.print_error(f"Clipboard paste failed: {e}")

    def _clear_attachments(self):
        """Clear pending attachments."""
        self._pending_attachments.clear()
        self.renderer.print_system("Attachments cleared.")

    def _copy_last_message(self, arg: str = ""):
        """Copy last agent response (or Nth) to clipboard."""
        if not self._last_responses:
            self.renderer.print_error("No responses to copy")
            return
        idx = -1
        if arg and arg.isdigit():
            idx = -int(arg) if int(arg) > 0 else -1
        try:
            text = self._last_responses[idx]
        except IndexError:
            self.renderer.print_error(f"Only {len(self._last_responses)} responses available")
            return
        try:
            # Try pyperclip first (cross-platform)
            import pyperclip
            pyperclip.copy(text)
            self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
        except ImportError:
            # Fallback: platform-specific
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["clip"], input=text.encode("utf-8"), check=True)
                self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
                self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
            else:
                # Linux — try xclip
                try:
                    import subprocess
                    subprocess.run(["xclip", "-selection", "clipboard"],
                                   input=text.encode("utf-8"), check=True)
                    self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
                except Exception:
                    self.renderer.print_error("Install pyperclip or xclip for clipboard support")
        except Exception as e:
            self.renderer.print_error(f"Copy failed: {e}")

    def _ensure_sse(self):
        """Ensure SSE client is connected for the current conversation."""
        if self.conversation_id and (not self.sse or not self.sse.connected):
            self.sse = SSEClient(self.server_url, self.session_token)
            self.sse.connect(self.conversation_id)

    def _event_consumer(self):
        """Background thread: continuously consume SSE events and render them."""
        streaming_agent = ""
        thinking_agent = ""

        while self._running:
            # Wait for SSE client to be available
            if not self.sse:
                time.sleep(0.2)
                continue

            try:
                event = self.sse.events.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                time.sleep(0.5)
                continue

            try:
                still_waiting = self._dispatch_event(event, streaming_agent, thinking_agent)
                streaming_agent = self._ev_streaming_agent
                thinking_agent = self._ev_thinking_agent
                # On done/error/cancelled, reset streaming state
                if not still_waiting:
                    streaming_agent = ""
                    thinking_agent = ""
            except Exception as e:
                self._safe_stop_live()
                try:
                    self.renderer.print_error(f"Event error: {e}")
                except Exception:
                    pass

    def _dispatch_event(self, event, streaming_agent, thinking_agent):
        """Dispatch a single SSE event. Returns True to keep waiting, False when done."""
        self._ev_streaming_agent = streaming_agent
        self._ev_thinking_agent = thinking_agent
        ev_type = event.get("event", "")
        data = event.get("data", {})

        if ev_type == "thinking" or ev_type == "thinking_content":
            agent = data.get("agent_name", "assistant")
            if ev_type == "thinking" and not thinking_agent:
                self._ev_thinking_agent = agent
                self.renderer.start_thinking(agent)
            elif ev_type == "thinking_content":
                self.renderer.thinking_token(agent, data.get("text", ""))

        elif ev_type == "token":
            agent = data.get("agent_name", "assistant")
            if thinking_agent:
                self.renderer.end_thinking(thinking_agent)
                self._ev_thinking_agent = ""
            # Multi-agent: each agent accumulates independently
            if agent not in self.renderer._streams:
                source = data.get("source", {})
                svc = source.get("llm_service", "") if isinstance(source, dict) else ""
                self.renderer.start_stream(agent, svc)
            self.renderer.stream_token(agent, data.get("text", ""))

        elif ev_type == "tool_call":
            # Don't end other agents' streams — they continue independently
            agent = data.get("agent_name", "assistant")
            svc = data.get("llm_service", "")
            self.renderer.print_tool_call(
                data.get("tool", "?"),
                data.get("arguments", {}),
                agent, svc,
            )

        elif ev_type == "tool_result":
            self.renderer.print_tool_result(
                data.get("tool", "?"),
                data.get("result", ""),
                data.get("agent_name", ""),
            )

        elif ev_type == "iteration_status":
            self.renderer.print_iteration(
                data.get("agent_name", ""),
                data.get("iteration", 0),
                data.get("round", 0),
                data.get("max_rounds", 0),
                data.get("total_tools", 0),
            )

        elif ev_type == "exec_approval_request":
            self._handle_exec_approval(data)

        elif ev_type == "tool_approval_request":
            self._handle_tool_approval(data)

        elif ev_type == "ask_user":
            self.renderer.print_ask_user(
                data.get("question", ""),
                data.get("options", []),
            )

        elif ev_type == "btw_thinking":
            agent = data.get("agent_name", "assistant")
            self.renderer.print_system(f"[{agent} btw] thinking...")

        elif ev_type == "btw_token":
            agent = data.get("agent_name", "assistant")
            btw_key = f"btw:{agent}"
            if btw_key != streaming_agent:
                if streaming_agent:
                    self.renderer.end_stream(streaming_agent)
                self._ev_streaming_agent = btw_key
                self.renderer.print(f"[dim italic]  [{agent} btw][/dim italic]")
                self.renderer.start_stream(btw_key)
            self.renderer.stream_token(btw_key, data.get("text", ""))

        elif ev_type == "btw_done":
            agent = data.get("agent_name", "assistant")
            btw_key = f"btw:{agent}"
            if streaming_agent == btw_key:
                self.renderer.end_stream(btw_key, data.get("response", ""))
                self._ev_streaming_agent = ""

        elif ev_type == "sub_agent_start":
            self.renderer.print_system(f"Sub-agent [{data.get('agent_name', '?')}] started")

        elif ev_type == "sub_agent_done":
            agent = data.get("agent_name", "?")
            tokens = data.get("tokens_in", 0) + data.get("tokens_out", 0)
            self.renderer.print_system(f"Sub-agent [{agent}] done ({tokens} tokens)")
            resp = data.get("response", "")
            if resp:
                self.renderer.print_agent_badge(agent, data.get("llm_service", ""))
                self.renderer.print_markdown(resp[:500])

        elif ev_type == "exec_output":
            self.renderer.print_exec_output(
                data.get("command", ""), data.get("exit_code", -1),
                data.get("stdout", ""), data.get("stderr", ""))

        elif ev_type == "notification":
            msg = data.get("message", "")
            if data.get("urgency") == "high":
                self.renderer.print_error(msg)
            else:
                self.renderer.print_system(msg)

        elif ev_type == "done":
            response_text = data.get("response", "")
            agent = data.get("agent_name", "assistant")
            # End this specific agent's stream (multi-agent safe)
            if agent in self.renderer._streams:
                self.renderer.end_stream(agent, response_text)
            elif response_text:
                agent = data.get("agent_name", "assistant")
                self.renderer.print_agent_badge(agent)
                self.renderer.print_markdown(response_text)
            # Track for /copy
            if response_text:
                self._last_responses.append(response_text)
                if len(self._last_responses) > 10:
                    self._last_responses.pop(0)
            self.renderer.print_done(
                data.get("agent_name", ""),
                data.get("tokens_in", 0),
                data.get("tokens_out", 0),
                data.get("duration_ms", 0),
                data.get("model", ""),
            )
            if not data.get("continuing"):
                return False

        elif ev_type == "error_event":
            self.renderer.print_error(data.get("message", "Unknown error"))
            return False

        elif ev_type == "cancelled":
            self.renderer.print_system(f"[{data.get('agent_name', '?')}] Cancelled")
            return False

        elif ev_type == "compact_progress":
            stage = data.get("stage", "")
            detail = data.get("detail", "")
            if stage == "done":
                before = data.get("before", 0)
                after = data.get("after", 0)
                self.renderer.print_system(f"Compacted: {before} → {after} messages")
                self._update_status("")
            else:
                self._update_status(f"▶ Compacting... {stage} {detail}")

        elif ev_type == "task_progress":
            stage = data.get("stage", "")
            agent = data.get("agent", "")
            task = data.get("task", "")
            if stage == "done":
                self.renderer.print_system(f"Task '{task}' completed by {agent}")
                self._update_status("")
            else:
                self._update_status(f"▶ {agent} task: {stage}")

        elif ev_type == "thought_scheduled":
            agent = data.get("agent", "")
            delay = data.get("delay", 0)
            self.renderer.print_system(f"[{agent}] next auto-message in ~{delay}s")

        elif ev_type == "thought_firing":
            agent = data.get("agent", "")
            self._update_status(f"▶ {agent} thinking...")

        elif ev_type == "sub_agent_iteration":
            agent = data.get("agent_name", "")
            iteration = data.get("iteration", 0)
            tools = data.get("total_tools", 0)
            self._update_status(f"▶ sub:{agent} iter {iteration} · {tools} tools")

        elif ev_type == "sub_agent_tool":
            agent = data.get("agent_name", "")
            tool = data.get("tool", "")
            self._update_status(f"▶ sub:{agent} {tool}...")

        elif ev_type == "interrupting":
            agent = data.get("agent", "")
            self.renderer.print_system(f"Interrupting {agent}...")

        elif ev_type == "discard":
            pass  # silently discard

        elif ev_type == "agent_response":
            agent = data.get("agent_name", data.get("source", {}).get("name", "assistant") if isinstance(data.get("source"), dict) else "assistant")
            response = data.get("response", "")
            if response:
                self.renderer.print_system("")  # spacing
                self.renderer.end_stream(agent, response)

        elif ev_type == "broadcast_done":
            count = data.get("agent_count", 0)
            self.renderer.print_system(f"Broadcast complete — {count} agent(s) responded")

        return True  # keep waiting

    def _handle_exec_approval(self, data: dict):
        """Handle exec approval request — delegate to main thread via queue."""
        self.renderer.print_exec_approval(
            data.get("command", "?"),
            data.get("risk_level", "normal"),
            data.get("request_id", ""),
        )
        # Put request in queue for main thread to handle
        self._approval_queue.put({
            "type": "exec",
            "request_id": data.get("request_id", ""),
            "result_map": {"y": "approved", "n": "denied", "s": "session_allow", "a": "always_allow"},
            "action": "exec_result",
        })
        # Wait for response from main thread (timeout 60s)
        try:
            result = self._approval_response.get(timeout=60)
        except queue.Empty:
            result = "denied"
        try:
            self.api.send_action("exec_result",
                                 request_id=data.get("request_id", ""),
                                 result=result,
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_tool_approval(self, data: dict):
        """Handle tool approval request — delegate to main thread via queue."""
        self.renderer.print_approval_request(
            data.get("tool_name", "?"),
            data.get("action_summary", ""),
            data.get("request_id", ""),
        )
        self._approval_queue.put({
            "type": "tool",
            "request_id": data.get("request_id", ""),
            "result_map": {"y": "allow_once", "n": "denied", "s": "session_allow", "a": "always_allow"},
            "action": "tool_approval_result",
        })
        try:
            result = self._approval_response.get(timeout=60)
        except queue.Empty:
            result = "denied"
        try:
            self.api.send_action("tool_approval_result",
                                 request_id=data.get("request_id", ""),
                                 result=result,
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_command(self, text: str):
        """Handle slash commands."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            self.renderer.print_system("Shutting down...")
            self._running = False
            self._cleanup()
            return

        if cmd == "/help":
            self.renderer.print_markdown(
                "## Conversation\n"
                "- `/new` — New conversation\n"
                "- `/conv` — List conversations\n"
                "- `/resume <id> [N]` — Resume conversation\n"
                "- `/history [N] [offset]` — Show messages\n"
                "- `/delete <id>` — Delete conversation\n"
                "- `/export [json|md]` — Export conversation\n"
                "- `/compact` — Compact context\n"
                "- `/clear` — Clear screen\n"
                "\n## Agents\n"
                "- `/agent list` — List agents\n"
                "- `/agent create <name> <prompt>` — Create agent\n"
                "- `/agent delete <name>` — Delete agent\n"
                "- `/agent setname <real> [nick]` — Set nickname\n"
                "- `/agent enable|disable|promote <name>` — Agent state\n"
                "- `/agent <name>` — Switch to agent\n"
                "- `/msg <agent|ALL> <text>` — Send to agent\n"
                "- `/btw <agent|ALL> <question>` — Side question\n"
                "- `/stop <agent|ALL> [-f]` — Interrupt/cancel agent\n"
                "\n## Context\n"
                "- `/rebuild` — Rebuild context\n"
                "- `/restart [agent] [keep]` — Restart context\n"
                "- `/summary [agent] [tokens]` — Summarize context\n"
                "- `/context` — Show context info\n"
                "\n## Memory\n"
                "- `/memory list` — List memories\n"
                "- `/memory add <text>` — Add memory\n"
                "- `/memory del <id>` — Delete memory\n"
                "- `/memory edit <id> <text>` — Edit memory\n"
                "- `/memory search <query>` — Search memories\n"
                "\n## Skills\n"
                "- `/skill list` — List skills\n"
                "- `/skill add <name> <prompt>` — Create skill\n"
                "- `/skill del <name>` — Delete skill\n"
                "\n## Tasks\n"
                "- `/task list` — List tasks\n"
                "- `/task create <name> <prompt>` — Create task\n"
                "- `/task assign <agent> <task>` — Assign task\n"
                "- `/task del <name>` — Delete task\n"
                "- `/task pause|resume|cancel <id>` — Task control\n"
                "- `/task log [name]` — Show task timeline log\n"
                "\n## Services\n"
                "- `/service list` — List services\n"
                "- `/service install <type> <name> [config]` — Install\n"
                "- `/service uninstall <name>` — Remove\n"
                "- `/service enable|disable <name>` — Toggle\n"
                "\n## Resources\n"
                "- `/resources` — List all resources\n"
                "- `/activate <type> <name>` — Activate resource\n"
                "- `/deactivate <type> <name>` — Deactivate resource\n"
                "- `/tools` — List tools\n"
                "- `/call <tool> {json}` — Call tool directly\n"
                "\n## Secrets & Variables\n"
                "- `/add-secret <name> <value>` — Store secret\n"
                "- `/secrets` — List secrets\n"
                "- `/add-variable <name> <value>` — Set variable\n"
                "- `/variables` — List variables\n"
                "\n## Schedules\n"
                "- `/schedules list` — List schedules\n"
                "- `/schedules add <when>` — Add schedule\n"
                "- `/schedules clear` — Clear schedules\n"
                "\n## LLM & Model\n"
                "- `/model <name>` — Switch model\n"
                "- `/llm <agent> <service>` — Override LLM service\n"
                "\n## Files & Prompts\n"
                "- `/files` — List conversation files\n"
                "- `/upload <path>` — Upload file (or drag file onto terminal)\n"
                "- `/paste` — Upload image from clipboard\n"
                "- `/view <path|url>` — Open file in browser\n"
                "- `/clear-files` — Clear pending attachments (`/detach` alias)\n"
                "- `/prompt list` — List prompts\n"
                "- `/prompt use <name>` — Show prompt\n"
                "\n## Dev Tools\n"
                "- `/run <command>` — Run shell command on relay directly\n"
                "- `/diff [file|ref]` — Show git diff with colors\n"
                "- `/watch <file>` — Watch file for changes (poll 3s)\n"
                "- `/watch stop` — Stop watching\n"
                "- `/multi` — Multiline input mode\n"
                "- `/copy [N]` — Copy last response to clipboard\n"
                "\n## Other\n"
                "- `/cost` — Token usage/cost\n"
                "- `/login` — Re-authenticate\n"
                "- `/quit` — Exit (`/exit` alias)\n"
                "\n*Aliases: `/message` = `/msg`, `/interrupt` = `/stop`, "
                "`/detach` = `/clear-files`*\n"
            )
            return

        if cmd == "/clear":
            if self.renderer.console:
                self.renderer.console.clear()
            else:
                os.system("cls" if os.name == "nt" else "clear")
            return

        if cmd == "/new":
            self.conversation_id = None
            self.selected_agent = ""
            if self.sse:
                self.sse.disconnect()
                self.sse = None
            self.renderer.print_system("New conversation started.")
            return

        if cmd == "/login":
            from pawflow_cli.auth import authenticate
            auth = authenticate(self.server_url, force=True)
            self.session_token = auth["token"]
            self.username = auth["username"]
            self.api.session_token = self.session_token
            self.renderer.print_system(f"Re-authenticated as {self.username}")
            return

        if cmd in ("/conv", "/conversations"):
            try:
                data = self.api.send_action("list_conversations")
                self.renderer.print_conversation_list(data.get("conversations", []))
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/resume":
            if not arg:
                self.renderer.print_error("Usage: /resume <id> [num_messages]")
                return
            parts = arg.split()
            cid_partial = parts[0]
            show_n = int(parts[1]) if len(parts) > 1 else 50
            full_cid = self._resolve_conversation_id(cid_partial)
            if not full_cid:
                self.renderer.print_error(f"No conversation matching '{cid_partial}'")
                return
            try:
                data = self.api.send_action("load_history",
                                             conversation_id=full_cid,
                                             limit=show_n, offset=0)
                if data.get("error"):
                    self.renderer.print_error(data["error"])
                else:
                    self.conversation_id = full_cid
                    self._last_history = data.get("messages", [])
                    save_config({"last_conversation_id": full_cid})
                    if self.sse:
                        self.sse.disconnect()
                    self.sse = SSEClient(self.server_url, self.session_token)
                    self.sse.connect(full_cid)
                    total = data.get("message_count", 0)
                    has_more = data.get("has_more", False)
                    shown = len(self._last_history)
                    more_hint = f" — /history for older" if has_more else ""
                    self.renderer.print_system(
                        f"Resumed {full_cid[:8]} (showing {shown} of {total}{more_hint})")
                    self._display_history(self._last_history, show_n)
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/history":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            parts = arg.split() if arg else []
            n = int(parts[0]) if parts and parts[0].isdigit() else 50
            offset = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            try:
                data = self.api.send_action("load_history",
                                             conversation_id=self.conversation_id,
                                             limit=n, offset=offset)
                if data.get("error"):
                    self.renderer.print_error(data["error"])
                    return
                messages = data.get("messages", [])
                total = data.get("message_count", 0)
                has_more = data.get("has_more", False)
                self._display_history(messages, len(messages))
                more_hint = f" — /history {n} {offset + len(messages)} for older" if has_more else ""
                self.renderer.print_system(f"Showing {len(messages)} of {total}{more_hint}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/compact":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            try:
                data = self.api.send_action("compact", conversation_id=self.conversation_id,
                                             agent_name=arg or "")
                self.renderer.print_system(f"Compaction started")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/model":
            if not arg:
                self.renderer.print_error("Usage: /model <model_name> or /model reset")
                return
            try:
                data = self.api.send_action("model", model=arg, agent=self.selected_agent or "assistant",
                                             conversation_id=self.conversation_id or "")
                self.renderer.print_system(data.get("message", "Model updated"))
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/agent":
            if not arg or arg == "list":
                try:
                    data = self.api.send_action("list_agents",
                                                 conversation_id=self.conversation_id or "")
                    agents = data.get("agents", [])
                    for a in agents:
                        name = a.get("name", "?")
                        active = " (active)" if a.get("active") else ""
                        self.renderer.print(f"  {name}{active}")
                except Exception as e:
                    self.renderer.print_error(str(e))
            else:
                parts = arg.split(None, 2)
                subcmd = parts[0].lower()

                if subcmd == "create":
                    if len(parts) < 3:
                        self.renderer.print_error("Usage: /agent create <name> <prompt>")
                        return
                    try:
                        data = self.api.send_action("create_agent", conversation_id=self.conversation_id or "", name=parts[1], prompt=parts[2])
                        self.renderer.print_system(f"Agent '{parts[1]}' created")
                    except Exception as e:
                        self.renderer.print_error(str(e))

                elif subcmd == "delete":
                    if len(parts) < 2:
                        self.renderer.print_error("Usage: /agent delete <name>")
                        return
                    try:
                        data = self.api.send_action("delete_agent", name=parts[1])
                        self.renderer.print_system(f"Agent '{parts[1]}' deleted")
                    except Exception as e:
                        self.renderer.print_error(str(e))

                elif subcmd == "setname":
                    if len(parts) < 2:
                        self.renderer.print_error("Usage: /agent setname <real> [nickname]")
                        return
                    nick = parts[2] if len(parts) > 2 else ""
                    try:
                        self.api.send_action("set_agent_nickname", conversation_id=self.conversation_id, real_name=parts[1], nickname=nick)
                        self.renderer.print_system(f"Nickname set: {parts[1]} → {nick or '(cleared)'}")
                    except Exception as e:
                        self.renderer.print_error(str(e))

                elif subcmd in ("disable", "enable", "promote"):
                    if len(parts) < 2:
                        self.renderer.print_error(f"Usage: /agent {subcmd} <name>")
                        return
                    try:
                        action = f"agent_{subcmd}"
                        self.api.send_action(action, agent_name=parts[1], conversation_id=self.conversation_id or "")
                        self.renderer.print_system(f"Agent '{parts[1]}' {subcmd}d")
                    except Exception as e:
                        self.renderer.print_error(str(e))

                else:
                    self.selected_agent = arg
                    self.renderer.print_system(f"Switched to agent: {arg}")
            return

        if cmd == "/resources":
            try:
                data = self.api.send_action("list_resources",
                                             conversation_id=self.conversation_id or "")
                for rtype, items in data.items():
                    if isinstance(items, list) and items:
                        self.renderer.print(f"\n  [bold]{rtype}[/bold]")
                        for item in items:
                            name = item.get("name", "?")
                            active = " ✓" if item.get("active") else ""
                            self.renderer.print(f"    {name}{active}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/tools":
            try:
                data = self.api.send_action("list_tools",
                                             conversation_id=self.conversation_id or "")
                tools = data.get("tools", [])
                for t in tools:
                    self.renderer.print(f"  {t.get('name', '?')}: {t.get('description', '')[:80]}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/cost":
            try:
                data = self.api.send_action("cost", agent=arg or "ALL")
                self.renderer.print_markdown(f"```\n{data}\n```" if isinstance(data, str) else str(data))
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/export":
            fmt = arg or "markdown"
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            try:
                data = self.api.send_action("export", conversation_id=self.conversation_id,
                                             format=fmt)
                url = data.get("url", "")
                fname = data.get("filename", "")
                self.renderer.print_system(f"Exported: {url} ({fname})")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/delete":
            if not arg:
                self.renderer.print_error("Usage: /delete <conversation_id>")
                return
            try:
                data = self.api.send_action("delete_conversation", conversation_id=arg)
                if data.get("deleted"):
                    self.renderer.print_system(f"Deleted {arg[:8]}")
                    if self.conversation_id == arg:
                        self.conversation_id = None
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Agent Messaging ---

        if cmd in ("/msg", "/message"):
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /msg <agent|ALL> <text>")
                return
            target, message = parts
            try:
                if target.upper() == "ALL":
                    data = self.api.send_action("broadcast_agents", conversation_id=self.conversation_id, message=message)
                    self.renderer.print_system(f"Broadcast sent")
                else:
                    self.api.send_message(message, conversation_id=self.conversation_id, target_agent=target)
                    self.renderer.print_system(f"Message sent to {target}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/btw":
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /btw <agent|ALL> <question>")
                return
            target, question = parts
            try:
                self.api.send_action("btw", conversation_id=self.conversation_id, message=question, agent_name=target)
                self.renderer.print_system(f"Side question sent to {target}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd in ("/stop", "/interrupt"):
            if not arg:
                self.renderer.print_error("Usage: /stop <agent|ALL> [-f]")
                return
            force = "-f" in arg
            target = arg.replace("-f", "").strip()
            try:
                action = "cancel" if force else "interrupt"
                self.api.send_action(action, conversation_id=self.conversation_id, target=target, agent_name=target)
                self.renderer.print_system(f"{'Cancelled' if force else 'Interrupted'} {target}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Context Management ---

        if cmd == "/rebuild":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            try:
                self.api.send_action("rebuild", conversation_id=self.conversation_id, agent_name=arg or "")
                self.renderer.print_system("Rebuild started")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/restart":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            parts = arg.split()
            agent = parts[0] if parts else ""
            keep = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            try:
                self.api.send_action("restart_from", conversation_id=self.conversation_id, agent_name=agent, keep_last=keep)
                self.renderer.print_system(f"Context restarted (keeping last {keep})")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/summary":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            parts = arg.split()
            agent = parts[0] if parts else ""
            tokens = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 4000
            try:
                self.api.send_action("resume_conversation", conversation_id=self.conversation_id, agent_name=agent, max_tokens=tokens)
                self.renderer.print_system("Summary started")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/context":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            try:
                data = self.api.send_action("get_context", conversation_id=self.conversation_id, agent_name=arg or "")
                messages = data.get("context", data.get("messages", []))
                tokens = data.get("token_estimate", data.get("estimated_tokens", 0))
                diverged = data.get("diverged", False)
                agent_name = data.get("agent_name", arg or "shared")
                label = f"{agent_name} ({'diverged' if diverged else 'shared'})"
                self.renderer.print_system(f"Context [{label}]: {len(messages)} messages, ~{tokens:,} tokens")
                # Show available sub-contexts
                agent_ctxs = data.get("agent_contexts", {})
                if agent_ctxs:
                    ctx_list = ", ".join(f"{k} ({v})" for k, v in agent_ctxs.items() if k != "*")
                    if ctx_list:
                        self.renderer.print_system(f"Available: {ctx_list}")
                for i, m in enumerate(messages[-20:]):
                    role = m.get("role", "?")
                    content = m.get("content", "")[:100]
                    self.renderer.print(f"  [{i}] {role}: {content}...")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Memory Management ---

        if cmd == "/memory":
            if not self.conversation_id:
                self.renderer.print_error("No active conversation")
                return
            parts = arg.split(None, 1) if arg else ["list"]
            subcmd = parts[0].lower()
            subarg = parts[1] if len(parts) > 1 else ""

            try:
                if subcmd == "list":
                    data = self.api.send_action("list_memories", conversation_id=self.conversation_id, agent_name=subarg or "")
                    memories = data.get("memories", [])
                    if not memories:
                        self.renderer.print_system("No memories.")
                    else:
                        for m in memories:
                            tags = " ".join(f"#{t}" for t in m.get("tags", []))
                            self.renderer.print(f"  [{m.get('id', '?')[:8]}] {m.get('content', '')[:80]} {tags}")

                elif subcmd == "add":
                    if not subarg:
                        self.renderer.print_error("Usage: /memory add <text> [@agent] [#tag1 #tag2]")
                        return
                    self.api.send_action("add_memory", conversation_id=self.conversation_id, content=subarg)
                    self.renderer.print_system("Memory added")

                elif subcmd in ("del", "delete"):
                    if not subarg:
                        self.renderer.print_error("Usage: /memory del <id>")
                        return
                    self.api.send_action("delete_memory", conversation_id=self.conversation_id, memory_id=subarg)
                    self.renderer.print_system("Memory deleted")

                elif subcmd == "edit":
                    edit_parts = subarg.split(None, 1)
                    if len(edit_parts) < 2:
                        self.renderer.print_error("Usage: /memory edit <id> <new text>")
                        return
                    self.api.send_action("edit_memory", conversation_id=self.conversation_id, memory_id=edit_parts[0], content=edit_parts[1])
                    self.renderer.print_system("Memory updated")

                elif subcmd == "search":
                    data = self.api.send_action("search_memories", conversation_id=self.conversation_id, query=subarg)
                    results = data.get("results", [])
                    for r in results:
                        self.renderer.print(f"  [{r.get('id', '?')[:8]}] ({r.get('score', 0):.2f}) {r.get('content', '')[:80]}")

                else:
                    self.renderer.print_error("Usage: /memory list|add|del|edit|search")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Skill Management ---

        if cmd == "/skill":
            parts = arg.split(None, 2) if arg else ["list"]
            subcmd = parts[0].lower()
            try:
                if subcmd == "list":
                    data = self.api.send_action("list_resources", conversation_id=self.conversation_id or "")
                    skills = data.get("skill", data.get("skills", []))
                    if isinstance(skills, list):
                        for s in skills:
                            name = s.get("name", "?")
                            active = " ✓" if s.get("active") else ""
                            self.renderer.print(f"  {name}{active}: {s.get('description', '')[:60]}")
                    else:
                        self.renderer.print_system("No skills.")
                elif subcmd == "add":
                    if len(parts) < 3:
                        self.renderer.print_error("Usage: /skill add <name> <prompt>")
                        return
                    self.api.send_action("create_resource", resource_type="skill", name=parts[1], prompt=parts[2], conversation_id=self.conversation_id or "")
                    self.renderer.print_system(f"Skill '{parts[1]}' created")
                elif subcmd in ("del", "delete"):
                    if len(parts) < 2:
                        self.renderer.print_error("Usage: /skill del <name>")
                        return
                    self.api.send_action("delete_resource", resource_type="skill", name=parts[1])
                    self.renderer.print_system(f"Skill '{parts[1]}' deleted")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Task Management ---

        if cmd == "/task":
            parts = arg.split(None, 2) if arg else ["list"]
            subcmd = parts[0].lower()
            try:
                if subcmd == "list":
                    data = self.api.send_action("task_status", conversation_id=self.conversation_id or "")
                    tasks = data.get("tasks", [])
                    for t in tasks:
                        status = t.get("status", "?")
                        self.renderer.print(f"  [{status}] {t.get('name', '?')}: {t.get('description', '')[:60]}")
                    if not tasks:
                        self.renderer.print_system("No tasks.")
                elif subcmd == "create":
                    if len(parts) < 3:
                        self.renderer.print_error("Usage: /task create <name> <prompt>")
                        return
                    self.api.send_action("create_task_def", name=parts[1], prompt=parts[2], conversation_id=self.conversation_id or "")
                    self.renderer.print_system(f"Task '{parts[1]}' created")
                elif subcmd == "assign":
                    assign_parts = (parts[1] if len(parts) > 1 else "").split(None, 1)
                    if len(assign_parts) < 2:
                        self.renderer.print_error("Usage: /task assign <agent> <task> [--context last:10]")
                        return
                    # Parse optional --context
                    task_arg = assign_parts[1]
                    context = "isolated"
                    if "--context" in task_arg:
                        task_parts = task_arg.split("--context", 1)
                        task_arg = task_parts[0].strip()
                        context = task_parts[1].strip()
                    self.api.send_action("assign_task", agent_name=assign_parts[0],
                                         task_name=task_arg, context=context,
                                         conversation_id=self.conversation_id or "")
                    self.renderer.print_system(f"Task assigned to {assign_parts[0]} (context: {context})")
                elif subcmd in ("del", "delete"):
                    if len(parts) < 2:
                        self.renderer.print_error("Usage: /task del <name>")
                        return
                    self.api.send_action("delete_task_def", name=parts[1])
                    self.renderer.print_system(f"Task '{parts[1]}' deleted")
                elif subcmd == "log":
                    task_name = parts[1] if len(parts) > 1 else ""
                    data = self.api.send_action("task_log", name=task_name,
                                                 conversation_id=self.conversation_id or "")
                    if task_name:
                        log = data.get("log", [])
                        if not log:
                            self.renderer.print_system(f"No log for task '{task_name}'")
                        else:
                            import datetime
                            for entry in log[-30:]:  # last 30 entries
                                ts = datetime.datetime.fromtimestamp(entry.get("ts", 0))
                                t = entry.get("type", "?")
                                agent = entry.get("agent", "")
                                detail = entry.get("detail", "")
                                badge = f"[{agent}] " if agent else ""
                                self.renderer.print(f"  {ts.strftime('%H:%M:%S')} {badge}{t}: {detail[:100]}")
                    else:
                        logs = data.get("logs", {})
                        for tname, entries in logs.items():
                            self.renderer.print(f"  {tname}: {len(entries)} entries")
                elif subcmd in ("pause", "resume", "cancel"):
                    if len(parts) < 2:
                        self.renderer.print_error(f"Usage: /task {subcmd} <task_id|agent>")
                        return
                    self.api.send_action(f"{subcmd}_task", task_id=parts[1], conversation_id=self.conversation_id or "")
                    self.renderer.print_system(f"Task {subcmd}d: {parts[1]}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Service Management ---

        if cmd == "/service":
            parts = arg.split(None, 2) if arg else ["list"]
            subcmd = parts[0].lower()
            try:
                if subcmd == "list":
                    data = self.api.send_action("service_list")
                    services = data.get("services", [])
                    for s in services:
                        status = "+" if s.get("enabled") or s.get("connected") else "-"
                        self.renderer.print(f"  [{status}] {s.get('id', '?')} ({s.get('type', '?')}): {s.get('description', '')[:50]}")
                    if not services:
                        self.renderer.print_system("No services.")
                elif subcmd == "install":
                    if len(parts) < 3:
                        self.renderer.print_error("Usage: /service install <type> <name> [key=val,...]")
                        return
                    rest = parts[2].split(None, 1)
                    name = rest[0]
                    config_str = rest[1] if len(rest) > 1 else ""
                    self.api.send_action("service_install", service_type=parts[1], service_name=name, config_str=config_str)
                    self.renderer.print_system(f"Service '{name}' installed")
                elif subcmd == "uninstall":
                    if len(parts) < 2:
                        self.renderer.print_error("Usage: /service uninstall <name>")
                        return
                    self.api.send_action("service_uninstall", service_id=parts[1])
                    self.renderer.print_system(f"Service '{parts[1]}' removed")
                elif subcmd in ("enable", "disable"):
                    if len(parts) < 2:
                        self.renderer.print_error(f"Usage: /service {subcmd} <name>")
                        return
                    self.api.send_action(f"service_{subcmd}", service_id=parts[1])
                    self.renderer.print_system(f"Service '{parts[1]}' {subcmd}d")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Secrets & Variables ---

        if cmd in ("/add-secret", "/secret"):
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /add-secret <name> <value>")
                return
            try:
                self.api.send_action("add_secret", name=parts[0], value=parts[1])
                self.renderer.print_system(f"Secret '{parts[0]}' stored")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd in ("/secrets", "/list-secrets"):
            try:
                data = self.api.send_action("list_secrets")
                secrets = data.get("secrets", [])
                for s in secrets:
                    self.renderer.print(f"  {s}")
                if not secrets:
                    self.renderer.print_system("No secrets.")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd in ("/add-variable", "/add-var"):
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /add-variable <name> <value>")
                return
            try:
                self.api.send_action("add_variable", name=parts[0], value=parts[1])
                self.renderer.print_system(f"Variable '{parts[0]}' set")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd in ("/variables", "/vars", "/list-variables"):
            try:
                data = self.api.send_action("list_variables")
                variables = data.get("variables", {})
                for k, v in variables.items() if isinstance(variables, dict) else []:
                    self.renderer.print(f"  {k} = {v}")
                if not variables:
                    self.renderer.print_system("No variables.")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Schedules ---

        if cmd in ("/schedules", "/tasks"):
            parts = arg.split(None, 1) if arg else ["list"]
            subcmd = parts[0].lower()
            try:
                if subcmd == "list" or not arg:
                    data = self.api.send_action("list_schedules", conversation_id=self.conversation_id or "")
                    scheds = data.get("schedules", [])
                    for s in scheds:
                        import datetime
                        at = datetime.datetime.fromtimestamp(s.get("recheck_at", 0))
                        self.renderer.print(f"  {at.strftime('%Y-%m-%d %H:%M')} — {s.get('reason', 'recheck')}")
                    if not scheds:
                        self.renderer.print_system("No scheduled tasks.")
                elif subcmd == "add":
                    subarg = parts[1] if len(parts) > 1 else ""
                    self.api.send_action("add_schedule", conversation_id=self.conversation_id or "", when=subarg)
                    self.renderer.print_system("Schedule added")
                elif subcmd in ("del", "delete", "clear"):
                    self.api.send_action("delete_schedule", conversation_id=self.conversation_id or "")
                    self.renderer.print_system("Schedules cleared")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- LLM Override ---

        if cmd == "/llm":
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /llm <agent> <service|restore>")
                return
            try:
                self.api.send_action("set_llm_service", conversation_id=self.conversation_id or "", agent_name=parts[0], llm_service=parts[1])
                self.renderer.print_system(f"LLM service for '{parts[0]}' set to '{parts[1]}'")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Resource Activate/Deactivate ---

        if cmd == "/activate":
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /activate <type> <name>")
                return
            try:
                self.api.send_action("activate_resource", conversation_id=self.conversation_id or "", resource_type=parts[0], name=parts[1])
                self.renderer.print_system(f"Activated {parts[0]} '{parts[1]}'")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/deactivate":
            parts = arg.split(None, 1)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /deactivate <type> <name>")
                return
            try:
                self.api.send_action("deactivate_resource", conversation_id=self.conversation_id or "", resource_type=parts[0], name=parts[1])
                self.renderer.print_system(f"Deactivated {parts[0]} '{parts[1]}'")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Call Tool ---

        if cmd == "/call":
            if not arg:
                self.renderer.print_error("Usage: /call <tool_name> {json_args}")
                return
            parts = arg.split(None, 1)
            tool_name = parts[0]
            try:
                import json as _json
                args_dict = _json.loads(parts[1]) if len(parts) > 1 else {}
                data = self.api.send_action("call_tool", tool_name=tool_name, arguments=args_dict, conversation_id=self.conversation_id or "")
                result = data.get("result", str(data))
                self.renderer.print_system(f"Tool result:\n{result[:1000]}")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        # --- Files & Prompts ---

        if cmd == "/files":
            try:
                data = self.api.send_action("list_conv_files", conversation_id=self.conversation_id or "")
                files = data.get("files", [])
                for f in files:
                    self.renderer.print(f"  {f.get('file_id', '?')[:8]}  {f.get('filename', '?')}  ({f.get('size', 0):,} bytes)")
                if not files:
                    self.renderer.print_system("No files in this conversation.")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/prompt":
            parts = arg.split(None, 1) if arg else ["list"]
            subcmd = parts[0].lower()
            try:
                if subcmd == "list":
                    data = self.api.send_action("list_prompts", conversation_id=self.conversation_id or "")
                    prompts = data.get("prompts", [])
                    for p in prompts:
                        self.renderer.print(f"  {p.get('name', '?')}: {p.get('description', p.get('content', ''))[:60]}")
                    if not prompts:
                        self.renderer.print_system("No prompts.")
                elif subcmd == "use":
                    name = parts[1] if len(parts) > 1 else ""
                    data = self.api.send_action("get_prompt", conversation_id=self.conversation_id or "", name=name)
                    content = data.get("content", "")
                    if content:
                        self.renderer.print_system(f"Prompt '{name}':")
                        self.renderer.print_markdown(content)
                    else:
                        self.renderer.print_error(f"Prompt '{name}' not found")
            except Exception as e:
                self.renderer.print_error(str(e))
            return

        if cmd == "/upload":
            if not arg:
                self.renderer.print_error("Usage: /upload <file_path>")
                return
            self._upload_file(arg.strip().strip('"').strip("'"))
            return

        if cmd == "/paste":
            self._paste_clipboard_image()
            return

        if cmd in ("/clear-files", "/detach"):
            self._pending_attachments.clear()
            self.renderer.print_system("Pending attachments cleared.")
            return

        if cmd == "/copy":
            self._copy_last_message(arg)
            return

        if cmd == "/view":
            if not arg:
                self.renderer.print_error("Usage: /view <file_path_or_url>")
                return
            target = arg.strip().strip('"').strip("'")
            import webbrowser
            # If it's a URL, open directly
            if target.startswith("http://") or target.startswith("https://") or target.startswith("/files/"):
                if target.startswith("/files/"):
                    target = f"{self.server_url}{target}"
                webbrowser.open(target)
                self.renderer.print_system(f"Opened: {target}")
            # If it's a local file, open it
            elif os.path.isfile(target):
                webbrowser.open(f"file:///{os.path.abspath(target)}")
                self.renderer.print_system(f"Opened: {target}")
            # If it's a path on the relay filesystem
            else:
                # Try to get the file via the agent API and open from FileStore
                try:
                    data = self.api.send_action("fs_copy_to_store",
                                                 service=self.relay.relay_id if self.relay else "",
                                                 path=target)
                    if data.get("url"):
                        url = f"{self.server_url}{data['url']}"
                        webbrowser.open(url)
                        self.renderer.print_system(f"Opened: {url}")
                    elif data.get("error"):
                        self.renderer.print_error(data["error"])
                except Exception as e:
                    self.renderer.print_error(f"Cannot open: {e}")
            return

        if cmd == "/run":
            if not arg:
                self.renderer.print_error("Usage: /run <command>")
                return
            try:
                result = self.api.send_action("fs_exec",
                    service=self.relay.relay_id if self.relay else "",
                    command=arg, timeout=30)
                if result.get("error"):
                    self.renderer.print_error(result["error"])
                else:
                    stdout = result.get("stdout", "")
                    stderr = result.get("stderr", "")
                    rc = result.get("returncode", -1)
                    self.renderer.print_exec_output(arg, rc, stdout, stderr)
            except Exception as e:
                self.renderer.print_error(f"Exec failed: {e}")
            return

        if cmd == "/diff":
            if not arg:
                arg = "."
            try:
                data = self.api.send_action("fs_exec",
                    service=self.relay.relay_id if self.relay else "",
                    command=f"git diff {arg}", timeout=15)
                output = data.get("stdout", "")
                if not output:
                    self.renderer.print_system("No changes.")
                else:
                    self.renderer.print_tool_result("diff", output)
            except Exception as e:
                self.renderer.print_error(f"Diff failed: {e}")
            return

        if cmd == "/multi":
            self.renderer.print_system("Multiline mode: type your message. Press Alt+Enter or Escape then Enter to send.")
            try:
                if HAS_PROMPT_TOOLKIT:
                    from prompt_toolkit import prompt as pt_prompt
                    text = pt_prompt("... ", multiline=True)
                else:
                    lines = []
                    self.renderer.print_system("Type lines, empty line to send:")
                    while True:
                        line = input("... ")
                        if line == "":
                            break
                        lines.append(line)
                    text = "\n".join(lines)
                if text.strip():
                    self._send_message(text.strip())
            except (EOFError, KeyboardInterrupt):
                self.renderer.print_system("Cancelled.")
            return

        if cmd == "/watch":
            if not arg:
                self.renderer.print_error("Usage: /watch <file_path> | /watch stop")
                return
            if arg.strip() == "stop":
                if hasattr(self, '_watch_thread') and self._watch_thread:
                    self._watch_stop.set()
                    self._watch_thread = None
                    self.renderer.print_system("File watch stopped.")
                else:
                    self.renderer.print_system("No active watch.")
                return
            # Start watching in background
            self._watch_stop = threading.Event()
            filepath = arg.strip()
            def _watch():
                import hashlib
                last_hash = ""
                while not self._watch_stop.is_set():
                    try:
                        data = self.api.send_action("fs_read_file",
                            service=self.relay.relay_id if self.relay else "",
                            path=filepath)
                        content = data.get("content", "")
                        h = hashlib.md5(content.encode()).hexdigest()
                        if last_hash and h != last_hash:
                            self.renderer.print_system(f"File changed: {filepath}")
                            sys.stdout.write("\a")
                            sys.stdout.flush()
                        last_hash = h
                    except Exception:
                        pass
                    self._watch_stop.wait(3)
            self._watch_thread = threading.Thread(target=_watch, daemon=True)
            self._watch_thread.start()
            self.renderer.print_system(f"Watching {filepath} (poll every 3s). /watch stop to cancel.")
            return

        # Unknown command — send as message (might be a skill like /review)
        self._send_message(text)

    def _display_history(self, messages: list, show_n: int = 10):
        """Display the last N messages from conversation history."""
        # Filter out system messages but keep tool_call/tool_result for context
        displayable = [m for m in messages if m.get("type", m.get("role", "")) != "system"]
        recent = displayable[-show_n:] if len(displayable) > show_n else displayable
        if len(displayable) > show_n:
            self.renderer.print_system(
                f"... ({len(displayable) - show_n} earlier messages, use /history {len(displayable)} to see all)")
        import traceback as _tb
        for i, m in enumerate(recent):
            try:
                self.renderer.render_history_message(m)
            except Exception as e:
                sys.stderr.write(f"Render error msg {i}: {e}\n")
                _tb.print_exc(file=sys.stderr)

    def _update_status(self, text: str):
        """Update the bottom toolbar status text (called from renderer)."""
        self._status_text = text

    def _safe_stop_live(self):
        """Force-stop any active Rich Live display to prevent output corruption."""
        try:
            if self.renderer._live:
                self.renderer._live.stop()
                self.renderer._live = None
        except Exception:
            pass

    def _resolve_conversation_id(self, partial: str) -> str:
        """Resolve a partial conversation ID to full ID."""
        try:
            data = self.api.send_action("list_conversations")
            for c in data.get("conversations", []):
                cid = c.get("conversation_id", "")
                if cid.startswith(partial):
                    return cid
        except Exception:
            pass
        return ""

    def _signal_handler(self, sig, frame):
        self.renderer.print_system("\nShutting down...")
        self._running = False
        self._cleanup()
        sys.exit(0)

    def _cleanup(self):
        if self.sse:
            self.sse.disconnect()
        if self.relay:
            self.relay.stop()


def main():
    """Entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="PawCode — Terminal chat frontend")
    default_server = os.environ.get("PAWFLOW_SERVER", "http://localhost:9090")
    parser.add_argument("--server", default=default_server,
                        help=f"PawFlow server URL (env: PAWFLOW_SERVER, default: {default_server})")
    parser.add_argument("--dir", default=".",
                        help="Directory to mount as filesystem (default: current directory)")
    parser.add_argument("--no-exec", action="store_true",
                        help="Disable shell execution on the mounted directory")
    parser.add_argument("--no-relay", action="store_true",
                        help="Don't mount filesystem relay (chat only)")
    parser.add_argument("--login", action="store_true",
                        help="Force re-authentication")
    args = parser.parse_args()

    cli = PawCode(
        server_url=args.server,
        directory=args.dir,
        allow_exec=not args.no_exec,
    )

    if args.login:
        from pawflow_cli.config import clear_session
        clear_session()

    try:
        cli.start()
    except ConnectionRefusedError:
        print(f"\n  Error: Cannot connect to PawFlow server at {args.server}")
        print(f"  Make sure the server is running and the URL is correct.")
        print(f"  Set PAWFLOW_SERVER env var or use --server to change the URL.\n")
        sys.exit(1)
    except TimeoutError as e:
        print(f"\n  Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        if "Connection refused" in str(e) or "connect" in str(e).lower():
            print(f"\n  Error: Cannot connect to PawFlow server at {args.server}")
            print(f"  Make sure the server is running.\n")
            sys.exit(1)
        raise
