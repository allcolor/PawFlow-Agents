# Flow Visualizer

"""
Visualisation interactive des flux avec graphviz.
Permet de visualiser le DAG des tâches.
"""

import logging
from typing import Dict, Any, List, Optional

try:
    from graphviz import Digraph
    GRAPHVIZ_AVAILABLE = True
except ImportError:
    GRAPHVIZ_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("graphviz non installé - visualisation désactivée")

from core import Flow, Task

logger = logging.getLogger(__name__)


class FlowVisualizer:
    """Visualiseur de flux utilisant graphviz."""

    # Couleurs par type de relation
    RELATION_COLORS = {
        "success": "#28a745",  # Green
        "failure": "#dc3545",  # Red
        "both": "#ffc107",  # Yellow
    }

    # Couleurs par type de tâche
    TASK_COLORS = {
        "log": "#1f77b4",
        "replace_text": "#ff7f0e",
        "wait": "#2ca02c",
        "fail": "#d62728",
        "http": "#9467bd",
        "sftp": "#8c564b",
        "s3": "#e377c2",
        "db": "#7f7f7f",
        "script": "#bcbd22",
        "shell": "#17becf",
    }

    def __init__(self, flow: Flow):
        """
        Initialiser le visualiseur.

        Args:
            flow: Flux à visualiser
        """
        self.flow = flow
        self._dot = None
        self._digraph_class = Digraph if GRAPHVIZ_AVAILABLE else None

    def render(self, show_labels: bool = True) -> str:
        """
        Générer le code DOT du flux.

        Args:
            show_labels: Afficher les labels détaillés

        Returns:
            Code DOT du flux
        """
        if not GRAPHVIZ_AVAILABLE:
            return "# Graphviz non disponible - visualisation désactivée"

        self._create_graph()

        # Ajouter les entrées
        for entry in self.flow.entries:
            entry_id = entry.get("id", "entry_unknown")
            self._dot.node(
                entry_id,
                label=entry.get("name", entry_id),
                shape="box",
                style="filled",
                fillcolor="#90ee90",
            )

        # Ajouter les sorties
        for exit_item in self.flow.exits:
            exit_id = exit_item.get("id", "exit_unknown")
            self._dot.node(
                exit_id,
                label=exit_item.get("name", exit_id),
                shape="box",
                style="filled",
                fillcolor="#ffb6c1",
            )

        # Ajouter les tâches
        for task_id, task in self.flow.tasks.items():
            self._add_task_node(task_id, task)

        # Ajouter les relations
        for relation in self.flow.relations:
            self._add_relation_edge(relation)

        return self._dot.source if self._dot else ""

    def _create_graph(self):
        """Créer le graphique DOT."""
        if not GRAPHVIZ_AVAILABLE:
            return
        self._dot = Digraph(
            name=self.flow.id,
            format="svg",
            graph_attr={
                "rankdir": "LR",
                "dpi": "150",
                "node_spacing": "0.5",
                "ranksep": "1.0",
            },
            node_attr={
                "fontsize": "12",
                "fontname": "Helvetica",
            },
            edge_attr={
                "fontsize": "10",
                "fontname": "Helvetica",
            },
        )

        # Titre du graphe
        self._dot.attr("graph", label=f"{self.flow.name}\n{self.flow.description}")
        self._dot.attr("graph", labelloc="t", fontsize="14")

    def _add_task_node(self, task_id: str, task: Task, show_labels: bool = True):
        """
        Ajouter un nœud de tâche au graphique.

        Args:
            task_id: ID de la tâche
            task: Objet Task
        """
        task_type = task.get_type()
        task_name = task.get_name()

        # Déterminer la couleur
        color = self.TASK_COLORS.get(task_type, "#cccccc")

        # Créer le label
        if show_labels:
            label = f"{task_name}\\n({task_type})"
        else:
            label = task_type

        self._dot.node(
            task_id,
            label=label,
            shape="box",
            style="filled",
            fillcolor=color,
        )

    def _add_relation_edge(self, relation: Dict[str, Any]):
        if not GRAPHVIZ_AVAILABLE or self._dot is None:
            return
        """
        Ajouter une relation (edge) au graphique.

        Args:
            relation: Relation de type {from, to, type}
        """
        from_id = relation.get("from")
        to_id = relation.get("to")
        relation_type = relation.get("type", "success")

        # Couleur de l'arête
        color = self.RELATION_COLORS.get(relation_type, "#000000")

        # Style de l'arête
        style = "solid"
        if relation_type == "failure":
            style = "dashed"

        self._dot.edge(
            from_id,
            to_id,
            label=relation_type,
            color=color,
            style=style,
        )

    def render_to_file(self, filepath: str, show_labels: bool = True):
        if not GRAPHVIZ_AVAILABLE:
            logger.warning("graphviz non disponible - fichier non généré")
            return
        dot_source = self.render(show_labels)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(dot_source)
        logger.info(f"Graphique sauvegardé: {filepath}")

    def render_as_svg(self, show_labels: bool = True) -> Optional[str]:
        """
        Générer le graphique en SVG.

        Args:
            show_labels: Afficher les labels détaillés

        Returns:
            Contenu SVG ou None en cas d'erreur
        """
        if not GRAPHVIZ_AVAILABLE or self._dot is None:
            return None
        try:
            svg_bytes = self._dot.pipe("svg")
            return svg_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"Erreur de génération SVG: {e}")
            return None

    def render_as_png(self, show_labels: bool = True) -> Optional[bytes]:
        """
        Générer le graphique en PNG.

        Args:
            show_labels: Afficher les labels détaillés

        Returns:
            Contenu PNG ou None en cas d'erreur
        """
        if not GRAPHVIZ_AVAILABLE or self._dot is None:
            return None
        try:
            png_bytes = self._dot.pipe("png")
            return png_bytes
        except Exception as e:
            logger.error(f"Erreur de génération PNG: {e}")
            return None

    def get_summary(self) -> Dict[str, Any]:
        """
        Récupérer un résumé du flux.

        Returns:
            Dictionnaire avec les statistiques du flux
        """
        return {
            "id": self.flow.id,
            "name": self.flow.name,
            "version": self.flow.version,
            "tasks_count": len(self.flow.tasks),
            "relations_count": len(self.flow.relations),
            "entries_count": len(self.flow.entries),
            "exits_count": len(self.flow.exits),
            "task_types": self._get_task_types_summary(),
        }

    def _get_task_types_summary(self) -> Dict[str, int]:
        """Compter les tâches par type."""
        summary = {}
        for task in self.flow.tasks.values():
            task_type = task.get_type()
            summary[task_type] = summary.get(task_type, 0) + 1
        return summary