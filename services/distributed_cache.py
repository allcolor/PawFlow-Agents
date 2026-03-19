"""
DistributedMapCacheService - Cache distribue cle-valeur.

Backends:
- memory: dict Python thread-safe (defaut, single process)
- redis: Redis pour la synchronisation multi-process/multi-machine

Usage NiFi-like: les tasks FetchDistributedMapCache et PutDistributedMapCache
utilisent ce service pour partager des donnees entre flux.
"""

import threading
import logging
from typing import Dict, Any, Optional, List
from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

try:
    import redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


class DistributedMapCacheService(BaseService):
    """Service de cache distribue cle-valeur."""

    TYPE = "distributedMapCache"
    VERSION = "1.0.0"
    NAME = "Distributed Map Cache"
    DESCRIPTION = "Cache distribue cle-valeur (in-memory ou Redis)"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.backend = self.config.get("backend", "memory")
        self.redis_url = self.config.get("redis_url", "redis://localhost:6379/0")
        self.key_prefix = self.config.get("key_prefix", "openpaw:")
        self.default_ttl = int(self.config.get("default_ttl", 0))

        self._memory_store: Dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._redis_client = None

    def _create_connection(self):
        if self.backend == "redis":
            if not HAS_REDIS:
                raise ImportError("redis package requis: pip install redis")
            self._redis_client = redis.from_url(self.redis_url)
            self._redis_client.ping()
            logger.info(f"Connecte a Redis: {self.redis_url}")

    def _close_connection(self):
        if self._redis_client:
            self._redis_client.close()
            self._redis_client = None

    def _get_connection(self):
        return self._redis_client

    def put(self, key: str, value: bytes, ttl: int = 0) -> bool:
        """
        Stocker une valeur.

        Args:
            key: Cle
            value: Valeur (bytes)
            ttl: Time-to-live en secondes (0 = pas d'expiration)

        Returns:
            True si succes
        """
        full_key = self.key_prefix + key
        ttl = ttl or self.default_ttl

        if self.backend == "redis" and self._redis_client:
            if ttl > 0:
                self._redis_client.setex(full_key, ttl, value)
            else:
                self._redis_client.set(full_key, value)
            return True
        else:
            with self._lock:
                self._memory_store[full_key] = value
            return True

    def get(self, key: str) -> Optional[bytes]:
        """
        Recuperer une valeur.

        Args:
            key: Cle

        Returns:
            Valeur ou None si absent
        """
        full_key = self.key_prefix + key

        if self.backend == "redis" and self._redis_client:
            return self._redis_client.get(full_key)
        else:
            with self._lock:
                return self._memory_store.get(full_key)

    def delete(self, key: str) -> bool:
        """Supprimer une cle."""
        full_key = self.key_prefix + key

        if self.backend == "redis" and self._redis_client:
            return self._redis_client.delete(full_key) > 0
        else:
            with self._lock:
                return self._memory_store.pop(full_key, None) is not None

    def contains(self, key: str) -> bool:
        """Verifier si une cle existe."""
        full_key = self.key_prefix + key

        if self.backend == "redis" and self._redis_client:
            return self._redis_client.exists(full_key) > 0
        else:
            with self._lock:
                return full_key in self._memory_store

    def keys(self, pattern: str = "*") -> List[str]:
        """Lister les cles correspondant au pattern."""
        full_pattern = self.key_prefix + pattern

        if self.backend == "redis" and self._redis_client:
            raw_keys = self._redis_client.keys(full_pattern)
            prefix_len = len(self.key_prefix)
            return [k.decode('utf-8')[prefix_len:] for k in raw_keys]
        else:
            with self._lock:
                prefix_len = len(self.key_prefix)
                if pattern == "*":
                    return [k[prefix_len:] for k in self._memory_store]
                # Simple wildcard matching
                import fnmatch
                return [
                    k[prefix_len:] for k in self._memory_store
                    if fnmatch.fnmatch(k, full_pattern)
                ]

    def size(self) -> int:
        """Nombre d'entrees dans le cache."""
        if self.backend == "redis" and self._redis_client:
            return len(self._redis_client.keys(self.key_prefix + "*"))
        else:
            with self._lock:
                return len(self._memory_store)

    def clear(self):
        """Vider le cache."""
        if self.backend == "redis" and self._redis_client:
            keys = self._redis_client.keys(self.key_prefix + "*")
            if keys:
                self._redis_client.delete(*keys)
        else:
            with self._lock:
                self._memory_store.clear()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "backend": {
                "type": "select", "required": False,
                "options": ["memory", "redis"],
                "default": "memory",
                "description": "Backend de stockage (memory ou redis)",
            },
            "redis_url": {
                "type": "string", "required": False,
                "default": "redis://localhost:6379/0",
                "description": "URL de connexion Redis",
            },
            "key_prefix": {
                "type": "string", "required": False,
                "default": "openpaw:",
                "description": "Prefixe pour toutes les cles",
            },
            "default_ttl": {
                "type": "integer", "required": False,
                "default": 0,
                "description": "TTL par defaut en secondes (0 = pas d'expiration)",
            },
        }


# Singleton in-memory instance for tasks that don't specify a service
_default_cache = None
_default_lock = threading.Lock()


def get_default_cache() -> DistributedMapCacheService:
    """Obtenir le cache distribue par defaut (in-memory)."""
    global _default_cache
    if _default_cache is None:
        with _default_lock:
            if _default_cache is None:
                _default_cache = DistributedMapCacheService({"backend": "memory"})
    return _default_cache


ServiceFactory.register(DistributedMapCacheService)
