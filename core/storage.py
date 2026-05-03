# Core Storage Module

"""
Abstract storage module for PawFlow.
Provides a unified interface for different storage backends.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StorageInterface(ABC):
    """Abstract interface for all storage managers."""

    @abstractmethod
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Save a flow."""
        pass

    @abstractmethod
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Load a flow."""
        pass

    @abstractmethod
    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        pass

    @abstractmethod
    def list_flows(self) -> List[str]:
        """List all flows."""
        pass

    @abstractmethod
    def save_version(
        self, flow_id: str, config: Dict[str, Any], version: str
    ) -> bool:
        """Save a version of a flow."""
        pass

    @abstractmethod
    def get_version(self, flow_id: str, version: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific version of a flow."""
        pass

    @abstractmethod
    def get_versions(self, flow_id: str) -> List[str]:
        """List all versions of a flow."""
        pass


class StorageManager:
    """
    Unified storage manager.

    Provides a unified interface for different storage backends.
    Supports: FileSystem, SQLite, Git, PostgreSQL.
    """

    def __init__(self, storage: Optional[StorageInterface] = None):
        """
        Initialize the StorageManager.

        Args:
            storage: Storage implementation (default: FileSystemStorage)
        """
        from core.storage_backends.filesystem_storage import FilesystemStorage

        self._storage = storage or FilesystemStorage({"flows_path": "./flows"})
        self._initialized = False

    def initialize(self):
        """Initialize storage."""
        if not self._initialized:
            self._storage.save_flow = self._wrap_storage_method(
                self._storage.save_flow
            )
            self._initialized = True

    def _wrap_storage_method(self, method):
        """Wrapper for error handling and logging."""

        def wrapped(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except Exception as e:
                logger.error(f"Storage error: {e}")
                raise

        return wrapped

    # Base storage methods

    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """
        Save a flow.

        Args:
            flow_id: Flow ID
            config: Flow configuration

        Returns:
            True if successful
        """
        self.initialize()

        try:
            # Add metadata
            if "metadata" not in config:
                config["metadata"] = {}

            config["metadata"]["saved_at"] = datetime.now().isoformat()
            config["metadata"]["version"] = config.get("version", "1.0.0")

            return self._storage.save_flow(flow_id, config)
        except Exception as e:
            logger.error(f"Error saving flow {flow_id}: {e}")
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a flow.

        Args:
            flow_id: Flow ID

        Returns:
            Flow configuration or None
        """
        try:
            return self._storage.load_flow(flow_id)
        except Exception as e:
            logger.error(f"Error loading flow {flow_id}: {e}")
            return None

    def delete_flow(self, flow_id: str) -> bool:
        """
        Delete a flow.

        Args:
            flow_id: Flow ID

        Returns:
            True if successful
        """
        try:
            return self._storage.delete_flow(flow_id)
        except Exception as e:
            logger.error(f"Error deleting flow {flow_id}: {e}")
            return False

    def list_flows(self) -> List[str]:
        """
        List all flows.

        Returns:
            List of flow IDs
        """
        try:
            return self._storage.list_flows()
        except Exception as e:
            logger.error(f"Error listing flows: {e}")
            return []

    # Versioning methods

    def save_version(
        self, flow_id: str, config: Dict[str, Any], version: str
    ) -> bool:
        """
        Save a version of a flow.

        Args:
            flow_id: Flow ID
            config: Flow configuration
            version: Version number

        Returns:
            True if successful
        """
        try:
            # Add version metadata
            version_config = config.copy()
            version_config["metadata"] = config.get("metadata", {})
            version_config["metadata"]["version"] = version
            version_config["metadata"]["saved_at"] = datetime.now().isoformat()

            # Store with a version ID
            version_id = f"{flow_id}_v{version}"
            return self._storage.save_flow(version_id, version_config)
        except Exception as e:
            logger.error(f"Error saving version {flow_id} v{version}: {e}")
            return False

    def get_version(self, flow_id: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific version of a flow.

        Args:
            flow_id: Flow ID
            version: Version number

        Returns:
            Flow configuration or None
        """
        try:
            version_id = f"{flow_id}_v{version}"
            return self._storage.load_flow(version_id)
        except Exception as e:
            logger.error(f"Error retrieving version {flow_id} v{version}: {e}")
            return None

    def get_versions(self, flow_id: str) -> List[str]:
        """
        List all versions of a flow.

        Args:
            flow_id: Flow ID

        Returns:
            List of version numbers
        """
        try:
            # Extract versions from flow IDs
            all_flows = self.list_flows()
            versions = []

            for flow_id_candidate in all_flows:
                if flow_id_candidate.startswith(f"{flow_id}_v"):
                    version = flow_id_candidate.replace(f"{flow_id}_v", "")
                    versions.append(version)

            return sorted(versions, key=lambda x: self._version_sort_key(x))
        except Exception as e:
            logger.error(f"Error retrieving versions for {flow_id}: {e}")
            return []

    def _version_sort_key(self, version: str) -> tuple:
        """
        Sort key for versions (supports semver).

        Args:
            version: Version to sort

        Returns:
            Tuple for sorting
        """
        try:
            parts = version.split(".")
            return tuple(int(p) if p.isdigit() else 0 for p in parts)
        except (ValueError, AttributeError):
            return (0, 0, 0)

    def restore_version(self, flow_id: str, version: str) -> bool:
        """
        Restore a version of a flow.

        Args:
            flow_id: Flow ID
            version: Version number to restore

        Returns:
            True if successful
        """
        try:
            version_data = self.get_version(flow_id, version)
            if version_data:
                # Remove version suffix for the save
                version_data["metadata"] = version_data.get("metadata", {})
                version_data["metadata"]["restored_from"] = version
                return self.save_flow(flow_id, version_data)
            return False
        except Exception as e:
            logger.error(f"Error restoring version {flow_id} v{version}: {e}")
            return False

    # Search methods

    def search_flows(self, query: str) -> List[Dict[str, Any]]:
        """
        Search flows by text.

        Args:
            query: Search text

        Returns:
            List of matching flows
        """
        try:
            flows = self.list_flows()
            results = []

            for flow_id in flows:
                flow_data = self.load_flow(flow_id)
                if flow_data:
                    # Search in name, description, author
                    searchable = " ".join(
                        str(flow_data.get(k, ""))
                        for k in ["name", "description", "author", "id"]
                    ).lower()

                    if query.lower() in searchable:
                        results.append({"id": flow_id, **flow_data})

            return results
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    # Utility methods

    def backup_flow(self, flow_id: str, backup_path: str) -> bool:
        """
        Save a backup copy of a flow.

        Args:
            flow_id: Flow ID
            backup_path: Backup path

        Returns:
            True if successful
        """
        try:
            flow_data = self.load_flow(flow_id)
            if flow_data:
                # Save to the backup path
                backup_config = {
                    "backup_of": flow_id,
                    "backed_up_at": datetime.now().isoformat(),
                    "data": flow_data,
                }
                return self._storage.save_flow(backup_path, backup_config)
            return False
        except Exception as e:
            logger.error(f"Error backing up flow {flow_id}: {e}")
            return False

    def get_flow_stats(self, flow_id: str) -> Dict[str, Any]:
        """
        Get statistics for a flow.

        Args:
            flow_id: Flow ID

        Returns:
            Statistics dictionary
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
            logger.error(f"Error getting stats for flow {flow_id}: {e}")
            return {}

    def set_storage(self, storage: StorageInterface):
        """
        Change the storage backend.

        Args:
            storage: New storage implementation
        """
        self._storage = storage
        self._initialized = False
        logger.info(f"Storage backend changed: {type(storage).__name__}")

    def get_storage_type(self) -> str:
        """
        Get the current storage type.

        Returns:
            Storage type name
        """
        return type(self._storage).__name__
