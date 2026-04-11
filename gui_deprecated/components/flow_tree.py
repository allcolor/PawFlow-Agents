# Flow Tree

"""
Hierarchical tree for visualizing flow structure.
Navigate between tasks and relations.
"""

import logging
from typing import Dict, Any, List, Optional, Set
import streamlit as st
from gui.i18n import t

from core import Flow, Task

logger = logging.getLogger(__name__)


class FlowTree:
    """Flow visualization tree."""

    def __init__(self, flow: Flow):
        self.flow = flow
        self._expanded_nodes: Set[str] = set()

    def render_tree(self, selected_task: Optional[str] = None):
        """Render flow structure tree."""
        st.markdown(f"### 🌳 {t('tree.structure')}")

        if self.flow.entries:
            self._render_section(f"📥 {t('tree.entries')}", self._render_entry_nodes)

        if self.flow.tasks:
            self._render_section(f"⚙️ {t('tree.tasks')}", self._render_task_nodes, selected_task)

        if self.flow.relations:
            self._render_section(f"🔗 {t('tree.relations')}", self._render_relation_nodes)

        if self.flow.exits:
            self._render_section(f"📤 {t('tree.exits')}", self._render_exit_nodes)

    def _render_section(self, title: str, render_func, *args):
        with st.expander(title, expanded=True):
            render_func(*args)

    def _render_entry_nodes(self):
        for entry in self.flow.entries:
            entry_id = entry.get("id", "entry_unknown")
            entry_name = entry.get("name", entry_id)

            if entry_id == self._selected_node():
                st.metric(entry_name, f"🟢 {t('tree.selected')}")
            else:
                st.write(f"🟢 {entry_name} ({entry_id})")

    def _render_task_nodes(self, selected_task: Optional[str] = None):
        for task_id, task in self.flow.tasks.items():
            task_type = task.get_type()
            task_name = task.get_name()

            if task_id == selected_task:
                st.metric(f"{task_name}", f"Type: {task_type}", f"🔴 {t('tree.selected')}")
            else:
                st.write(f"🟡 {task_name} ({task_id}) - Type: {task_type}")

    def _render_relation_nodes(self):
        for relation in self.flow.relations:
            from_id = relation.get("from", "")
            to_id = relation.get("to", "")
            relation_type = relation.get("type", "success")

            color = "🟢" if relation_type == "success" else "🔴"
            if relation_type == "both":
                color = "🟡"

            st.write(f"{color} {from_id} → {to_id} ({relation_type})")

    def _render_exit_nodes(self):
        for exit_item in self.flow.exits:
            if isinstance(exit_item, dict):
                exit_id = exit_item.get("id", "exit_unknown")
                exit_name = exit_item.get("name", exit_id)
            else:
                exit_id = str(exit_item)
                exit_name = exit_id

            st.write(f"🔴 {exit_name} ({exit_id})")

    def _selected_node(self) -> Optional[str]:
        return st.session_state.get("selected_node")

    def get_task_order(self) -> List[str]:
        """Get topological order of tasks."""
        return self._topological_sort()

    def _topological_sort(self) -> List[str]:
        from collections import defaultdict, deque

        graph = defaultdict(set)
        in_degree = defaultdict(int)

        for relation in self.flow.relations:
            from_id = relation.get("from")
            to_id = relation.get("to")

            if to_id in self.flow.tasks:
                graph[from_id].add(to_id)
                in_degree[to_id] += 1

        queue = deque(
            [task_id for task_id in self.flow.tasks if in_degree[task_id] == 0]
        )
        sorted_tasks = []

        while queue:
            task_id = queue.popleft()
            sorted_tasks.append(task_id)

            for dependent in graph[task_id]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(sorted_tasks) != len(self.flow.tasks):
            logger.warning("Cycle detected in flow")
            return list(self.flow.tasks.keys())

        return sorted_tasks

    def get_dependencies(self, task_id: str) -> List[str]:
        dependencies = []
        for relation in self.flow.relations:
            if relation.get("to") == task_id:
                dependencies.append(relation.get("from"))
        return dependencies

    def get_dependents(self, task_id: str) -> List[str]:
        dependents = []
        for relation in self.flow.relations:
            if relation.get("from") == task_id:
                dependents.append(relation.get("to"))
        return dependents

    def find_path(self, from_task: str, to_task: str) -> Optional[List[str]]:
        from collections import deque

        queue = deque([(from_task, [from_task])])
        visited = {from_task}

        while queue:
            current, path = queue.popleft()

            if current == to_task:
                return path

            for relation in self.flow.relations:
                if relation.get("from") == current:
                    neighbor = relation.get("to")
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, path + [neighbor]))

        return None

    def visualize_connections(self, task_id: str):
        st.markdown(f"### 🔗 {t('tree.connections_of', task=task_id)}")

        deps = self.get_dependencies(task_id)
        if deps:
            st.write(f"📥 {t('tree.depends_on')}: {', '.join(deps)}")
        else:
            st.write(f"📥 {t('tree.no_dependencies')}")

        dependents = self.get_dependents(task_id)
        if dependents:
            st.write(f"📤 {t('tree.dependents')}: {', '.join(dependents)}")
        else:
            st.write(f"📤 {t('tree.no_dependents')}")

        if len(self.flow.tasks) > 1:
            st.markdown(f"#### {t('tree.possible_paths')}")
            for other_id in self.flow.tasks:
                if other_id != task_id:
                    path = self.find_path(task_id, other_id)
                    if path:
                        st.write(f"→ {other_id}: {' → '.join(path)}")


