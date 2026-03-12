"""OAuthRedirect Task — Redirect user to OAuth2 provider for authentication.

Generates a CSRF state token, builds the authorization URL, and returns
an HTTP 302 redirect response.

Flow pattern:
    httpReceiver (GET /auth/login) → oauthRedirect → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class OAuthRedirectTask(BaseTask):
    """Redirect to OAuth2 provider for authentication."""

    TYPE = "oauthRedirect"
    VERSION = "1.0.0"
    NAME = "OAuth2 Redirect"
    DESCRIPTION = "Redirect user to OAuth2 provider for login"
    ICON = "key"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "oauth_service_id": {
                "type": "string", "required": False, "default": "oauth",
                "description": "ID of the oauthProvider service",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("oauth_service_id", "oauth")

        # Try to get the service from the flow context
        service = getattr(self, '_services', {}).get(service_id)
        if service is None:
            # Fallback: resolve from flowfile attribute (set by flow executor)
            service = flowfile.get_attribute("_service." + service_id)
        if service is None:
            # Last resort: use config directly to build inline
            service = self._build_inline_service()

        if service is None:
            flowfile.set_content(b'{"error": "OAuth service not configured"}')
            flowfile.set_attribute("http.response.status", "500")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        state = service.generate_state()
        authorize_url = service.get_authorize_url(state)

        logger.info(f"OAuth2 redirect to {service.provider} (state={state[:8]}...)")

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", authorize_url)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        return [flowfile]

    def _build_inline_service(self):
        """Build OAuthProviderService from task config (when service isn't injected)."""
        from services.oauth_provider_service import OAuthProviderService
        # Check if we have oauth config directly in task config
        if self.config.get("client_id"):
            return OAuthProviderService(self.config)
        return None

    def set_services(self, services: Dict[str, Any]):
        """Called by the flow executor to inject services."""
        self._services = services


TaskFactory.register(OAuthRedirectTask)
