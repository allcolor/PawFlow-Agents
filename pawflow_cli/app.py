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
    "/memory", "/skill", "/task", "/service", "/flow",
    "/resources", "/activate", "/deactivate",
    "/tools", "/call", "/model", "/llm",
    "/files", "/upload", "/paste", "/view", "/prompt",
    "/clear-files", "/detach",
    "/add-secret", "/secrets", "/add-variable", "/variables",
    "/schedules", "/cost", "/copy", "/clear", "/login", "/quit", "/exit",
    "/help", "/run", "/diff", "/watch", "/multi", "/plan",
    "/rename", "/delete-msg", "/search",
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
                                agent = source.get("name", "") if isinstance(source, dict) else ""
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
                                 result=result,
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_command(self, text: str):
        """Handle slash commands — thin dispatcher to sub-modules."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # /help stays here (lists all commands)
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
                "- `/context [agent|task:id]` — Show context\n"
                "- `/context delete task:id` — Delete task sub-context\n"
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
                "\n## Flows\n"
                "- `/flow list` — List deployed flows\n"
                "- `/flow templates` — List available flow templates\n"
                "- `/flow deploy <template_id> [scope]` — Deploy (scope: user|conversation)\n"
                "- `/flow start <id> [key=val ...]` — Start (with optional param overrides)\n"
                "- `/flow stop <instance_id>` — Stop flow\n"
                "- `/flow params <instance_id>` — View flow parameters\n"
                "- `/flow promote <instance_id>` — Promote conv flow to user scope\n"
                "- `/flow undeploy <instance_id>` — Remove flow\n"
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
                "\n## Conversation Management\n"
                "- `/rename <title>` — Rename current conversation\n"
                "- `/delete-msg <index>` — Delete message by index\n"
                "- `/search <query>` — Search messages in current conversation\n"
                "\n## Dev Tools\n"
                "- `/plan <description>` — Ask agent for a plan (read-only, no changes)\n"
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

        from pawflow_cli.commands.session import handle_session_commands
        from pawflow_cli.commands.conversation import handle_conversation_commands
        from pawflow_cli.commands.agent import handle_agent_commands
        from pawflow_cli.commands.context import handle_context_commands
        from pawflow_cli.commands.resources import handle_resources_commands
        from pawflow_cli.commands.memory import handle_memory_commands
        from pawflow_cli.commands.secrets import handle_secrets_commands
        from pawflow_cli.commands.files import handle_files_commands

        for handler in [
            handle_session_commands,
            handle_conversation_commands,
            handle_agent_commands,
            handle_context_commands,
            handle_resources_commands,
            handle_memory_commands,
            handle_secrets_commands,
            handle_files_commands,
        ]:
            if handler(self, cmd, arg, text):
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
