"""Authenticated webchat actions for synchronized personal appearance."""

import json
from urllib.parse import quote

from core.appearance_store import (
    clear_conversation_preferences,
    referenced_file_ids,
    resolve_preferences,
    save_preferences,
)
from core.file_store import FileStore


_ACTIONS = {
    "appearance_get",
    "appearance_save",
    "appearance_clear_conversation",
}


def _set_result(flowfile, payload):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return [flowfile]


def _uploaded_preferences(user_id, prefs):
    candidate = dict(prefs)
    if candidate.get("source") != "upload":
        return candidate
    file_id = str(candidate.get("file_id") or "")
    metadata = FileStore.instance().get_metadata(file_id)
    if not metadata:
        raise ValueError("Appearance upload was not found")
    if metadata.get("user_id") != user_id:
        raise PermissionError("Appearance upload belongs to another user")
    if (metadata.get("conversation_id") != "_appearance"
            or metadata.get("category") != "appearance"):
        raise ValueError("File is not an appearance upload")
    mime = str(metadata.get("content_type") or "")
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise ValueError("Appearance upload must be an image or video")
    candidate["kind"] = "video" if mime.startswith("video/") else "image"
    candidate["name"] = metadata.get("filename") or ""
    candidate["url"] = (
        f"/files/{file_id}/{quote(candidate['name'], safe='')}"
    )
    return candidate


def _delete_if_unreferenced(user_id, file_id):
    if file_id and file_id not in referenced_file_ids(user_id):
        FileStore.instance().delete(file_id, user_id=user_id)


def _handle_appearance(self, action, body, store, user_id, flowfile):
    """Handle synchronized appearance actions. Returns [flowfile] or None."""
    if action not in _ACTIONS:
        return None
    if not user_id:
        return _set_result(flowfile, {"error": "Authenticated user is required"})
    conversation_id = str(body.get("conversation_id") or "")
    try:
        if action == "appearance_get":
            return _set_result(
                flowfile, resolve_preferences(user_id, conversation_id))

        if action == "appearance_clear_conversation":
            previous = resolve_preferences(
                user_id, conversation_id).get("conversation") or {}
            result = clear_conversation_preferences(user_id, conversation_id)
            _delete_if_unreferenced(user_id, previous.get("file_id"))
            return _set_result(flowfile, result)

        scope = str(body.get("scope") or "")
        previous = resolve_preferences(user_id, conversation_id)
        previous_scope = (
            previous.get("conversation") if scope == "conversation"
            else previous.get("global")
        ) or {}
        prefs = _uploaded_preferences(user_id, body.get("prefs") or {})
        result = save_preferences(
            user_id, scope, prefs, conversation_id=conversation_id)
        _delete_if_unreferenced(user_id, previous_scope.get("file_id"))
        return _set_result(flowfile, result)
    except Exception as exc:
        return _set_result(flowfile, {"error": str(exc)})
