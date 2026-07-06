# HTTP Client Service

"""
Shared HTTP client service with a persistent session.
"""

from typing import Any, Dict, Optional
from core.base_service import BaseService
from core import ServiceFactory, ServiceError
from core.relay_proxy_url import CONV_RELAY_EXPR, is_relay_proxy_url, resolve_relay_aware_url

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
        self.allow_private_base_url = str(
            self.config.get("allow_private_base_url", True)).lower() not in {"0", "false", "no", "off"}
        self.timeout = self.config.get("timeout", 30)
        self.headers = self.config.get("headers", {})
        self.verify_ssl = self.config.get("verify_ssl", True)
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""

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
            raw = url
        else:
            raw = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"
        if not raw or not is_relay_proxy_url(raw):
            return raw
        return resolve_relay_aware_url(
            raw,
            user_id=self._runtime_user_id,
            conversation_id=self._runtime_conversation_id,
            agent_name=self._runtime_agent_name,
            allow_private=self.allow_private_base_url,
            service_name="HTTP client",
            transform_relay=True,
        )

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
                'description': f'Base URL for requests. Relay URLs use relay://{CONV_RELAY_EXPR}/host:port/path.',
            },
            'allow_private_base_url': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Allow direct private/loopback base_url targets. Kept enabled for compatibility with internal HTTP clients.',
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
