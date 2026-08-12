#!/usr/bin/env python3
"""Session-scoped lifecycle hook bridge for published PawFlow MCP clients."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlparse


_MARKER_TTL_SECONDS = 300.0


def _load_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def _state_lock(state_path: Path) -> Iterator[dict]:
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            state = _load_json(state_path, {"cursor": 0, "bootstrapped": False})
            yield state
            _atomic_json(state_path, state)
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PublishedMCPClient:
    def __init__(self, profile: dict) -> None:
        self.endpoint = str(profile.get("url") or "").rstrip("/")
        self.api_key = str(profile.get("api_key") or "")
        self.gateway_key = str(profile.get("gateway_key") or "")
        if not self.endpoint or not self.api_key:
            raise ValueError("PawFlow MCP profile is missing url or api_key")
        self.session_id = ""

    def _post(self, payload: dict) -> Any:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("PawFlow MCP URL must be absolute HTTP(S)")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.gateway_key:
            headers["X-PawFlow-Gateway-Key"] = self.gateway_key
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            if os.environ.get("PAWFLOW_RELAY_INSECURE") == "1":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            connection = http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443, context=context, timeout=30)
        else:
            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port or 80, timeout=30)
        connection.request(
            "POST", parsed.path or "/",
            body=json.dumps(payload).encode("utf-8"), headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {
            key.lower(): value for key, value in response.getheaders()}
        connection.close()
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        data = json.loads(raw.decode("utf-8") or "{}")
        if response.status >= 400 or data.get("error"):
            error = data.get("error") if isinstance(data, dict) else data
            raise RuntimeError(f"PawFlow MCP hook request failed: {error}")
        return data

    def _initialize(self) -> None:
        initialized = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pawflow-mcp-hook", "version": "1"},
            },
        })
        if not ((initialized.get("result") or {}).get("protocolVersion")):
            raise RuntimeError("PawFlow MCP hook initialization failed")

    def list_tools(self) -> list[dict]:
        self._initialize()
        response = self._post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        tools = (response.get("result") or {}).get("tools") or []
        return [item for item in tools if isinstance(item, dict)]

    def call_tool_result(self, name: str, arguments: dict) -> dict:
        self._initialize()
        response = self._post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = response.get("result") or {}
        blocks = result.get("content") or []
        text = "".join(
            str(block.get("text") or "") for block in blocks
            if isinstance(block, dict) and block.get("type") == "text")
        if result.get("isError") or text.startswith("Error:"):
            raise RuntimeError(text or f"PawFlow tool {name} failed")
        return result

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.call_tool_result(name, arguments)
        blocks = result.get("content") or []
        text = "".join(
            str(block.get("text") or "") for block in blocks
            if isinstance(block, dict) and block.get("type") == "text")
        value = json.loads(text or "{}")
        if not isinstance(value, dict):
            raise RuntimeError(f"PawFlow tool {name} returned invalid data")
        return value


def _consume_injected_marker(marker_path: Path, prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    now = time.time()
    try:
        rows = [json.loads(line) for line in marker_path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return ""
    message_id = ""
    for row in reversed(rows):
        if (not row.get("consumed_at")
                and row.get("prompt_sha256") == digest
                and now - float(row.get("created_at") or 0) <= _MARKER_TTL_SECONDS):
            row["consumed_at"] = now
            message_id = str(row.get("message_id") or "")
            break
    if message_id:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        temp = marker_path.with_name(
            f".{marker_path.name}.{os.getpid()}.{time.time_ns()}")
        temp.write_text("".join(
            json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8")
        os.replace(temp, marker_path)
    return message_id


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        for key in ("content", "message", "parts"):
            text = _content_text(value.get(key))
            if text:
                return text
    return ""


def _last_transcript_message(path_value: str, role: str) -> str:
    path = Path(path_value) if path_value else None
    if path is None or not path.is_file():
        return ""
    found = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates = [row]
        if isinstance(row, dict) and isinstance(row.get("message"), dict):
            candidates.append(row["message"])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_role = str(
                candidate.get("role") or candidate.get("type") or "").lower()
            aliases = {"assistant": {"assistant", "model"},
                       "user": {"user", "human"}}[role]
            if candidate_role in aliases:
                text = _content_text(candidate.get("content") or candidate)
                if text.strip():
                    found = text
    return found


def _last_jcode_message_data(raw: dict, role: str) -> tuple[str, str]:
    home = Path(str(raw.get("jcode_home") or os.environ.get("JCODE_HOME") or ""))
    session_id = str(raw.get("session_id") or os.environ.get(
        "JCODE_HOOK_SESSION_ID") or "")
    if not home.is_dir() or not session_id:
        return "", ""
    snapshot = _load_json(home / "sessions" / f"{session_id}.json", {})
    found = ""
    message_id = ""
    for message in snapshot.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").lower() == role:
            text = _content_text(message.get("content") or message)
            if text.strip():
                found = text
                message_id = str(message.get("id") or "")
    return found, message_id


def _last_jcode_message(raw: dict, role: str) -> str:
    return _last_jcode_message_data(raw, role)[0]


def _message_id(raw: dict, role: str, content: str) -> str:
    seed = "\x00".join((
        role, str(raw.get("session_id") or raw.get("sessionId") or ""),
        str(raw.get("turn_id") or raw.get("turnId") or ""), content))
    return "pfhook_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _render_updates(messages: list, exclude_message_id: str = "") -> str:
    visible = [
        row for row in messages if isinstance(row, dict)
        and str(row.get("msg_id") or "") != exclude_message_id
    ]
    if not visible:
        return ""
    return (
        "<pawflow_context_updates>\n"
        + json.dumps(visible, ensure_ascii=False, indent=2)
        + "\n</pawflow_context_updates>")


def _hook_output(client: str, event: str, context: str) -> dict:
    if not context:
        return {}
    if client == "agy":
        return {"hookSpecificOutput": {
            "hookEventName": "PreInvocation",
            "injectSteps": [{"type": "context", "content": context}],
        }}
    if client in {"opencode", "pi", "hermes", "jcode"}:
        return {"context": context}
    return {"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}


def process_hook(client: str, event: str, raw: dict, profile: dict,
                 state_path: Path, marker_path: Path) -> dict:
    api = PublishedMCPClient(profile)
    with _state_lock(state_path) as state:
        bootstrap_events = {
            "SessionStart", "PreInvocation", "session_start",
            "before_agent_start", "pre_llm_call", "chat.message", "turn_start",
        }
        prompt_events = {
            "UserPromptSubmit", "PreInvocation", "before_agent_start",
            "pre_llm_call", "chat.message", "turn_start",
        }
        final_events = {
            "Stop", "agent_end", "post_llm_call", "session.idle", "turn_end",
        }
        contexts: list[str] = []
        if event in bootstrap_events and not state.get("bootstrapped"):
            initial = api.call_tool("get_initial_context", {})
            state["cursor"] = int(initial.get("cursor") or 0)
            state["bootstrapped"] = True
            document = str(initial.get("document") or "")
            if document:
                contexts.append(document)
            has_prompt_payload = bool(
                raw.get("prompt") or raw.get("user_message")
                or raw.get("transcriptPath") or raw.get("transcript_path"))
            if event not in prompt_events or not has_prompt_payload:
                return _hook_output(client, event, "\n\n".join(contexts))

        if event in prompt_events:
            updates = api.call_tool(
                "get_context_updates", {"after_seq": int(state.get("cursor") or 0)})
            prompt = str(raw.get("prompt") or raw.get("user_message") or "")
            if not prompt and client == "agy":
                prompt = _last_transcript_message(
                    str(raw.get("transcriptPath") or raw.get("transcript_path") or ""),
                    "user")
            if not prompt and client == "jcode":
                prompt, jcode_id = _last_jcode_message_data(raw, "user")
                if jcode_id:
                    raw["turn_id"] = jcode_id
            injected_id = _consume_injected_marker(marker_path, prompt) if prompt else ""
            if injected_id:
                state["reply_to_message_id"] = injected_id
            elif prompt:
                state.pop("reply_to_message_id", None)
            context = _render_updates(
                list(updates.get("messages") or []), injected_id)
            if context:
                contexts.append(context)
            state["cursor"] = int(updates.get("cursor") or state.get("cursor") or 0)
            if prompt and not injected_id:
                sent = api.call_tool("send_user_message", {
                    "message_id": _message_id(raw, "user", prompt),
                    "content": prompt,
                })
                state["cursor"] = int(sent.get("cursor") or state["cursor"])
            return _hook_output(client, event, "\n\n".join(contexts))

        if event in final_events:
            content = str(
                raw.get("last_assistant_message")
                or raw.get("lastAssistantMessage")
                or raw.get("last_assistant_text")
                or raw.get("assistant_response") or "")
            if not content:
                content = _last_transcript_message(
                    str(raw.get("transcriptPath") or raw.get("transcript_path") or ""),
                    "assistant")
            if not content and client == "jcode":
                content, jcode_id = _last_jcode_message_data(raw, "assistant")
                if jcode_id:
                    raw["turn_id"] = jcode_id
            if content.strip():
                send_arguments = {
                    "message_id": _message_id(raw, "assistant", content),
                    "content": content,
                }
                reply_to = str(state.get("reply_to_message_id") or "")
                if reply_to:
                    send_arguments["reply_to_message_id"] = reply_to
                sent = api.call_tool("send_agent_message", send_arguments)
                state["cursor"] = int(sent.get("cursor") or state.get("cursor") or 0)
                state.pop("reply_to_message_id", None)
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--client", required=True,
        choices=("cc", "codex", "agy", "opencode", "jcode", "pi", "hermes"))
    parser.add_argument("--event", default="")
    parser.add_argument(
        "--operation", choices=("hook", "list-tools", "call-tool"),
        default="hook")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(sys.stdin.read() or "{}")
        if not isinstance(raw, dict):
            raw = {}
        if args.operation == "list-tools":
            result = {"tools": PublishedMCPClient(
                _load_json(Path(args.profile), {})).list_tools()}
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.operation == "call-tool":
            name = str(raw.get("name") or "")
            arguments = raw.get("arguments") or {}
            if not name or not isinstance(arguments, dict):
                raise ValueError("call-tool requires name and object arguments")
            result = PublishedMCPClient(
                _load_json(Path(args.profile), {})).call_tool_result(
                    name, arguments)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.client == "jcode" and not raw:
            raw = json.loads(os.environ.get("JCODE_HOOK_PAYLOAD") or "{}")
        event = args.event or str(
            raw.get("hook_event_name") or raw.get("hookEventName")
            or raw.get("event") or "")
        result = process_hook(
            args.client, event, raw,
            _load_json(Path(args.profile), {}),
            Path(args.state), Path(args.marker))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Lifecycle synchronization is fail-open: the TUI remains usable and a
        # later idempotent hook can retry. Never print profile contents/secrets.
        print(f"[pawflow-mcp-hook] {exc}", file=sys.stderr)
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
