"""PawCode slash-command dispatch / offline help / history."""

import os
import shlex
import subprocess  # nosec B404
import sys

# Split out of pawflow_cli/app.py for the <=800-line rule; composed back into
# PawCode (invariant 2: MRO/shared state).


class _PawCodeCommandsMixin:
    """slash-command dispatch / offline help / history."""


    # Client-side help: categories mirror the server's /help so the terminal
    # always has a usable command reference, even offline or before login.
    _HELP_CATEGORIES = {
        "Conversation": ["/new", "/conv", "/delete", "/rename", "/export",
                         "/history", "/search", "/fork"],
        "Agent": ["/agent", "/msg", "/btw", "/stop", "/resume", "/setname"],
        "Context": ["/compact", "/git-prune", "/context", "/model", "/llm",
                    "/effort", "/fast", "/rebuild", "/restart", "/rewind",
                    "/summary", "/cc_restart", "/cc_live"],
        "Resources": ["/resources", "/tools", "/call", "/skill", "/task",
                      "/service", "/flow", "/prompt", "/memory", "/cost"],
        "Secrets & Variables": ["/secrets", "/add-secret", "/variables",
                                "/add-variable"],
        "Scheduling": ["/schedules", "/autoconv", "/loop"],
        "Files": ["/files", "/upload", "/paste", "/copy", "/view", "/run",
                  "/diff", "/relay", "/workspace"],
        "Mode": ["/plan", "/hooks", "/permission"],
        "Session": ["/login", "/help", "/doctor", "/clear", "/quit"],
        "Analysis": ["/stats", "/insights", "/security-review", "/feedback"],
    }

    def _print_offline_help(self, topic: str = ""):
        """Render the command reference client-side (no server round-trip)."""
        if topic:
            cmd = topic if topic.startswith("/") else f"/{topic}"
            self.renderer.print_system(
                f"For detailed help on {cmd}, run it with no argument or see "
                "the docs. (Full per-command help requires a logged-in session.)")
            return
        lines = ["## Available Commands"]
        for cat, cmds in self._HELP_CATEGORIES.items():
            lines.append(f"\n**{cat}**: " + "  ".join(cmds))
        lines.append("\nType a message to talk to the selected agent. "
                     "/login to authenticate (/login paste for headless/SSH), "
                     "/quit to exit.")
        self.renderer.print_markdown("\n".join(lines))

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

        # /help is rendered client-side: it must work offline (before login
        # or when the server is unreachable) and never round-trips, so it
        # can't hang waiting on the server.
        if cmd == "/help":
            self._print_offline_help(arg.strip())
            return

        # Client-only commands (UI-specific, never sent to server)
        if cmd == "/clear":
            subprocess.run(["cmd", "/c", "cls"] if os.name == "nt" else ["clear"], check=False)  # nosec B603
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

        if self._handle_agent_stream_command(cmd, arg, text):
            return

        # Everything past this point needs an authenticated session. Without
        # one the POST would 401/block — tell the user to /login instead.
        if not self.session_token:
            self.renderer.print_error(
                f"Not logged in — {cmd} needs a server session. Run /login first.")
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

    @staticmethod
    def _parse_command_args(text: str) -> list:
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()

    @staticmethod
    def _strip_agent_target(value: str) -> str:
        return (value or "").strip().lstrip("@")

    def _handle_agent_stream_command(self, cmd: str, arg: str, text: str) -> bool:
        """Handle commands whose useful result is the agent stream itself."""
        if cmd == "/resume":
            target = self._strip_agent_target(arg.split()[0]) if arg else self.selected_agent
            if target.upper() == "ALL":
                self.api.send_action_fire("broadcast_agents",
                                          conversation_id=self.conversation_id or "",
                                          message="Continue from where you stopped")
                self.renderer.print_system("Resume requested for all agents.")
                return True
            self._send_targeted_message("Continue from where you stopped", target)
            return True

        if cmd in ("/msg", "/message"):
            parts = self._parse_command_args(text)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /msg [@agent|@ALL] <message>")
                return True
            if parts[1].startswith("@"):
                target = self._strip_agent_target(parts[1])
                message = " ".join(parts[2:])
            else:
                target = self.selected_agent
                message = " ".join(parts[1:])
            if not target or not message:
                self.renderer.print_error("Usage: /msg [@agent|@ALL] <message>")
                return True
            if target.upper() == "ALL":
                self.api.send_action_fire("broadcast_agents",
                                          conversation_id=self.conversation_id or "",
                                          message=message)
                self.renderer.print_system("Broadcast requested.")
                return True
            self._send_targeted_message(message, target)
            return True

        if cmd == "/btw":
            parts = self._parse_command_args(text)
            if len(parts) < 2:
                self.renderer.print_error("Usage: /btw [@agent|@ALL] <question>")
                return True
            if parts[1].startswith("@"):
                target = self._strip_agent_target(parts[1])
                question = " ".join(parts[2:])
            else:
                target = self.selected_agent
                question = " ".join(parts[1:])
            if not question:
                self.renderer.print_error("Usage: /btw [@agent|@ALL] <question>")
                return True
            self.api.send_action_fire("btw", conversation_id=self.conversation_id or "",
                                      agent_name=target or "", message=question)
            self.renderer.print_system("Side question requested.")
            return True

        if cmd in ("/stop", "/interrupt"):
            target = self._strip_agent_target(arg.replace("-f", "").strip())
            if target.upper() == "ALL":
                target = ""
            action = "interrupt" if cmd == "/interrupt" else "cancel"
            self.api.send_action_fire(action, conversation_id=self.conversation_id or "",
                                      agent_name=target or self.selected_agent or "")
            self.renderer.print_system("Interrupt requested." if action == "interrupt" else "Stop requested.")
            return True

        if cmd == "/agent":
            parts = self._parse_command_args(text)
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub in ("resume", "msg", "message", "btw", "interrupt"):
                target = self._strip_agent_target(parts[2]) if len(parts) > 2 else ""
                rest = " ".join(parts[3:])
                if sub == "resume":
                    resume_text = rest or "Continue from where you stopped"
                    if target.upper() == "ALL":
                        self.api.send_action_fire("broadcast_agents",
                                                  conversation_id=self.conversation_id or "",
                                                  message=resume_text)
                        self.renderer.print_system("Resume requested for all agents.")
                    else:
                        self._send_targeted_message(resume_text, target or self.selected_agent)
                    return True
                if sub in ("msg", "message"):
                    if not target or not rest:
                        self.renderer.print_error("Usage: /agent msg <agent|ALL> <message>")
                        return True
                    if target.upper() == "ALL":
                        self.api.send_action_fire("broadcast_agents",
                                                  conversation_id=self.conversation_id or "",
                                                  message=rest)
                        self.renderer.print_system("Broadcast requested.")
                    else:
                        self._send_targeted_message(rest, target)
                    return True
                if sub == "btw":
                    if not rest:
                        self.renderer.print_error("Usage: /agent btw <agent|ALL> <question>")
                        return True
                    self.api.send_action_fire("btw", conversation_id=self.conversation_id or "",
                                              agent_name=target or "", message=rest)
                    self.renderer.print_system("Side question requested.")
                    return True
                if sub == "interrupt":
                    self.api.send_action_fire("interrupt", conversation_id=self.conversation_id or "",
                                              agent_name="" if target.upper() == "ALL" else target)
                    self.renderer.print_system("Interrupt requested.")
                    return True

        return False

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
