# Base Service Implementation

"""
Base implementation for all services.
Provides lifecycle management and common functionality.
"""

from typing import Dict, Any, Optional
from abc import ABC
from core import Service, ServiceError
from core.variable_resolver import VariableResolverMixin
import logging


class BaseService(VariableResolverMixin, Service, ABC):
    """
    Base implementation for all services.
    
    Manages connection lifecycle, validation and common utilities.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the service.

        Args:
            config: Service configuration (may be LazyResolveDict)
        """
        from core.expression import LazyResolveDict
        # ALWAYS wrap config in LazyResolveDict — every .get() resolves
        # expressions automatically. No service needs manual resolution.
        self._original_config = config if isinstance(config, dict) else {}
        if not isinstance(config, LazyResolveDict):
            config = LazyResolveDict(config or {})
        super().__init__(config)
        self.config = config

        # Connection state
        self._connection: Optional[Any] = None
        self._initialized = False

    def connect(self):
        """
        Establish the service connection.
        
        This abstract method must be implemented by subclasses.
        
        Raises:
            ServiceError: If connection fails
        """
        try:
            self._connection = self._create_connection()
            self._initialized = True
            self._log_connection("Connected successfully")
        except Exception as e:
            raise ServiceError(f"Connection failed: {e}")
    
    def disconnect(self):
        """
        Close the connection cleanly.
        
        This abstract method must be implemented by subclasses.
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
        Create the actual connection (to be implemented by subclasses).
        
        Returns:
            Connection instance
            
        Raises:
            NotImplementedError: If not implemented
        """
        raise NotImplementedError("Subclasses must implement _create_connection()")
    
    def _close_connection(self):
        """
        Close the actual connection (to be implemented by subclasses).
        
        Raises:
            NotImplementedError: If not implemented
        """
        raise NotImplementedError("Subclasses must implement _close_connection()")
    
    def _get_connection(self) -> Any:
        """
        Get the connection, establishing it if necessary.
        
        Returns:
            Connection instance
            
        Raises:
            ServiceError: If connection is not established
        """
        if self._connection is None:
            self.connect()
        
        if not self._initialized:
            raise ServiceError("Service not initialized")
        
        return self._connection
    
    def _log_connection(self, message: str, level: str = "INFO"):
        """
        Log connection messages.
        
        Args:
            message: Message to log
            level: Log level
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
        Check if the service is connected.
        
        Returns:
            True if connected
        """
        return self._connection is not None and self._initialized
    
    def ensure_connected(self):
        """
        Ensure the service is connected, otherwise establish connection.
        """
        if not self.is_connected():
            self.connect()
    
    def validate_config(self) -> bool:
        """
        Validate service configuration.
        
        Returns:
            True if valid
        """
        try:
            self._validate_config()
            return True
        except ValueError:
            return False
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get the service configuration.
        
        Returns:
            Resolved configuration
        """
        return self.config.copy()
    
    def get_original_config(self) -> Dict[str, Any]:
        """
        Get the original configuration (before variable resolution).
        
        Returns:
            Original configuration
        """
        return self._original_config.copy()
    
    def reset(self):
        """
        Reset the service (close connection).
        """
        self.disconnect()
        self._initialized = False
        self._connection = None
