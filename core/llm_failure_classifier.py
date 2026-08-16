"""Typed, conservative classification of LLM provider failures."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from core._llm_types import (
    AgentSuperseded,
    CCCompactDetected,
    ColdStartRequired,
    DeltaContextRequired,
    LLMCallError,
)


_MAX_SAFE_MESSAGE = 300
_SECRET_RE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token)"
    r"\s*[:=]\s*[^\s,;]+")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_error_message(value: Any) -> str:
    """Return a bounded diagnostic with common secret and URL parts removed."""
    text = _CONTROL_RE.sub("", str(value or "LLM call failed"))
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_RE.sub(lambda match: match.group(1) + "=<redacted>", text)
    words = []
    for word in text.split():
        if "://" in word:
            try:
                parsed = urlsplit(word.rstrip(".,;"))
                host = parsed.hostname or ""
                port = f":{parsed.port}" if parsed.port else ""
                word = urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))
            except Exception:
                word = "<redacted-url>"
        words.append(word)
    return " ".join(words)[:_MAX_SAFE_MESSAGE] or "LLM call failed"


def parse_retry_after(value: Any, *, now: float | None = None) -> float:
    """Parse Retry-After seconds or HTTP-date, returning zero when invalid."""
    if value is None:
        return 0.0
    text = str(value).strip()
    try:
        seconds = float(text)
        if math.isfinite(seconds) and 0 < seconds <= 86400:
            return seconds
        return 0.0
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        base = datetime.fromtimestamp(
            now if now is not None else datetime.now(tz=timezone.utc).timestamp(),
            tz=timezone.utc)
        seconds = (parsed - base).total_seconds()
        return seconds if 0 < seconds <= 86400 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def classify_http_error(status: int, *, headers: Mapping[str, Any] | None = None,
                        body: Any = "", provider: str = "", model: str = "",
                        local_timeout: bool = False) -> LLMCallError:
    """Classify an HTTP failure where status and headers are authoritative."""
    status = int(status or 0)
    normalized = {str(key).lower(): value for key, value in (headers or {}).items()}
    retry_after = parse_retry_after(normalized.get("retry-after"))
    lowered = str(body or "").lower()
    category, retryable, origin = "unknown", True, "provider"
    if local_timeout:
        category, retryable, origin = "local_timeout", True, "local"
    elif status in {401, 403}:
        category, retryable = "auth_invalid", False
    elif status == 402:
        category, retryable = "billing_exhausted", False
    elif status == 404:
        category, retryable = "model_unavailable", False
    elif status in {408, 504}:
        category = "upstream_timeout"
    elif status == 429:
        category = "quota_exhausted" if "quota" in lowered else "rate_limited"
    elif status >= 500:
        category = "provider_unavailable"
    elif 400 <= status < 500:
        if any(marker in lowered for marker in (
                "context length", "context window", "too many tokens")):
            category, retryable = "context_overflow", False
        elif "budget" in lowered and "strict" in lowered:
            category, retryable, origin = "caller_invalid", False, "policy"
        else:
            category, retryable, origin = "caller_invalid", False, "caller"
    detail = f"LLM API error {status}: {body}" if body else f"LLM API error {status}"
    return LLMCallError(
        safe_error_message(detail),
        category=category, origin=origin, provider_status=status,
        retryable=retryable, retry_after_seconds=retry_after,
        provider=provider, model=model,
        caused_by_local_timeout=local_timeout)


def is_control_flow(exc: BaseException) -> bool:
    """Return true for signals that must never affect router health."""
    from tasks.ai.agent_exceptions import AgentCancelled
    return isinstance(exc, (
        AgentCancelled, AgentSuperseded, CCCompactDetected, ColdStartRequired,
        DeltaContextRequired))


_CLI_ALLOWLISTS = {
    "claude-code": {
        "auth_invalid": ("authentication_error", "invalid api key"),
        "rate_limited": ("rate_limit_error",),
        "context_overflow": ("prompt is too long",),
    },
    "claude-code-interactive": {
        "auth_invalid": ("authentication_error",),
        "rate_limited": ("rate_limit_error",),
        "context_overflow": ("prompt is too long",),
    },
    "codex-app-server": {
        "auth_invalid": ("invalid_api_key",),
        "rate_limited": ("rate_limit_exceeded",),
        "context_overflow": ("context_length_exceeded",),
    },
    "codex-interactive": {
        "auth_invalid": ("invalid_api_key",),
        "rate_limited": ("rate_limit_exceeded",),
        "context_overflow": ("context_length_exceeded",),
    },
    "gemini": {
        "auth_invalid": ("unauthenticated",),
        "quota_exhausted": ("resource_exhausted", "quota will reset"),
        "context_overflow": ("input token count exceeds",),
    },
    "antigravity-interactive": {
        "auth_invalid": ("unauthenticated",),
        "quota_exhausted": ("resource_exhausted",),
        "context_overflow": ("input token count exceeds",),
    },
}


def classify_cli_error(provider: str, exc: BaseException, *, signal: str = "",
                       model: str = "") -> LLMCallError:
    """Classify only adapter-owned signals and narrow provider diagnostics."""
    if is_control_flow(exc):
        raise exc
    if isinstance(exc, LLMCallError):
        return exc
    signal = str(signal or "").strip().lower()
    if signal in {"process_launch", "pipe", "socket", "transport", "unexpected_exit"}:
        category, origin = "network", "adapter"
    elif signal in {"watchdog_timeout", "local_timeout"}:
        category, origin = "local_timeout", "local"
    else:
        lowered = str(exc).lower()
        category, origin = "unknown", "provider"
        for candidate, patterns in _CLI_ALLOWLISTS.get(provider, {}).items():
            if any(pattern in lowered for pattern in patterns):
                category = candidate
                break
    return LLMCallError(
        safe_error_message(exc), category=category, origin=origin,
        provider_status=0, retryable=category not in {"auth_invalid"},
        retry_after_seconds=0, provider=provider, model=model,
        caused_by_local_timeout=category == "local_timeout")
