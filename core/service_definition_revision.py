"""Canonical, secret-free revisions for persisted service definitions."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from core import ServiceFactory


SECRET_MARKER = "<sensitive>"


def service_sensitive_keys(service_type: str) -> set[str]:
    """Return config fields marked sensitive by the registered service schema."""
    try:
        service_cls = ServiceFactory.get(str(service_type or ""))
    except Exception:
        try:
            from tasks import _register_all_services
            _register_all_services()
            service_cls = ServiceFactory.get(str(service_type or ""))
        except Exception:
            return set()
    try:
        schema = service_cls.get_parameter_schema(service_cls)
    except Exception:
        return set()
    return {
        str(key) for key, spec in schema.items()
        if isinstance(spec, Mapping) and spec.get("sensitive")}


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Service definition contains a non-finite value")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key in sorted(value, key=lambda item: str(item)):
            key = str(key)
            if key.startswith("_"):
                continue
            result[key] = _canonical(value[key])
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise ValueError(
        f"Unsupported service definition value: {type(value).__name__}")


def compute_service_definition_revision(service_def: Any) -> str:
    """Return a stable SHA-256 over material, non-secret service state."""
    service_type = str(getattr(service_def, "service_type", "") or "")
    created_at = getattr(service_def, "created_at", None)
    try:
        created_at = float(created_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("Service definition requires a valid created_at") from exc
    if not math.isfinite(created_at) or created_at <= 0:
        raise ValueError("Service definition requires a valid created_at")
    config = dict(getattr(service_def, "config", {}) or {})
    for key in service_sensitive_keys(service_type):
        if key in config:
            config[key] = SECRET_MARKER
    payload = _canonical({
        "service_type": service_type,
        "created_at": created_at,
        "enabled": bool(getattr(service_def, "enabled", True)),
        "config": config,
    })
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
