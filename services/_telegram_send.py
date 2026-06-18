"""Telegram send-path helpers: per-token send channels, retry/backoff,
static API calls, multipart upload, and message text splitting.

Extracted from ``telegram_bot_service`` to keep that module <=800 lines.
This is a leaf module (no import back into telegram_bot_service); every
name is re-exported there for backward compatibility and as patch targets
(invariant 1).
"""

import http.client
import json
import logging
import re
import ssl
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_API_HOST = "api.telegram.org"
_TELEGRAM_TEXT_LIMIT = 4096
_TELEGRAM_SPLIT_LIMIT = 4000


def _api_call_static(token: str, method: str,
                     params: Optional[Dict] = None) -> Any:
    """Standalone Telegram API call (no service instance needed)."""
    ctx = ssl.create_default_context()
    timeout = 10 if params and params.get("timeout", 0) == 0 else 40
    conn = http.client.HTTPSConnection(_API_HOST, timeout=timeout, context=ctx)
    try:
        if params:
            body = json.dumps(params).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            body = None
            headers = {}
        conn.request(
            "POST" if params else "GET",
            f"/bot{token}/{method}",
            body=body, headers=headers,
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(
                f"Telegram API {method} returned {resp.status}: {raw[:200]}"
            )
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {data.get('description', 'unknown')}"
            )
        return data.get("result")
    finally:
        conn.close()


# ── Persistent send transport ─────────────────────────────────────
#
# Message sends run on the ConversationEventBus per-conversation listener lane
# (one in-flight drain per conversation). Opening a fresh TLS connection per
# message added a full handshake (~200-400ms) to every send; under a burst the
# lane could not drain fast enough and Telegram fell minutes behind the webchat
# (which is delivered directly via SSE). A persistent keep-alive connection per
# bot token removes the per-message handshake. It is kept SEPARATE from the
# long-poll getUpdates connection so a 30s long-poll never blocks a send. Each
# token serializes its own sends under a lock (Telegram rate-limits per bot
# anyway) and 429 responses are honoured with retry_after backoff.

_SEND_MAX_RETRIES = 3
_SEND_MAX_BACKOFF = 30.0


class _SendChannel:
    __slots__ = ("lock", "conn")

    def __init__(self):
        self.lock = threading.Lock()
        self.conn = None


_SEND_CHANNELS: Dict[str, _SendChannel] = {}
_SEND_CHANNELS_LOCK = threading.Lock()


def _send_channel(token: str) -> _SendChannel:
    with _SEND_CHANNELS_LOCK:
        ch = _SEND_CHANNELS.get(token)
        if ch is None:
            ch = _SendChannel()
            _SEND_CHANNELS[token] = ch
        return ch


def _close_send_conn(ch: _SendChannel) -> None:
    try:
        if ch.conn is not None:
            ch.conn.close()
    except Exception:
        logger.debug("telegram: error closing send connection", exc_info=True)
    ch.conn = None


def _parse_retry_after(raw: str) -> float:
    try:
        data = json.loads(raw)
        ra = (data.get("parameters") or {}).get("retry_after")
        if ra is not None:
            return float(ra)
    except Exception:
        logger.debug("telegram: could not parse retry_after", exc_info=True)
    return 1.0


def _send_api_call(token: str, method: str,
                   params: Optional[Dict] = None, timeout: int = 40) -> Any:
    """Telegram API call over a persistent per-token keep-alive connection.

    Reconnects on a stale/broken connection and honours 429 retry_after. Used
    for message sends (NOT long-poll getUpdates, which keeps its own short-lived
    connection so it never blocks a send).
    """
    ch = _send_channel(token)
    body = json.dumps(params).encode("utf-8") if params else None
    headers = {"Content-Type": "application/json"} if params else {}
    path = f"/bot{token}/{method}"
    verb = "POST" if params else "GET"
    with ch.lock:
        attempt = 0
        while True:
            attempt += 1
            try:
                if ch.conn is None:
                    ch.conn = http.client.HTTPSConnection(
                        _API_HOST, timeout=timeout,
                        context=ssl.create_default_context())
                ch.conn.request(verb, path, body=body, headers=headers)
                resp = ch.conn.getresponse()
                raw = resp.read().decode("utf-8")
                status = resp.status
            except (http.client.HTTPException, OSError) as e:
                # Broken/stale keep-alive socket: drop it and reconnect.
                _close_send_conn(ch)
                if attempt <= _SEND_MAX_RETRIES:
                    continue
                raise RuntimeError(
                    f"Telegram API {method} connection failed: {e}")
            if status == 429:
                retry_after = _parse_retry_after(raw)
                if attempt <= _SEND_MAX_RETRIES:
                    time.sleep(min(retry_after, _SEND_MAX_BACKOFF))
                    continue
                raise RuntimeError(
                    f"Telegram API {method} rate-limited (429)")
            if status != 200:
                raise RuntimeError(
                    f"Telegram API {method} returned {status}: {raw[:200]}")
            data = json.loads(raw)
            if not data.get("ok"):
                raise RuntimeError(
                    f"Telegram API error: {data.get('description', 'unknown')}")
            return data.get("result")


