"""Shared helpers for parsing LLM-emitted tool argument JSON."""

import json
import logging
import re
from typing import Any, Dict, Optional


PARSE_ERROR_KEY = "_pawflow_tool_arg_parse_error"
RAW_ARGUMENTS_KEY = "_pawflow_raw_tool_arguments"


logger = logging.getLogger(__name__)


class ToolArgumentError(ValueError):
    """Raised when tool arguments cannot be decoded at all.

    Handlers run behind ToolRegistry.execute, which already rejects the
    sentinel before dispatch — but a handler that decodes again on its own
    has no way to return the sentinel: its caller expects a result string.
    Raising carries the diagnostic (position window included) instead of
    degrading to empty arguments, which used to surface as a confusing
    "missing required argument" one layer further down.
    """
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


# Characters that form a valid JSON escape when they follow a backslash.
_JSON_VALID_ESCAPES = frozenset('"' + chr(92) + "/bfnrtu")


def repair_invalid_json_escapes(s: str) -> str:
    """Last-resort repair for JSON an LLM nearly got right.

    Call ONLY after a strict json.loads has already failed: this is a
    fallback, never a pre-processor. It returns the input unchanged when
    there is nothing to fix, so a valid payload is never altered. Two
    narrow, common mistakes are repaired, both only inside string
    literals: an invalid backslash escape (a backslash before a single
    quote becomes a bare single quote; any other invalid backslash is
    treated as a literal backslash; a lone trailing backslash is dropped),
    and a raw control character (newline/tab/etc.) is replaced by its JSON
    escape. Anything outside string literals is left untouched.
    """
    bs = chr(92)
    ctrl = {chr(10): bs + "n", chr(9): bs + "t", chr(13): bs + "r",
            chr(8): bs + "b", chr(12): bs + "f"}
    out = []
    in_string = False
    changed = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
            i += 1
            continue
        if c == '"':
            out.append(c)
            in_string = False
            i += 1
            continue
        if c == bs:
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in _JSON_VALID_ESCAPES:
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            if nxt == "'":
                out.append("'")
                changed = True
                i += 2
                continue
            if nxt == "":
                changed = True
                i += 1
                continue
            out.append(bs + bs)
            changed = True
            i += 1
            continue
        if ord(c) < 0x20:
            out.append(ctrl.get(c, bs + "u%04x" % ord(c)))
            changed = True
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out) if changed else s


def repair_bare_quotes(s: str) -> str:
    """Repair JSON whose string values contain unescaped double quotes.

    The common LLM mistake is a shell command embedding its own quoting,
    e.g. {"command": "cd /workspace && grep -n "pattern" file"} — the
    inner quotes terminate the string early and the whole payload fails
    with "Expecting ',' delimiter". Every plausible (opening, closing)
    quote pair is tried with the quotes between them escaped, and the
    first variant that parses is returned. Runs ONLY after strict parsing,
    truncation autoclose and escape repair have all failed, so a valid
    payload is never altered.
    """
    n = len(s)
    quotes = [i for i, c in enumerate(s) if c == '"']
    if len(quotes) < 2:
        return s
    # A plausible opener sits right after '{', '[', ',', ':' (spaces ok).
    openers = []
    for i in quotes:
        j = i - 1
        while j >= 0 and s[j] in " \t\r\n":
            j -= 1
        if j >= 0 and s[j] in "{[,:":
            openers.append(i)
    # A plausible closer is followed (spaces ok) by '}', ']', ',', ':' or EOF.
    closers = []
    for i in quotes:
        j = i + 1
        while j < n and s[j] in " \t\r\n":
            j += 1
        if j >= n or s[j] in "}],:":
            closers.append(i)
    for o in openers:
        for cpos in closers:
            if cpos <= o:
                continue
            out = [s[:o], '"']
            for k in range(o + 1, cpos):
                out.append('\\"' if s[k] == '"' else s[k])
            out.append('"')
            out.append(s[cpos + 1:])
            candidate = "".join(out)
            if candidate == s:
                continue
            try:
                json.loads(candidate)
            except (ValueError, TypeError):
                continue
            return candidate
    return s


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
            # A truncated value: the error is near EOF, OR it is an unterminated
            # string (Python reports its position at the string's opening quote,
            # which can be far from EOF, yet it is still an EOF truncation).
            at_end = (getattr(exc, "pos", -1) >= len(value) - 4
                      or "Unterminated string" in msg)
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
            # Last resort: repair near-valid JSON (invalid \escape, raw
            # control chars). Only runs because strict parsing already
            # failed; returns the input unchanged for genuinely-valid JSON,
            # so a correct call is never rewritten.
            repaired = repair_invalid_json_escapes(value)
            if repaired != value:
                try:
                    value = json.loads(repaired)
                    _log.warning(
                        "[%s] repaired near-valid tool JSON for %s "
                        "(invalid escapes / control chars)",
                        provider or "llm", tool_name or "<unknown>",
                    )
                    continue
                except (json.JSONDecodeError, TypeError) as exc4:
                    last_error = exc4
            # Bare double quotes inside string values (shell commands
            # embedding their own quoting, e.g. grep -n "pattern"). Only
            # runs after strict parse, autoclose and escape repair failed.
            # Starts from `repaired` so a mix of invalid \' escapes and
            # bare quotes is fixed in sequence, not left half-broken.
            requoted = repair_bare_quotes(repaired)
            if requoted != repaired:
                try:
                    value = json.loads(requoted)
                    _log.warning(
                        "[%s] repaired unescaped quotes in tool JSON for %s",
                        provider or "llm", tool_name or "<unknown>",
                    )
                    continue
                except (json.JSONDecodeError, TypeError) as exc5:
                    last_error = exc5
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


