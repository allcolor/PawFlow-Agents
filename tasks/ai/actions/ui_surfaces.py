"""Authenticated generic UI-surface query actions."""

from __future__ import annotations

import json


def _handle_ui_surfaces(self, action, body, store, user_id, flowfile):
    if action != "ui_surface_list":
        return None

    def _reply(payload, status=""):
        flowfile.set_content(json.dumps(
            payload, ensure_ascii=False, default=str).encode())
        if status:
            flowfile.set_attribute("http.response.status", status)
        return [flowfile]

    if not user_id:
        return _reply({"error": "Authentication required"}, "401")
    conversation_id = str(body.get("conversation_id") or "").strip()
    if not conversation_id:
        return _reply({"error": "conversation_id is required"}, "400")
    try:
        from core.ui_surface_store import UiSurfaceStore
        return _reply({
            "surfaces": UiSurfaceStore.instance().list(
                user_id=user_id, conversation_id=conversation_id),
        })
    except ValueError as exc:
        return _reply({"error": str(exc)}, "400")


__all__ = ["_handle_ui_surfaces"]
