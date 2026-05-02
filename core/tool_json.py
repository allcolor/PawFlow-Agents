"""Shared helpers for parsing LLM-emitted tool argument JSON."""

import json
import logging
from typing import Any, Dict, Optional


PARSE_ERROR_KEY = "_pawflow_tool_arg_parse_error"
RAW_ARGUMENTS_KEY = "_pawflow_raw_tool_arguments"


logger = logging.getLogger(__name__)


def autoclose_truncated_json(s: str, max_appends: int = 4) -> str:
    """Append missing JSON closers for narrow EOF-truncation cases only."""
    stack = []
    in_string = False
    escape_next = False
    for c in s:
        if in_string:
            if escape_next:
                escape_next = False
            elif c == "\\":
                escape_next = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c in ("}", "]"):
                if stack and stack[-1] == c:
                    stack.pop()
    suffix = ""
    if in_string:
        suffix += '"'
    while stack and len(suffix) < max_appends:
        suffix += stack.pop()
    return s + suffix if suffix else s


def _error_payload(raw: Any, detail: str) -> Dict[str, Any]:
    raw_text = raw if isinstance(raw, str) else repr(raw)
    return {
        PARSE_ERROR_KEY: detail,
        RAW_ARGUMENTS_KEY: raw_text[:2000],
    }


def _json_error_window(raw: str, exc: BaseException) -> str:
    pos = getattr(exc, "pos", None)
    if not isinstance(pos, int) or pos < 0 or pos > len(raw):
        return ""
    lo = max(0, pos - 120)
    hi = min(len(raw), pos + 120)
    prefix = "..." if lo > 0 else ""
    suffix = "..." if hi < len(raw) else ""
    return f" Window around char {pos}: {prefix}{raw[lo:hi]!r}{suffix}"


def parse_tool_arguments(raw: Any, *, tool_name: str = "", provider: str = "",
                         log: Optional[logging.Logger] = None,
                         max_unwraps: int = 3) -> Dict[str, Any]:
    """Parse provider-emitted tool arguments without silently returning {}.

    Valid empty input still maps to {}. Malformed non-empty JSON returns a
    sentinel dict that the agent executor rejects before calling the tool.
    """
    _log = log or logger
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw

    value = raw
    last_error: Optional[BaseException] = None
    for _ in range(max_unwraps):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
            continue
        except json.JSONDecodeError as exc:
            last_error = exc
            if "Extra data" in str(exc):
                try:
                    value, _ = json.JSONDecoder().raw_decode(value)
                    continue
                except (json.JSONDecodeError, TypeError) as exc2:
                    last_error = exc2
            msg = str(exc)
            trunc_like = (
                "Expecting ',' delimiter" in msg
                or "Expecting property name" in msg
                or "Expecting value" in msg
                or "Unterminated string" in msg
            )
            at_end = getattr(exc, "pos", -1) >= len(value) - 4
            if trunc_like and at_end:
                patched = autoclose_truncated_json(value)
                if patched != value:
                    try:
                        appended = len(patched) - len(value)
                        value = json.loads(patched)
                        _log.warning(
                            "[%s] repaired truncated tool JSON for %s by appending %d char(s)",
                            provider or "llm", tool_name or "<unknown>", appended,
                        )
                        continue
                    except (json.JSONDecodeError, TypeError) as exc3:
                        last_error = exc3
            detail = f"{last_error or exc}.{_json_error_window(value, last_error or exc)}"
            _log.error(
                "[%s] failed to decode tool arguments for %s: %s raw=%r",
                provider or "llm", tool_name or "<unknown>", last_error or exc, value[:500],
            )
            return _error_payload(value, detail)
        except TypeError as exc:
            last_error = exc
            break

    if isinstance(value, dict):
        return value
    detail = f"expected JSON object for tool arguments, got {type(value).__name__}"
    _log.error("[%s] invalid tool arguments for %s: %s", provider or "llm", tool_name or "<unknown>", detail)
    return _error_payload(raw, detail)


def tool_argument_parse_error(arguments: Any) -> str:
    if isinstance(arguments, dict) and arguments.get(PARSE_ERROR_KEY):
        detail = arguments.get(PARSE_ERROR_KEY)
        raw = arguments.get(RAW_ARGUMENTS_KEY, "")
        suffix = f" Raw arguments: {raw!r}" if raw else ""
        return f"Error: failed to decode tool arguments. {detail}.{suffix}"
    return ""


def missing_required_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> list:
    if not isinstance(schema, dict) or not isinstance(arguments, dict):
        return []
    required = schema.get("required") or []
    if not isinstance(required, list):
        return []
    return [name for name in required if name not in arguments]
