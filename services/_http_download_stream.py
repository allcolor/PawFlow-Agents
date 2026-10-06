"""Bounded-memory HTTP downloads from relay filesystems.

The file explorer used to download through the ``fs_read_file`` UI action:
the whole file travelled as one JSON/base64 response and was rebuilt as a
Blob in the browser, so large files failed. This route streams the relay's
bounded chunks straight to the HTTP response instead.
"""

import json
import logging
import mimetypes
from urllib.parse import parse_qs, quote

from core.handlers._fs_helpers import find_fs_service


logger = logging.getLogger("services.http_listener_service")


def _send_json(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    if getattr(handler, "_renew_cookie", ""):
        handler.send_header("Set-Cookie", handler._renew_cookie)
    handler.end_headers()
    handler.wfile.write(body)


def _one(params, name):
    values = params.get(name) or []
    return values[0].strip() if values else ""


def _content_disposition(filename):
    """Attachment header safe for any file name (RFC 6266 / RFC 5987)."""
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_"
        for ch in filename) or "download"
    return (f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def handle_relay_download(handler, session, query):
    """Stream one relay file to the client without holding it in memory."""
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

    if not handler.server.transfer_current_dispatch_to_long_lived(
            "relay download", handler.connection):
        return

    filename = relay_path.replace("\\", "/").rsplit("/", 1)[-1]
    handler.send_response(200)
    handler.send_header(
        "Content-Type",
        mimetypes.guess_type(filename)[0] or "application/octet-stream")
    handler.send_header("Content-Disposition", _content_disposition(filename))
    handler.send_header("Content-Length", str(int(entry.size)))
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
        for chunk in reader(relay_path):
            if chunk:
                handler.wfile.write(chunk)
                sent += len(chunk)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        logger.debug("Client disconnected during relay download %s",
                     relay_path)
        return
    except Exception as exc:
        logger.warning("Relay download failed after %d bytes: %s", sent, exc)
