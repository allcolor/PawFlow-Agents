"""Static + sandbox validation for dynamic tool source code.

Extracted from the former DynamicToolStore so the same checks gate every
create path: REST install, LLM `create_tool`, manage_resource(type='tool').
"""

import ast
import datetime as _dt
import math as _math
from typing import Any, Dict, List, Tuple

from core.tool_handler import ToolHandler


_SAFE_MODULES = frozenset({
    "datetime", "math", "re", "json", "time",
    "collections", "itertools", "functools", "statistics",
    "decimal", "fractions", "random", "string", "textwrap",
    "urllib", "urllib.parse", "urllib.request", "urllib.error",
    "http", "http.client", "http.cookiejar",
    "hashlib", "base64", "uuid",
    "numpy", "csv", "io", "operator", "copy",
    "struct", "html", "xml", "xml.etree", "xml.etree.ElementTree",
    "requests",
})

_SAFE_PREFIXES = (
    "requests.", "urllib.", "http.", "xml.", "collections.",
    "html.", "numpy.",
)

_FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "open",
    "globals", "locals", "breakpoint", "exit", "quit",
})

_FORBIDDEN_ATTRS = frozenset({
    "__subclasses__", "__globals__", "__builtins__", "__code__",
    "__class__", "__bases__", "__mro__",
})


def validate_source(source: str) -> List[str]:
    """Static AST analysis. Returns a list of violations (empty = OK)."""
    violations: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                full = alias.name
                if (full not in _SAFE_MODULES
                        and mod not in _SAFE_MODULES
                        and not any(full.startswith(p) for p in _SAFE_PREFIXES)):
                    violations.append(f"Forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            full = node.module or ""
            if (full not in _SAFE_MODULES
                    and mod not in _SAFE_MODULES
                    and not any(full.startswith(p) for p in _SAFE_PREFIXES)):
                violations.append(f"Forbidden import: from {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES:
                violations.append(f"Forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS:
                violations.append(f"Forbidden attribute: {node.attr}")
    return violations


def sandbox_load(source: str) -> Tuple[ToolHandler, str]:
    """Exec source in a restricted namespace, return (instance, name).

    Raises ValueError on any failure (forbidden import at runtime,
    no ToolHandler subclass found, invalid handler properties).
    """
    def _safe_import(name, *args, **kwargs):
        if (name not in _SAFE_MODULES
                and not any(name.startswith(p) for p in _SAFE_PREFIXES)):
            raise ImportError(f"Module '{name}' is not allowed")
        return __import__(name, *args, **kwargs)

    safe_builtins: Dict[str, Any] = {
        "abs": abs, "all": all, "any": any, "bool": bool,
        "dict": dict, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "list": list, "max": max,
        "min": min, "range": range, "round": round, "set": set,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
        "type": type, "zip": zip, "True": True, "False": False,
        "None": None, "isinstance": isinstance, "map": map,
        "filter": filter, "reversed": reversed,
        "__import__": _safe_import,
        "__build_class__": __build_class__, "__name__": "__dynamic_tool__",
        "print": print,
        "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
        "property": property, "staticmethod": staticmethod,
        "classmethod": classmethod, "super": super,
        "issubclass": issubclass, "callable": callable,
        "iter": iter, "next": next, "repr": repr, "hash": hash,
        "id": id, "frozenset": frozenset, "bytes": bytes,
        "bytearray": bytearray, "complex": complex,
        "chr": chr, "ord": ord, "hex": hex, "oct": oct, "bin": bin,
        "format": format,
        "ValueError": ValueError, "TypeError": TypeError,
        "KeyError": KeyError, "IndexError": IndexError,
        "AttributeError": AttributeError, "RuntimeError": RuntimeError,
        "StopIteration": StopIteration, "Exception": Exception,
        "NotImplementedError": NotImplementedError,
        "datetime": _dt, "math": _math,
        "ToolHandler": ToolHandler,
    }

    namespace: Dict[str, Any] = {}
    try:
        exec(source, {"__builtins__": safe_builtins}, namespace)
    except Exception as e:
        raise ValueError(f"Failed to execute tool source: {e}")

    handler_class = None
    for obj in namespace.values():
        if (isinstance(obj, type)
                and issubclass(obj, ToolHandler)
                and obj is not ToolHandler):
            handler_class = obj
            break

    if handler_class is None:
        raise ValueError(
            "No ToolHandler subclass found. Your file must define a class "
            "that inherits from ToolHandler with name, description, "
            "parameters_schema properties and an execute() method."
        )

    try:
        handler = handler_class()
        name = handler.name
        desc = handler.description
        schema = handler.parameters_schema
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        if not desc:
            raise ValueError("Tool description must be non-empty")
        if not isinstance(schema, dict):
            raise ValueError("parameters_schema must be a dict")
    except Exception as e:
        raise ValueError(f"Invalid ToolHandler: {e}")

    return handler, name


def validate_and_load(source: str) -> Tuple[ToolHandler, str]:
    """Run static + sandbox validation in one shot.

    Raises ValueError if either step fails.
    """
    violations = validate_source(source)
    if violations:
        raise ValueError(
            "Security validation failed:\n"
            + "\n".join(f"- {v}" for v in violations))
    return sandbox_load(source)
