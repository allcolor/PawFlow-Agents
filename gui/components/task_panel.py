# Task Panel

"""
Panneau de configuration des tâches.
Fournit validation et sélection de type. Le rendu des formulaires
est délégué à render_schema_fields() (gui/components/schema_form.py).
"""

import logging
from typing import Dict, Any, List, Optional
import streamlit as st

from gui.i18n import t

from core import TaskFactory

logger = logging.getLogger(__name__)


class TaskPanel:
    """Panneau de configuration dynamique des tâches."""

    def __init__(self):
        """Initialiser le panneau."""
        self._task_schemas = {}

    def _get_task_schema(self, task_type: str) -> Dict[str, Any]:
        """
        Récupérer le schéma des paramètres d'une tâche.

        Args:
            task_type: Type de tâche

        Returns:
            Schéma des paramètres
        """
        if task_type in self._task_schemas:
            return self._task_schemas[task_type]

        try:
            task_class = TaskFactory.get(task_type)
            task_instance = task_class({})
            schema = task_instance.get_parameter_schema()
            self._task_schemas[task_type] = schema
            return schema
        except Exception as e:
            logger.error(f"Erreur récupération schéma tâche {task_type}: {e}")
            return {}

    def render_task_type_selector(
        self,
        current_type: Optional[str] = None,
        key: str = "task_type_selector",
    ) -> str:
        """
        Afficher un sélecteur de type de tâche.

        Args:
            current_type: Type actuel (optionnel)
            key: Clé Streamlit

        Returns:
            Type sélectionné
        """
        # Lister les tâches disponibles
        try:
            from core import TaskFactory

            tasks = TaskFactory.list_types()
            tasks.sort()
        except Exception as e:
            logger.error(f"Erreur récupération tâches: {e}")
            tasks = ["log", "replace_text"]

        # Afficher le sélecteur
        selected_type = st.selectbox(
            t("common.type"),
            options=tasks,
            index=tasks.index(current_type) if current_type and current_type in tasks else 0,
            key=key,
        )

        return selected_type

    def validate_task_config(
        self, task_type: str, config: Dict[str, Any]
    ) -> List[str]:
        """
        Valider la configuration d'une tâche.

        Args:
            task_type: Type de tâche
            config: Configuration à valider

        Returns:
            Liste de messages d'erreur (vide si valide)
        """
        errors = []
        schema = self._get_task_schema(task_type)

        for param_name, param_schema in schema.items():
            required = param_schema.get("required", False)
            # Conditional required: {"field": ["val1", "val2"]}
            if isinstance(required, dict):
                is_required = False
                for dep_field, dep_values in required.items():
                    current_val = config.get(dep_field)
                    if current_val in dep_values:
                        is_required = True
                        break
            else:
                is_required = bool(required)

            if is_required:
                if param_name not in config:
                    errors.append(f"{t('runtime.all_fields_required')}: {param_name}")
                elif config[param_name] is None or config[param_name] == "":
                    errors.append(f"{t('runtime.all_fields_required')}: {param_name}")

        return errors

    def get_parameter_info(self, task_type: str, param_name: str) -> Dict[str, Any]:
        """
        Récupérer les informations sur un paramètre.

        Args:
            task_type: Type de tâche
            param_name: Nom du paramètre

        Returns:
            Informations sur le paramètre
        """
        schema = self._get_task_schema(task_type)
        return schema.get(param_name, {})