"""Expression pipeline — chainable operations for ${...} expressions.

Syntax: ${scope.key:op1:op2("arg"):op3}
Operations are applied left-to-right (pipe pattern).
Arguments can contain nested ${...} expressions (resolved before eval).

Generators (no input value): ${:uuid}, ${:now}, ${:random_int(1,100)}
"""

import base64
import hashlib
import json
import logging
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def parse_pipeline(expr: str) -> Tuple[str, List[Tuple[str, List[str]]]]:
    """Parse 'scope.key:op1:op2("arg1","arg2"):op3' into (scope_key, operations).

    Handles:
    - Nested ${...} in arguments
    - Quoted strings with commas inside
    - :!important (returns as-is, not a pipeline op)

    Returns:
        ("scope.key", [("op1", []), ("op2", ["arg1", "arg2"]), ...])
        For generators: ("", [("uuid", []), ...])
    """
    if not expr or ":" not in expr:
        return expr, []

    # Handle :!important (already processed upstream)
    if expr.endswith(":!important"):
        return expr, []

    # Find the first ':' that separates scope.key from operations
    # But scope.key can contain '.' — the ':' after the key starts the pipeline
    # We need to find colons NOT inside parentheses or ${...}
    parts = _split_pipeline(expr)

    if len(parts) <= 1:
        return expr, []

    scope_key = parts[0]
    operations = []
    for part in parts[1:]:
        op_name, args = _parse_operation(part)
        if op_name:
            operations.append((op_name, args))

    return scope_key, operations


def _split_pipeline(expr: str) -> List[str]:
    """Split expression on ':' but respect parentheses and ${...} nesting."""
    parts = []
    current = []
    depth_paren = 0
    depth_expr = 0
    i = 0
    while i < len(expr):
        c = expr[i]
        if c == '$' and i + 1 < len(expr) and expr[i + 1] == '{':
            depth_expr += 1
            current.append(c)
        elif c == '}' and depth_expr > 0:
            depth_expr -= 1
            current.append(c)
        elif c == '(':
            depth_paren += 1
            current.append(c)
        elif c == ')':
            depth_paren -= 1
            current.append(c)
        elif c == ':' and depth_paren == 0 and depth_expr == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    if current:
        parts.append("".join(current))
    return parts


def _parse_operation(text: str) -> Tuple[str, List[str]]:
    """Parse 'op_name(arg1, arg2)' or 'op_name' into (name, [args])."""
    text = text.strip()
    if not text:
        return "", []

    paren_idx = text.find("(")
    if paren_idx < 0:
        return text, []

    op_name = text[:paren_idx].strip()
    # Extract args between ( and last )
    if not text.endswith(")"):
        return op_name, []
    args_str = text[paren_idx + 1:-1]
    args = _split_args(args_str)
    # Strip quotes from args
    cleaned = []
    for a in args:
        a = a.strip()
        if (a.startswith('"') and a.endswith('"')) or (a.startswith("'") and a.endswith("'")):
            a = a[1:-1]
        cleaned.append(a)
    return op_name, cleaned


def _split_args(args_str: str) -> List[str]:
    """Split comma-separated args respecting quotes and ${...}."""
    args = []
    current = []
    in_quote = None
    depth = 0
    for c in args_str:
        if c in ('"', "'") and depth == 0:
            if in_quote == c:
                in_quote = None
            elif in_quote is None:
                in_quote = c
            current.append(c)
        elif c == '$' and len(current) > 0 and False:
            # Don't count $ for depth
            current.append(c)
        elif c == '{' and not in_quote:
            depth += 1
            current.append(c)
        elif c == '}' and not in_quote:
            depth -= 1
            current.append(c)
        elif c == ',' and in_quote is None and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(c)
    if current:
        args.append("".join(current))
    return args


def evaluate_pipeline(value: str, operations: List[Tuple[str, List[str]]],
                       resolve_fn=None) -> str:
    """Evaluate a chain of operations on a value.

    Args:
        value: The starting value (from variable resolution)
        operations: List of (op_name, [args]) tuples
        resolve_fn: Optional function to resolve ${...} in arguments

    Returns:
        The transformed value as string.
    """
    current: Any = value

    for op_name, raw_args in operations:
        # Resolve ${...} in arguments
        args = []
        for a in raw_args:
            if resolve_fn and "${" in a:
                a = resolve_fn(a)
            args.append(a)

        current = _apply_op(current, op_name, args)

    # Final: convert to string
    if isinstance(current, bool):
        return "true" if current else "false"
    if current is None:
        return ""
    return str(current)