# ── Schema coercion ──────────────────────────────────────────────────
#
# parse_tool_arguments repairs the argument BLOB: it decides whether the
# text is JSON at all. Nothing repaired an individual FIELD, so a value of
# the wrong declared type travelled untouched to the handler, which then
# improvised (bool("false") is True, a bare int(raw) that throws, ...).
#
# Deliberately narrow. Only unambiguous, reversible fixes are applied:
# a value that already matches its declared type is never touched, and a
# shape that would require guessing is refused with a message naming the
# field instead of being reinterpreted. In particular a BARE string is
# never split or wrapped into an array - handlers own that decision
# (core/handlers/_arg_normalize.py), and guessing here would silently
# change what `tags="a,b"` means.

_MAX_COERCE_DEPTH = 32
_MAX_COERCE_NODES = 10000
_INT_RE = re.compile(r"[+-]?[0-9]+\Z")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches(value: Any, expected: str) -> bool:
    """Whether a value already satisfies its declared JSON type.

    `True` is an int in Python but never a valid integer/number argument:
    accepting it would let a boolean silently become 1.
    """
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return _json_type(value) == expected


def _within_bounds(value: Any, depth: int = 0) -> bool:
    """Reject structures deep or large enough to be a decoding accident."""
    nodes = [0]

    def walk(node, level):
        if level > _MAX_COERCE_DEPTH:
            return False
        nodes[0] += 1
        if nodes[0] > _MAX_COERCE_NODES:
            return False
        if isinstance(node, dict):
            return all(walk(v, level + 1) for v in node.values())
        if isinstance(node, list):
            return all(walk(v, level + 1) for v in node)
        return True

    return walk(value, depth)


def _coerce_value(key: str, value: Any, expected: str, spec: Dict[str, Any]):
    """Return (new_value, repair_note, error). Exactly one of note/error is set."""
    # A JSON-encoded structure sent as text. Mechanically unambiguous:
    # it either decodes to the declared type or it does not.
    if expected in ("object", "array") and isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError) as exc:
                return None, "", (
                    f"'{key}' looks like JSON text but does not parse ({exc}). "
                    f"Send the {expected} itself, not a string containing it.")
            if not _matches(parsed, expected):
                return None, "", (
                    f"'{key}' decoded to {_json_type(parsed)} but the schema "
                    f"expects {expected}.")
            if not _within_bounds(parsed):
                return None, "", (
                    f"'{key}' decoded to a structure too deep or too large "
                    "to be intentional.")
            return parsed, f"{key}: decoded JSON string to {expected}", ""
        return None, "", (
            f"'{key}' must be {'an' if expected == 'object' else 'a'} "
            f"{expected}, got a string. Send the {expected} itself, not a "
            "string containing it.")

    if expected == "boolean" and isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1"):
            return True, f"{key}: string to boolean true", ""
        if text in ("false", "0"):
            return False, f"{key}: string to boolean false", ""
        return None, "", (
            f"'{key}' must be a boolean (true or false), got {value!r}.")

    if expected == "integer":
        if isinstance(value, str) and _INT_RE.match(value.strip()):
            return int(value.strip()), f"{key}: string to integer", ""
        if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
            return int(value), f"{key}: whole number to integer", ""
        return None, "", (
            f"'{key}' must be an integer, got {_json_type(value)} "
            f"({value!r}).")

    if expected == "number" and isinstance(value, str):
        try:
            number = float(value.strip())
        except (ValueError, TypeError):
            return None, "", f"'{key}' must be a number, got {value!r}."
        if number != number or number in (float("inf"), float("-inf")):
            return None, "", f"'{key}' must be a finite number, got {value!r}."
        return number, f"{key}: string to number", ""

    return None, "", (
        f"'{key}' must be {expected}, got {_json_type(value)}.")


def coerce_to_schema(arguments: Dict[str, Any], schema: Dict[str, Any],
                     tool_name: str = ""):
    """Align argument values with their declared types.

    Returns (arguments, repairs, error). `arguments` is returned unchanged
    - the same object, not a copy - when nothing needed fixing. Keys absent
    from the schema are left alone: the registry rejects them separately
    with a list of valid names.
    """
    if not isinstance(arguments, dict) or not isinstance(schema, dict):
        return arguments, [], ""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return arguments, [], ""
    required = schema.get("required")
    required = set(required) if isinstance(required, list) else set()

    where = f" for tool '{tool_name}'" if tool_name else ""
    out = arguments
    repairs = []

    for key in list(arguments):
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if not isinstance(expected, str) or not expected:
            continue  # untyped or union type: not ours to interpret
        value = arguments[key]

        if value is None:
            if key in required:
                return arguments, repairs, (
                    f"Error: '{key}' is required{where} and cannot be null.")
            if out is arguments:
                out = dict(arguments)
            out.pop(key, None)
            repairs.append(f"{key}: dropped null optional")
            continue

        if _matches(value, expected):
            continue

        new_value, note, error = _coerce_value(key, value, expected, spec)
        if error:
            return arguments, repairs, f"Error{where}: {error}"
        if out is arguments:
            out = dict(arguments)
        out[key] = new_value
        repairs.append(note)

    return out, repairs, ""
