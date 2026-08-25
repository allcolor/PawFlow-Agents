"""Static structural validation for declarative semantic mutations."""

from __future__ import annotations

from typing import Any, Iterable

from core.flow_definition_validator import normalize_relation


def find_cycle(
    node_ids: Iterable[str], relations: Iterable[dict[str, Any]],
) -> list[str]:
    """Return one deterministic directed cycle, or an empty list."""
    graph = {str(node_id): set() for node_id in node_ids}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        normalized = normalize_relation(relation)
        source = normalized["from"]
        target = normalized["to"]
        if source in graph and target in graph:
            graph[source].add(target)

    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node_id: str) -> list[str]:
        visiting.add(node_id)
        path.append(node_id)
        for target in sorted(graph[node_id]):
            if target in visiting:
                start = path.index(target)
                return [*path[start:], target]
            if target not in visited:
                cycle = visit(target)
                if cycle:
                    return cycle
        path.pop()
        visiting.remove(node_id)
        visited.add(node_id)
        return []

    for node_id in sorted(graph):
        if node_id not in visited:
            cycle = visit(node_id)
            if cycle:
                return cycle
    return []


__all__ = ["find_cycle"]
