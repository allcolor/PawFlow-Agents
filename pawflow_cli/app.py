"""PawCode — Terminal frontend for PawFlow."""
import logging

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
    "/memory", "/skill", "/pfp", "/task", "/service", "/flow",
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


from pawflow_cli._app_messaging import _PawCodeMessagingMixin  # noqa: E402
from pawflow_cli._app_events import _PawCodeEventsMixin  # noqa: E402
from pawflow_cli._app_commands import _PawCodeCommandsMixin  # noqa: E402


class PawCode(_PawCodeMessagingMixin, _PawCodeEventsMixin, _PawCodeCommandsMixin):
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
        self.session_token = ""  # nosec B105
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
        # Message ids already rendered (our own sends + finished turns), so
        # new_message SSE echoes from the shared conversation don't duplicate.
        self._seen_msg_ids = set()

    def _new_outgoing_msg_id(self) -> str:
        """Mint a client msg_id and pre-register it so the server's
        new_message SSE echo of our own message does not render twice."""
        from uuid import uuid4
        if getattr(self, "_seen_msg_ids", None) is None:
            self._seen_msg_ids = set()
        mid = uuid4().hex[:12]
        self._seen_msg_ids.add(mid)
        if len(self._seen_msg_ids) > 2000:
            # Bound memory in long sessions — drop the oldest arbitrary half.
            self._seen_msg_ids = set(list(self._seen_msg_ids)[-1000:])
        return mid

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
            self.session_token = ""  # nosec B105
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
                    self.selected_agent = data.get("active_agent", "") or self.selected_agent
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
                # \x1b[13;2u = Shift+Enter, \x1b[13;5u = Ctrl+Enter
                bindings.add(Keys.Vt100MouseEvent)  # dummy to test availability
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                        import io
                        import base64
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
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _resolve_conversation_id(self, partial: str) -> str:
        """Resolve a partial conversation ID to full ID."""
        try:
            data = self.api.send_action("list_conversations")
            for c in data.get("conversations", []):
                cid = c.get("conversation_id", "")
                if cid.startswith(partial):
                    return cid
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        os._exit(0)
    def connect_relay(self, directory: str = ""):
        """Relay lifecycle is owned by the standalone relay client."""
        self.renderer.print_error(
            "PawCode no longer starts relays. Use PawFlow webchat resource "
            "management for server relays, or run `pawflow-relay` for client relays."
        )

    def _cleanup(self):
        if self.sse:
            self.sse.disconnect()

def _normalize_server_url(entered: str) -> str:
    """Add a scheme when the user types a bare host: http for loopback,
    https for everything else (production servers sit behind TLS)."""
    entered = entered.strip().rstrip("/")
    if not entered or "://" in entered:
        return entered
    host = entered.split("/", 1)[0].split(":", 1)[0].lower()
    scheme = "http" if host in ("localhost", "127.0.0.1", "::1") else "https"
    return f"{scheme}://{entered}"


def _prompt_first_run_setup(args, input_fn=input, getpass_fn=None):
    """Interactive first-run setup: ask for the server URL and, if the
    server sits behind a Private Gateway, the gateway key.

    Mutates ``args.server`` / ``args.gateway_key`` in place. Any abort
    (Ctrl+C / EOF) keeps the current defaults — setup must never block
    the prompt from starting.
    """
    if getpass_fn is None:
        import getpass
        getpass_fn = getpass.getpass
    try:
        entered = _normalize_server_url(
            input_fn(f"PawFlow server URL [{args.server}]: "))
        if entered:
            args.server = entered
        if not args.gateway_key:
            yn = input_fn(
                "Is the server behind a Private Gateway? [y/N]: ").strip().lower()
            if yn in ("y", "yes", "o", "oui"):
                key = getpass_fn("Private Gateway key (input hidden): ").strip()
                if key:
                    args.gateway_key = key
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n[PawCode] Setup skipped — using defaults.\n")


