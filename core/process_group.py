"""Process Group - container for grouping tasks into reusable sub-flows."""

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
    """

    def __init__(self, group_id: Optional[str] = None, name: str = "Process Group",
                 description: str = ""):
        self.id = group_id or str(uuid.uuid4())[:8]
        self.name = name
        self.description = description
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.relations: List[Dict[str, str]] = []
        self.variables: Dict[str, str] = {}
        self.input_ports: List[str] = []
        self.output_ports: List[str] = []
        self.child_groups: Dict[str, "ProcessGroup"] = {}

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for JSON storage)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tasks": self.tasks,
            "relations": self.relations,
            "variables": self.variables,
            "input_ports": self.input_ports,
            "output_ports": self.output_ports,
            "child_groups": {
                gid: g.to_dict() for gid, g in self.child_groups.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProcessGroup":
        """Deserialize from dict."""
        group = cls(
            group_id=data.get("id"),
            name=data.get("name", "Process Group"),
            description=data.get("description", ""),
        )
        group.tasks = data.get("tasks", {})
        group.relations = data.get("relations", [])
        group.variables = data.get("variables", {})
        group.input_ports = data.get("input_ports", [])
        group.output_ports = data.get("output_ports", [])
        for gid, gdata in data.get("child_groups", {}).items():
            group.child_groups[gid] = cls.from_dict(gdata)
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

    def __repr__(self):
        return f"ProcessGroup(id={self.id}, name={self.name}, tasks={len(self.tasks)})"
