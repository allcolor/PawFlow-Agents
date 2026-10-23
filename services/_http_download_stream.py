"""Bounded-memory HTTP downloads and previews from relay filesystems.

The file explorer used to download and preview through the ``fs_read_file``
UI action: the whole file travelled as one JSON/base64 response and was
rebuilt as a Blob in the browser, so large files failed. This route streams
the relay's bounded chunks straight to the HTTP response instead, as an
attachment for downloads or inline (``inline=1``) for the file viewer.
A single ``Range`` is answered with ``206 Partial Content`` from the relay
chunks covering it, so a video player can seek or read a trailing index.
"""

import json
import logging
import mimetypes
import re
from urllib.parse import parse_qs, quote

from core.handlers._fs_helpers import find_fs_service
from services._http_base import _SECURITY_HEADERS


logger = logging.getLogger("services.http_listener_service")

# A preview opened directly in a tab renders in the webchat origin, whose
# policy allows inline script: sandbox it so relay HTML or SVG cannot run
# script there. PDF stays unsandboxed because Chrome refuses to start its PDF
# viewer in a sandboxed document, and PDF script never runs in the page.
_INLINE_PREVIEW_CSP = _SECURITY_HEADERS["Content-Security-Policy"] + "; sandbox"

# One byte range, as media players send it: ``first-last``, ``first-`` or a
# ``-suffix``. Several ranges or another unit are ignored: RFC 9110 lets a
# server send the whole file instead.
_BYTE_RANGE = re.compile(r"bytes=([0-9]*)-([0-9]*)")


class _RangeNotSatisfiable(Exception):
    """A valid byte range that starts past the end of the file (416)."""


def _send_json(handler, status, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    if getattr(handler, "_renew_cookie", ""):
        handler.send_header("Set-Cookie", handler._renew_cookie)
    handler.end_headers()
    handler.wfile.write(body)


def _one(params, name):
    values = params.get(name) or []
    return values[0].strip() if values else ""


def _content_disposition(filename, inline=False):
    """Disposition header safe for any file name (RFC 6266 / RFC 5987)."""
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_"
        for ch in filename) or "download"
    kind = "inline" if inline else "attachment"
    return (f'{kind}; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def _byte_range(header, size):
    """Inclusive ``(start, end)`` asked by a ``Range`` header, or ``None``.

    ``None`` sends the whole file: no header, an empty file, or a range this
    route ignores (several ranges, another unit, malformed, last < first).
    """
    match = _BYTE_RANGE.fullmatch((header or "").strip().lower())
    if not match or size <= 0:
        return None
    first, last = match.groups()
    if not first:
        if not last:
            return None
        if int(last) == 0:
            raise _RangeNotSatisfiable()
        return max(0, size - int(last)), size - 1
    start = int(first)
    if last and int(last) < start:
        return None
    if start >= size:
        raise _RangeNotSatisfiable()
    end = min(int(last), size - 1) if last else size - 1
    return start, end


def handle_relay_download(handler, session, query):
    """Stream one relay file to the client without holding it in memory.

    ``inline=1`` serves it for display (the file viewer points its media
    element at this URL) instead of as an attachment. A single ``Range`` is
    served as ``206 Partial Content`` (see ``_byte_range``).
    """
    user_id = ""
    if session and session is not True:
        user_id = getattr(session, "username", "") or ""
    if not user_id:
        _send_json(handler, 403, {"error": "Authenticated user is required"})
        return

    params = parse_qs(query, keep_blank_values=True)
    conversation_id = _one(params, "conversation_id")
    service_name = _one(params, "service")
    relay_path = _one(params, "path")
    inline = _one(params, "inline") == "1"
    if not conversation_id or not service_name or not relay_path:
        _send_json(handler, 400, {
            "error": "conversation_id, service and path are required"})
        return

    # Same role as the fs_read_file UI action: reading live relay files
    # drives the conversation.
    from core.conversation_access import ConversationAccessError, require_write
    try:
        require_write(conversation_id, user_id)
    except ConversationAccessError:
        _send_json(handler, 404, {"error": "Conversation not found"})
        return

    service = find_fs_service(
        user_id, service_name, conversation_id=conversation_id)
    reader = getattr(service, "iter_file_chunks", None) if service else None
    if not callable(reader):
        _send_json(handler, 400, {
            "error": "Selected filesystem does not support streamed downloads"})
        return

    try:
        entry = service.stat(relay_path)
    except Exception as exc:
        _send_json(handler, 404, {"error": str(exc)})
        return
    if entry.kind == "directory":
        _send_json(handler, 400, {"error": "Cannot download a directory"})
        return
    size = int(entry.size)
    try:
        byte_range = _byte_range(handler.headers.get("Range"), size)
    except _RangeNotSatisfiable:
        _send_json(handler, 416, {"error": "Requested range not satisfiable"},
                   {"Content-Range": f"bytes */{size}"})
        return

    if not handler.server.transfer_current_dispatch_to_long_lived(
            "relay download", handler.connection):
        return

    filename = relay_path.replace("\\", "/").rsplit("/", 1)[-1]
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if byte_range is None:
        handler.send_response(200)
        length = size
        chunks = reader(relay_path)
    else:
        start, end = byte_range
        handler.send_response(206)
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        length = end - start + 1
        chunks = service.iter_file_range(relay_path, start, end)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition",
                        _content_disposition(filename, inline=inline))
    if inline and content_type != "application/pdf":
        handler.send_header("Content-Security-Policy", _INLINE_PREVIEW_CSP)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    handler.send_header("Cache-Control", "no-store")
    if getattr(handler, "_renew_cookie", ""):
        handler.send_header("Set-Cookie", handler._renew_cookie)
    handler.end_headers()

    # A file that changes size mid-transfer cannot honour Content-Length:
    # never reuse the connection, and a short body then reads as a failed
    # download in the browser instead of a silently truncated file.
    handler.close_connection = True
    sent = 0
    try:
        for chunk in chunks:
            if chunk:
                handler.wfile.write(chunk)
                sent += len(chunk)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        logger.debug("Client disconnected during relay download %s",
                     relay_path)
        return
    except Exception as exc:
        logger.warning("Relay download failed after %d bytes: %s", sent, exc)
