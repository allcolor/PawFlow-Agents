"""SSL Context Service — provides TLS certificates for HTTP listeners.

Reusable service that manages SSL/TLS context. Can be shared between
multiple HTTPListenerService instances.

Config:
    certfile: str           — path to PEM certificate file (required)
    keyfile: str            — path to PEM private key file (optional if in certfile)
    keyfile_password: str   — password for encrypted key (optional)
    ca_certfile: str        — path to CA certificate for client verification (optional)
    verify_client: bool     — require client certificates (default False)
    protocol: str           — TLS protocol version (default "TLS")
    ciphers: str            — allowed cipher suites (optional)
"""

import logging
import ssl
from typing import Any, Dict, Optional

from core.base_service import BaseService

logger = logging.getLogger(__name__)


class SSLContextService(BaseService):
    """Provides an ssl.SSLContext for server or client use."""

    TYPE = "sslContext"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._ssl_context: Optional[ssl.SSLContext] = None

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "certfile": {"type": "string", "required": True, "default": "", "description": "Path to PEM certificate file"},
            "keyfile": {"type": "string", "required": False, "default": "", "description": "Path to PEM private key file"},
            "keyfile_password": {"type": "string", "required": False, "default": "", "description": "Password for encrypted key"},
            "ca_certfile": {"type": "string", "required": False, "default": "", "description": "Path to CA certificate for client verification"},
            "verify_client": {"type": "boolean", "required": False, "default": False, "description": "Require client certificates"},
            "ciphers": {"type": "string", "required": False, "default": "", "description": "Allowed cipher suites"},
        }

    def _create_connection(self):
        """Build the SSL context."""
        certfile = self.config.get("certfile", "")
        keyfile = self.config.get("keyfile", "")
        keyfile_password = self.config.get("keyfile_password", "")
        ca_certfile = self.config.get("ca_certfile", "")
        verify_client = self.config.get("verify_client", False)
        ciphers = self.config.get("ciphers", "")

        if not certfile:
            raise ValueError("SSLContextService requires 'certfile' config")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(
            certfile=certfile,
            keyfile=keyfile or None,
            password=keyfile_password or None,
        )

        if ca_certfile:
            ctx.load_verify_locations(ca_certfile)
            if verify_client:
                ctx.verify_mode = ssl.CERT_REQUIRED
            else:
                ctx.verify_mode = ssl.CERT_OPTIONAL

        if ciphers:
            ctx.set_ciphers(ciphers)

        self._ssl_context = ctx
        logger.info(f"SSLContextService loaded cert from {certfile}")
        return ctx

    def _close_connection(self):
        """Nothing to close."""
        self._ssl_context = None

    def get_ssl_context(self) -> ssl.SSLContext:
        """Get the configured SSL context."""
        if self._ssl_context is None:
            self.connect()
        return self._ssl_context


# Auto-register
from core import ServiceFactory
ServiceFactory.register(SSLContextService)
