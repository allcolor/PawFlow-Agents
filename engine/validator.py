"""Flow Validator - validates flow structure before execution."""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of flow validation."""
    valid: bool
    errors: List[str]
    warnings: List[str]

    def __bool__(self):
        return self.valid


class FlowValidator:
    """Validates flow JSON structure and DAG integrity."""

    def validate(self, flow_dict: Dict[str, Any]) -> ValidationResult:
        """Full validation of a flow dictionary."""
        errors = []
        warnings = []

        # Basic structure
        errors.extend(self._check_structure(flow_dict))

        # Tasks
        errors.extend(self._check_tasks(flow_dict))

        # Relations
        errors.extend(self._check_relations(flow_dict))

        # DAG
        cycle_errors = self._check_cycles(flow_dict)
        errors.extend(cycle_errors)

        # Connectivity
        warnings.extend(self._check_connectivity(flow_dict))

        # Task types
        warnings.extend(self._check_task_types(flow_dict))

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_structure(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Check basic flow structure."""
        errors = []
        if not isinstance(flow_dict, dict):
            return ["Flow must be a dictionary"]

        if "id" not in flow_dict:
            errors.append("Missing required field: 'id'")
        if "name" not in flow_dict:
            errors.append("Missing required field: 'name'")
        if "tasks" not in flow_dict:
            errors.append("Missing required field: 'tasks'")
        elif not isinstance(flow_dict["tasks"], dict):
            errors.append("'tasks' must be a dictionary")
        if "relations" in flow_dict and not isinstance(flow_dict["relations"], list):
            errors.append("'relations' must be a list")

        return errors

    def _check_tasks(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Check task definitions."""
        errors = []
        tasks = flow_dict.get("tasks", {})

        if not tasks:
            errors.append("Flow has no tasks defined")
            return errors

        for task_id, task_config in tasks.items():
            if not isinstance(task_config, dict):
                errors.append(f"Task '{task_id}' config must be a dictionary")
                continue
            if "type" not in task_config:
                errors.append(f"Task '{task_id}' missing required field: 'type'")

        return errors

    def _check_relations(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Check relation validity."""
        errors = []
        tasks = flow_dict.get("tasks", {})
        relations = flow_dict.get("relations", [])

        for i, rel in enumerate(relations):
            if not isinstance(rel, dict):
                errors.append(f"Relation {i} must be a dictionary")
                continue
            if "from" not in rel:
                errors.append(f"Relation {i} missing 'from' field")
            elif rel["from"] not in tasks:
                errors.append(f"Relation {i}: source '{rel['from']}' not found in tasks")
            if "to" not in rel:
                errors.append(f"Relation {i} missing 'to' field")
            elif rel["to"] not in tasks:
                errors.append(f"Relation {i}: target '{rel['to']}' not found in tasks")

        # Check duplicates
        seen = set()
        for rel in relations:
            key = (rel.get("from", ""), rel.get("to", ""), rel.get("type", "success"))
            if key in seen:
                errors.append(f"Duplicate relation: {key[0]} -> {key[1]} ({key[2]})")
            seen.add(key)

        return errors

    def _check_cycles(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Detect cycles in the DAG using DFS."""
        errors = []
        tasks = flow_dict.get("tasks", {})
        relations = flow_dict.get("relations", [])

        # Build adjacency list
        adj: Dict[str, List[str]] = {tid: [] for tid in tasks}
        for rel in relations:
            src = rel.get("from", "")
            tgt = rel.get("to", "")
            if src in adj:
                adj[src].append(tgt)

        # DFS cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in tasks}

        def dfs(node: str, path: List[str]) -> Optional[List[str]]:
            color[node] = GRAY
            path.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:]
                if color[neighbor] == WHITE:
                    result = dfs(neighbor, path)
                    if result:
                        return result
            path.pop()
            color[node] = BLACK
            return None

        for tid in tasks:
            if color[tid] == WHITE:
                cycle = dfs(tid, [])
                if cycle:
                    cycle_str = " -> ".join(cycle + [cycle[0]])
                    errors.append(f"Cycle detected: {cycle_str}")
                    break

        return errors

    def _check_connectivity(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Check for disconnected tasks."""
        warnings = []
        tasks = flow_dict.get("tasks", {})
        relations = flow_dict.get("relations", [])

        if len(tasks) <= 1:
            return warnings

        connected = set()
        for rel in relations:
            connected.add(rel.get("from", ""))
            connected.add(rel.get("to", ""))

        for tid in tasks:
            if tid not in connected:
                warnings.append(f"Task '{tid}' is disconnected (no relations)")

        return warnings

    def _check_task_types(self, flow_dict: Dict[str, Any]) -> List[str]:
        """Check if task types are registered."""
        warnings = []
        try:
            from core import TaskFactory
            registered = set(TaskFactory.list_types())
            for task_id, task_config in flow_dict.get("tasks", {}).items():
                task_type = task_config.get("type", "")
                if task_type and task_type not in registered:
                    warnings.append(f"Task '{task_id}': type '{task_type}' not registered")
        except ImportError:
            pass
        return warnings