def _reset_send_channels() -> None:
    """Close all persistent send connections (test teardown / shutdown)."""
    with _SEND_CHANNELS_LOCK:
        channels = list(_SEND_CHANNELS.values())
        _SEND_CHANNELS.clear()
    for ch in channels:
        with ch.lock:
            _close_send_conn(ch)


def _split_telegram_text(text: str, parse_mode: Optional[str] = None) -> List[str]:
    """Split Bot API text into complete Telegram-sized messages.

    For ``parse_mode=HTML`` the split is tag-aware: it never cuts inside a tag,
    closes any tags still open at a chunk boundary, and reopens them at the
    start of the next chunk. Splitting raw HTML on whitespace alone produces
    chunks like ``...<blockquote>foo`` whose dangling tag makes the Telegram API
    reject the whole message (400 "Can't find end tag"). Non-HTML text keeps
    the plain whitespace-boundary split.
    """
    if not text:
        return [""]
    if len(text) <= _TELEGRAM_TEXT_LIMIT:
        return [text]
    if parse_mode and parse_mode.lower() == "html":
        return _split_telegram_html(text, _TELEGRAM_SPLIT_LIMIT)
    return _split_telegram_plain(text, _TELEGRAM_SPLIT_LIMIT)


def _split_telegram_plain(text: str, limit: int) -> List[str]:
    chunks: List[str] = []
    remaining = text
    while len(remaining) > _TELEGRAM_TEXT_LIMIT:
        split_at = _best_telegram_split(remaining, limit)
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _best_telegram_split(text: str, limit: int) -> int:
    window = text[:limit]
    for marker in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = window.rfind(marker)
        if idx >= max(1, limit // 2):
            return idx + len(marker)
    return limit


# Telegram HTML tags are all paired (no void tags); a tag is <name ...> or
# </name>. We only need name + whether it's a closer to balance them.
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")


def _tokenize_html(text: str) -> List[tuple]:
    """Split into ('text', s) and ('tag', name, is_close, full) tokens."""
    tokens: List[tuple] = []
    pos = 0
    for m in _HTML_TAG_RE.finditer(text):
        if m.start() > pos:
            tokens.append(("text", text[pos:m.start()]))
        tokens.append(("tag", m.group(2).lower(), bool(m.group(1)), m.group(0)))
        pos = m.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))
    return tokens


def _text_cut(run: str, avail: int) -> int:
    """Index to cut a text run at, preferring a whitespace boundary."""
    if avail >= len(run):
        return len(run)
    window = run[:avail]
    for marker in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = window.rfind(marker)
        if idx >= max(1, avail // 2):
            return idx + len(marker)
    return max(1, avail)


def _split_telegram_html(text: str, limit: int) -> List[str]:
    """Tag-aware split: every chunk is independently well-formed HTML."""
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    stack: List[tuple] = []  # (name, opening_str) of tags open in cur

    def flush() -> None:
        nonlocal cur, cur_len
        closers = "".join("</%s>" % name for name, _ in reversed(stack))
        body = "".join(cur) + closers
        if body.strip():
            chunks.append(body)
        openers = "".join(opening for _, opening in stack)
        cur = [openers] if openers else []
        cur_len = len(openers)

    for tok in _tokenize_html(text):
        if tok[0] == "tag":
            _, name, is_close, full = tok
            if cur_len + len(full) > limit and cur_len > 0:
                flush()
            cur.append(full)
            cur_len += len(full)
            if is_close:
                for j in range(len(stack) - 1, -1, -1):
                    if stack[j][0] == name:
                        del stack[j:]
                        break
            else:
                stack.append((name, full))
        else:
            run = tok[1]
            while run:
                avail = limit - cur_len
                if avail <= 0:
                    flush()
                    avail = limit - cur_len
                if len(run) <= avail:
                    cur.append(run)
                    cur_len += len(run)
                    run = ""
                else:
                    cut = _text_cut(run, avail)
                    cur.append(run[:cut])
                    cur_len += cut
                    run = run[cut:]
                    flush()
    if cur:
        closers = "".join("</%s>" % name for name, _ in reversed(stack))
        body = "".join(cur) + closers
        if body.strip():
            chunks.append(body)
    return chunks or [""]


def _api_upload(token: str, method: str, chat_id: str, field_name: str,
                file_bytes: bytes, filename: str, content_type: str,
                caption: str = "") -> dict:
    """Upload a file to Telegram with multipart/form-data."""
    boundary = "----TelegramBotBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="{field_name}"; '
        f'filename="{filename}"\r\n'
    ).encode()
    body += f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode()
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(_API_HOST, timeout=60, context=ctx)
    try:
        conn.request(
            "POST", f"/bot{token}/{method}", body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(
                f"Telegram API {method} returned {resp.status}: {raw[:200]}"
            )
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {data.get('description', 'unknown')}"
            )
        return data.get("result", {})
    finally:
        conn.close()
