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
            self._live.stop()
            self._live = None
        if text:
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

    # ── Conversations ──

    def print_conversation_list(self, conversations: list):
        if not conversations:
            self.print_system("No conversations.")
            return
        for c in conversations:
            cid = c.get("conversation_id", "?")[:8]
            title = c.get("title", "") or c.get("last_message", "")[:60] or "(empty)"
            age = c.get("age", "")
            if self.console:
                self.console.print(f"  [cyan]{cid}[/cyan]  {title}  [dim]{age}[/dim]")
            else:
                print(f"  {cid}  {title}  {age}")
