"""Generic tree-sitter extraction and per-language config-based extractors.

Extracted from extract.py to keep files <=800 lines. Depends downward on
core.graphify._extract_base only.
"""
from __future__ import annotations
import importlib
from pathlib import Path
from core.graphify._extract_base import (
    LanguageConfig,
    _CPP_CONFIG,
    _CSHARP_CONFIG,
    _C_CONFIG,
    _JAVA_CONFIG,
    _JS_CONFIG,
    _KOTLIN_CONFIG,
    _LUA_CONFIG,
    _PHP_CONFIG,
    _PYTHON_CONFIG,
    _RUBY_CONFIG,
    _SCALA_CONFIG,
    _SWIFT_CONFIG,
    _TS_CONFIG,
    _csharp_extra_walk,
    _find_body,
    _js_extra_walk,
    _make_id,
    _read_text,
    _swift_extra_walk,
)


def _extract_generic(path: Path, config: LanguageConfig) -> dict:
    """Generic AST extractor driven by LanguageConfig."""
    try:
        mod = importlib.import_module(config.ts_module)
        from tree_sitter import Language, Parser
        lang_fn = getattr(mod, config.ts_language_fn, None)
        if lang_fn is None:
            # Fallback for PHP: try "language_php" then "language"
            lang_fn = getattr(mod, "language", None)
        if lang_fn is None:
            return {"nodes": [], "edges": [], "error": f"No language function in {config.ts_module}"}
        language = Language(lang_fn())
    except ImportError:
        return {"nodes": [], "edges": [], "error": f"{config.ts_module} not installed"}
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    try:
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = path.stem
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    function_bodies: list[tuple[str, object]] = []

    def add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({
                "id": nid,
                "label": label,
                "file_type": "code",
                "source_file": str_path,
                "source_location": f"L{line}",
            })

    def add_edge(src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", weight: float = 1.0) -> None:
        edges.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": weight,
        })

    file_nid = _make_id(stem)
    add_node(file_nid, path.name, 1)

    def walk(node, parent_class_nid: str | None = None) -> None:
        t = node.type

        # Import types
        if t in config.import_types:
            if config.import_handler:
                config.import_handler(node, source, file_nid, stem, edges, str_path)
            return

        # Class types
        if t in config.class_types:
            # Resolve class name
            name_node = node.child_by_field_name(config.name_field)
            if name_node is None:
                for child in node.children:
                    if child.type in config.name_fallback_child_types:
                        name_node = child
                        break
            if not name_node:
                return
            class_name = _read_text(name_node, source)
            class_nid = _make_id(stem, class_name)
            line = node.start_point[0] + 1
            add_node(class_nid, class_name, line)
            add_edge(file_nid, class_nid, "contains", line)

            # Python-specific: inheritance
            if config.ts_module == "tree_sitter_python":
                args = node.child_by_field_name("superclasses")
                if args:
                    for arg in args.children:
                        if arg.type == "identifier":
                            base = _read_text(arg, source)
                            base_nid = _make_id(stem, base)
                            if base_nid not in seen_ids:
                                base_nid = _make_id(base)
                                if base_nid not in seen_ids:
                                    nodes.append({
                                        "id": base_nid,
                                        "label": base,
                                        "file_type": "code",
                                        "source_file": "",
                                        "source_location": "",
                                    })
                                    seen_ids.add(base_nid)
                            add_edge(class_nid, base_nid, "inherits", line)

            # Swift-specific: conformance / inheritance
            if config.ts_module == "tree_sitter_swift":
                for child in node.children:
                    if child.type == "inheritance_specifier":
                        for sub in child.children:
                            if sub.type in ("user_type", "type_identifier"):
                                base = _read_text(sub, source)
                                base_nid = _make_id(stem, base)
                                if base_nid not in seen_ids:
                                    base_nid = _make_id(base)
                                    if base_nid not in seen_ids:
                                        nodes.append({
                                            "id": base_nid,
                                            "label": base,
                                            "file_type": "code",
                                            "source_file": "",
                                            "source_location": "",
                                        })
                                        seen_ids.add(base_nid)
                                add_edge(class_nid, base_nid, "inherits", line)

            # Find body and recurse
            body = _find_body(node, config)
            if body:
                for child in body.children:
                    walk(child, parent_class_nid=class_nid)
            return

        # Function types
        if t in config.function_types:
            # Swift deinit/subscript have no name field — resolve before generic fallback
            if t == "deinit_declaration":
                func_name: str | None = "deinit"
            elif t == "subscript_declaration":
                func_name = "subscript"
            elif config.resolve_function_name_fn is not None:
                # C/C++ style: use declarator
                declarator = node.child_by_field_name("declarator")
                func_name = None
                if declarator:
                    func_name = config.resolve_function_name_fn(declarator, source)
            else:
                name_node = node.child_by_field_name(config.name_field)
                if name_node is None:
                    for child in node.children:
                        if child.type in config.name_fallback_child_types:
                            name_node = child
                            break
                func_name = _read_text(name_node, source) if name_node else None

            if not func_name:
                return

            line = node.start_point[0] + 1
            if parent_class_nid:
                func_nid = _make_id(parent_class_nid, func_name)
                add_node(func_nid, f".{func_name}()", line)
                add_edge(parent_class_nid, func_nid, "method", line)
            else:
                func_nid = _make_id(stem, func_name)
                add_node(func_nid, f"{func_name}()", line)
                add_edge(file_nid, func_nid, "contains", line)

            body = _find_body(node, config)
            if body:
                function_bodies.append((func_nid, body))
            return

        # JS/TS arrow functions and C# namespaces — language-specific extra handling
        if config.ts_module in ("tree_sitter_javascript", "tree_sitter_typescript"):
            if _js_extra_walk(node, source, file_nid, stem, str_path,
                              nodes, edges, seen_ids, function_bodies,
                              parent_class_nid, add_node, add_edge):
                return

        if config.ts_module == "tree_sitter_c_sharp":
            if _csharp_extra_walk(node, source, file_nid, stem, str_path,
                                   nodes, edges, seen_ids, function_bodies,
                                   parent_class_nid, add_node, add_edge, walk):
                return

        if config.ts_module == "tree_sitter_swift":
            if _swift_extra_walk(node, source, file_nid, stem, str_path,
                                  nodes, edges, seen_ids, function_bodies,
                                  parent_class_nid, add_node, add_edge):
                return

        # Default: recurse
        for child in node.children:
            walk(child, parent_class_nid=None)

    walk(root)

    # ── Call-graph pass ───────────────────────────────────────────────────────
    label_to_nid: dict[str, str] = {}
    for n in nodes:
        raw = n["label"]
        normalised = raw.strip("()").lstrip(".")
        label_to_nid[normalised.lower()] = n["id"]

    seen_call_pairs: set[tuple[str, str]] = set()

    def walk_calls(node, caller_nid: str) -> None:
        if node.type in config.function_boundary_types:
            return

        if node.type in config.call_types:
            callee_name: str | None = None

            # Special handling per language
            if config.ts_module == "tree_sitter_swift":
                # Swift: first child may be simple_identifier or navigation_expression
                first = node.children[0] if node.children else None
                if first:
                    if first.type == "simple_identifier":
                        callee_name = _read_text(first, source)
                    elif first.type == "navigation_expression":
                        for child in first.children:
                            if child.type == "navigation_suffix":
                                for sc in child.children:
                                    if sc.type == "simple_identifier":
                                        callee_name = _read_text(sc, source)
            elif config.ts_module == "tree_sitter_kotlin":
                # Kotlin: first child may be simple_identifier or navigation_expression
                first = node.children[0] if node.children else None
                if first:
                    if first.type == "simple_identifier":
                        callee_name = _read_text(first, source)
                    elif first.type == "navigation_expression":
                        for child in reversed(first.children):
                            if child.type == "simple_identifier":
                                callee_name = _read_text(child, source)
                                break
            elif config.ts_module == "tree_sitter_scala":
                # Scala: first child
                first = node.children[0] if node.children else None
                if first:
                    if first.type == "identifier":
                        callee_name = _read_text(first, source)
                    elif first.type == "field_expression":
                        field = first.child_by_field_name("field")
                        if field:
                            callee_name = _read_text(field, source)
                        else:
                            for child in reversed(first.children):
                                if child.type == "identifier":
                                    callee_name = _read_text(child, source)
                                    break
            elif config.ts_module == "tree_sitter_c_sharp" and node.type == "invocation_expression":
                # C#: try name field, then first named child
                name_node = node.child_by_field_name("name")
                if name_node:
                    callee_name = _read_text(name_node, source)
                else:
                    for child in node.children:
                        if child.is_named:
                            raw = _read_text(child, source)
                            if "." in raw:
                                callee_name = raw.split(".")[-1]
                            else:
                                callee_name = raw
                            break
            elif config.ts_module == "tree_sitter_php":
                # PHP: distinguish function_call_expression vs member_call_expression
                if node.type == "function_call_expression":
                    func_node = node.child_by_field_name("function")
                    if func_node:
                        callee_name = _read_text(func_node, source)
                else:
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        callee_name = _read_text(name_node, source)
            elif config.ts_module == "tree_sitter_cpp":
                # C++: function field, then field_expression/qualified_identifier
                func_node = node.child_by_field_name(config.call_function_field) if config.call_function_field else None
                if func_node:
                    if func_node.type == "identifier":
                        callee_name = _read_text(func_node, source)
                    elif func_node.type in ("field_expression", "qualified_identifier"):
                        name = func_node.child_by_field_name("field") or func_node.child_by_field_name("name")
                        if name:
                            callee_name = _read_text(name, source)
            else:
                # Generic: get callee from call_function_field
                func_node = node.child_by_field_name(config.call_function_field) if config.call_function_field else None
                if func_node:
                    if func_node.type == "identifier":
                        callee_name = _read_text(func_node, source)
                    elif func_node.type in config.call_accessor_node_types:
                        if config.call_accessor_field:
                            attr = func_node.child_by_field_name(config.call_accessor_field)
                            if attr:
                                callee_name = _read_text(attr, source)
                    else:
                        # Try reading the node directly (e.g. Java name field is the callee)
                        callee_name = _read_text(func_node, source)

            if callee_name:
                tgt_nid = label_to_nid.get(callee_name.lower())
                if tgt_nid and tgt_nid != caller_nid:
                    pair = (caller_nid, tgt_nid)
                    if pair not in seen_call_pairs:
                        seen_call_pairs.add(pair)
                        line = node.start_point[0] + 1
                        edges.append({
                            "source": caller_nid,
                            "target": tgt_nid,
                            "relation": "calls",
                            "confidence": "INFERRED",
                            "source_file": str_path,
                            "source_location": f"L{line}",
                            "weight": 0.8,
                        })

        for child in node.children:
            walk_calls(child, caller_nid)

    for caller_nid, body_node in function_bodies:
        walk_calls(body_node, caller_nid)

    # ── Clean edges ───────────────────────────────────────────────────────────
    valid_ids = seen_ids
    clean_edges = []
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in valid_ids and (tgt in valid_ids or edge["relation"] in ("imports", "imports_from")):
            clean_edges.append(edge)

    return {"nodes": nodes, "edges": clean_edges}