def _apply_op(value: Any, op: str, args: List[str]) -> Any:
    """Apply a single operation to a value."""
    s = str(value) if value is not None else ""

    # ── String operations ──────────────────────────────────────────
    if op == "upper":
        return s.upper()
    if op == "lower":
        return s.lower()
    if op == "trim":
        return s.strip()
    if op == "ltrim":
        return s.lstrip()
    if op == "rtrim":
        return s.rstrip()
    if op == "capitalize":
        return s.capitalize()
    if op == "title":
        return s.title()
    if op == "reverse":
        return s[::-1]
    if op == "length":
        return str(len(s))
    if op == "count":
        if isinstance(value, list):
            return str(len(value))
        return str(len(s))

    # ── Substring / Replace ────────────────────────────────────────
    if op == "substr":
        start = int(args[0]) if args else 0
        end = int(args[1]) if len(args) > 1 else None
        return s[start:end]
    if op == "replace":
        if len(args) >= 2:
            return s.replace(args[0], args[1])
        return s
    if op == "replace_regex":
        if len(args) >= 2:
            return re.sub(args[0], args[1], s)
        return s
    if op == "append":
        return s + (args[0] if args else "")
    if op == "prepend":
        return (args[0] if args else "") + s
    if op == "pad_left":
        width = int(args[0]) if args else 0
        char = args[1] if len(args) > 1 else " "
        return s.rjust(width, char[0]) if char else s
    if op == "pad_right":
        width = int(args[0]) if args else 0
        char = args[1] if len(args) > 1 else " "
        return s.ljust(width, char[0]) if char else s

    # ── Split / Join / Index ───────────────────────────────────────
    if op == "split":
        sep = args[0] if args else ","
        return s.split(sep)
    if op == "join":
        if isinstance(value, list):
            sep = args[0] if args else ","
            return sep.join(str(v) for v in value)
        return s
    if op == "index":
        if isinstance(value, list) and args:
            idx = int(args[0])
            return value[idx] if 0 <= idx < len(value) else ""
        return s
    if op == "first":
        if isinstance(value, list) and value:
            return value[0]
        return s
    if op == "last":
        if isinstance(value, list) and value:
            return value[-1]
        return s

    # ── Conditional ────────────────────────────────────────────────
    if op == "default":
        return s if s else (args[0] if args else "")
    if op == "equals":
        return s == args[0] if args else False
    if op == "not_equals":
        return s != args[0] if args else True
    if op == "contains":
        return (args[0] in s) if args else False
    if op == "starts_with":
        return s.startswith(args[0]) if args else False
    if op == "ends_with":
        return s.endswith(args[0]) if args else False
    if op == "matches":
        return bool(re.search(args[0], s)) if args else False
    if op == "is_empty":
        return not bool(s.strip())
    if op == "then":
        if value is True:
            return args[0] if args else ""
        return value  # pass through (not true)
    if op == "else":
        if value is False:
            return args[0] if args else ""
        return value  # pass through (not false)

    # ── Conversion / Encoding ──────────────────────────────────────
    if op == "to_int":
        try:
            return str(int(float(s)))
        except (ValueError, TypeError):
            return "0"
    if op == "to_float":
        try:
            return str(float(s))
        except (ValueError, TypeError):
            return "0.0"
    if op == "to_bool":
        return "true" if s.lower() in ("true", "1", "yes", "on") else "false"
    if op == "base64_encode":
        return base64.b64encode(s.encode()).decode()
    if op == "base64_decode":
        try:
            return base64.b64decode(s).decode()
        except Exception:
            return s
    if op == "url_encode":
        return urllib.parse.quote(s, safe="")
    if op == "url_decode":
        return urllib.parse.unquote(s)
    if op == "json_get":
        if args:
            try:
                obj = json.loads(s)
                for key in args[0].split("."):
                    obj = obj[key] if isinstance(obj, dict) else obj[int(key)]
                return str(obj)
            except Exception:
                return ""
        return s
    if op == "hash_md5":
        return hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()
    if op == "hash_sha256":
        return hashlib.sha256(s.encode()).hexdigest()

    # ── Generators (ignore input value) ────────────────────────────
    if op == "uuid":
        return str(uuid.uuid4())
    if op == "uuid_short":
        return uuid.uuid4().hex[:12]
    if op == "random_int":
        import random
        lo = int(args[0]) if args else 0
        hi = int(args[1]) if len(args) > 1 else 100
        return str(random.randint(lo, hi))  # nosec B311
    if op == "random_string":
        import random, string
        n = int(args[0]) if args else 16
        return "".join(random.choices(string.ascii_letters + string.digits, k=n))  # nosec B311
    if op == "timestamp":
        return str(int(time.time()))
    if op == "now":
        fmt = args[0] if args else "%Y-%m-%dT%H:%M:%S"
        return datetime.now().strftime(fmt)

    # ── Date operations ────────────────────────────────────────────
    if op == "format_date":
        fmt = args[0] if args else "%Y-%m-%d"
        try:
            dt = datetime.fromisoformat(s)
            return dt.strftime(fmt)
        except Exception:
            return s
    if op == "add_days":
        try:
            dt = datetime.fromisoformat(s)
            dt += timedelta(days=int(args[0]) if args else 0)
            return dt.isoformat()
        except Exception:
            return s

    # Unknown operation — return value unchanged
    logger.debug(f"[expression] Unknown operation: {op}")
    return s
