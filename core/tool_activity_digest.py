"""Tool-call activity digest for shared-bucket summarization.

Shared.jsonl strips tool results and tool_calls — so a summary built from
it alone loses the "which files were touched" axis, which is the thing
a developer most wants to remember. This module fills the gap:

At bg-bucket-build time, the worker reads the RAW transcript for the
same seq range it's about to summarize, extracts file ops / commands /
delegations into a structured trace, and feeds both a textual digest
(for the summarizer prompt) and the structured object (for persistence
in the bucket doc) back to the caller.

The structured form is cheap to aggregate at rollup time — SB buckets
merge their sources' traces into a deduplicated union.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Tool-name → category mapping. Lowercased, matches the names emitted by
# the tool registry. Aliases (e.g. "str_replace_editor" vs "edit") are
# all normalised to the same category at extraction time.
_FILE_EDIT_TOOLS = {"edit", "str_replace", "str_replace_editor", "multiedit"}
_FILE_WRITE_TOOLS = {"write", "create_file"}
_FILE_READ_TOOLS = {"read", "view"}
_FILE_DELETE_TOOLS = {"delete", "rm", "remove_file"}
_COMMAND_TOOLS = {"bash", "shell", "run_command", "powershell"}
_DELEGATION_TOOLS = {"spawn_agent", "delegate", "flash_delegate", "ask_agent", "run_agent"}


def _extract_path(args: Dict[str, Any]) -> str:
    """Best-effort path recovery from heterogeneous tool argument shapes."""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "filename", "filepath", "target"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _extract_command(args: Dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd", "script"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _strip_tool_output_wrapper(text: str) -> str:
    """Strip the anti-injection wrapper added by
    AgentCore._wrap_tool_output_safety (agent_core.py):
      "<tool_output tool=\"name\">\n<body>\n</tool_output>\nNote: ..."
    Without stripping, the FIRST non-empty line of a wrapped result is
    just the wrapper opening tag — _result_digest then returns the
    useless string "<tool_output tool=\"bash\">" and every command
    in the bucket trace loses its actual output line.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    if text.startswith("<tool_output tool="):
        first_nl = text.find("\n")
        if first_nl >= 0:
            text = text[first_nl + 1:]
        _close = text.rfind("</tool_output>")
        if _close >= 0:
            text = text[:_close].rstrip("\n")
    return text


def _result_digest(result_text: str, limit: int = 60) -> str:
    """First non-empty line of a tool result, trimmed. Used as success/
    failure hint in command entries. Empty → empty. Errors preserved
    because they carry the most useful signal. Strips the
    anti-injection wrapper first so the digest reflects real output,
    not the wrapper opening tag."""
    if not isinstance(result_text, str):
        return ""
    result_text = _strip_tool_output_wrapper(result_text)
    for line in result_text.splitlines():
        s = line.strip()
        if s:
            return s[:limit] + ("..." if len(s) > limit else "")
    return ""


def extract_tool_activity(
    transcript: Iterable[Dict[str, Any]],
    first_seq: int,
    last_seq: int,
) -> Dict[str, Any]:
    """Walk the transcript slice [first_seq..last_seq] and build a
    structured tool-activity record.

    The input is expected to be already loaded transcript dicts, preferably
    a bounded seq window from ConversationStore.load_transcript_seq_range.
    Only messages whose seq falls inside the inclusive range contribute.

    Return shape:
        {
          "edits":   {path: count},
          "creates": [path, ...],
          "reads":   {path: count},
          "deletes": [path, ...],
          "commands": [{"cmd": str, "result": str}, ...],
          "delegations": [{"agent": str, "brief": str}, ...],
        }
    """
    edits: Counter = Counter()
    reads: Counter = Counter()
    creates: List[str] = []
    deletes: List[str] = []
    commands: List[Dict[str, str]] = []
    delegations: List[Dict[str, str]] = []

    # Materialise once so we can do a second pass for tool_result lookup
    # without re-iterating an exhausted generator.
    msgs = list(transcript)

    # tool_call_id → (tool_name, args) for pairing with results
    pending_calls: Dict[str, Tuple[str, Dict[str, Any]]] = {}

    for m in msgs:
        seq = int(m.get("seq") or 0)
        if seq < first_seq or seq > last_seq:
            continue

        role = m.get("role") or ""

        if role == "assistant":
            for tc in (m.get("tool_calls") or []):
                name = (tc.get("name") or tc.get("function", {}).get("name")
                        or "").lower()
                args = tc.get("arguments") or tc.get("function", {}).get(
                    "arguments") or {}
                if not isinstance(args, dict):
                    # Some providers serialize arguments as a JSON string
                    try:
                        import json as _json
                        args = _json.loads(args) if isinstance(args, str) else {}
                    except Exception:
                        args = {}

                tc_id = tc.get("id") or ""
                pending_calls[tc_id] = (name, args)

                if name in _FILE_EDIT_TOOLS:
                    p = _extract_path(args)
                    if p:
                        edits[p] += 1
                elif name in _FILE_WRITE_TOOLS:
                    p = _extract_path(args)
                    if p and p not in creates:
                        creates.append(p)
                elif name in _FILE_READ_TOOLS:
                    p = _extract_path(args)
                    if p:
                        reads[p] += 1
                elif name in _FILE_DELETE_TOOLS:
                    p = _extract_path(args)
                    if p and p not in deletes:
                        deletes.append(p)
                elif name in _COMMAND_TOOLS:
                    cmd = _extract_command(args)
                    if cmd:
                        commands.append({
                            "cmd": cmd[:200],
                            "result": "",  # filled by matching tool result
                            "tc_id": tc_id,
                        })
                elif name in _DELEGATION_TOOLS:
                    agent = args.get("agent") or args.get("name") or "?"
                    brief = (args.get("message") or args.get("task")
                              or args.get("prompt") or "")
                    if isinstance(brief, str):
                        brief = brief[:120]
                    delegations.append({
                        "agent": str(agent),
                        "brief": brief or "",
                    })

        elif role == "tool":
            tc_id = m.get("tool_call_id") or ""
            if not tc_id:
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                # multi-part content (e.g. tool_use blocks) — concat text
                content = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text")
            digest = _result_digest(content)
            # Match back onto a pending command if any
            for entry in commands:
                if entry.get("tc_id") == tc_id and not entry["result"]:
                    entry["result"] = digest
                    break

    # Strip tc_id (internal) before returning
    for entry in commands:
        entry.pop("tc_id", None)

    return {
        "edits": dict(edits),
        "creates": creates,
        "reads": dict(reads),
        "deletes": deletes,
        "commands": commands,
        "delegations": delegations,
    }


