"""Process Group - container for grouping tasks into reusable sub-flows."""

import json
import uuid
from typing import Dict, Any, List, Optional


class ProcessGroup:
    """A named group of tasks and relations that acts as a sub-flow.

    Process groups can contain:
    - Tasks (processors)
    - Relations (connections between tasks)
    - Input/Output ports for interfacing with the parent flow
    - Nested process groups
    - Variables (scoped to this group)

    A ProcessGroup with a non-null ``flow_ref`` is a **sub-flow**: its tasks
    and relations are loaded from an external flow JSON file.
    """

    def __init__(self, group_id: Optional[str] = None, name: str = "Process Group",
                 description: str = "", color: str = "#4285f4",
                 collapsed: bool = False,
                 flow_ref: Optional[Dict[str, str]] = None):
        self.id = group_id or str(uuid.uuid4())[:8]
        self.name = name
        self.description = description
        self.color = color
        self.collapsed = collapsed
        self.flow_ref = flow_ref  # {"path": "flows/x.json", "version": "1.0.0"} or None
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.relations: List[Dict[str, str]] = []
        self.variables: Dict[str, str] = {}
        self.input_ports: List[str] = []
        self.output_ports: List[str] = []
        self.child_groups: Dict[str, "ProcessGroup"] = {}

    # -- Properties --

    @property
    def is_subflow(self) -> bool:
        """True if this group references an external flow file."""
        return self.flow_ref is not None

    # -- Task management --

    def add_task(self, task_id: str, task_type: str, parameters: Optional[Dict] = None):
        """Add a task to this group."""
        self.tasks[task_id] = {
            "type": task_type,
            "parameters": parameters or {},
            "group_id": self.id,
        }

    def remove_task(self, task_id: str):
        """Remove a task and its connections."""
        self.tasks.pop(task_id, None)
        self.relations = [
            r for r in self.relations
            if r["from"] != task_id and r["to"] != task_id
        ]

    def add_relation(self, from_id: str, to_id: str, rel_type: str = "success"):
        """Add a connection between two tasks."""
        self.relations.append({
            "from": from_id,
            "to": to_id,
            "type": rel_type,
        })

    def add_input_port(self, port_id: str):
        """Add an input port to receive FlowFiles from parent."""
        if port_id not in self.input_ports:
            self.input_ports.append(port_id)
            self.add_task(port_id, "inputPort")

    def add_output_port(self, port_id: str):
        """Add an output port to send FlowFiles to parent."""
        if port_id not in self.output_ports:
            self.output_ports.append(port_id)
            self.add_task(port_id, "outputPort")

    def add_child_group(self, group: "ProcessGroup"):
        """Nest a process group inside this one."""
        self.child_groups[group.id] = group

    def set_variable(self, name: str, value: str):
        """Set a group-scoped variable."""
        self.variables[name] = value

    # -- Sub-flow loading --

    def load_from_ref(self, base_dir: str = ".") -> bool:
        """Load tasks and relations from the external flow_ref file.

        Returns True if loading succeeded, False otherwise.
        Only applies to sub-flows (flow_ref is not None).
        """
        if not self.flow_ref:
            return False

        import os
        path = self.flow_ref.get("path", "")
        full_path = os.path.join(base_dir, path) if not os.path.isabs(path) else path

        if not os.path.exists(full_path):
            return False

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                flow_data = json.load(f)

            self.tasks = flow_data.get("tasks", {})
            # Tag all tasks with group_id
            for tid in self.tasks:
                self.tasks[tid]["group_id"] = self.id
            self.relations = flow_data.get("relations", [])

            # Sync ports from entries/exits of the referenced flow
            entries = flow_data.get("entries", [])
            exits = flow_data.get("exits", [])
            if entries:
                self.input_ports = entries
            if exits:
                self.output_ports = exits

            return True
        except Exception:
            return False

    # -- Serialization --

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for JSON storage)."""
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "collapsed": self.collapsed,
            "flow_ref": self.flow_ref,
            "tasks": self.tasks,
            "relations": self.relations,
            "variables": self.variables,
            "input_ports": self.input_ports,
            "output_ports": self.output_ports,
            "child_groups": {
                gid: g.to_dict() for gid, g in self.child_groups.items()
            },
        }
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProcessGroup":
        """Deserialize from dict.

        Handles both:
        - New format: full ProcessGroup dict with id, name, tasks, etc.
        - Legacy format: {"color": "#hex", "tasks": ["id1", "id2"]}
        """
        # Detect legacy format: tasks is a list of IDs, not a dict
        tasks_val = data.get("tasks", {})
        if isinstance(tasks_val, list):
            return cls._from_legacy_dict(data)

        group = cls(
            group_id=data.get("id"),
            name=data.get("name", "Process Group"),
            description=data.get("description", ""),
            color=data.get("color", "#4285f4"),
            collapsed=data.get("collapsed", False),
            flow_ref=data.get("flow_ref"),
        )
        group.tasks = data.get("tasks", {})
        group.relations = data.get("relations", [])
        group.variables = data.get("variables", {})
        group.input_ports = data.get("input_ports", [])
        group.output_ports = data.get("output_ports", [])
        for gid, gdata in data.get("child_groups", {}).items():
            group.child_groups[gid] = cls.from_dict(gdata)
        return group

    @classmethod
    def _from_legacy_dict(cls, data: Dict[str, Any]) -> "ProcessGroup":
        """Convert legacy group format {"color", "tasks": [ids]} to ProcessGroup.

        The legacy format only had visual grouping — no real tasks dict.
        We create a ProcessGroup with empty tasks/relations; the actual task
        definitions remain in the parent flow's tasks dict.
        """
        group_id = data.get("id", str(uuid.uuid4())[:8])
        group = cls(
            group_id=group_id,
            name=data.get("name", group_id),
            description=data.get("description", ""),
            color=data.get("color", "#4285f4"),
        )
        # Legacy tasks is a list of task IDs — store them so the canvas knows
        # which tasks belong to this group, but the actual task definitions
        # are in the parent flow.
        group._legacy_task_ids = data.get("tasks", [])
        return group

    def flatten(self) -> Dict[str, Any]:
        """Flatten to a flow-compatible dict (tasks + relations), including nested groups."""
        all_tasks = dict(self.tasks)
        all_relations = list(self.relations)
        for child in self.child_groups.values():
            flat = child.flatten()
            all_tasks.update(flat["tasks"])
            all_relations.extend(flat["relations"])
        return {"tasks": all_tasks, "relations": all_relations}

    def get_member_task_ids(self) -> List[str]:
        """Get all task IDs that belong to this group (including legacy)."""
        if hasattr(self, '_legacy_task_ids'):
            return list(self._legacy_task_ids)
        return list(self.tasks.keys())

    def __repr__(self):
        suffix = " [subflow]" if self.is_subflow else ""
        return f"ProcessGroup(id={self.id}, name={self.name}, tasks={len(self.tasks)}{suffix})"
