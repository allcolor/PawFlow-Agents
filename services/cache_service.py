# Cache Service

"""
In-memory key-value cache service with TTL.
"""

import time
from typing import Any, Dict, Optional, Tuple
from core.base_service import BaseService
from core import ServiceFactory


class CacheService(BaseService):
    """In-memory key-value cache with expiration."""

    TYPE = "cacheService"
    VERSION = "1.0.0"
    NAME = "Cache Service"
    DESCRIPTION = "Cache clé-valeur en mémoire avec TTL"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_size = self.config.get("max_size", 10000)
        self.ttl_seconds = self.config.get("ttl_seconds", 3600)
        self._cache: Dict[str, Tuple[Any, float]] = {}

    def _create_connection(self) -> Dict:
        return self._cache

    def _close_connection(self):
        self._cache.clear()

    def put(self, key: str, value: Any):
        if len(self._cache) >= self.max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        self._cache[key] = (value, time.time())

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._cache:
            return default
        value, ts = self._cache[key]
        if time.time() - ts > self.ttl_seconds:
            del self._cache[key]
            return default
        return value

    def delete(self, key: str):
        self._cache.pop(key, None)

    def contains(self, key: str) -> bool:
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        if time.time() - ts > self.ttl_seconds:
            del self._cache[key]
            return False
        return True

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'max_size': {
                'type': 'integer', 'required': False, 'default': 10000,
                'description': 'Taille maximale du cache',
            },
            'ttl_seconds': {
                'type': 'integer', 'required': False, 'default': 3600,
                'description': 'Durée de vie des entrées (secondes)',
            },
        }


ServiceFactory.register(CacheService)
