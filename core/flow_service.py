# Flow Service

"""
Service pour la gestion des flux.
Couche d'abstraction entre le GUI et FlowParser/FlowValidator.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from engine import FlowParser, FlowValidator
from core import Flow, FlowFile, TaskFactory, ServiceFactory, StorageManager

logger = logging.getLogger(__name__)


class FlowService:
    """Service de gestion des flux."""

    def __init__(self, storage_manager: Optional[StorageManager] = None):
        """
        Initialiser le service.

        Args:
            storage_manager: Gestionnaire de stockage (par défaut: FileSystem)
        """
        self.storage_manager = storage_manager or StorageManager()
        self._initialized = False

    def initialize(self):
        """Initialiser le service et enregistrer toutes les tâches/services."""
        if not self._initialized:
            # Enregistrer toutes les tâches et services
            from tasks import register_all_tasks

            register_all_tasks()

            self._initialized = True
            logger.info("FlowService initialisé")

    def parse(self, config: Dict[str, Any]) -> Flow:
        """
        Parser un flux depuis une configuration.

        Args:
            config: Configuration du flux

        Returns:
            Objet Flow parseé
        """
        self.initialize()
        return FlowParser.parse(config)

    def parse_from_file(self, filepath: str) -> Flow:
        """
        Parser un flux depuis un fichier JSON.

        Args:
            filepath: Chemin vers le fichier JSON

        Returns:
            Objet Flow parseé
        """
        self.initialize()
        return FlowParser.parse_from_file(filepath)

    def parse_from_json(self, json_string: str) -> Flow:
        """
        Parser un flux depuis une chaîne JSON.

        Args:
            json_string: Chaîne JSON

        Returns:
            Objet Flow parseé
        """
        self.initialize()
        return FlowParser.parse_from_json(json_string)

    def validate(self, flow: Flow, strict: bool = True) -> List[str]:
        """
        Valider un flux.

        Args:
            flow: Flux à valider
            strict: Mode strict (lève des erreurs) ou non

        Returns:
            Liste de messages d'erreur (vide si valide)
        """
        self.initialize()
        return FlowValidator.validate(flow, strict)

    def save(self, flow: Flow, filepath: Optional[str] = None) -> str:
        """
        Sauvegarder un flux.

        Args:
            flow: Flux à sauvegarder
            filepath: Chemin optionnel (généré si non spécifié)

        Returns:
            Chemin du fichier sauvegardé
        """
        self.initialize()

        if filepath is None:
            filepath = f"flows/{flow.id}.json"

        # Créer le répertoire s'il n'existe pas
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        # Convertir le flow en dict
        config = self.flow_to_dict(flow)

        # Sauvegarder
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        logger.info(f"Flux sauvegardé: {filepath}")
        return filepath

    def load(self, filepath: str) -> Flow:
        """
        Charger un flux depuis un fichier.

        Args:
            filepath: Chemin du fichier

        Returns:
            Objet Flow chargé
        """
        self.initialize()
        return self.parse_from_file(filepath)

    def list_flows(self, directory: str = "flows") -> List[str]:
        """
        Lister tous les flux dans un répertoire.

        Args:
            directory: Répertoire à scanner

        Returns:
            Liste des chemins des fichiers JSON
        """
        self.initialize()
        flows_dir = Path(directory)
        if not flows_dir.exists():
            return []
        return [str(f) for f in flows_dir.glob("*.json")]

    def delete(self, filepath: str) -> bool:
        """
        Supprimer un flux.

        Args:
            filepath: Chemin du fichier à supprimer

        Returns:
            True si succès
        """
        try:
            Path(filepath).unlink()
            logger.info(f"Flux supprimé: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Erreur de suppression: {e}")
            return False

    def flow_to_dict(self, flow: Flow) -> Dict[str, Any]:
        """
        Convertir un objet Flow en dictionnaire JSON.

        Args:
            flow: Objet Flow

        Returns:
            Dictionnaire JSON
        """
        return {
            "id": flow.id,
            "name": flow.name,
            "version": flow.version,
            "description": flow.description,
            "author": flow.author,
            "created_at": flow.created_at.isoformat() if hasattr(flow, 'created_at') and flow.created_at else None,
            "parameters": flow.parameters,
            "entries": flow.entries,
            "exits": flow.exits,
            "tasks": {
                task_id: {
                    "type": task.get_type(),
                    "parameters": task.config,
                }
                for task_id, task in flow.tasks.items()
            },
            "services": {
                service_id: {
                    "type": service.get_type(),
                    "parameters": service.config,
                }
                for service_id, service in flow.services.items()
            },
            "groups": flow.groups,
            "relations": flow.relations,
            "variables": flow.variables,
        }

    def dict_to_flow(self, config: Dict[str, Any]) -> Flow:
        """
        Convertir un dictionnaire JSON en objet Flow.

        Args:
            config: Dictionnaire de configuration

        Returns:
            Objet Flow
        """
        return self.parse(config)

    def get_task_schema(self, task_type: str) -> Dict[str, Any]:
        """
        Récupérer le schéma des paramètres d'une tâche.

        Args:
            task_type: Type de tâche

        Returns:
            Schéma des paramètres
        """
        self.initialize()
        try:
            task_class = TaskFactory.get(task_type)
            task_instance = task_class({})
            return task_instance.get_parameter_schema()
        except Exception as e:
            logger.error(f"Erreur récupération schéma tâche {task_type}: {e}")
            return {}

    def get_available_tasks(self) -> List[str]:
        """
        Lister toutes les tâches disponibles.

        Returns:
            Liste des types de tâches
        """
        self.initialize()
        return TaskFactory.list_types()

    def get_available_services(self) -> List[str]:
        """
        Lister tous les services disponibles.

        Returns:
            Liste des types de services
        """
        self.initialize()
        return ServiceFactory.list_types()