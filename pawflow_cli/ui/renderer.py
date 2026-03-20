"""Terminal rendering for PawCode using Rich."""

import re
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


def _agent_color(name: str) -> str:
    h = sum(ord(c) for c in name)
    return _AGENT_COLORS[h % len(_AGENT_COLORS)]


class TerminalRenderer:
    """Renders PawFlow chat events to the terminal."""

    def __init__(self):
        if HAS_RICH:
            self.console = Console()
        else:
            self.console = None
        self._streams: Dict[str, str] = {}  # agent -> accumulated markdown
        self._live: Optional[Live] = None
        self._thinking: Dict[str, str] = {}  # agent -> thinking text

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
            self.console.print(f"[dim]{text}[/dim]")
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

    def start_stream(self, agent: str):
        self._streams[agent] = ""
        if self.console and not self._live:
            self._live = Live("", console=self.console, refresh_per_second=10)
            self._live.start()

    def stream_token(self, agent: str, text: str):
        self._streams[agent] = self._streams.get(agent, "") + text
        if self._live:
            combined = self._streams.get(agent, "")
            try:
                self._live.update(Markdown(combined))
            except Exception:
                self._live.update(Text(combined))

    def end_stream(self, agent: str, final_text: str = ""):
        text = final_text or self._streams.pop(agent, "")
        if self._live:
            # Update with final content, then stop (freezes in place — no re-print)
            if text:
                try:
                    self._live.update(Markdown(text))
                except Exception:
                    self._live.update(Text(text))
            self._live.stop()
            self._live = None
        elif text:
            # No Live was active — print directly
            self.print_markdown(text)

    # ── Thinking ──

    def start_thinking(self, agent: str):
        self._thinking[agent] = ""
        if self.console:
            self.console.print(f"[dim italic]Thinking...[/dim italic]", end="")

    def thinking_token(self, agent: str, text: str):
        self._thinking[agent] = self._thinking.get(agent, "") + text

    def end_thinking(self, agent: str):
        text = self._thinking.pop(agent, "")
        if text and self.console:
            # Show condensed thinking
            lines = text.strip().split("\n")
            preview = lines[0][:100] + ("..." if len(lines) > 1 or len(lines[0]) > 100 else "")
            self.console.print(f"\r[dim italic]Thought: {preview}[/dim italic]")
        elif text:
            print(f"\n[Thought: {text[:200]}...]")

    # ── Tool calls ──

    def print_tool_call(self, tool: str, arguments: dict, agent: str = "",
                        service: str = ""):
        color = _agent_color(agent) if agent else "yellow"
        # Format arguments compactly
        args_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in arguments.items())
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        badge = f"[{agent} via {service}] " if agent and service else ""
        if self.console:
            self.console.print(
                f"[{color}]  {badge}{tool}[/{color}]([dim]{args_str}[/dim])"
            )
        else:
            print(f"  {badge}{tool}({args_str})")

    def print_tool_result(self, tool: str, result: str, agent: str = ""):
        # Truncate long results
        if len(result) > 500:
            result = result[:500] + f"\n... ({len(result)} chars total)"
        if self.console:
            self.console.print(f"[green]  {tool}:[/green] [dim]{result}[/dim]")
        else:
            print(f"  {tool}: {result}")

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

    def print_iteration(self, agent: str, iteration: int, round_n: int,
                        max_rounds: int, tools: int):
        if self.console:
            self.console.print(
                f"[dim]  iter {iteration} · round {round_n}/{max_rounds} · {tools} tools[/dim]"
            )

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
        """Render a classified history message."""
        mtype = msg.get("type", msg.get("role", ""))
        content = msg.get("content", "")
        source = msg.get("source", {})
        agent = source.get("name", "") if isinstance(source, dict) else ""
        svc = source.get("llm_service", "") if isinstance(source, dict) else ""
        channel = msg.get("channel", "")

        if not content:
            return

        if mtype == "user":
            # Separator before user messages
            self.print_separator()
            channel_badge = f" [dim]({channel})[/dim]" if channel and channel != "chat" else ""
            if self.console:
                # Truncate long user messages
                display = content if len(content) <= 300 else content[:300] + "..."
                self.console.print(f"[bold green]>[/bold green]{channel_badge} {display}")
            else:
                print(f"> {content[:300]}")

        elif mtype in ("assistant", "agent_response"):
            # Separator before agent responses
            self.print_separator()
            badge = agent or "assistant"
            svc_info = f" via {svc}" if svc else ""
            color = _agent_color(badge)
            if self.console:
                self.console.print(f"[bold {color}][{badge}{svc_info}][/bold {color}]")
                # Truncate very long responses in history
                display = content if len(content) <= 2000 else content[:2000] + "\n..."
                try:
                    self.console.print(Markdown(display), style="")
                except Exception:
                    self.console.print(display)
            else:
                print(f"[{badge}{svc_info}]")
                print(content[:2000])

        elif mtype == "tool_call":
            # Tool call — yellow with tool name and args preview
            if self.console:
                self.console.print(f"[yellow]  {content}[/yellow]")
            else:
                print(f"  {content}")

        elif mtype == "tool_result":
            # Tool result — dim green, truncated
            if self.console:
                display = content if len(content) <= 200 else content[:200] + "..."
                self.console.print(f"[dim green]  > {display}[/dim green]")
            else:
                print(f"  > {content[:200]}")

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