def format_activity_digest(trace: Dict[str, Any],
                            max_paths_per_section: int = 30) -> str:
    """Render a structured trace as human-readable text for a summarizer
    prompt. Empty sections are omitted — a trace with no files touched
    and no commands produces an empty string.
    """
    if not trace:
        return ""

    lines: List[str] = []
    edits = trace.get("edits") or {}
    creates = trace.get("creates") or []
    reads = trace.get("reads") or {}
    deletes = trace.get("deletes") or []
    commands = trace.get("commands") or []
    delegations = trace.get("delegations") or []

    def _top(counter: Dict[str, int], n: int) -> List[Tuple[str, int]]:
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    if edits:
        lines.append("Files edited:")
        for p, c in _top(edits, max_paths_per_section):
            lines.append(f"  - {p}  ({c} edit{'s' if c != 1 else ''})")
    if creates:
        lines.append("Files created:")
        for p in creates[:max_paths_per_section]:
            lines.append(f"  - {p}")
    if reads:
        lines.append("Files read:")
        for p, c in _top(reads, max_paths_per_section):
            lines.append(f"  - {p}  ({c} read{'s' if c != 1 else ''})")
    if deletes:
        lines.append("Files deleted:")
        for p in deletes[:max_paths_per_section]:
            lines.append(f"  - {p}")
    # Commands + delegations are append-only chronological lists.
    # Show the TAIL (most recent) so the digest reflects current
    # activity, not sedimented ancient entries — same rationale as the
    # merge-time cap in merge_traces.
    if commands:
        lines.append("Commands run:")
        for entry in commands[-max_paths_per_section:]:
            cmd = entry.get("cmd", "")
            res = entry.get("result", "")
            suffix = f"  → {res}" if res else ""
            lines.append(f"  - {cmd}{suffix}")
    if delegations:
        lines.append("Delegations:")
        for d in delegations[-max_paths_per_section:]:
            brief = d.get("brief") or ""
            lines.append(f"  - {d.get('agent', '?')}: {brief}")

    if not lines:
        return ""
    return "[TOOL ACTIVITY in this phase]\n" + "\n".join(lines)


# Cap for commands/delegations stored in a merged trace. These are
# append-only lists with no dedup (same command can reasonably run many
# times), so without a cap every rollup inherits all previous history
# and the tool_trace grows unboundedly (observed 3700+ commands in a
# single level-1 bucket after multiple rollups). Keep the most recent
# N so the stored state stays bounded and the agent-visible digest
# reflects current activity rather than sedimented ancient commands.
_MERGE_MAX_COMMANDS = 100
_MERGE_MAX_DELEGATIONS = 100


def merge_traces(traces: Iterable[Dict[str, Any]],
                 *, max_commands: int = _MERGE_MAX_COMMANDS,
                 max_delegations: int = _MERGE_MAX_DELEGATIONS
                 ) -> Dict[str, Any]:
    """Consolidate N structured traces into one. Used at rollup time
    when multiple level-1 buckets collapse into a single SB.

    Bounding strategy (must be applied to ALL fields, not just commands /
    delegations — prior versions accumulated edits/creates/reads/deletes
    forever and the tool_trace ballooned across successive rollups):

    - edits / reads (Counters), creates / deletes (de-duped lists):
      keep ONLY the most recent input bucket's snapshot. The narrative
      summary captures the older activity; the structured trace stays
      bounded by mirroring whatever the latest bucket held. Earlier
      buckets' file-touch counts would otherwise re-accumulate at every
      rollup.
    - commands / delegations (append-only lists):
      concatenate then tail-cap. These are intentionally not de-duped
      (the same command running 50x is signal), so we keep the most
      recent N entries.
    """
    traces_list = [t for t in traces if isinstance(t, dict)]

    last_trace: Dict[str, Any] = traces_list[-1] if traces_list else {}
    last_edits = dict(last_trace.get("edits") or {})
    last_reads = dict(last_trace.get("reads") or {})
    last_creates = list(last_trace.get("creates") or [])
    last_deletes = list(last_trace.get("deletes") or [])

    commands: List[Dict[str, str]] = []
    delegations: List[Dict[str, str]] = []
    for t in traces_list:
        for cmd in (t.get("commands") or []):
            commands.append(cmd)
        for d in (t.get("delegations") or []):
            delegations.append(d)

    if len(commands) > max_commands:
        commands = commands[-max_commands:]
    if len(delegations) > max_delegations:
        delegations = delegations[-max_delegations:]

    return {
        "edits": last_edits,
        "creates": last_creates,
        "reads": last_reads,
        "deletes": last_deletes,
        "commands": commands,
        "delegations": delegations,
    }


def is_empty(trace: Optional[Dict[str, Any]]) -> bool:
    if not trace:
        return True
    return not any(trace.get(k) for k in (
        "edits", "creates", "reads", "deletes", "commands", "delegations"))
