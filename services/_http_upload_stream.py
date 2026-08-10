"""Bounded-memory raw HTTP uploads for FileStore and relay filesystems."""

import json
import logging
import shlex
import uuid
from urllib.parse import parse_qs

from core._file_store_stream import store_stream
from core.file_store import FileStore
from core.file_ttl import resolve_ttl_seconds
from core.handlers._fs_helpers import find_fs_service


logger = logging.getLogger("services.http_listener_service")
_CHUNK_SIZE = 1024 * 1024


def _send_json(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    if getattr(handler, "_renew_cookie", ""):
        handler.send_header("Set-Cookie", handler._renew_cookie)
    handler.end_headers()
    handler.wfile.write(body)


def _body_chunks(handler, content_length):
    remaining = content_length
    while remaining:
        chunk = handler.rfile.read(min(_CHUNK_SIZE, remaining))
        if not chunk:
            raise ConnectionError(
                f"Upload ended early with {remaining} bytes remaining")
        remaining -= len(chunk)
        yield chunk


def _one(params, name):
    values = params.get(name) or []
    return values[0].strip() if values else ""


def _stream_to_relay(user_id, conversation_id, service_name, path,
                     chunks, content_length):
    service = find_fs_service(
        user_id, service_name, conversation_id=conversation_id)
    writer = getattr(service, "write_file_stream", None) if service else None
    if not callable(writer):
        raise ValueError(
            "Selected filesystem does not support streamed uploads")

    temp_path = f"{path}.pawflow-upload-{uuid.uuid4().hex[:12]}.part"
    try:
        written = writer(temp_path, chunks, expected_size=content_length)
        command = f"mv -f -- {shlex.quote(temp_path)} {shlex.quote(path)}"
        moved = service.exec(".", command, timeout=30)
        exit_code = int(moved.get("returncode", moved.get("exit_code", 1)))
        if exit_code != 0:
            raise RuntimeError(moved.get("stderr") or "Could not publish upload")
        return written
    except Exception:
        try:
            service.delete_file(temp_path)
        except Exception:
            logger.debug("Could not remove failed upload %s", temp_path,
                         exc_info=True)
        raise


def handle_upload_stream(handler, content_length, session, query):
    """Consume one raw request body without materializing it in memory."""
    if content_length < 0:
        _send_json(handler, 400, {"ok": False, "error": "Invalid Content-Length"})
        return
    if not handler.headers.get("Content-Length"):
        _send_json(handler, 411, {"ok": False, "error": "Content-Length is required"})
        return

    user_id = ""
    if session and session is not True:
        user_id = getattr(session, "username", "") or ""
    if not user_id:
        _send_json(handler, 403, {"ok": False, "error": "Authenticated user is required"})
        return

    params = parse_qs(query, keep_blank_values=True)
    conversation_id = _one(params, "conversation_id")
    service_name = _one(params, "service")
    relay_path = _one(params, "path")
    mime = handler.headers.get("Content-Type") or "application/octet-stream"
    chunks = _body_chunks(handler, content_length)

    try:
        if service_name or relay_path:
            if not service_name or not relay_path:
                raise ValueError("service and path are both required")
            written = _stream_to_relay(
                user_id, conversation_id, service_name, relay_path,
                chunks, content_length)
            result = {
                "filename": relay_path.rsplit("/", 1)[-1],
                "mime_type": mime,
                "size": written,
                "service": service_name,
                "path": relay_path,
            }
        else:
            filename = _one(params, "filename")
            if not filename:
                raise ValueError("filename is required")
            ttl_text = _one(params, "ttl")
            try:
                ttl = int(ttl_text or "0")
            except ValueError:
                ttl = 0
            if ttl <= 0:
                ttl = resolve_ttl_seconds(
                    conversation_id=conversation_id,
                    conv_keys=("webchat_upload_ttl_seconds",
                               "attachment_ttl_seconds"),
                    env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
                    default=3600,
                )
            else:
                ttl = max(60, ttl)
            file_id = store_stream(
                FileStore.instance(), filename, chunks,
                expected_size=content_length, content_type=mime,
                user_id=user_id, conversation_id=conversation_id or "_upload",
                ttl=ttl, category="upload")
            if not file_id:
                raise RuntimeError("Conversation was deleted during upload")
            result = {
                "file_id": file_id,
                "filename": filename,
                "mime_type": mime,
                "size": content_length,
                "url": f"/files/{file_id}/{filename}",
            }
        logger.info("Stream upload: %s (%d bytes)",
                    result.get("filename"), content_length)
        _send_json(handler, 200, {"ok": True, "files": [result]})
    except Exception as exc:
        logger.warning("Stream upload failed: %s", exc)
        _send_json(handler, 400, {"ok": False, "error": str(exc)})
