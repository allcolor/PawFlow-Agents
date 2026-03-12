"""
Distributed Map Cache Tasks - Cache distribue pour synchronisation inter-processus.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask
from services.distributed_cache import get_default_cache


class FetchDistributedMapCacheTask(BaseTask):
    """Recupere une valeur du DistributedMapCacheService par cle."""

    TYPE = "fetchDistributedMapCache"
    VERSION = "1.0.0"
    NAME = "Fetch Distributed Cache"
    DESCRIPTION = "Recupere une valeur du cache distribue par cle"
    ICON = "search"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache_key = self.config.get("cache_key", "")

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.cache_key:
            raise TaskError("Le parametre 'cache_key' est requis.")

        cache = get_default_cache()
        value = cache.get(self.cache_key)

        if value is None:
            raise TaskError(f"Cache miss: cle '{self.cache_key}' introuvable")

        flowfile.set_content(value)
        flowfile.set_attribute("cache.hit", "true")
        flowfile.set_attribute("cache.key", self.cache_key)

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "cache_key": {
                "type": "string", "required": True,
                "description": "Cle de la valeur a recuperer",
            },
        }


class PutDistributedMapCacheTask(BaseTask):
    """Stocke le contenu du FlowFile dans le DistributedMapCacheService."""

    TYPE = "putDistributedMapCache"
    VERSION = "1.0.0"
    NAME = "Put Distributed Cache"
    DESCRIPTION = "Stocke le contenu du FlowFile dans le cache distribue"
    ICON = "save"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache_key = self.config.get("cache_key", "")
        self.ttl = self.config.get("ttl", 0)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.cache_key:
            raise TaskError("Le parametre 'cache_key' est requis.")

        cache = get_default_cache()
        cache.put(self.cache_key, flowfile.get_content(), self.ttl)

        flowfile.set_attribute("cache.key", self.cache_key)

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "cache_key": {
                "type": "string", "required": True,
                "description": "Cle sous laquelle stocker le contenu",
            },
            "ttl": {
                "type": "integer", "required": False, "default": 0,
                "description": "Duree de vie en secondes (0 = pas d'expiration)",
            },
        }


TaskFactory.register(FetchDistributedMapCacheTask)
TaskFactory.register(PutDistributedMapCacheTask)
