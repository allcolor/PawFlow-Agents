# HTTP Client Service

"""
Shared HTTP client service with a persistent session.
"""

from typing import Any, Dict, Optional
from core.base_service import BaseService
from core import ServiceFactory, ServiceError

try:
    import requests
except ImportError:
    requests = None


class HTTPClientService(BaseService):
    """Shared HTTP client with session, headers, and authentication."""

    TYPE = "httpClientService"
    VERSION = "1.0.0"
    NAME = "HTTP Client Service"
    DESCRIPTION = "Client HTTP partagé avec session persistante"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = self.config.get("base_url", "")
        self.timeout = self.config.get("timeout", 30)
        self.headers = self.config.get("headers", {})
        self.verify_ssl = self.config.get("verify_ssl", True)

    def _create_connection(self):
        if requests is None:
            raise ServiceError("requests non installé. pip install requests")
        session = requests.Session()
        session.headers.update(self.headers)
        session.verify = self.verify_ssl
        return session

    def _close_connection(self):
        if self._connection:
            self._connection.close()

    def _build_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"

    def request(self, method: str, url: str, **kwargs) -> Any:
        session = self._get_connection()
        return session.request(method, self._build_url(url), timeout=self.timeout, **kwargs)

    def get(self, url: str, **kwargs): return self.request("GET", url, **kwargs)
    def post(self, url: str, **kwargs): return self.request("POST", url, **kwargs)
    def put(self, url: str, **kwargs): return self.request("PUT", url, **kwargs)
    def delete(self, url: str, **kwargs): return self.request("DELETE", url, **kwargs)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'base_url': {
                'type': 'string', 'required': False, 'default': '',
                'description': 'URL de base pour les requêtes',
            },
            'timeout': {
                'type': 'integer', 'required': False, 'default': 30,
                'description': 'Timeout en secondes',
            },
            'headers': {
                'type': 'map', 'required': False, 'default': {},
                'description': 'Headers HTTP par défaut',
            },
            'verify_ssl': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Vérifier les certificats SSL',
            },
        }


ServiceFactory.register(HTTPClientService)
