"""Pure rendering helpers for the PawCode terminal renderer.

Color/verb/diff/summary helpers extracted from renderer.py to keep each module
<=800 lines. Rich is imported lazily inside the functions that need it, so this
module has no hard dependency on Rich. renderer.py re-exports these names so the
public import path (pawflow_cli.ui.renderer) stays unchanged.
"""


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
    return random.choice(_FUN_VERBS)  # nosec B311


def _agent_color(name: str) -> str:
    h = sum(ord(c) for c in name)
    return _AGENT_COLORS[h % len(_AGENT_COLORS)]


_TOOL_DISPLAY_NAMES = {
    "bash": "Bash", "read": "Read", "write": "Write", "edit": "Update",
    "glob": "Glob", "grep": "Grep", "delete": "Delete", "mkdir": "Mkdir",
    "stat": "Stat", "exists": "Exists", "list_dir": "ListDir",
    "batch_edit": "BatchEdit", "apply_patch": "ApplyPatch",
    "find_replace": "FindReplace", "notebook_edit": "NotebookEdit",
    "copy": "Copy", "execute_script": "Script",
    "web_search": "WebSearch", "fetch": "Fetch",
    "generate_image": "ImageGen", "remember": "Remember", "recall": "Recall",
    "delegate": "Delegate",
    "show_file": "ShowFile", "get_tool_schema": "GetToolSchema",
}


def _tool_summary(tool: str, args: dict) -> str:
    """Smart argument summary — show primary arg instead of all key=value."""
    if tool in ("bash", "execute_script"):
        s = args.get("command") or args.get("code") or ""
    elif tool in ("read", "write", "edit", "delete", "stat", "exists",
                  "mkdir", "list_dir", "batch_edit", "apply_patch",
                  "find_replace", "notebook_edit"):
        s = args.get("path") or ""
    elif tool == "glob":
        s = args.get("pattern") or ""
    elif tool == "grep":
        s = (args.get("pattern") or "") + (", " + args["path"] if args.get("path") else "")
    elif tool in ("web_search",):
        s = args.get("query") or ""
    elif tool in ("fetch",):
        s = args.get("url") or ""
    else:
        parts = []
        for k, v in list(args.items())[:3]:
            vs = v if isinstance(v, str) else repr(v)
            if len(vs) > 60:
                vs = vs[:60] + "..."
            parts.append(f"{k}={vs}")
        s = ", ".join(parts)
    if len(s) > 120:
        s = s[:120] + "\u2026"
    return s


_EXT_LANG = {
    "js": "javascript", "ts": "typescript", "py": "python", "rb": "ruby",
    "rs": "rust", "go": "go", "java": "java", "cpp": "cpp", "c": "c",
    "cs": "csharp", "php": "php", "sh": "bash", "bash": "bash",
    "json": "json", "html": "html", "xml": "xml", "css": "css",
    "sql": "sql", "yaml": "yaml", "yml": "yaml", "jsx": "javascript",
    "tsx": "typescript", "vue": "html", "svelte": "html",
}


def _lang_from_path(fpath: str) -> str:
    ext = fpath.rsplit(".", 1)[-1].lower() if "." in fpath else ""
    return _EXT_LANG.get(ext, "")


def _syn_diff_line(console, marker: str, code: str, lang: str, bg: str):
    """Print a single diff line with syntax highlighting + colored background."""
    from rich.text import Text
    from rich.style import Style
    marker_style = "green" if marker == "+" else "red"
    prefix = Text(f"    {marker} ", style=marker_style)
    if lang:
        try:
            from rich.syntax import Syntax
            syn = Syntax("", lang, theme="monokai", background_color="default")
            highlighted = syn.highlight(code)
            highlighted.rstrip()
            line = prefix + highlighted
        except Exception:
            line = Text(f"    {marker} {code}", style=marker_style)
    else:
        line = Text(f"    {marker} {code}", style=marker_style)
    line.stylize(Style(bgcolor=bg))
    console.print(line, highlight=False)


