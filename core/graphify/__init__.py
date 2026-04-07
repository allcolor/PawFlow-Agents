"""Graphify integration — AST-based code structure extraction.

Integrated from graphify (https://github.com/safishamsi/graphify).
Supports 17 languages via tree-sitter AST parsing.

Key functions:
  detect(root)          — discover files, classify, corpus health check
  extract(paths)        — AST extraction (classes, functions, imports, calls)
  build(extractions)    — assemble into NetworkX graph
  collect_files(target) — list all code files in a directory
"""

from core.graphify.detect import detect, classify_file  # noqa: F401
from core.graphify.extract import extract, collect_files  # noqa: F401
from core.graphify.build import build, build_from_json  # noqa: F401
from core.graphify.validate import validate_extraction  # noqa: F401
