#!/usr/bin/env python3
"""Lifecycle hook bridge for PawFlow-managed interactive CLIs.

Installed as the ``UserPromptSubmit`` / ``Stop`` / compaction / session hooks
of Claude Code, Codex and Antigravity. It sends
one fire-and-forget ``hook`` event per invocation to ``CCInteractiveEventService``
over the managed session's authenticated WebSocket registration.

Managed MCP mode reads the turn's final answer from the ``Stop`` event, so this
hook keeps ``last_assistant_message`` (bounded) and, when the CLI leaves that
field empty, extracts the last assistant message from the CLI's own local
transcript. It never scrapes tmux or vendor traffic.
"""

from __future__ import annotations

import base64
import logging

try:
    import fcntl  # POSIX-only; marker-file locking is best-effort elsewhere.
except ImportError:
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
import os
import socket
import ssl
import sys
import time
from urllib.parse import urlparse

_INJECTED_PROMPT_TTL_SECONDS = 300
_INJECTED_FRAGMENT_BURST_SECONDS = 180
_MIN_INJECTED_FRAGMENT_CHARS = 12

# Bound on the final text carried by a Stop event. Large enough for any real
# answer, small enough that a runaway transcript cannot exceed the event
# service's frame budget.
_MAX_FINAL_CHARS = 200_000
# How much of a local transcript is read (from its end) to find the last
# assistant message. Transcripts are append-only JSONL, so the tail is enough.
_TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
# One short retry when the event service refuses the first connection: the
# hook has a five-second command timeout, so this stays well inside it.
_DELIVERY_RETRIES = 1
_DELIVERY_RETRY_DELAY_SECONDS = 0.4


def _masked_frame(obj: dict) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.extend([0x80 | 126, (n >> 8) & 0xFF, n & 0xFF])
    else:
        header.append(0x80 | 127)
        header.extend(n.to_bytes(8, "big"))
    mask = os.urandom(4)
    header.extend(mask)
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bytes(header) + payload


