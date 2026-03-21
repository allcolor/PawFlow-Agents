"""Terminal rendering for PawCode using Rich."""

import re
import sys
from typing import Dict, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.theme import Theme
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# Agent color palette (hash-based like web UI)
_AGENT_COLORS = [
    "cyan", "green", "yellow", "magenta", "blue",
    "bright_cyan", "bright_green", "bright_yellow", "bright_magenta",
]

_FUN_VERBS = [
    "Refactoring", "Compiling", "Debugging", "Deploying", "Optimizing",
    "Transpiling", "Dockerizing", "Rebasing", "Sautéing", "Flambéing",
    "Caramelizing", "Fermenting", "Contemplating", "Ruminating",
    "Philosophizing", "Cogitating", "Bamboozling", "Discombobulating",
    "Recombobulating", "Confuzzling", "Lollygagging", "Skedaddling",
    "Razzle-dazzling", "Hocus-pocusing", "Abracadabra-ing",
    "Supercalifragilisting", "Rickrolling", "Jedi-mind-tricking",
    "Pokémon-catching", "Hadouken-ing", "Falcon-punching",
    "Portal-thinking", "Speedrunning", "Kerfuffling",
    "Gobsmacking", "Wibble-wobbling", "Shenanigan-foiling",
    "Defenestrating", "Brain-in-a-vat-ing", "Trolley-problem-solving",
]


def _random_verb() -> str:
    import random
    return random.choice(_FUN_VERBS)


def _agent_color(name: str) -> str:
    h = sum(ord(c) for c in name)
    return _AGENT_COLORS[h % len(_AGENT_COLORS)]


