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
    "/memory", "/skill", "/task", "/service", "/flow",
    "/resources", "/activate", "/deactivate",
    "/tools", "/call", "/model", "/llm",
    "/files", "/upload", "/paste", "/view", "/prompt",
    "/clear-files", "/detach",
    "/add-secret", "/secrets", "/add-variable", "/variables",
    "/schedules", "/cost", "/copy", "/clear", "/login", "/quit", "/exit",
    "/help", "/run", "/diff", "/watch", "/multi", "/plan",
    "/rename", "/delete-msg", "/search", "/link",
    "/vidservice", "/imgservice", "/share", "/install", "/uninstall",
    "/rebuild-full", "/rebuild_clean", "/setname", "/autoconv",
    "/bg", "/cancel",
    "/vm",
    "/claude-login-relay", "/clr", "/claude-login-credentials", "/clc",
]
_completer = WordCompleter(_COMMANDS, sentence=True) if HAS_PROMPT_TOOLKIT else None


class PawCode:
    """Main CLI application."""

    def __init__(self, server_url: str, directory: str,
                 docker_image: str = "", gateway_cookie: str = "",
                 gateway_key: str = "",
                 docker_cpus: str = "", docker_memory: str = "",
                 allow_local: bool = False):
        self.server_url = server_url
        self.directory = str(Path(directory).resolve())
        self.docker_image = docker_image
        self.gateway_cookie = gateway_cookie
        self.gateway_key = gateway_key
        self.docker_cpus = docker_cpus
        self.docker_memory = docker_memory
        self.allow_local = allow_local

        self.renderer = TerminalRenderer()
        self.api: AgentAPIClient = None
        self.sse: SSEClient = None
        self.relay = None

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
        self._active_agents = {}  # server-polled active agents (single source of truth for typing)

    def _on_relay_token_refresh(self, new_token):
        """Called when the server sends a refreshed session token via relay."""
        self.session_token = new_token
        from pawflow_cli.config import save_session
        save_session(new_token, self.username, self.server_url,
                     __import__('time').time() + 8 * 3600)
        sys.stderr.write("[PawCode] Session token refreshed\n")

    def start(self):
        """Initialize auth, relay, and start the main loop."""
        # Wire status callback for bottom toolbar
        self.renderer.set_status_callback(self._update_status)

        # Check auth — don't auto-open browser, let user /login manually
        self.renderer.print_banner(self.directory)

        from pawflow_cli.auth import check_session
        auth = check_session(self.server_url, gateway_cookie=self.gateway_cookie)
        if auth:
            self.session_token = auth["token"]
            self.username = auth["username"]
            self.renderer.print_system(f"Authenticated as {self.username}")
        else:
            self.session_token = ""
            self.username = ""
            self.renderer.print_system(
                "Not logged in. Use /login or run: pawcode auth login")

        # API client
        self.api = AgentAPIClient(self.server_url, self.session_token, self.gateway_cookie)

        if self.session_token:
            self.renderer.print_system(
                "PawCode is chat-only; manage filesystem relays from webchat "
                "or the standalone pawflow-relay client.")

        # Cleanup on exit
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Resume last conversation with sliding window
        config = load_config()
        last_cid = config.get("last_conversation_id")
        if last_cid and self.session_token:
            try:
                self.conversation_id = last_cid
                # Connect SSE FIRST so send_action can receive command_result
                self._ensure_sse()
                # Start event consumer thread so SSE events are processed
                self._start_event_consumer()
                # Now we can safely wait for the result
                data = self.api.send_action("load_history",
                                             conversation_id=last_cid, limit=50, offset=0)
                if not data.get("error"):
                    total = data.get("message_count", 0)
                    has_more = data.get("has_more", False)
                    messages = data.get("messages", [])
                    more_hint = " — /history for older" if has_more else ""
                    self.renderer.print_system(
                        f"Resumed {last_cid[:8]} (showing {len(messages)} of {total}{more_hint})")
                    self._display_history(messages, len(messages))
                    if total > 100 and messages:
                        for m in reversed(messages):
                            if m.get("type") in ("assistant", "agent_response"):
                                source = m.get("source", {})
                                agent = source.get("name", "") if isinstance(source, dict) else ""
                                preview = m.get("content", "")[:200]
                                if preview:
                                    self.renderer.print_system(f"Last ({agent}): {preview}...")
                                break
                else:
                    self.conversation_id = None
            except Exception:
                self.conversation_id = None

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

        # Start background event consumer (if not already started at resume)
        self._start_event_consumer()

        # Start active-agents poller (single source of truth for typing indicator)
        self._active_poll_thread = threading.Thread(target=self._active_agents_poller,
                                                     daemon=True, name="pawcode-active-poll")
        self._active_poll_thread.start()

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
                    except KeyboardInterrupt:
                        self._running = False
                        break
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
                except KeyboardInterrupt:
                    self._running = False
                    break
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
        # Detect @filepath references and auto-upload
        import re
        at_files = re.findall(r'@((?:[A-Za-z]:\\|/|\.\.?/)\S+)', text)
        for fpath in at_files:
            clean_at = fpath.strip('"').strip("'")
            if os.path.isfile(clean_at):
                self._upload_file(clean_at)
                text = text.replace(f"@{fpath}", f"[attached: {os.path.basename(clean_at)}]")
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
            self.sse = SSEClient(self.server_url, self.session_token, self.gateway_cookie)
            self.sse.connect(self.conversation_id)

    def _start_event_consumer(self):
        """Start the event consumer thread (idempotent)."""
        if hasattr(self, '_event_thread') and self._event_thread and self._event_thread.is_alive():
            return
        self._event_thread = threading.Thread(target=self._event_consumer,
                                               daemon=True, name="pawcode-events")
        self._event_thread.start()

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

    _last_session_renew = 0.0

    def _active_agents_poller(self):
        """Background thread: poll server for active agents every 3s.

        This is the SINGLE source of truth for the typing/status indicator,
        matching the web UI's syncActiveFromServer approach.
        Also renews the local session expiry every 30 minutes.
        """
        while self._running:
            time.sleep(3)
            if not self._running or not self.api or not self.conversation_id:
                continue
            try:
                data = self.api.send_action("list_active",
                                             conversation_id=self.conversation_id)
                server_active = data.get("active", [])
                server_keys = set()
                for _sa in server_active:
                    _n = _sa.get("agent_name", "").lower()
                    _t = _sa.get("task_id", "")
                    server_keys.add((_n + "::" + _t) if _t else _n)

                # Remove agents server doesn't know about
                for key in list(self._active_agents.keys()):
                    if key not in server_keys:
                        del self._active_agents[key]

                # Add/update from server
                for a in server_active:
                    _an = a.get("agent_name", "").lower()
                    _tid = a.get("task_id", "")
                    key = (_an + "::" + _tid) if _tid else _an
                    existing = self._active_agents.get(key, {})
                    self._active_agents[key] = {
                        "name": a.get("agent_name", ""),
                        "task_id": a.get("task_id", ""),
                        "iteration": a.get("iteration", existing.get("iteration", 0)),
                        "round": a.get("round", 0),
                        "max_rounds": a.get("max_rounds", 0),
                        "last_tool": a.get("last_tool", existing.get("last_tool", "")),
                        "total_tools": a.get("total_tools", existing.get("total_tools", 0)),
                        "duration_s": a.get("duration_s", 0),
                    }

                # Update status bar based on active agents (single source of truth)
                if self._active_agents:
                    from pawflow_cli.ui.renderer import _random_verb
                    parts = []
                    for info in self._active_agents.values():
                        name = info["name"]
                        if info.get("task_id"):
                            name += f" [task:{info['task_id']}]"
                        detail_parts = []
                        if info.get("iteration"):
                            detail_parts.append(f"iter {info['iteration']}")
                        if info.get("round") and info.get("max_rounds", 0) > 1:
                            detail_parts.append(f"round {info['round']}/{info['max_rounds']}")
                        if info.get("total_tools"):
                            detail_parts.append(f"{info['total_tools']} tools")
                        if info.get("last_tool"):
                            detail_parts.append(info["last_tool"])
                        detail = " \u00b7 ".join(detail_parts) if detail_parts else _random_verb() + "..."
                        parts.append(f"{name} ({detail})")
                    self._update_status(f"\u25b6 {', '.join(parts)}")
                else:
                    # Only clear if no active streams either (avoid flicker during token streaming)
                    if not self.renderer._streams:
                        self._update_status("")
                # Renew local session expiry every 30 min (sliding window)
                now = time.time()
                if now - self._last_session_renew > 1800 and self.session_token:
                    self._last_session_renew = now
                    from pawflow_cli.config import save_session
                    save_session(self.session_token, self.username,
                                 self.server_url, now + 8 * 3600)
            except Exception:
                pass  # silent — network may be down

    def _dispatch_event(self, event, streaming_agent, thinking_agent):
        """Dispatch a single SSE event. Returns True to keep waiting, False when done."""
        from pawflow_cli.event_handler import dispatch_event
        result, self._ev_streaming_agent, self._ev_thinking_agent = dispatch_event(
            self, event, streaming_agent, thinking_agent)
        return result

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
                                 result={"choice": result},
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_command(self, text: str):
        """Handle slash commands — server-first, client-only exceptions.

        ALL commands go to the server except UI-specific ones that
        only make sense client-side (clear, quit, file upload, etc.).
        """
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # Blocked commands (not available in CLI/relay context)
        if cmd in ("/cls", "/claude-login-server"):
            self.renderer.print_error("Server login is only available from the webchat.")
            return

        # Client-only commands (UI-specific, never sent to server)
        if cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            return
        if cmd in ("/quit", "/exit"):
            raise KeyboardInterrupt()

        # File/relay/session commands — need local state
        from pawflow_cli.commands.files import handle_files_commands
        from pawflow_cli.commands.session import handle_session_commands
        from pawflow_cli.commands.conversation import handle_conversation_commands
        for handler in (handle_files_commands, handle_session_commands, handle_conversation_commands):
            if handler(self, cmd, arg, text):
                return

        # Everything else → server (single source of truth)
        try:
            data = self.api.send_action("command", text=text,
                                         agent_name=self.selected_agent or "",
                                         conversation_id=self.conversation_id or "")
            if isinstance(data, dict):
                # Apply state updates from server
                if data.get("state_update"):
                    for k, v in data["state_update"].items():
                        if hasattr(self, k):
                            setattr(self, k, v)
                # Display
                if data.get("client_only"):
                    self.renderer.print_system(f"Client-only command: {cmd}")
                elif data.get("help"):
                    self.renderer.print_markdown(data["help"])
                elif data.get("display"):
                    self.renderer.print_system(data["display"])
                elif data.get("message"):
                    self.renderer.print_system(data["message"])
                elif data.get("conversations"):
                    # Conversation list
                    for c in data["conversations"][:20]:
                        cid = c.get("id", "?")[:8]
                        title = c.get("title") or c.get("preview", "")[:60] or "(empty)"
                        count = c.get("message_count", 0)
                        self.renderer.print_system(f"  {cid}  {title}  ({count} msgs)")
                elif data.get("error"):
                    self.renderer.print_error(data["error"])
                elif data.get("status") in ("ok", "accepted"):
                    # Silent success (agent action dispatched)
                    pass
                else:
                    self.renderer.print_system(str(data))
            else:
                self.renderer.print_error(f"Unknown command: {cmd}. Type /help for available commands.")
        except Exception as e:
            self.renderer.print_error(f"Command failed: {e}")
        return

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
        if getattr(self, '_shutting_down', False):
            os._exit(1)  # Force exit on second Ctrl-C
        self._shutting_down = True
        self.renderer.print_system("\nShutting down...")
        self._running = False
        try:
            self._cleanup()
        except Exception:
            pass
        os._exit(0)

    def run_prompt(self, prompt: str, conversation_id: str = None,
                   output_format: str = "text"):
        """Non-interactive mode: send one prompt, stream response, exit."""
        import json as _json

        # Authenticate silently
        auth = authenticate(self.server_url, gateway_cookie=self.gateway_cookie)
        self.session_token = auth["token"]
        self.username = auth["username"]
        self.api = AgentAPIClient(self.server_url, self.session_token, self.gateway_cookie)

        # PawCode no longer owns relay lifecycle. Filesystem relays are managed
        # by webchat server resources or the standalone pawflow-relay client.

        # Resolve conversation
        if not conversation_id:
            config = load_config()
            conversation_id = config.get("last_conversation_id")

        # Send message
        resp = self.api.send_message(
            message=prompt,
            conversation_id=conversation_id,
        )
        if resp.get("error"):
            print(resp["error"], file=sys.stderr)
            self._cleanup()
            sys.exit(1)

        cid = resp.get("conversation_id")
        if cid:
            self.conversation_id = cid
            save_config({"last_conversation_id": cid})

        # Connect SSE and wait for response
        self.sse = SSEClient(self.server_url, self.session_token, self.gateway_cookie)
        self.sse.connect(cid)

        response_text = ""
        streaming_tokens = {}
        is_full = output_format == "full"

        while True:
            try:
                event = self.sse.events.get(timeout=120)
            except queue.Empty:
                print("Timeout waiting for response", file=sys.stderr)
                break

            ev_type = event.get("event", "")
            data = event.get("data", {})

            if ev_type == "token":
                agent = data.get("agent_name", "")
                text = data.get("text", "")
                streaming_tokens.setdefault(agent, "")
                streaming_tokens[agent] += text
                if output_format == "text":
                    sys.stdout.write(text)
                    sys.stdout.flush()

            elif ev_type == "tool_call" and is_full:
                tool = data.get("tool", "?")
                args = data.get("arguments", {})
                print(f"\n[tool_call] {tool}({_json.dumps(args, ensure_ascii=False)[:200]})",
                      file=sys.stderr)

            elif ev_type == "tool_result" and is_full:
                tool = data.get("tool", "?")
                result = str(data.get("result", ""))[:500]
                print(f"[tool_result] {tool}: {result}", file=sys.stderr)

            elif ev_type == "done":
                response_text = data.get("response", "")
                agent = data.get("agent_name", "")
                # If we were streaming tokens, we already printed them
                if not streaming_tokens.get(agent) and response_text:
                    if output_format == "text":
                        sys.stdout.write(response_text)
                elif streaming_tokens.get(agent) and output_format == "text":
                    pass  # already printed via tokens

                if output_format == "json":
                    result = {
                        "response": response_text or streaming_tokens.get(agent, ""),
                        "agent": agent,
                        "conversation_id": cid,
                        "tokens_in": data.get("tokens_in", 0),
                        "tokens_out": data.get("tokens_out", 0),
                        "model": data.get("model", ""),
                    }
                    print(_json.dumps(result, ensure_ascii=False))
                elif output_format == "markdown":
                    text = response_text or streaming_tokens.get(agent, "")
                    print(text)

                if not data.get("continuing"):
                    break

            elif ev_type == "error_event":
                print(f"\nError: {data.get('message', 'Unknown error')}", file=sys.stderr)
                break

            elif ev_type == "cancelled":
                print(f"\nCancelled", file=sys.stderr)
                break

        # Ensure trailing newline for text mode
        if output_format == "text" and (response_text or streaming_tokens):
            sys.stdout.write("\n")
            sys.stdout.flush()

        self._cleanup()

    def connect_relay(self, directory: str = ""):
        """Relay lifecycle is owned by the standalone relay client."""
        self.renderer.print_error(
            "PawCode no longer starts relays. Use PawFlow webchat resource "
            "management for server relays, or run `pawflow-relay` for client relays."
        )

    def _cleanup(self):
        if self.sse:
            self.sse.disconnect()


