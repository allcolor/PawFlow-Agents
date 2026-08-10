"""Streaming FileStore writes kept separate from the legacy byte API."""

import os
import time
import uuid
from pathlib import Path

from core.file_store import ACCESS_PRIVATE


def store_stream(store, filename, chunks, *, expected_size,
                 content_type="application/octet-stream",
                 conversation_id="", user_id="", ttl=0,
                 agent_name="", category=""):
    """Store an iterable of byte chunks with bounded memory and atomic publish."""
    if not user_id:
        raise ValueError("FileStore stream: user_id is required")
    if not conversation_id:
        raise ValueError("FileStore stream: conversation_id is required")
    if expected_size < 0:
        raise ValueError("FileStore stream: expected_size must be non-negative")

    file_id = uuid.uuid4().hex[:12]
    safe_name = Path(filename).name or "file"
    wipes = store._wipe_count(conversation_id)
    user_id, bucket, file_path = store._reserve_scope(
        conversation_id, user_id, f"{file_id}_{safe_name}")
    temp_path = file_path.with_name(
        f".{file_path.name}.{uuid.uuid4().hex[:12]}.part")
    size = 0
    try:
        with temp_path.open("xb") as output:
            for chunk in chunks:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("FileStore stream chunks must be bytes")
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("FileStore stream exceeded Content-Length")
                output.write(chunk)
        if size != expected_size:
            raise ValueError(
                f"FileStore stream size mismatch: {size} != {expected_size}")
        os.replace(temp_path, file_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        if store._wipe_count(conversation_id) != wipes:
            return ""
        raise

    with store._store_lock:
        if store._abandon_if_wiped(conversation_id, wipes, file_path):
            return ""
        user_id, file_path = store._settle_scope(
            conversation_id, user_id, bucket, file_path)
        store._entries[file_id] = {
            "filename": safe_name,
            "path": str(file_path),
            "content_type": content_type,
            "size": size,
            "created_at": time.time(),
            "conversation_id": conversation_id,
            "user_id": user_id,
            "access": ACCESS_PRIVATE,
            "shared_with": [],
            "ttl": ttl,
            "agent_name": agent_name,
            "category": category,
        }
        store._save_index()
    return file_id
