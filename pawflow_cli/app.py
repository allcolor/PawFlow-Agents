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
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


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
        return HTML('<style bg="#0f1629" fg="#555"> PawCode </style>')

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

            session = PromptSession(
                history=FileHistory(str(HISTORY_FILE)),
                multiline=False,
                enable_history_search=True,
                bottom_toolbar=self._get_toolbar,
                refresh_interval=0.5,  # refresh toolbar every 0.5s
            )

            # patch_stdout(raw=True) preserves ANSI codes from Rich
            with patch_stdout(raw=True):
                # Re-create Rich Console to write through the patched stdout
                self.renderer.init_patched_console()
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
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._send_message(text)

    def _send_message(self, text: str):
        """Send a message to the agent (non-blocking — events rendered by background thread)."""
        # Erase the raw prompt line, replace with styled Panel
        sys.stdout.write("\033[A\033[2K")
        sys.stdout.flush()
        self.renderer.print_user_message(text)
        try:
            resp = self.api.send_message(
                message=text,
                conversation_id=self.conversation_id,
                target_agent=self.selected_agent,
            )
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
            if agent != streaming_agent:
                if streaming_agent:
                    self.renderer.end_stream(streaming_agent)
                self._ev_streaming_agent = agent
                source = data.get("source", {})
                svc = source.get("llm_service", "") if isinstance(source, dict) else ""
                self.renderer.start_stream(agent, svc)
            self.renderer.stream_token(agent, data.get("text", ""))

        elif ev_type == "tool_call":
            if streaming_agent:
                self.renderer.end_stream(streaming_agent)
                self._ev_streaming_agent = ""
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
            if streaming_agent:
                self.renderer.end_stream(streaming_agent, data.get("response", ""))
                self._ev_streaming_agent = ""
            elif data.get("response"):
                agent = data.get("agent_name", "assistant")
                self.renderer.print_agent_badge(agent)
                self.renderer.print_markdown(data["response"])
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

        return True  # keep waiting

    def _handle_exec_approval(self, data: dict):
        """Handle exec approval request."""
        self.renderer.print_exec_approval(
            data.get("command", "?"),
            data.get("risk_level", "normal"),
            data.get("request_id", ""),
        )
        choice = input().strip().lower()
        result_map = {"y": "approved", "n": "denied", "s": "session_allow", "a": "always_allow"}
        result = result_map.get(choice, "denied")
        try:
            self.api.send_action("exec_result",
                                 request_id=data.get("request_id", ""),
                                 result=result,
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_tool_approval(self, data: dict):
        """Handle tool approval request."""
        self.renderer.print_approval_request(
            data.get("tool_name", "?"),
            data.get("action_summary", ""),
            data.get("request_id", ""),
        )
        choice = input().strip().lower()
        result_map = {"y": "allow_once", "n": "denied", "s": "session_allow", "a": "always_allow"}
        result = result_map.get(choice, "denied")
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
                "## Commands\n"
                "- `/new` — New conversation\n"
                "- `/conv` — List conversations\n"
                "- `/resume <id> [N]` — Resume conversation (show last N messages, default 10)\n"
                "- `/history [N]` — Show last N messages (default 20)\n"
                "- `/delete <id>` — Delete conversation\n"
                "- `/export [json|md]` — Export conversation\n"
                "- `/agent list|<name>` — List or select agent\n"
                "- `/compact` — Compact context\n"
                "- `/model <name>` — Switch model\n"
                "- `/resources` — List resources\n"
                "- `/tools` — List tools\n"
                "- `/cost` — Show token usage/cost\n"
                "- `/explore` — File explorer\n"
                "- `/clear` — Clear screen\n"
                "- `/login` — Re-authenticate\n"
                "- `/quit` — Exit\n"
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

    cli.start()