class TerminalRenderer:
    """Renders PawFlow chat events to the terminal."""

    def __init__(self):
        # Force UTF-8 on Windows
        if sys.platform == "win32":
            import os
            os.system("")  # Enable VT100 escape sequences
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if HAS_RICH:
            self.console = Console(force_terminal=True)
        else:
            self.console = None
        self._streams: Dict[str, str] = {}
        self._live: Optional[Live] = None
        self._thinking: Dict[str, str] = {}
        self._status_callback = None  # set by app.py to update toolbar

    def init_patched_console(self):
        """Re-create Rich Console to write through patch_stdout's proxy."""
        if HAS_RICH:
            self.console = Console(force_terminal=True)

    def set_status_callback(self, callback):
        """Set callback to update bottom toolbar status text."""
        self._status_callback = callback

    def _set_status(self, text: str):
        """Update the bottom toolbar status."""
        if self._status_callback:
            self._status_callback(text)

    def print_user_message(self, text: str):
        """Echo the user's message as a visible Panel."""
        if self.console:
            from rich.markup import escape
            display = text if len(text) <= 500 else text[:500] + "..."
            self.console.print()
            self.console.print(Panel(
                f"[bold white]{escape(display)}[/bold white]",
                title="[bold green]❯ You[/bold green]",
                title_align="left",
                border_style="bright_green",
                padding=(0, 2),
                style="on #0a2a0a",
            ))
        else:
            print(f"\n❯ {text}")

    def print_banner(self, directory: str):
        if self.console:
            self.console.print(Panel(
                f"[bold]Directory:[/bold] {directory}",
                title="[bold cyan]PawCode[/bold cyan]",
                border_style="cyan",
                padding=(0, 2),
            ))
        else:
            print(f"\n  PawCode\n  ───────\n  Directory: {directory}\n")

    def print(self, text: str, style: str = ""):
        if self.console:
            self.console.print(text, style=style)
        else:
            print(text)

    def print_markdown(self, text: str):
        if self.console:
            self.console.print(Markdown(text))
        else:
            print(text)

    def print_system(self, text: str):
        if self.console:
            from rich.markup import escape
            self.console.print(f"[dim]{escape(text)}[/dim]")
        else:
            print(f"[system] {text}")

    def print_error(self, text: str):
        if self.console:
            self.console.print(f"[bold red]{text}[/bold red]")
        else:
            print(f"[ERROR] {text}")

    def print_agent_badge(self, agent: str, service: str = ""):
        color = _agent_color(agent)
        svc = f" via {service}" if service else ""
        if self.console:
            self.console.print(f"[bold {color}][{agent}{svc}][/bold {color}]", end=" ")
        else:
            print(f"[{agent}{svc}]", end=" ")

    # ── Streaming ──

    def start_stream(self, agent: str, service: str = ""):
        self._streams[agent] = ""
        self._stream_agent = agent
        self._stream_service = service
        # Update status bar to show agent is writing
        self._set_status(f"▶ {agent}  writing...")

    def stream_token(self, agent: str, text: str):
        self._streams[agent] = self._streams.get(agent, "") + text
        # Don't print raw tokens — just accumulate and update status bar
        # Show all active agents in status
        active = []
        for a, s in self._streams.items():
            wc = len(s.split())
            active.append(f"{a} ({wc}w)")
        self._set_status(f"▶ writing: {', '.join(active)}")

    def end_stream(self, agent: str, final_text: str = ""):
        streamed = self._streams.pop(agent, "")
        text = final_text or streamed
        # Only clear status if no more active streams
        if not self._streams:
            self._set_status("")
        # Render the complete response as a Rich Markdown Panel
        if text and self.console:
            color = _agent_color(agent)
            svc_info = f" via {self._stream_service}" if self._stream_service else ""
            try:
                body = Markdown(text)
            except Exception:
                body = Text(text)
            self.console.print()  # spacing
            self.console.print(Panel(
                body,
                title=f"[bold {color}]{agent}{svc_info}[/bold {color}]",
                title_align="left",
                border_style=color,
                padding=(0, 2),
            ))
        elif text:
            print(text)
        self._stream_agent = ""
        self._stream_service = ""

    # ── Thinking ──

    def _status_line(self, agent: str, text: str):
        """Print a styled status line for an agent (thinking, iterating, etc.)."""
        color = _agent_color(agent)
        if self.console:
            from rich.markup import escape
            self.console.print(f"  [{color}]▶ {escape(agent)}[/{color}] [dim italic]{escape(text)}[/dim italic]")
        else:
            print(f"  ▶ {agent} {text}")

    def start_thinking(self, agent: str):
        self._thinking[agent] = ""
        # Only update bottom status bar — no print in chat
        self._set_status(f"▶ {agent}  ✶ {_random_verb()}...")

    def thinking_token(self, agent: str, text: str):
        self._thinking[agent] = self._thinking.get(agent, "") + text
        # Cycle verb every ~100 chars for "animation" effect
        if len(self._thinking[agent]) % 100 < len(text):
            self._set_status(f"▶ {agent}  ✶ {_random_verb()}...")

    def end_thinking(self, agent: str):
        self._thinking.pop(agent, "")
        self._set_status("")

    # ── Tool calls ──

    def _strip_tool_wrapper(self, text: str) -> str:
        """Strip [TOOL OUTPUT...] wrapper from display text."""
        if text.startswith("[TOOL OUTPUT"):
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1:]
            if text.endswith("[/TOOL OUTPUT]"):
                text = text[:-len("[/TOOL OUTPUT]")].rstrip("\n")
        return text

    def print_tool_call(self, tool: str, arguments: dict, agent: str = "",
                        service: str = ""):
        # Format arguments compactly — Claude Code style
        args_parts = []
        for k, v in arguments.items():
            vs = repr(v) if not isinstance(v, str) else v
            if len(vs) > 60:
                vs = vs[:60] + "..."
            args_parts.append(f"{k}={vs}")
        args_str = ", ".join(args_parts)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."

        self._set_status(f"▶ {agent or ''}  {tool}...")
        if self.console:
            from rich.markup import escape
            color = _agent_color(agent) if agent else "yellow"
            self.console.print(
                f"  [{color}]● {escape(agent or '')}[/{color}] "
                f"[yellow]{escape(tool)}[/yellow]"
                f"[dim]({escape(args_str)})[/dim]"
            )
        else:
            print(f"  ● {agent or ''} {tool}({args_str})")

    def print_tool_result(self, tool: str, result: str, agent: str = ""):
        result = self._strip_tool_wrapper(result)
        # Detect diff output — both unified format and PawFlow custom format
        lines = result.split("\n")
        has_diff_lines = any(
            line.lstrip().startswith("+ ") or line.lstrip().startswith("- ")
            or line.startswith("@@")
            for line in lines
        )
        is_diff = has_diff_lines and ("replacement" in result.lower() or "edited " in result.lower()
                  or "diff " in result or "---" in result)

        # Truncate non-diff results
        if not is_diff and len(result) > 500:
            result = result[:500] + f"\n... ({len(result)} chars total)"

        if self.console:
            from rich.markup import escape
            if is_diff:
                # Render diff with colors
                rendered_lines = []
                for line in result.split("\n"):
                    stripped = line.lstrip()
                    # Check for line-number prefixed diffs: "  42 + new_code" or "  42 - old_code"
                    if stripped.startswith("+ ") or (len(stripped) > 4 and stripped[0].isdigit() and " + " in stripped):
                        rendered_lines.append(f"  [green]{escape(line)}[/green]")
                    elif stripped.startswith("- ") or (len(stripped) > 4 and stripped[0].isdigit() and " - " in stripped):
                        rendered_lines.append(f"  [red]{escape(line)}[/red]")
                    elif stripped.startswith("@@"):
                        rendered_lines.append(f"  [cyan]{escape(line)}[/cyan]")
                    elif "replacement" in line.lower() or "edited " in line.lower():
                        rendered_lines.append(f"  [bold]{escape(line)}[/bold]")
                    else:
                        rendered_lines.append(f"  [dim]{escape(line)}[/dim]")
                self.console.print("\n".join(rendered_lines))
            else:
                # Compact result — green checkmark, first line only
                first_line = result.split("\n")[0][:200]
                if len(result) > len(first_line):
                    self.console.print(f"  [green]✓[/green] [dim]{escape(first_line)}...[/dim]")
                else:
                    self.console.print(f"  [green]✓[/green] [dim]{escape(first_line)}[/dim]")
        else:
            print(f"  ✓ {result[:200]}")

    # ── Approval ──

    def print_approval_request(self, tool: str, summary: str, request_id: str):
        if self.console:
            self.console.print(Panel(
                f"[yellow]{tool}[/yellow]: {summary}",
                title="[bold]Tool Approval Required[/bold]",
                border_style="yellow",
            ))
            self.console.print(
                "[y]Allow once  [s]Session  [a]Always  [n]Deny: ",
                end="",
            )
        else:
            print(f"\n[APPROVAL] {tool}: {summary}")
            print("[y]Allow once  [s]Session  [a]Always  [n]Deny: ", end="")

    def print_exec_approval(self, command: str, risk: str, request_id: str):
        if self.console:
            color = "red" if risk == "high" else "yellow"
            self.console.print(Panel(
                Syntax(command, "bash", theme="monokai"),
                title=f"[bold {color}]Execute Command ({risk} risk)[/bold {color}]",
                border_style=color,
            ))
            self.console.print(
                "[y]Run  [n]Deny  [e]Edit  [s]Session allow: ",
                end="",
            )
        else:
            print(f"\n[EXEC {risk}] {command}")
            print("[y]Run  [n]Deny  [e]Edit  [s]Session allow: ", end="")

    # ── Status ──

    def print_done(self, agent: str, tokens_in: int, tokens_out: int,
                   duration_ms: int, model: str = ""):
        self._set_status("")  # clear status bar
        if self.console:
            info = f"[dim]  {tokens_in:,}↑ {tokens_out:,}↓"
            if duration_ms:
                info += f" · {duration_ms/1000:.1f}s"
            if model:
                info += f" · {model}"
            info += "[/dim]"
            self.console.print(info)
        else:
            print(f"  [{tokens_in}↑ {tokens_out}↓ {duration_ms/1000:.1f}s]")
        # Terminal bell notification
        sys.stdout.write("\a")
        sys.stdout.flush()

    def print_iteration(self, agent: str, iteration: int, round_n: int,
                        max_rounds: int, tools: int):
        # Only status bar — no chat print
        self._set_status(f"▶ {agent}  ✶ {_random_verb()}...  iter {iteration} · round {round_n}/{max_rounds} · {tools} tools")

    def print_ask_user(self, question: str, options: list):
        if self.console:
            self.console.print(Panel(question, title="[bold cyan]Agent Question[/bold cyan]",
                                     border_style="cyan"))
            if options:
                for i, opt in enumerate(options, 1):
                    self.console.print(f"  [{i}] {opt}")
        else:
            print(f"\n[QUESTION] {question}")
            for i, opt in enumerate(options, 1):
                print(f"  [{i}] {opt}")

    # ── Exec output ──

    def print_exec_output(self, command: str, exit_code: int, stdout: str, stderr: str):
        """Render shell command output."""
        if self.console:
            self.console.print(Panel(
                Syntax(command, "bash", theme="monokai"),
                title="exec",
                border_style="green" if exit_code == 0 else "red",
                subtitle=f"exit {exit_code}",
            ))
            if stdout:
                self.console.print(f"[dim]{stdout[:1000]}[/dim]")
            if stderr:
                self.console.print(f"[red]{stderr[:500]}[/red]")
        else:
            print(f"$ {command}")
            if stdout:
                print(stdout[:1000])
            if stderr:
                print(f"STDERR: {stderr[:500]}")
            print(f"(exit {exit_code})")

    # ── History messages ──

    def print_separator(self):
        """Print a visual separator between messages."""
        if self.console:
            self.console.print("[dim]─[/dim]" * 40, style="dim")
        else:
            print("─" * 40)

    def render_history_message(self, msg: dict):
        """Render a classified history message with clear visual separation."""
        mtype = msg.get("type", msg.get("role", ""))
        content = msg.get("content", "")
        source = msg.get("source", {})
        agent = source.get("name", "") if isinstance(source, dict) else ""
        svc = source.get("llm_service", "") if isinstance(source, dict) else ""
        channel = msg.get("channel", "")

        if not content:
            return

        if mtype == "user":
            channel_info = f" ({channel})" if channel and channel != "chat" else ""
            display = content if len(content) <= 500 else content[:500] + "..."
            if self.console:
                from rich.markup import escape
                self.console.print()  # spacing
                self.console.print(Panel(
                    f"[bold white]{escape(display)}[/bold white]",
                    title=f"[bold green]❯ You{channel_info}[/bold green]",
                    title_align="left",
                    border_style="bright_green",
                    padding=(0, 2),
                    style="on #0a2a0a",
                ))
            else:
                print(f"\n── You{channel_info} ──")
                print(display)

        elif mtype in ("assistant", "agent_response"):
            badge = agent or ""
            svc_info = f" via {svc}" if svc else ""
            color = _agent_color(badge)
            display = content if len(content) <= 2000 else content[:2000] + "\n..."
            if self.console:
                try:
                    body = Markdown(display)
                except Exception:
                    body = Text(display)
                self.console.print()  # spacing
                self.console.print(Panel(
                    body,
                    title=f"[bold {color}]{badge}{svc_info}[/bold {color}]",
                    title_align="left",
                    border_style=color,
                    padding=(0, 2),
                ))
            else:
                print(f"\n── {badge}{svc_info} ──")
                print(display)

        elif mtype == "tool_call":
            if self.console:
                from rich.markup import escape
                self.console.print(f"  [yellow]● {escape(content)}[/yellow]")
            else:
                print(f"  ● {content}")

        elif mtype == "tool_result":
            content = self._strip_tool_wrapper(content)
            display = content if len(content) <= 200 else content[:200] + "..."
            if self.console:
                from rich.markup import escape
                self.console.print(f"  [dim]  ↳ {escape(display)}[/dim]")
            else:
                print(f"    ↳ {display[:200]}")

    # ── Conversations ──

    def print_conversation_list(self, conversations: list):
        if not conversations:
            self.print_system("No conversations.")
            return
        if self.console:
            from rich.table import Table
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            table.add_column("ID", style="cyan", width=10)
            table.add_column("Last message", style="", ratio=1)
            table.add_column("Messages", style="dim", justify="right", width=8)
            table.add_column("Age", style="dim", width=12)
            for c in conversations:
                cid = c.get("conversation_id", "?")[:8]
                title = c.get("title", "") or c.get("last_message", "")[:60] or "(empty)"
                count = str(c.get("message_count", ""))
                age = c.get("age", "")
                table.add_row(cid, title, count, age)
            self.console.print(table)
        else:
            for c in conversations:
                cid = c.get("conversation_id", "?")[:8]
                title = c.get("title", "") or c.get("last_message", "")[:60] or "(empty)"
                print(f"  {cid}  {title}")
