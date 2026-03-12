"""
Cache Tasks - Stocker et recuperer des donnees en cache.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

# Module-level cache storage
_CACHE: Dict[str, bytes] = {}


class PutCacheTask(BaseTask):
    """Store FlowFile content in cache under a given key."""

    TYPE = "putCache"
    VERSION = "1.0.0"
    NAME = "Put Cache"
    DESCRIPTION = "Stocker le contenu du FlowFile en cache"
    ICON = "save"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache_key = self.config.get('cache_key', '')
        self.ttl = self.config.get('ttl', 0)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.cache_key:
            raise TaskError("Le parametre 'cache_key' est requis.")

        content = flowfile.get_content()
        _CACHE[self.cache_key] = content
        flowfile.set_attribute('cache.key', self.cache_key)

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'cache_key': {
                'type': 'string', 'required': True,
                'description': 'Cle sous laquelle stocker le contenu',
            },
            'ttl': {
                'type': 'integer', 'required': False,
                'description': 'Duree de vie en secondes (0 = pas d\'expiration)',
                'default': 0,
            },
        }


class GetCacheTask(BaseTask):
    """Retrieve content from cache and set as FlowFile content."""

    TYPE = "getCache"
    VERSION = "1.0.0"
    NAME = "Get Cache"
    DESCRIPTION = "Recuperer le contenu du cache par cle"
    ICON = "search"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache_key = self.config.get('cache_key', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.cache_key:
            raise TaskError("Le parametre 'cache_key' est requis.")

        cached_content = _CACHE.get(self.cache_key)

        if cached_content is None:
            raise TaskError(f"Cache miss: cle '{self.cache_key}' introuvable")

        flowfile.set_content(cached_content)
        flowfile.set_attribute('cache.hit', 'true')

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'cache_key': {
                'type': 'string', 'required': True,
                'description': 'Cle a recuperer du cache',
            },
        }


TaskFactory.register(PutCacheTask)
TaskFactory.register(GetCacheTask)
