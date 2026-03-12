# Task Panel

"""
Panneau de configuration des tâches.
Génère dynamiquement les widgets Streamlit selon le schéma des paramètres.
"""

import logging
from typing import Dict, Any, List, Optional, Any
import streamlit as st
from gui.i18n import t

from core import TaskFactory
from gui.settings import WIDGET_TYPES

logger = logging.getLogger(__name__)


class TaskPanel:
    """Panneau de configuration dynamique des tâches."""

    def __init__(self):
        """Initialiser le panneau."""
        self._task_schemas = {}

    def render_task_editor(
        self,
        task_type: str,
        existing_config: Optional[Dict[str, Any]] = None,
        key_prefix: str = "task",
    ) -> Dict[str, Any]:
        """
        Afficher l'éditeur de tâche avec des widgets dynamiques.

        Args:
            task_type: Type de tâche
            existing_config: Configuration existante à pré-remplir
            key_prefix: Préfixe pour les clés Streamlit

        Returns:
            Configuration mise à jour
        """
        # Récupérer le schéma des paramètres
        schema = self._get_task_schema(task_type)

        if not schema:
            st.error(f"{t('common.error')}: {task_type}")
            return existing_config or {}

        # Initialiser la configuration
        config = existing_config.copy() if existing_config else {}

        # Titre de la tâche
        st.markdown(f"### ⚙️ {t('editor.task_config')}: {task_type}")

        # Afficher le schéma et générer les widgets
        for param_name, param_schema in schema.items():
            key = f"{key_prefix}_{task_type}_{param_name}"

            # Récupérer la valeur existante ou la valeur par défaut
            default_value = config.get(param_name) if config else None
            if default_value is None:
                default_value = param_schema.get("default")

            # Générer le widget selon le type
            widget_value = self._render_widget(
                key=key,
                param_name=param_name,
                param_schema=param_schema,
                default_value=default_value,
            )

            # Mettre à jour la configuration
            if widget_value is not None:
                config[param_name] = widget_value

        # Bouton de réinitialisation
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button(f"🔄 {t('common.reset')}", key=f"{key_prefix}_reset_{task_type}"):
                config = {}
                st.rerun()

        with col2:
            st.caption(t("editor.task_config"))

        return config

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

    def _render_widget(
        self,
        key: str,
        param_name: str,
        param_schema: Dict[str, Any],
        default_value: Any,
    ) -> Any:
        """
        Générer le widget Streamlit approprié.

        Args:
            key: Clé Streamlit
            param_name: Nom du paramètre
            param_schema: Schéma du paramètre
            default_value: Valeur par défaut

        Returns:
            Valeur saisie ou None
        """
        param_type = param_schema.get("type", "string")
        required = param_schema.get("required", False)
        description = param_schema.get("description", "")
        help_text = param_schema.get("help", "")
        placeholder = param_schema.get("placeholder", "")

        # Afficher la description
        if description:
            st.caption(description)

        # Afficher l'aide si disponible
        if help_text:
            with st.expander(f"ℹ️ {t('common.help')}"):
                st.caption(help_text)

        # Choisir le widget selon le type
        widget_type = WIDGET_TYPES.get(param_type, "text_input")

        try:
            if widget_type == "text_input":
                return st.text_input(
                    param_name,
                    key=key,
                    value=default_value if default_value else "",
                    placeholder=placeholder,
                )

            elif widget_type == "number_input":
                min_val = param_schema.get("min")
                max_val = param_schema.get("max")
                step = param_schema.get("step", 1)

                return st.number_input(
                    param_name,
                    key=key,
                    value=default_value if default_value else 0,
                    min_value=min_val,
                    max_value=max_val,
                    step=step,
                )

            elif widget_type == "selectbox":
                options = param_schema.get("options", [])
                return st.selectbox(
                    param_name,
                    options=options,
                    key=key,
                    index=options.index(default_value) if default_value and default_value in options else 0,
                )

            elif widget_type == "multiselect":
                options = param_schema.get("options", [])
                return st.multiselect(
                    param_name,
                    options=options,
                    key=key,
                    default=default_value if default_value else [],
                )

            elif widget_type == "checkbox":
                # Checkbox spécial pour les booléens
                return st.checkbox(
                    param_name,
                    key=key,
                    value=bool(default_value) if default_value is not None else False,
                )

            elif widget_type == "text_area":
                return st.text_area(
                    param_name,
                    key=key,
                    value=default_value if default_value else "",
                    placeholder=placeholder,
                )

            elif widget_type == "slider":
                min_val = param_schema.get("min", 0)
                max_val = param_schema.get("max", 100)
                step = param_schema.get("step", 1)

                return st.slider(
                    param_name,
                    key=key,
                    min_value=min_val,
                    max_value=max_val,
                    value=default_value if default_value else min_val,
                    step=step,
                )

            else:
                # Fallback sur text_input
                return st.text_input(
                    param_name,
                    key=key,
                    value=str(default_value) if default_value else "",
                )

        except Exception as e:
            logger.error(f"ErreurWidget pour {param_name}: {e}")
            return default_value

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
            if param_schema.get("required", False):
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