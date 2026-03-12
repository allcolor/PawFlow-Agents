"""Flow diff — compare two flow configurations."""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class DiffEntry:
    """A single difference between two flows."""
    category: str       # "task", "relation", "parameter", "service", "metadata"
    change_type: str    # "added", "removed", "modified"
    path: str           # e.g. "tasks.log1.parameters.message"
    old_value: Any = None
    new_value: Any = None
    description: str = ""


class FlowDiff:
    """Compare two flow configurations and produce a structured diff.

    Usage:
        diff = FlowDiff.compare(old_flow_dict, new_flow_dict)
        for entry in diff.entries:
            print(f"{entry.change_type}: {entry.path}")
    """

    def __init__(self, entries: List[DiffEntry] = None):
        self.entries = entries or []

    @classmethod
    def compare(cls, old: Dict, new: Dict) -> 'FlowDiff':
        """Compare two flow dicts and return differences."""
        entries = []

        # Metadata changes
        for key in ['name', 'version', 'description', 'author']:
            old_val = old.get(key, '')
            new_val = new.get(key, '')
            if old_val != new_val:
                entries.append(DiffEntry(
                    category="metadata", change_type="modified",
                    path=key, old_value=old_val, new_value=new_val,
                    description=f"Changed {key}",
                ))

        # Task changes
        old_tasks = old.get('tasks', {})
        new_tasks = new.get('tasks', {})

        for tid in set(old_tasks) | set(new_tasks):
            if tid not in old_tasks:
                entries.append(DiffEntry(
                    category="task", change_type="added",
                    path=f"tasks.{tid}", new_value=new_tasks[tid],
                    description=f"Added task '{tid}' ({new_tasks[tid].get('type', '?')})",
                ))
            elif tid not in new_tasks:
                entries.append(DiffEntry(
                    category="task", change_type="removed",
                    path=f"tasks.{tid}", old_value=old_tasks[tid],
                    description=f"Removed task '{tid}' ({old_tasks[tid].get('type', '?')})",
                ))
            else:
                # Compare task configs
                task_diffs = cls._compare_dicts(
                    old_tasks[tid], new_tasks[tid], f"tasks.{tid}"
                )
                for d in task_diffs:
                    d.category = "task"
                    entries.append(d)

        # Relation changes
        old_rels = set(cls._rel_key(r) for r in old.get('relations', []))
        new_rels = set(cls._rel_key(r) for r in new.get('relations', []))

        for rel in new_rels - old_rels:
            entries.append(DiffEntry(
                category="relation", change_type="added",
                path=f"relations.{rel}", new_value=rel,
                description=f"Added connection {rel}",
            ))
        for rel in old_rels - new_rels:
            entries.append(DiffEntry(
                category="relation", change_type="removed",
                path=f"relations.{rel}", old_value=rel,
                description=f"Removed connection {rel}",
            ))

        # Parameter changes
        old_params = old.get('parameters', {})
        new_params = new.get('parameters', {})
        for key in set(old_params) | set(new_params):
            if key not in old_params:
                entries.append(DiffEntry(
                    category="parameter", change_type="added",
                    path=f"parameters.{key}", new_value=new_params[key],
                    description=f"Added parameter '{key}'",
                ))
            elif key not in new_params:
                entries.append(DiffEntry(
                    category="parameter", change_type="removed",
                    path=f"parameters.{key}", old_value=old_params[key],
                    description=f"Removed parameter '{key}'",
                ))
            elif old_params[key] != new_params[key]:
                entries.append(DiffEntry(
                    category="parameter", change_type="modified",
                    path=f"parameters.{key}",
                    old_value=old_params[key], new_value=new_params[key],
                    description=f"Changed parameter '{key}'",
                ))

        return cls(entries)

    @staticmethod
    def _rel_key(rel: Dict) -> str:
        return f"{rel.get('from', '?')} -> {rel.get('to', '?')} [{rel.get('type', 'success')}]"

    @classmethod
    def _compare_dicts(cls, old: Dict, new: Dict, prefix: str) -> List[DiffEntry]:
        """Deep compare two dicts."""
        entries = []
        all_keys = set(old.keys()) | set(new.keys())
        for key in all_keys:
            path = f"{prefix}.{key}"
            if key not in old:
                entries.append(DiffEntry(
                    change_type="added", path=path, new_value=new[key],
                    description=f"Added {path}", category="",
                ))
            elif key not in new:
                entries.append(DiffEntry(
                    change_type="removed", path=path, old_value=old[key],
                    description=f"Removed {path}", category="",
                ))
            elif old[key] != new[key]:
                if isinstance(old[key], dict) and isinstance(new[key], dict):
                    entries.extend(cls._compare_dicts(old[key], new[key], path))
                else:
                    entries.append(DiffEntry(
                        change_type="modified", path=path,
                        old_value=old[key], new_value=new[key],
                        description=f"Changed {path}", category="",
                    ))
        return entries

    @property
    def has_changes(self) -> bool:
        return len(self.entries) > 0

    @property
    def summary(self) -> Dict[str, int]:
        """Count changes by type."""
        counts = {"added": 0, "removed": 0, "modified": 0}
        for e in self.entries:
            counts[e.change_type] = counts.get(e.change_type, 0) + 1
        return counts

    def filter(self, category: str = None, change_type: str = None) -> List[DiffEntry]:
        """Filter diff entries."""
        result = self.entries
        if category:
            result = [e for e in result if e.category == category]
        if change_type:
            result = [e for e in result if e.change_type == change_type]
        return result

    def to_dict(self) -> Dict:
        """Serialize to dict."""
        return {
            "summary": self.summary,
            "has_changes": self.has_changes,
            "total_changes": len(self.entries),
            "entries": [
                {
                    "category": e.category,
                    "change_type": e.change_type,
                    "path": e.path,
                    "old_value": e.old_value,
                    "new_value": e.new_value,
                    "description": e.description,
                }
                for e in self.entries
            ],
        }
