# Core Storage Module

"""
Module de stockage abstrait pour OpenPaw.
Fournit une interface unifiée pour différents backends de stockage.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StorageInterface(ABC):
    """Interface abstraite pour tous les gestionnaires de stockage."""

    @abstractmethod
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un flux."""
        pass

    @abstractmethod
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Charger un flux."""
        pass

    @abstractmethod
    def delete_flow(self, flow_id: str) -> bool:
        """Supprimer un flux."""
        pass

    @abstractmethod
    def list_flows(self) -> List[str]:
        """Lister tous les flux."""
        pass

    @abstractmethod
    def save_version(
        self, flow_id: str, config: Dict[str, Any], version: str
    ) -> bool:
        """Sauvegarder une version d'un flux."""
        pass

    @abstractmethod
    def get_version(self, flow_id: str, version: str) -> Optional[Dict[str, Any]]:
        """Récupérer une version spécifique d'un flux."""
        pass

    @abstractmethod
    def get_versions(self, flow_id: str) -> List[str]:
        """Lister toutes les versions d'un flux."""
        pass


class StorageManager:
    """
    Gestionnaire de stockage unifié.

    Fournit une interface unifiée pour différents backends de stockage.
    Supporte: FileSystem, SQLite, Git, PostgreSQL.
    """

    def __init__(self, storage: Optional[StorageInterface] = None):
        """
        Initialiser le StorageManager.

        Args:
            storage: Implémentation de stockage (par défaut: FileSystemStorage)
        """
        from config.storage.filesystem_storage import FilesystemStorage

        self._storage = storage or FilesystemStorage({"flows_path": "./flows"})
        self._initialized = False

    def initialize(self):
        """Initialiser le stockage."""
        if not self._initialized:
            self._storage.save_flow = self._wrap_storage_method(
                self._storage.save_flow
            )
            self._initialized = True

    def _wrap_storage_method(self, method):
        """Wrappage pour gestion d'erreurs et logging."""

        def wrapped(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except Exception as e:
                logger.error(f"Erreur dans le stockage: {e}")
                raise

        return wrapped

    # Méthodes de stockage de base

    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """
        Sauvegarder un flux.

        Args:
            flow_id: ID du flux
            config: Configuration du flux

        Returns:
            True si succès
        """
        self.initialize()

        try:
            # Ajouter métadonnées
            if "metadata" not in config:
                config["metadata"] = {}

            config["metadata"]["saved_at"] = datetime.now().isoformat()
            config["metadata"]["version"] = config.get("version", "1.0.0")

            return self._storage.save_flow(flow_id, config)
        except Exception as e:
            logger.error(f"Erreur de sauvegarde du flux {flow_id}: {e}")
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """
        Charger un flux.

        Args:
            flow_id: ID du flux

        Returns:
            Configuration du flux ou None
        """
        try:
            return self._storage.load_flow(flow_id)
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
        try:
            return self._storage.delete_flow(flow_id)
        except Exception as e:
            logger.error(f"Erreur de suppression du flux {flow_id}: {e}")
            return False

    def list_flows(self) -> List[str]:
        """
        Lister tous les flux.

        Returns:
            Liste des IDs de flux
        """
        try:
            return self._storage.list_flows()
        except Exception as e:
            logger.error(f"Erreur de liste des flux: {e}")
            return []

    # Méthodes de versionning

    def save_version(
        self, flow_id: str, config: Dict[str, Any], version: str
    ) -> bool:
        """
        Sauvegarder une version d'un flux.

        Args:
            flow_id: ID du flux
            config: Configuration du flux
            version: Numéro de version

        Returns:
            True si succès
        """
        try:
            # Ajouter métadonnées de version
            version_config = config.copy()
            version_config["metadata"] = config.get("metadata", {})
            version_config["metadata"]["version"] = version
            version_config["metadata"]["saved_at"] = datetime.now().isoformat()

            # Stocker avec un ID de version
            version_id = f"{flow_id}_v{version}"
            return self._storage.save_flow(version_id, version_config)
        except Exception as e:
            logger.error(f"Erreur de sauvegarde de version {flow_id} v{version}: {e}")
            return False

    def get_version(self, flow_id: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Récupérer une version spécifique d'un flux.

        Args:
            flow_id: ID du flux
            version: Numéro de version

        Returns:
            Configuration du flux ou None
        """
        try:
            version_id = f"{flow_id}_v{version}"
            return self._storage.load_flow(version_id)
        except Exception as e:
            logger.error(f"Erreur de récupération de version {flow_id} v{version}: {e}")
            return None

    def get_versions(self, flow_id: str) -> List[str]:
        """
        Lister toutes les versions d'un flux.

        Args:
            flow_id: ID du flux

        Returns:
            Liste des numéros de version
        """
        try:
            # Extraire les versions depuis les IDs de flux
            all_flows = self.list_flows()
            versions = []

            for flow_id_candidate in all_flows:
                if flow_id_candidate.startswith(f"{flow_id}_v"):
                    version = flow_id_candidate.replace(f"{flow_id}_v", "")
                    versions.append(version)

            return sorted(versions, key=lambda x: self._version_sort_key(x))
        except Exception as e:
            logger.error(f"Erreur de récupération des versions {flow_id}: {e}")
            return []

    def _version_sort_key(self, version: str) -> tuple:
        """
        Clé de tri pour les versions (supporte semver).

        Args:
            version: Version à trier

        Returns:
            Tuple pour le tri
        """
        try:
            parts = version.split(".")
            return tuple(int(p) if p.isdigit() else 0 for p in parts)
        except (ValueError, AttributeError):
            return (0, 0, 0)

    def restore_version(self, flow_id: str, version: str) -> bool:
        """
        Restaurer une version d'un flux.

        Args:
            flow_id: ID du flux
            version: Numéro de version à restaurer

        Returns:
            True si succès
        """
        try:
            version_data = self.get_version(flow_id, version)
            if version_data:
                # Retirer le suffixe de version pour le save
                version_data["metadata"] = version_data.get("metadata", {})
                version_data["metadata"]["restored_from"] = version
                return self.save_flow(flow_id, version_data)
            return False
        except Exception as e:
            logger.error(f"Erreur de restauration de version {flow_id} v{version}: {e}")
            return False

    # Méthodes de recherche

    def search_flows(self, query: str) -> List[Dict[str, Any]]:
        """
        Rechercher des flux par texte.

        Args:
            query: Texte de recherche

        Returns:
            Liste des flux correspondants
        """
        try:
            flows = self.list_flows()
            results = []

            for flow_id in flows:
                flow_data = self.load_flow(flow_id)
                if flow_data:
                    # Recherche dans le nom, description, author
                    searchable = " ".join(
                        str(flow_data.get(k, ""))
                        for k in ["name", "description", "author", "id"]
                    ).lower()

                    if query.lower() in searchable:
                        results.append({"id": flow_id, **flow_data})

            return results
        except Exception as e:
            logger.error(f"Erreur de recherche: {e}")
            return []

    # Méthodes utilitaires

    def backup_flow(self, flow_id: str, backup_path: str) -> bool:
        """
        Sauvegarder une copie de sauvegarde d'un flux.

        Args:
            flow_id: ID du flux
            backup_path: Chemin de sauvegarde

        Returns:
            True si succès
        """
        try:
            flow_data = self.load_flow(flow_id)
            if flow_data:
                # Sauvegarder dans le chemin de backup
                backup_config = {
                    "backup_of": flow_id,
                    "backed_up_at": datetime.now().isoformat(),
                    "data": flow_data,
                }
                return self._storage.save_flow(backup_path, backup_config)
            return False
        except Exception as e:
            logger.error(f"Erreur de backup du flux {flow_id}: {e}")
            return False

    def get_flow_stats(self, flow_id: str) -> Dict[str, Any]:
        """
        Récupérer les statistiques d'un flux.

        Args:
            flow_id: ID du flux

        Returns:
            Dictionnaire de statistiques
        """
        try:
            flow_data = self.load_flow(flow_id)
            if not flow_data:
                return {}

            return {
                "id": flow_id,
                "name": flow_data.get("name", "Unknown"),
                "version": flow_data.get("version", "1.0.0"),
                "tasks_count": len(flow_data.get("tasks", {})),
                "relations_count": len(flow_data.get("relations", [])),
                "saved_at": flow_data.get("metadata", {}).get("saved_at"),
            }
        except Exception as e:
            logger.error(f"Erreur de statistiques du flux {flow_id}: {e}")
            return {}

    def set_storage(self, storage: StorageInterface):
        """
        Changer le backend de stockage.

        Args:
            storage: Nouvelle implémentation de stockage
        """
        self._storage = storage
        self._initialized = False
        logger.info(f"Backend de stockage changé: {type(storage).__name__}")

    def get_storage_type(self) -> str:
        """
        Récupérer le type de stockage actuel.

        Returns:
            Nom du type de stockage
        """
        return type(self._storage).__name__