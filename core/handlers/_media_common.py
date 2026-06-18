"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import logging
import os
from typing import Dict, Any


logger = logging.getLogger(__name__)


def _media_bytes_or_path(result: Dict[str, Any], byte_key: str, path_key: str) -> Dict[str, Any]:
    if result.get(path_key):
        return {"path": str(result[path_key])}
    return {"bytes": result[byte_key]}


def _write_media_result(resolver, destination: str, filename: str,
                        result: Dict[str, Any], byte_key: str,
                        path_key: str, content_type: str) -> Dict[str, Any]:
    payload = _media_bytes_or_path(result, byte_key, path_key)
    if "path" in payload:
        try:
            return resolver.write_file(destination, filename, payload["path"], content_type)
        finally:
            if result.get("_delete_media_path"):
                try:
                    os.unlink(payload["path"])
                except OSError:
                    pass
    return resolver.write(destination, filename, payload["bytes"], content_type)


def _resolve_explicit_media_service(service_id: str, user_id: str = "", conversation_id: str = ""):
    if not service_id:
        return None, ""
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            service_id, user_id=user_id, conv_id=conversation_id)
    except Exception as exc:
        return None, f"media service '{service_id}' failed to resolve: {exc}"
    if not svc:
        return None, f"media service '{service_id}' not found or not connected"
    return svc, ""