def _recvn(sock, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("event WS closed")
        out += chunk
    return out


def _recv_json(sock) -> dict:
    hdr = _recvn(sock, 2)
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(_recvn(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recvn(sock, 8), "big")
    return json.loads(_recvn(sock, length).decode("utf-8"))


def _connect(url: str, token: str, session_token: str):
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/ws/cc-interactive/events"
    sock = socket.create_connection((host, port), timeout=5)
    if parsed.scheme == "wss":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    internal = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
    cookie = f"Cookie: pawflow_internal={internal}\r\n" if internal else ""
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"{cookie}\r\n"
    )
    sock.sendall(req.encode("latin-1"))
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("event WS closed during upgrade")
        resp += chunk
    if b"101" not in resp.split(b"\r\n", 1)[0]:
        raise ConnectionError(resp[:200].decode("latin-1", errors="replace"))
    sock.sendall(_masked_frame({
        "type": "register",
        "token": token,
        "session_token": session_token,
        "container_id": os.environ.get("HOSTNAME", ""),
        "client_kind": "hook",
    }))
    ack = _recv_json(sock)
    if ack.get("type") == "error":
        raise ConnectionError(ack.get("message", "registration failed"))
    return sock


def _hook_client() -> str:
    """Which official CLI invoked us: ``cc`` (default), ``codex`` or ``agy``."""
    return (os.environ.get("PAWFLOW_CCI_HOOK_CLIENT", "") or "cc").strip().lower()


def _bound_final_text(value) -> str:
    """Cap a final answer at ``_MAX_FINAL_CHARS`` including the notice."""
    if not isinstance(value, str):
        return ""
    if len(value) <= _MAX_FINAL_CHARS:
        return value
    notice = (
        f"\n\n[... final answer truncated by the hook at {_MAX_FINAL_CHARS:,} "
        f"chars; {len(value):,} chars total]")
    keep = max(0, _MAX_FINAL_CHARS - len(notice))
    return value[:keep] + notice


def _content_text(value) -> str:
    """Plain text of a transcript ``content`` value (string or block list)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                btype = str(item.get("type") or "")
                if btype in {"text", "output_text"} and isinstance(
                        item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        for key in ("content", "message", "parts"):
            text = _content_text(value.get(key))
            if text:
                return text
    return ""


def _transcript_role_text(row, role: str) -> str:
    """Text of ``row`` when it is a ``role`` message in any supported shape.

    Claude Code: ``{"type": "assistant", "message": {"role": "assistant",
    "content": [...]}}``. Codex rollout: ``{"type": "response_item",
    "payload": {"type": "message", "role": "assistant", "content": [...]}}``.
    Generic: ``{"role": ..., "content": ...}``. Tool results and tool calls
    never match: they carry no assistant/user role.
    """
    if not isinstance(row, dict):
        return ""
    aliases = {"assistant": {"assistant", "model"},
               "user": {"user", "human"}}[role]
    candidates = [row]
    for key in ("message", "payload"):
        nested = row.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        candidate_role = str(candidate.get("role") or "").lower()
        if not candidate_role and candidate is row:
            candidate_role = str(row.get("type") or "").lower()
        if candidate_role not in aliases:
            continue
        text = _content_text(candidate.get("content"))
        if text.strip():
            return text
    return ""


def _last_transcript_message(path: str, role: str) -> str:
    """Last ``role`` message of a local JSONL transcript, read from its tail."""
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - _TRANSCRIPT_TAIL_BYTES)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return ""
    lines = data.split(b"\n")
    if start > 0 and lines:
        # The first line of a mid-file read is a partial row.
        lines = lines[1:]
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        text = _transcript_role_text(row, role)
        if text:
            return text
    return ""


def _normalize_client_input(raw: dict, client: str) -> dict:
    """Map a client's native hook payload onto the Claude Code field names.

    Claude Code and Codex already speak the ``hook_event_name`` shape.
    Antigravity uses camelCase (``hookEventName``, ``transcriptPath``) and
    fires ``PreInvocation`` before a model call instead of a prompt hook; the
    submitted prompt is then only in its transcript. Nothing here is trusted
    for identity: the server binds the event to the registered session.
    """
    if not isinstance(raw, dict):
        return {}
    if client != "agy":
        return raw
    out = dict(raw)
    if "hook_event_name" not in out and out.get("hookEventName"):
        out["hook_event_name"] = out.get("hookEventName")
    if "transcript_path" not in out and out.get("transcriptPath"):
        out["transcript_path"] = out.get("transcriptPath")
    if "session_id" not in out and out.get("sessionId"):
        out["session_id"] = out.get("sessionId")
    if ("last_assistant_message" not in out
            and isinstance(out.get("finalModelOutput"), str)):
        out["last_assistant_message"] = out.get("finalModelOutput")
    if "reason" not in out and out.get("terminationReason"):
        out["reason"] = out.get("terminationReason")
    if out.get("hook_event_name") == "PreInvocation":
        out["hook_event_name"] = "UserPromptSubmit"
        if not isinstance(out.get("prompt"), str) or not out.get("prompt"):
            out["prompt"] = _last_transcript_message(
                str(out.get("transcript_path") or ""), "user")
    return out


def _compact_input(raw: dict) -> dict:
    keep = {
        "hook_event_name", "session_id", "cwd", "permission_mode",
        "source", "trigger", "error", "matcher", "agent_id", "agent_type",
        "turn_id", "model", "last_assistant_message", "stop_hook_active",
        "reason",
    }
    out = {k: v for k, v in raw.items() if k in keep}
    if raw.get("hook_event_name") == "UserPromptSubmit":
        prompt = raw.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        out["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        out["prompt_len"] = len(prompt)
        injected = _consume_injected_prompt(prompt)
        out["pawflow_injected_prompt"] = injected
        if prompt and not injected:
            out["prompt"] = prompt
    elif raw.get("hook_event_name") == "Stop":
        final = _bound_final_text(raw.get("last_assistant_message"))
        source = "hook_field" if final.strip() else ""
        if not source:
            final = _bound_final_text(_last_transcript_message(
                str(raw.get("transcript_path") or ""), "assistant"))
            source = "transcript" if final.strip() else ""
        out["last_assistant_message"] = final
        out["final_source"] = source
    return out


def _consume_injected_prompt(prompt: str) -> bool:
    path = os.environ.get("PAWFLOW_CCI_INJECTED_PROMPTS", "")
    if not path or not prompt:
        return False
    digest_candidates = {
        hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        for candidate in (prompt, prompt + "\n", prompt + "\r\n", prompt.rstrip("\r\n"))
    }
    now = time.time()
    cutoff = now - _INJECTED_PROMPT_TTL_SECONDS
    try:
        with open(path, "a+", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.seek(0)
            rows = fh.readlines()
            found = False
            payloads = []
            changed = False
            for row in rows:
                try:
                    payload = json.loads(row)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    changed = True
                    continue
                ts = float(payload.get("ts") or 0)
                consumed_at = float(payload.get("consumed_at") or 0)
                if max(ts, consumed_at) and max(ts, consumed_at) < cutoff:
                    changed = True
                    continue
                payloads.append(payload)

            normalized = " ".join(prompt.split())
            # Prefer the newest marker.  A retry or a second send can record
            # the same text more than once; one submitted chip must spend only
            # one record, otherwise a later real user message could be hidden.
            for payload in reversed(payloads):
                if payload.get("sha256") in digest_candidates:
                    found = True
                    payload["consumed_at"] = now
                    payload["remaining"] = ""
                    changed = True
                    break
                remaining = str(payload.get("remaining") or "")
                last_fragment = float(payload.get("fragment_seen_at")
                                      or payload.get("ts") or 0)
                if (len(normalized) < _MIN_INJECTED_FRAGMENT_CHARS
                        or not remaining
                        or now - last_fragment > _INJECTED_FRAGMENT_BURST_SECONDS
                        or normalized not in remaining):
                    continue
                found = True
                payload["remaining"] = remaining.replace(normalized, " ", 1)
                payload["fragment_seen_at"] = now
                payload["consumed_at"] = now
                changed = True
                break

            if changed:
                fh.seek(0)
                fh.truncate()
                kept = [
                    json.dumps(payload, separators=(",", ":")) + "\n"
                    for payload in payloads
                ]
                fh.writelines(kept)
            return found
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001 - marker damage must not classify a prompt
        return False


def _deliver(url: str, token: str, session_token: str, event: dict) -> bool:
    """Send one event; retry once on a refused/aborted connection."""
    attempts = _DELIVERY_RETRIES + 1
    for attempt in range(attempts):
        try:
            sock = _connect(url, token, session_token)
            try:
                sock.sendall(_masked_frame({"type": "event", "event": event}))
            finally:
                sock.close()
            return True
        except Exception:  # noqa: BLE001 - any transport failure is a retry, never a CLI error
            if attempt + 1 >= attempts:
                return False
            time.sleep(_DELIVERY_RETRY_DELAY_SECONDS)
    return False


def _client_output(client: str) -> str:
    """What the CLI expects on stdout. Antigravity reads a JSON object."""
    return "{}" if client == "agy" else ""


def main() -> int:
    session_token = os.environ.get("PAWFLOW_CCI_SESSION_TOKEN", "")
    url = os.environ.get("PAWFLOW_CCI_EVENT_URL", "")
    token = os.environ.get("PAWFLOW_CCI_EVENT_TOKEN", "")
    client = _hook_client()
    output = _client_output(client)
    if not session_token or not url or not token:
        if output:
            print(output)
        return 0
    try:
        raw = json.loads(sys.stdin.read() or "{}")
        raw = _normalize_client_input(raw, client)
        event = {
            "type": "hook",
            "hook_event_name": raw.get("hook_event_name", ""),
            "input": _compact_input(raw),
            "container_id": os.environ.get("HOSTNAME", ""),
            "timestamp": time.time(),
        }
        _deliver(url, token, session_token, event)
    except Exception:  # noqa: BLE001, S110  # nosec B110 - a hook must never fail the CLI turn
        pass
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
