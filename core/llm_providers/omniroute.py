"""Verified OmniRoute request controls and response metadata handling.

The wire contract is pinned to OmniRoute commit
c6c134300bd9d1c7a54448de1e5d5009b7143f3f.  This module deliberately
contains only the public Chat Completions surface used by PawFlow.
"""

from __future__ import annotations

import http.client
import json
import math
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import urlparse


PINNED_UPSTREAM_COMMIT = "c6c134300bd9d1c7a54448de1e5d5009b7143f3f"
MODES = ("balanced", "fast", "quality", "cheap", "reliable", "offline")
BUDGET_FALLBACKS = ("cheapest", "strict")
_MAX_MODELS_BYTES = 1024 * 1024
_MAX_MODELS = 2000
_MAX_TEXT = 256
_CONTROL_CHARS = frozenset(chr(value) for value in range(32)) | {chr(127)}

_HEADER_FIELDS = {
    "x-omniroute-cache-hit": ("cache_hit", "bool"),
    "x-omniroute-cost-saved": ("gateway_cost_saved_usd", "float"),
    "x-omniroute-decision": ("routing_strategy", "text"),
    "x-omniroute-fallback-attempts": ("fallback_attempts", "int"),
    "x-omniroute-latency-ms": ("gateway_latency_ms", "int"),
    "x-omniroute-model": ("upstream_model", "text"),
    "x-omniroute-provider": ("upstream_provider", "text"),
    "x-omniroute-request-id": ("gateway_request_id", "text"),
    "x-omniroute-response-cost": ("gateway_cost_usd", "float"),
    "x-omniroute-tokens-in": ("gateway_tokens_in", "int"),
    "x-omniroute-tokens-out": ("gateway_tokens_out", "int"),
    "x-omniroute-version": ("gateway_version", "text"),
}


def auth_headers(api_key: str, auth_mode: str) -> Dict[str, str]:
    """Return explicit OmniRoute authentication headers."""
    mode = str(auth_mode or "").strip().lower()
    if mode == "none":
        return {}
    if mode != "bearer":
        raise ValueError(
            "omniroute_auth_mode must be 'bearer' or 'none'")
    if not api_key:
        raise ValueError("omniroute bearer auth requires api_key")
    return {"Authorization": f"Bearer {api_key}"}


def request_headers(config: Mapping[str, Any]) -> Dict[str, str]:
    """Build the allowlisted per-request routing controls."""
    headers = {"X-PawFlow-Gateway-Hop": "1"}
    mode = str(config.get("omniroute_mode", "") or "").strip().lower()
    if mode:
        if mode not in MODES:
            raise ValueError(f"Unknown OmniRoute mode '{mode}'")
        headers["X-OmniRoute-Mode"] = mode

    raw_budget = config.get("omniroute_budget_usd", 0)
    try:
        budget = float(raw_budget or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("omniroute_budget_usd must be a finite number") from exc
    if not math.isfinite(budget) or budget < 0:
        raise ValueError("omniroute_budget_usd must be finite and non-negative")
    if budget > 0:
        fallback = str(config.get("omniroute_budget_fallback", "") or "").strip().lower()
        if fallback not in BUDGET_FALLBACKS:
            raise ValueError(
                "omniroute_budget_fallback must be 'cheapest' or 'strict' "
                "when a budget is configured")
        headers["X-OmniRoute-Budget"] = format(budget, ".15g")
        headers["X-OmniRoute-Budget-Fallback"] = fallback
    return headers


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_TEXT or any(char in _CONTROL_CHARS for char in text):
        return ""
    return text


def _bounded_number(value: Any, *, integer: bool) -> Any:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > 1_000_000_000:
        return None
    return int(number) if integer else number


def _metadata_values(
    response_headers: Iterable[tuple[str, str]], sse_comments: Iterable[str]
) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for name, value in response_headers:
        lowered = str(name).strip().lower()
        if lowered in _HEADER_FIELDS:
            values[lowered] = str(value)
    for comment in sse_comments:
        raw = str(comment).strip()
        if raw.startswith(":"):
            raw = raw[1:].strip()
        name, separator, value = raw.partition("=")
        lowered = name.strip().lower()
        if separator and lowered in _HEADER_FIELDS:
            values[lowered] = value.strip()
    return values


def parse_response_metadata(
    response_headers: Iterable[tuple[str, str]] = (),
    sse_comments: Iterable[str] = (),
) -> Dict[str, Any]:
    """Return bounded, typed metadata from the exact upstream allowlist."""
    metadata: Dict[str, Any] = {"gateway": "omniroute"}
    for header, value in _metadata_values(response_headers, sse_comments).items():
        field, kind = _HEADER_FIELDS[header]
        parsed: Any
        if kind == "text":
            parsed = _safe_text(value)
            if not parsed:
                continue
        elif kind == "bool":
            lowered = str(value).strip().lower()
            if lowered not in {"true", "false"}:
                continue
            parsed = lowered == "true"
        else:
            parsed = _bounded_number(value, integer=kind == "int")
            if parsed is None:
                continue
        metadata[field] = parsed
    return metadata


def fetch_models(client: Any) -> list[Dict[str, str]]:
    """Fetch and validate a bounded OpenAI-shaped model list."""
    from core.llm_http_headers import pawflow_user_agent

    base_url = str(client.base_url or "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("omniroute base_url must be an explicit HTTP(S) URL")
    from core.llm_providers.cli_shared import request_path

    suffix = "/models" if parsed.path.rstrip("/").endswith("/v1") else "/v1/models"
    path = request_path(base_url, suffix)
    connection_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https"
        else http.client.HTTPConnection)
    kwargs = {"timeout": client.timeout}
    if parsed.scheme == "https":
        from core.relay_proxy_url import relay_proxy_ssl_context
        kwargs["context"] = relay_proxy_ssl_context(base_url)
    conn = connection_cls(parsed.hostname, parsed.port, **kwargs)
    try:
        conn.request("GET", path, headers={
            **client._openai_auth_headers(),
            "User-Agent": pawflow_user_agent(),
        })
        response = conn.getresponse()
        raw = response.read(_MAX_MODELS_BYTES + 1)
        if len(raw) > _MAX_MODELS_BYTES:
            raise ValueError("OmniRoute model list exceeds the response limit")
        if response.status >= 400:
            raise ValueError(f"OmniRoute model discovery failed with HTTP {response.status}")
        payload = json.loads(raw.decode("utf-8"))
    finally:
        conn.close()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("OmniRoute model list has an invalid OpenAI response shape")
    models = []
    for item in payload["data"][:_MAX_MODELS]:
        if not isinstance(item, dict):
            continue
        model_id = _safe_text(item.get("id"))
        if not model_id:
            continue
        model = {"id": model_id}
        owned_by = _safe_text(item.get("owned_by"))
        if owned_by:
            model["owned_by"] = owned_by
        models.append(model)
    return models