def main():
    """Entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="PawCode — Terminal chat frontend")
    parser.add_argument("command", nargs="?", default=None,
                        help="Subcommand: 'auth login' or 'auth status'")
    parser.add_argument("subcommand", nargs="?", default=None,
                        help=argparse.SUPPRESS)
    default_server = os.environ.get("PAWFLOW_SERVER", "http://localhost:9090")
    parser.add_argument("--server", default=default_server,
                        help=f"PawFlow server URL (env: PAWFLOW_SERVER, default: {default_server})")
    parser.add_argument("--dir", default=".",
                        help="Client working directory hint (relay lifecycle is external)")
    parser.add_argument("--no-relay", action="store_true",
                        help="Deprecated no-op: PawCode is always chat-only")
    parser.add_argument("--docker-image", default="",
                        help="Deprecated for PawCode; configure relay images via pawflow-relay")
    parser.add_argument("--docker-cpus", default="",
                        help="Deprecated for PawCode; configure relay limits via pawflow-relay")
    parser.add_argument("--docker-memory", default="",
                        help="Deprecated for PawCode; configure relay limits via pawflow-relay")
    parser.add_argument("--allow-local", action="store_true", default=False,
                        help="Deprecated for PawCode; configure local execution via pawflow-relay")
    parser.add_argument("--login", action="store_true",
                        help="Force re-authentication")
    parser.add_argument("-p", "--prompt", nargs="?", const="-", default=None,
                        help="Non-interactive mode: send a prompt and exit. "
                             "Use -p \"prompt\" or pipe via stdin with -p -")
    parser.add_argument("-c", "--conversation", default=None,
                        help="Conversation ID to use with -p (default: last or new)")
    parser.add_argument("--output", choices=["text", "json", "markdown", "full"],
                        default="text",
                        help="Output format for -p mode (default: text)")
    parser.add_argument("--gateway-key", default=os.environ.get("PAWFLOW_GATEWAY_KEY", ""),
                        help="Private gateway access key (env: PAWFLOW_GATEWAY_KEY)")
    parser.add_argument("--input-format", choices=["text", "stream-json"], default="text",
                        help="Input format (stream-json for Claude Code compatible NDJSON)")
    parser.add_argument("--output-format", choices=["text", "stream-json"], default="text",
                        help="Output format (stream-json for Claude Code compatible NDJSON)")
    parser.add_argument("--session-id", default="",
                        help="Resume session by ID")
    parser.add_argument("--resume", default="",
                        help="Resume session (alias for --session-id)")
    args = parser.parse_args()

    # Acquire gateway cookie if key provided. If it fails, just warn
    # and continue — the user can still authenticate with /login at the
    # PawCode prompt (session auth works independently of the gateway).
    gateway_cookie = ""
    if args.gateway_key:
        from pawflow_cli.api import acquire_gateway_cookie
        try:
            gateway_cookie = acquire_gateway_cookie(args.server, args.gateway_key)
        except Exception as _ge:
            print(f"[PawCode] Gateway request failed: {_ge} — continuing "
                  f"without it; use /login at the prompt.",
                  file=sys.stderr)
        if gateway_cookie:
            print("[PawCode] Gateway cookie acquired.", file=sys.stderr)
        else:
            print("[PawCode] Gateway returned no cookie — continuing "
                  "without it; use /login at the prompt.",
                  file=sys.stderr)

    # ── Subcommands: pawcode auth login | pawcode auth status ──
    if args.command == "auth":
        _subcmd = args.subcommand or "status"
        if _subcmd == "login":
            from pawflow_cli.auth import authenticate
            try:
                auth = authenticate(args.server, force=True,
                                    gateway_cookie=gateway_cookie)
                print(f"Authenticated as {auth['username']}")
                print(f"Token saved to ~/.pawflow/session.json (encrypted)")
            except Exception as e:
                print(f"Login failed: {e}", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
        elif _subcmd == "status":
            from pawflow_cli.auth import check_session
            auth = check_session(args.server, gateway_cookie=gateway_cookie)
            if auth:
                print(f"Authenticated as {auth['username']}")
                print(f"Server: {auth['server_url']}")
                from pawflow_cli.config import load_session
                raw = load_session(include_expired=True)
                import datetime
                exp = raw.get("expires_at", 0)
                if exp:
                    dt = datetime.datetime.fromtimestamp(exp)
                    remaining = exp - time.time()
                    if remaining > 0:
                        h, m = divmod(int(remaining), 3600)
                        print(f"Expires: {dt.strftime('%Y-%m-%d %H:%M')} ({h}h{m//60}m remaining)")
                    else:
                        print(f"Expired: {dt.strftime('%Y-%m-%d %H:%M')} (server may silently refresh)")
            else:
                print("Not authenticated.")
                print(f"Run: pawcode auth login --server {args.server}"
                      + (f" --gateway-key <key>" if not gateway_cookie else ""))
                sys.exit(1)
            sys.exit(0)
        else:
            print(f"Unknown auth subcommand: {_subcmd}", file=sys.stderr)
            print("Usage: pawcode auth [login|status]", file=sys.stderr)
            sys.exit(1)

    # Stream-json mode: Claude Code compatible NDJSON protocol
    if args.input_format == "stream-json" and args.output_format == "stream-json":
        from pawflow_cli.stream_json import StreamJsonMode
        mode = StreamJsonMode(
            server_url=args.server,
            directory=args.dir,
            gateway_cookie=gateway_cookie,
            docker_image=args.docker_image,
        )
        if args.session_id or args.resume:
            mode.conversation_id = args.session_id or args.resume
        sys.exit(mode.run())

    cli = PawCode(
        server_url=args.server,
        directory=args.dir,
        docker_image=args.docker_image,
        gateway_cookie=gateway_cookie,
        gateway_key=args.gateway_key,
        docker_cpus=args.docker_cpus,
        docker_memory=args.docker_memory,
        allow_local=args.allow_local,
    )

    if args.login:
        from pawflow_cli.config import clear_session
        clear_session()

    # Non-interactive prompt mode: -p "prompt" or echo "q" | pawcode -p -
    if args.prompt is not None:
        prompt_text = args.prompt
        if prompt_text == "-":
            prompt_text = sys.stdin.read().strip()
        if not prompt_text:
            print("Error: empty prompt", file=sys.stderr)
            sys.exit(1)
        try:
            cli.run_prompt(prompt_text,
                           conversation_id=args.conversation,
                           output_format=args.output)
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

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
