"""Human-readable rendering for unified slash-command results.

Action handlers keep returning their normal machine-readable JSON.  When the
request originated from ``action=command`` we add a ``display`` field so every
client can render the same useful text instead of dumping a Python/JSON object.
"""

from __future__ import annotations

import json
from typing import Any


_PREFERRED_TEXT_KEYS = ("help", "output", "message", "display")
_NOISY_KEYS = {
    "ok", "status", "server_start_time", "_callId", "state_update",
    "encoding",
}


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _row_label(row: Any) -> str:
    if not isinstance(row, dict):
        return _scalar(row)
    identity = next((
        _scalar(row.get(key)) for key in (
            "title", "name", "id", "conversation_id", "task_id", "key",
            "agent", "service_id", "tool", "path",
        ) if row.get(key) not in (None, "")
    ), "")
    details = []
    for key in ("description", "preview", "status", "role", "type", "scope",
                "frequency", "prompt", "message_count"):
        value = row.get(key)
        if value in (None, "", [], {}):
            continue
        text = _scalar(value).replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        details.append(f"{key}={text}")
    if identity and details:
        return f"{identity} — {', '.join(details)}"
    if identity:
        return identity
    if details:
        return ", ".join(details)
    pairs = []
    for key, value in row.items():
        if key in _NOISY_KEYS or value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            continue
        pairs.append(f"{key}={_scalar(value)}")
        if len(pairs) == 4:
            break
    return ", ".join(pairs) or "(empty)"


def _format_collection(label: str, values: list) -> str:
    title = label.replace("_", " ").strip().title()
    if not values:
        return f"{title}: none."
    return f"{title} ({len(values)}):\n" + "\n".join(
        f"• {_row_label(value)}" for value in values[:50]
    )


def format_command_payload(payload: Any) -> str:
    """Return a concise, human-readable representation of an action payload."""
    if not isinstance(payload, dict):
        if isinstance(payload, list):
            return _format_collection("results", payload)
        return _scalar(payload)

    if payload.get("error"):
        text = f"Error: {payload['error']}"
        if payload.get("hint"):
            text += f"\n{payload['hint']}"
        return text

    for key in _PREFERRED_TEXT_KEYS:
        value = payload.get(key)
        if value not in (None, ""):
            return _scalar(value)

    value = payload.get("result")
    if value not in (None, ""):
        if isinstance(value, (dict, list)):
            return format_command_payload(value)
        return _scalar(value)

    # Common list-shaped command responses.  Keep the order stable so mixed
    # responses such as /task list remain readable.
    sections = []
    for key in (
        "conversations", "definitions", "tasks", "flows", "templates",
        "agents", "tools", "services", "resources", "schedules", "loops",
        "hooks", "bindings", "messages", "commits", "files", "entries",
        "matches", "memories", "skills", "prompts",
    ):
        values = payload.get(key)
        if isinstance(values, list):
            sections.append(_format_collection(key, values))
    if sections:
        return "\n\n".join(sections)

    if payload.get("content") is not None:
        if payload.get("encoding") == "base64":
            return f"Binary file ({payload.get('size', '?')} bytes, base64)."
        return _scalar(payload["content"])

    pairs = []
    for key, value in payload.items():
        if key in _NOISY_KEYS or key == "display" or value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            pairs.append(_format_collection(key, value))
        elif isinstance(value, dict):
            pairs.append(f"{key.replace('_', ' ').title()}: {_row_label(value)}")
        else:
            pairs.append(f"{key.replace('_', ' ').title()}: {_scalar(value)}")
    if pairs:
        return "\n".join(pairs)
    if payload.get("status") == "accepted":
        return "Command accepted."
    if payload.get("ok"):
        return "Done."
    return "Command completed."


def decorate_command_flowfiles(result: Any) -> Any:
    """Add ``display`` to JSON objects stored in one or more FlowFiles."""
    flowfiles = result if isinstance(result, list) else [result]
    for flowfile in flowfiles:
        if not hasattr(flowfile, "get_content") or not hasattr(flowfile, "set_content"):
            continue
        try:
            payload = json.loads(
                flowfile.get_content().decode("utf-8", errors="replace"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("display"):
            continue
        payload["display"] = format_command_payload(payload)
        flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return result
