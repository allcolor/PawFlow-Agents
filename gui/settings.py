# Configuration GUI

"""
Configuration centrale pour l'interface graphique.
"""

from dataclasses import dataclass
from typing import Dict, Any, List
from pathlib import Path


@dataclass
class GUIConfig:
    """Configuration de l'application GUI."""

    # Application
    app_name: str = "PawFlow - Pipeline Framework"
    app_version: str = "0.1.0"
    page_icon: str = "🚀"

    # Storage
    flows_directory: Path = None  # Par défaut: ./flows
    storage_type: str = "filesystem"  # filesystem, git, postgresql
    git_repository: str = None

    # Execution
    max_workers: int = 10
    max_retries: int = 3
    default_timeout: int = 300

    # UI Preferences
    theme: str = "light"  # light, dark
    show_debug_info: bool = False
    auto_save: bool = True
    auto_validate: bool = True


@dataclass
class TaskConfig:
    """Configuration par défaut des tâches."""

    default_icon: str = "default"
    auto_expand: bool = True
    show_parameter_help: bool = True


@dataclass
class StorageConfig:
    """Configuration du stockage."""

    # Filesystem
    filesystem_path: Path = Path("./flows")

    # Git
    git_path: Path = Path("./flows.git")
    git_auto_commit: bool = True

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "pawflow"
    postgres_user: str = "pawflow"


# Configuration globale
DEFAULT_CONFIG = GUIConfig()


def get_config() -> GUIConfig:
    """Récupérer la configuration globale."""
    return DEFAULT_CONFIG


def update_config(**kwargs) -> GUIConfig:
    """Mettre à jour la configuration."""
    global DEFAULT_CONFIG
    for key, value in kwargs.items():
        if hasattr(DEFAULT_CONFIG, key):
            setattr(DEFAULT_CONFIG, key, value)
    return DEFAULT_CONFIG


# Mapping des types de widgets Streamlit
WIDGET_TYPES = {
    "string": "text_input",
    "integer": "number_input",
    "float": "number_input",
    "boolean": "checkbox",
    "select": "selectbox",
    "multiselect": "multiselect",
    "text_area": "text_area",
    "slider": "slider",
}

# Options par défaut pour les select
DEFAULT_SELECT_OPTIONS = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
}