"""Structured, secret-redacted events emitted by installer operations."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pawflow_installer.models import utc_now

_SENSITIVE_KEY = re.compile(
    r"(?:password|passphrase|secret|token|cookie|gateway[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|key|secret|code|password)=)[^&#\s]+"
)
_LABELED_SECRET = re.compile(
    r"(?i)((?:private\s+gateway\s+key|password|passphrase|session\s+token)"
    r"\s*:\s*)\S+"
)


def redact(value: Any, key: str = "") -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _QUERY_SECRET.sub(r"\1[REDACTED]", value)
        return _LABELED_SECRET.sub(r"\1[REDACTED]", value)
    return value


@dataclass(frozen=True)
class InstallEvent:
    operation_id: str
    step_id: str
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "created_at": self.created_at,
            "operation_id": self.operation_id,
            "step_id": self.step_id,
            "kind": self.kind,
            "message": redact(self.message),
            "data": redact(self.data),
        }
