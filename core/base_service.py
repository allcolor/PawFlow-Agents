# Base Service Implementation

"""
Implémentation de base pour tous les services.
Fournit la gestion du cycle de vie et les fonctionnalités communes.
"""

from typing import Dict, Any, Optional
from abc import ABC
from core import Service, ServiceError
from core.variable_resolver import VariableResolverMixin
import logging


class BaseService(VariableResolverMixin, Service, ABC):
    """
    Implémentation de base pour tous les services.
    
    Gère le cycle de vie de la connexion, la validation et les utilitaires communs.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser le service.
        
        Args:
            config: Configuration du service
        """
        # Sauvegarder la configuration originale
        self._original_config = config.copy()
        
        # Résoudre les variables dans la configuration
        resolved_config = self._resolve_variables(config)
        
        # Initialiser la classe parente
        super().__init__(resolved_config)
        
        # Stocker la configuration résolue
        self.config = resolved_config

        # État de la connexion
        self._connection: Optional[Any] = None
        self._initialized = False

    def connect(self):
        """
        Établir la connexion au service.
        
        Cette méthode abstraite doit être implémentée par les sous-classes.
        
        Raises:
            ServiceError: Si la connexion échoue
        """
        try:
            self._connection = self._create_connection()
            self._initialized = True
            self._log_connection("Connected successfully")
        except Exception as e:
            raise ServiceError(f"Échec de connexion: {e}")
    
    def disconnect(self):
        """
        Fermer la connexion proprement.
        
        Cette méthode abstraite doit être implémentée par les sous-classes.
        """
        if self._connection is not None:
            try:
                self._close_connection()
                self._log_connection("Disconnected")
            except Exception as e:
                self._log_connection(f"Error during disconnection: {e}", "ERROR")
            finally:
                self._connection = None
                self._initialized = False
    
    def _create_connection(self):
        """
        Créer la connexion réelle (à implémenter par les sous-classes).
        
        Returns:
            Instance de connexion
            
        Raises:
            NotImplementedError: Si non implémenté
        """
        raise NotImplementedError("Subclasses must implement _create_connection()")
    
    def _close_connection(self):
        """
        Fermer la connexion réelle (à implémenter par les sous-classes).
        
        Raises:
            NotImplementedError: Si non implémenté
        """
        raise NotImplementedError("Subclasses must implement _close_connection()")
    
    def _get_connection(self) -> Any:
        """
        Obtenir la connexion, en établissant si nécessaire.
        
        Returns:
            Instance de connexion
            
        Raises:
            ServiceError: Si la connexion n'est pas établie
        """
        if self._connection is None:
            self.connect()
        
        if not self._initialized:
            raise ServiceError("Service not initialized")
        
        return self._connection
    
    def _log_connection(self, message: str, level: str = "INFO"):
        """
        Logguer des messages de connexion.
        
        Args:
            message: Message à logguer
            level: Niveau de log
        """
        logger = logging.getLogger(self.__class__.__name__)
        
        if level == "INFO":
            logger.info(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)
    
    def is_connected(self) -> bool:
        """
        Vérifier si le service est connecté.
        
        Returns:
            True si connecté
        """
        return self._connection is not None and self._initialized
    
    def ensure_connected(self):
        """
        S'assurer que le service est connecté, sinon établir la connexion.
        """
        if not self.is_connected():
            self.connect()
    
    def validate_config(self) -> bool:
        """
        Valider la configuration du service.
        
        Returns:
            True si valide
        """
        try:
            self._validate_config()
            return True
        except ValueError:
            return False
    
    def get_config(self) -> Dict[str, Any]:
        """
        Obtenir la configuration du service.
        
        Returns:
            Configuration résolue
        """
        return self.config.copy()
    
    def get_original_config(self) -> Dict[str, Any]:
        """
        Obtenir la configuration originale (avant résolution de variables).
        
        Returns:
            Configuration originale
        """
        return self._original_config.copy()
    
    def reset(self):
        """
        Réinitialiser le service (fermer la connexion).
        """
        self.disconnect()
        self._initialized = False
        self._connection = None