def main():
    """Entry point for the CLI."""
    import argparse
    from core import __version__ as _pf_version

    parser = argparse.ArgumentParser(description="PawCode — Terminal chat frontend")
    parser.add_argument("--version", action="version", version=f"pawcode {_pf_version}")
    parser.add_argument("command", nargs="?", default=None,
                        help="Subcommand: 'auth login' or 'auth status'")
    parser.add_argument("subcommand", nargs="?", default=None,
                        help=argparse.SUPPRESS)
    # --reset-config: wipe saved server/gateway settings BEFORE reading them,
    # so the defaults below fall back to env/localhost and first-run setup
    # asks the questions again. Checked from argv directly because the saved
    # config is consumed while building the argument defaults.
    if any(a == "--reset-config" for a in sys.argv[1:]):
        from pawflow_cli.config import CONFIG_FILE
        try:
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
            print("[PawCode] Saved settings cleared (~/.pawflow/config.json).",
                  file=sys.stderr)
        except Exception as _re:
            print(f"[PawCode] Could not clear config: {_re}", file=sys.stderr)

    # Defaults: CLI arg > env > saved config (~/.pawflow/config.json) >
    # localhost. The config values are persisted on every successful start
    # so a bare `pawcode` keeps working after the first configured run.
    _saved_cfg = load_config()
    default_server = (os.environ.get("PAWFLOW_SERVER")
                      or _saved_cfg.get("server_url")
                      or "http://localhost:9090")
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
    parser.add_argument("--reset-config", action="store_true",
                        help="Erase saved server/gateway settings (~/.pawflow/config.json) "
                             "and re-run first-time setup")
    parser.add_argument("--no-browser", action="store_true",
                        help="Headless login: print the URL and paste the redirected "
                             "URL/token back (for SSH / remote / no-browser machines)")
    parser.add_argument("-p", "--prompt", nargs="?", const="-", default=None,
                        help="Prompt mode: send a prompt and exit. "
                             "Use -p \"prompt\" or pipe via stdin with -p -")
    parser.add_argument("-c", "--conversation", default=None,
                        help="Conversation ID to use with -p (default: last or new)")
    parser.add_argument("--output", choices=["text", "json", "markdown", "full"],
                        default="text",
                        help="Output format for -p mode (default: text)")
    parser.add_argument("--gateway-key",
                        default=(os.environ.get("PAWFLOW_GATEWAY_KEY")
                                 or _saved_cfg.get("gateway_key", "")),
                        help="Private gateway access key (env: PAWFLOW_GATEWAY_KEY, "
                             "persisted in ~/.pawflow/config.json after first use)")
    parser.add_argument("--input-format", choices=["text", "stream-json"], default="text",
                        help="Input format (stream-json for Claude Code compatible NDJSON)")
    parser.add_argument("--output-format", choices=["text", "stream-json"], default="text",
                        help="Output format (stream-json for Claude Code compatible NDJSON)")
    parser.add_argument("--session-id", default="",
                        help="Resume session by ID")
    parser.add_argument("--resume", default="",
                        help="Resume session (alias for --session-id)")
    args = parser.parse_args()

    # First-run interactive setup: a bare `pawcode` with no server from
    # CLI/env/config asks for the server URL (and gateway key when the
    # server is behind a Private Gateway) instead of silently targeting
    # localhost and appearing dead. Skipped in non-interactive contexts
    # (-p prompt mode, stream-json, piped stdin) so scripts never block.
    _server_from_cli = any(
        a == "--server" or a.startswith("--server=") for a in sys.argv[1:])
    _server_configured = bool(
        _server_from_cli
        or os.environ.get("PAWFLOW_SERVER")
        or _saved_cfg.get("server_url"))
    _interactive = (sys.stdin.isatty()
                    and args.prompt is None
                    and args.input_format != "stream-json"
                    and args.command is None)
    if not _server_configured and _interactive:
        _prompt_first_run_setup(args)

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

    # Persist working connection settings so the next bare `pawcode`
    # talks to the same server with the same gateway key.
    _cfg_update = {}
    if args.server and args.server != _saved_cfg.get("server_url"):
        _cfg_update["server_url"] = args.server
    if gateway_cookie and args.gateway_key != _saved_cfg.get("gateway_key"):
        _cfg_update["gateway_key"] = args.gateway_key
    if _cfg_update:
        try:
            save_config(_cfg_update)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    # ── Subcommands: pawcode auth login | pawcode auth status ──
    if args.command == "auth":
        _subcmd = args.subcommand or "status"
        if _subcmd == "login":
            from pawflow_cli.auth import authenticate
            try:
                auth = authenticate(args.server, force=True,
                                    gateway_cookie=gateway_cookie,
                                    no_browser=args.no_browser)
                print(f"Authenticated as {auth['username']}")
                print("Token saved to ~/.pawflow/session.json (encrypted)")
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
                      + (" --gateway-key <key>" if not gateway_cookie else ""))
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

    # Prompt mode: -p "prompt" or echo "q" | pawcode -p -
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
        print("  Make sure the server is running and the URL is correct.")
        print("  Set PAWFLOW_SERVER env var or use --server to change the URL.\n")
        sys.exit(1)
    except TimeoutError as e:
        print(f"\n  Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        if "Connection refused" in str(e) or "connect" in str(e).lower():
            print(f"\n  Error: Cannot connect to PawFlow server at {args.server}")
            print("  Make sure the server is running.\n")
            sys.exit(1)
        raise