def render_flow_tree_from_dict(flow_dict: dict, selected_task: str = None,
                                key_suffix: str = ""):
    """Render a flow tree from a dict (as used in the Editor).

    This is a lightweight version that doesn't require a Flow object.
    """
    tasks = flow_dict.get("tasks", {})
    relations = flow_dict.get("relations", [])
    entries = flow_dict.get("entries", [])
    exits = flow_dict.get("exits", [])

    if not tasks and not entries:
        st.caption(t("editor.empty_canvas_hint"))
        return

    # Entries
    if entries:
        with st.expander(f"📥 {t('tree.entries')} ({len(entries)})", expanded=False):
            for entry in entries:
                if isinstance(entry, dict):
                    eid = entry.get("id", "?")
                    ename = entry.get("name", eid)
                else:
                    eid = str(entry)
                    ename = eid
                st.write(f"🟢 {ename}")

    # Tasks — show execution order
    if tasks:
        # Build dependency graph for ordering
        from collections import defaultdict, deque
        in_degree = defaultdict(int)
        graph = defaultdict(set)
        for rel in relations:
            f, t_id = rel.get("from", ""), rel.get("to", "")
            if t_id in tasks:
                graph[f].add(t_id)
                in_degree[t_id] += 1

        queue = deque([tid for tid in tasks if in_degree[tid] == 0])
        ordered = []
        while queue:
            tid = queue.popleft()
            ordered.append(tid)
            for dep in graph[tid]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        # Add any remaining (cycles)
        for tid in tasks:
            if tid not in ordered:
                ordered.append(tid)

        with st.expander(f"⚙️ {t('tree.tasks')} ({len(tasks)})", expanded=True):
            for i, task_id in enumerate(ordered):
                task_data = tasks[task_id]
                task_type = task_data.get("type", "?")
                task_name = task_data.get("name", task_id)
                is_selected = (task_id == selected_task)

                prefix = "🔴" if is_selected else "🟡"
                label = f"{prefix} {i+1}. **{task_name}** ({task_type})"
                if is_selected:
                    label += f" — *{t('tree.selected')}*"

                if st.button(label, key=f"tree_task_{task_id}_{key_suffix}",
                             width="stretch"):
                    st.session_state.selected_node = task_id
                    st.rerun()

    # Relations
    if relations:
        with st.expander(f"🔗 {t('tree.relations')} ({len(relations)})", expanded=False):
            for rel in relations:
                f = rel.get("from", "?")
                t_id = rel.get("to", "?")
                rtype = rel.get("type", "success")
                color = "🟢" if rtype == "success" else ("🔴" if rtype == "failure" else "🟡")
                st.write(f"{color} {f} → {t_id} ({rtype})")

    # Exits
    if exits:
        with st.expander(f"📤 {t('tree.exits')} ({len(exits)})", expanded=False):
            for ex in exits:
                if isinstance(ex, dict):
                    eid = ex.get("id", "?")
                    ename = ex.get("name", eid)
                else:
                    eid = str(ex)
                    ename = eid
                st.write(f"🔴 {ename}")