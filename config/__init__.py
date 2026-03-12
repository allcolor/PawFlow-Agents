# Configuration Storage Module

"""
Module de configuration et de stockage.
Supporte différents backends de stockage (fichier, git, base de données).
"""

# Export des classes de stockage pour accessibilité
from config.storage.filesystem_storage import FilesystemStorage
from config.storage.sqlite_storage import SqliteStorage
from config.storage.git_storage import GitStorage
from config.storage.postgres_storage import PostgresStorage

import os
import json
import yaml
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod


class StorageType(Enum):
    """Types de stockage supportés."""
    FILESYSTEM = "filesystem"
    GIT = "git"
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class ConfigStorage(ABC):
    """Interface abstraite pour le stockage."""
    
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
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder une tâche custom."""
        pass
    
    @abstractmethod
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un service."""
        pass


class ConfigManager:
    """Manager principal de configuration."""
    
    def __init__(self, storage_type: StorageType, config: Dict[str, Any]):
        """
        Initialiser le manager de configuration.
        
        Args:
            storage_type: Type de stockage à utiliser
            config: Configuration du stockage
        """
        self.storage_type = storage_type
        self.storage = self._create_storage(storage_type, config)
    
    def _create_storage(self, storage_type: StorageType, config: Dict[str, Any]):
        """Factory pour créer le bon storage."""
        if storage_type == StorageType.FILESYSTEM:
            from config.storage.filesystem_storage import FilesystemStorage
            return FilesystemStorage(config)
        elif storage_type == StorageType.GIT:
            from config.storage.git_storage import GitStorage
            return GitStorage(config)
        elif storage_type == StorageType.SQLITE:
            from config.storage.sqlite_storage import SqliteStorage
            return SqliteStorage(config)
        elif storage_type == StorageType.POSTGRES:
            from config.storage.postgres_storage import PostgresStorage
            return PostgresStorage(config)
        else:
            raise ValueError(f"Type de stockage non supporté: {storage_type}")
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un flux."""
        return self.storage.save_flow(flow_id, config)
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Charger un flux."""
        return self.storage.load_flow(flow_id)
    
    def delete_flow(self, flow_id: str) -> bool:
        """Supprimer un flux."""
        return self.storage.delete_flow(flow_id)
    
    def list_flows(self) -> List[str]:
        """Lister tous les flux."""
        return self.storage.list_flows()


class Config:
    """Configuration globale de l'application."""
    
    def __init__(self):
        """Initialiser la configuration par défaut."""
        # Stockage
        self.storage_type = StorageType.FILESYSTEM
        self.storage_config: Dict[str, Any] = {}
        
        # Paths
        self.flows_path: str = os.path.join(os.path.dirname(__file__), '..', 'flows')
        self.tasks_path: str = os.path.join(os.path.dirname(__file__), '..', 'tasks')
        self.services_path: str = os.path.join(os.path.dirname(__file__), '..', 'services')
        self.logs_path: str = os.path.join(os.path.dirname(__file__), '..', 'logs')
        
        # Runtime
        self.max_workers: int = 10
        self.max_retries: int = 3
        self.retry_delay: int = 5
        self.timeout: int = 300
        
        # GUI
        self.gui_host: str = "0.0.0.0"
        self.gui_port: int = 8501
        
        # Variables globales
        self.global_variables: Dict[str, Any] = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertir en dictionnaire."""
        return {
            'storage_type': self.storage_type.value,
            'storage_config': self.storage_config,
            'flows_path': self.flows_path,
            'tasks_path': self.tasks_path,
            'services_path': self.services_path,
            'logs_path': self.logs_path,
            'max_workers': self.max_workers,
            'max_retries': self.max_retries,
            'retry_delay': self.retry_delay,
            'timeout': self.timeout,
            'gui_host': self.gui_host,
            'gui_port': self.gui_port,
            'global_variables': self.global_variables
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Créer Config depuis un dictionnaire."""
        config = cls()
        
        if 'storage_type' in data:
            config.storage_type = StorageType(data['storage_type'])
        
        if 'storage_config' in data:
            config.storage_config = data['storage_config']
        
        if 'flows_path' in data:
            config.flows_path = data['flows_path']
        
        if 'tasks_path' in data:
            config.tasks_path = data['tasks_path']
        
        if 'services_path' in data:
            config.services_path = data['services_path']
        
        if 'logs_path' in data:
            config.logs_path = data['logs_path']
        
        if 'max_workers' in data:
            config.max_workers = data['max_workers']
        
        if 'max_retries' in data:
            config.max_retries = data['max_retries']
        
        if 'retry_delay' in data:
            config.retry_delay = data['retry_delay']
        
        if 'timeout' in data:
            config.timeout = data['timeout']
        
        if 'gui_host' in data:
            config.gui_host = data['gui_host']
        
        if 'gui_port' in data:
            config.gui_port = data['gui_port']
        
        if 'global_variables' in data:
            config.global_variables = data['global_variables']
        
        return config


__all__ = [
    "FilesystemStorage",
    "SqliteStorage",
    "GitStorage",
    "PostgresStorage",
    "StorageType",
    "ConfigStorage",
    "ConfigManager",
    "Config",
    "get_config",
    "set_config",
    "save_config_to_file",
    "load_config_from_file",
]

# Instance globale de configuration
_global_config: Optional[Config] = None


def get_config() -> Config:
    """Obtenir l'instance de configuration globale."""
    global _global_config
    if _global_config is None:
        _global_config = Config()
    return _global_config


def set_config(config: Config):
    """Définir l'instance de configuration globale."""
    global _global_config
    _global_config = config


def save_config_to_file(filepath: str):
    """Sauvegarder la configuration dans un fichier."""
    config = get_config()
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(config.to_dict(), f, indent=2)


def load_config_from_file(filepath: str) -> Config:
    """Charger la configuration depuis un fichier."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    config = Config.from_dict(data)
    set_config(config)
    return config