# ── Python rationale extraction ───────────────────────────────────────────────

_RATIONALE_PREFIXES = ("# NOTE:", "# IMPORTANT:", "# HACK:", "# WHY:", "# RATIONALE:", "# TODO:", "# FIXME:")


def _extract_python_rationale(path: Path, result: dict) -> None:
    """Post-pass: extract docstrings and rationale comments from Python source.
    Mutates result in-place by appending to result['nodes'] and result['edges'].
    """
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser
        language = Language(tspython.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception:
        return

    stem = path.stem
    str_path = str(path)
    nodes = result["nodes"]
    edges = result["edges"]
    seen_ids = {n["id"] for n in nodes}
    file_nid = _make_id(stem)

    def _get_docstring(body_node) -> tuple[str, int] | None:
        if not body_node:
            return None
        for child in body_node.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        text = source[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                        text = text.strip("\"'").strip('"""').strip("'''").strip()
                        if len(text) > 20:
                            return text, child.start_point[0] + 1
            break
        return None

    def _add_rationale(text: str, line: int, parent_nid: str) -> None:
        label = text[:80].replace("\n", " ").strip()
        rid = _make_id(stem, "rationale", str(line))
        if rid not in seen_ids:
            seen_ids.add(rid)
            nodes.append({
                "id": rid,
                "label": label,
                "file_type": "rationale",
                "source_file": str_path,
                "source_location": f"L{line}",
            })
        edges.append({
            "source": rid,
            "target": parent_nid,
            "relation": "rationale_for",
            "confidence": "EXTRACTED",
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": 1.0,
        })

    # Module-level docstring
    ds = _get_docstring(root)
    if ds:
        _add_rationale(ds[0], ds[1], file_nid)

    # Class and function docstrings
    def walk_docstrings(node, parent_nid: str) -> None:
        t = node.type
        if t == "class_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                class_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                nid = _make_id(stem, class_name)
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
                for child in body.children:
                    walk_docstrings(child, nid)
            return
        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                func_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                nid = _make_id(parent_nid, func_name) if parent_nid != file_nid else _make_id(stem, func_name)
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
            return
        for child in node.children:
            walk_docstrings(child, parent_nid)

    walk_docstrings(root, file_nid)

    # Rationale comments (# NOTE:, # IMPORTANT:, etc.)
    source_text = source.decode("utf-8", errors="replace")
    for lineno, line_text in enumerate(source_text.splitlines(), start=1):
        stripped = line_text.strip()
        if any(stripped.startswith(p) for p in _RATIONALE_PREFIXES):
            _add_rationale(stripped, lineno, file_nid)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_python(path: Path) -> dict:
    """Extract classes, functions, and imports from a .py file via tree-sitter AST."""
    result = _extract_generic(path, _PYTHON_CONFIG)
    if "error" not in result:
        _extract_python_rationale(path, result)
    return result


def extract_js(path: Path) -> dict:
    """Extract classes, functions, arrow functions, and imports from a .js/.ts/.tsx file."""
    config = _TS_CONFIG if path.suffix in (".ts", ".tsx") else _JS_CONFIG
    return _extract_generic(path, config)


def extract_java(path: Path) -> dict:
    """Extract classes, interfaces, methods, constructors, and imports from a .java file."""
    return _extract_generic(path, _JAVA_CONFIG)


def extract_c(path: Path) -> dict:
    """Extract functions and includes from a .c/.h file."""
    return _extract_generic(path, _C_CONFIG)


def extract_cpp(path: Path) -> dict:
    """Extract functions, classes, and includes from a .cpp/.cc/.cxx/.hpp file."""
    return _extract_generic(path, _CPP_CONFIG)


def extract_ruby(path: Path) -> dict:
    """Extract classes, methods, singleton methods, and calls from a .rb file."""
    return _extract_generic(path, _RUBY_CONFIG)


def extract_csharp(path: Path) -> dict:
    """Extract classes, interfaces, methods, namespaces, and usings from a .cs file."""
    return _extract_generic(path, _CSHARP_CONFIG)


def extract_kotlin(path: Path) -> dict:
    """Extract classes, objects, functions, and imports from a .kt/.kts file."""
    return _extract_generic(path, _KOTLIN_CONFIG)


def extract_scala(path: Path) -> dict:
    """Extract classes, objects, functions, and imports from a .scala file."""
    return _extract_generic(path, _SCALA_CONFIG)


def extract_php(path: Path) -> dict:
    """Extract classes, functions, methods, namespace uses, and calls from a .php file."""
    return _extract_generic(path, _PHP_CONFIG)


def extract_lua(path: Path) -> dict:
    """Extract functions, methods, require() imports, and calls from a .lua file."""
    return _extract_generic(path, _LUA_CONFIG)


def extract_swift(path: Path) -> dict:
    """Extract classes, structs, protocols, functions, imports, and calls from a .swift file."""
    return _extract_generic(path, _SWIFT_CONFIG)


# ── Go extractor (custom walk) ────────────────────────────────────────────────

