# Storage Service

"""
Service pour la gestion du stockage des flux.
Supporte filesystem, Git, et bases de données.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from core import StorageManager
from config import FilesystemStorage, SqliteStorage, GitStorage, PostgresStorage

logger = logging.getLogger(__name__)


class StorageService:
    """Service de gestion du stockage."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialiser le service de stockage.

        Args:
            config: Configuration du stockage
        """
        self.config = config or {}
        self._storage_manager = StorageManager()
        self._initialized = False

    @property
    def storage_manager(self) -> StorageManager:
        """Récupérer ou initialiser le StorageManager."""
        if not self._initialized:
            self.initialize()
        return self._storage_manager

    def initialize(self):
        """Initialiser le service selon la configuration."""
        if self._initialized:
            return

        storage_type = self.config.get("storage_type", "filesystem")

        try:
            if storage_type == "filesystem":
                path = self.config.get("filesystem_path", "./flows")
                fs_storage = FilesystemStorage({"flows_path": path})
                self._storage_manager = StorageManager(storage=fs_storage)
            elif storage_type == "sqlite":
                path = self.config.get("sqlite_path", "./flows.db")
                sqlite_storage = SqliteStorage(path)
                self._storage_manager = StorageManager(storage=sqlite_storage)
            else:
                # Fallback sur filesystem
                fs_storage = FilesystemStorage({"flows_path": "./flows"})
                self._storage_manager = StorageManager(storage=fs_storage)

            self._initialized = True
            logger.info(f"StorageService initialisé avec {storage_type}")

        except Exception as e:
            logger.error(f"Erreur d'initialisation StorageService: {e}")
            raise

    def save_flow(self, flow_id: str, flow_data: Dict[str, Any]) -> bool:
        """
        Sauvegarder un flux.

        Args:
            flow_id: ID du flux
            flow_data: Données du flux

        Returns:
            True si succès
        """
        self.initialize()

        try:
            if hasattr(self._storage_manager, 'save_flow'):
                self._storage_manager.save_flow(flow_id, flow_data)
            logger.info(f"Flux sauvegardé: {flow_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur de sauvegarde du flux {flow_id}: {e}")
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """
        Charger un flux.

        Args:
            flow_id: ID du flux

        Returns:
            Données du flux ou None
        """
        self.initialize()

        try:
            if hasattr(self._storage_manager, 'load_flow'):
                return self._storage_manager.load_flow(flow_id)
            return None
        except Exception as e:
            logger.error(f"Erreur de chargement du flux {flow_id}: {e}")
            return None

    def delete_flow(self, flow_id: str) -> bool:
        """
        Supprimer un flux.

        Args:
            flow_id: ID du flux

        Returns:
            True si succès
        """
        self.initialize()

        try:
            if hasattr(self._storage_manager, 'delete_flow'):
                self._storage_manager.delete_flow(flow_id)
            logger.info(f"Flux supprimé: {flow_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur de suppression du flux {flow_id}: {e}")
            return False

    def list_flows(self) -> List[str]:
        """
        Lister tous les flux.

        Returns:
            Liste des IDs de flux
        """
        self.initialize()

        try:
            if hasattr(self._storage_manager, 'list_flows'):
                return self._storage_manager.list_flows()
            return []
        except Exception as e:
            logger.error(f"Erreur de liste des flux: {e}")
            return []

    def search_flows(self, query: str) -> List[Dict[str, Any]]:
        """
        Rechercher des flux.

        Args:
            query: Texte de recherche

        Returns:
            Liste des flux correspondants
        """
        self.initialize()
        # Recherche simplifiée sur filesystem
        try:
            flows = self.list_flows()
            results = []
            for flow_id in flows:
                flow_data = self.load_flow(flow_id)
                if flow_data and query.lower() in flow_data.get("name", "").lower():
                    results.append({"id": flow_id, **flow_data})
            return results
        except Exception as e:
            logger.error(f"Erreur de recherche: {e}")
            return []

    def get_storage_type(self) -> str:
        """
        Récupérer le type de stockage actuel.

        Returns:
            Type de stockage
        """
        return self.config.get("storage_type", "filesystem")

    def update_config(self, **kwargs):
        """
        Mettre à jour la configuration.

        Args:
            **kwargs: Nouveaux paramètres de configuration
        """
        self.config.update(kwargs)
        self._initialized = False  # Force réinitialisation