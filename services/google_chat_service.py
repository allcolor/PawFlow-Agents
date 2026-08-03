"""Google Chat app authentication and Chat API client service."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import quote

from core import ServiceFactory
from core.base_service import BaseService


_CHAT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
_CHAT_API = "https://chat.googleapis.com/v1"
_CHAT_ISSUER = "chat@system.gserviceaccount.com"
_CHAT_CERTS_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/"
    + _CHAT_ISSUER
)


class GoogleChatService(BaseService):
    """Verify Google-signed webhooks and send app-authenticated Chat messages."""

    TYPE = "googleChatBot"
    DESCRIPTION = "Google Chat HTTP app authentication and messaging client"
    CATEGORY = "messaging"
    TAGS = ["google", "chat", "bot", "messaging"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_account_json": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Google Cloud service-account JSON for the Chat app.",
            },
            "audience": {
                "type": "string", "required": True,
                "description": "Expected audience of Google-signed webhook ID tokens.",
            },
            "audience_type": {
                "type": "string", "required": False, "default": "endpoint_url",
                "allowable_values": ["endpoint_url", "project_number"],
                "description": "Token mode selected in the Google Chat app configuration.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 30,
                "description": "Chat API HTTP timeout in seconds.",
            },
        }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._service_account_json = self.config.get("service_account_json", "")
        self._audience = str(self.config.get("audience", "") or "").strip()
        self._audience_type = str(
            self.config.get("audience_type", "endpoint_url") or "").strip()
        self._timeout = int(self.config.get("timeout", 30))

    def _service_account_info(self) -> Dict[str, Any]:
        raw = self._service_account_json
        if isinstance(raw, dict):
            info = dict(raw)
        else:
            try:
                info = json.loads(str(raw or ""))
            except json.JSONDecodeError as exc:
                raise ValueError("service_account_json must be valid JSON") from exc
        if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
            raise ValueError("service_account_json is missing required credentials")
        return info

    def _create_connection(self):
        if not self._audience:
            raise ValueError("audience is required")
        if self._audience_type not in {"endpoint_url", "project_number"}:
            raise ValueError("audience_type must be endpoint_url or project_number")
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError("google-auth is required for googleChatBot") from exc
        credentials = service_account.Credentials.from_service_account_info(
            self._service_account_info(), scopes=[_CHAT_SCOPE])
        return AuthorizedSession(credentials)

    def _close_connection(self):
        if self._connection is not None and hasattr(self._connection, "close"):
            self._connection.close()

    def verify_request(self, authorization: str) -> Dict[str, Any]:
        """Verify a Google-issued bearer ID token against the configured audience."""
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise ValueError("missing Google Chat bearer token")
        token = authorization[len(prefix):].strip()
        if not token:
            raise ValueError("empty Google Chat bearer token")
        if not self._audience:
            raise ValueError("audience is required")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import id_token
        except ImportError as exc:
            raise RuntimeError("google-auth is required for Google Chat verification") from exc
        request = Request()
        if self._audience_type == "project_number":
            claims = id_token.verify_token(
                token, request, self._audience, _CHAT_CERTS_URL)
        else:
            claims = id_token.verify_oauth2_token(
                token, request, self._audience)
        if not isinstance(claims, dict):
            raise ValueError("invalid Google Chat token claims")
        if self._audience_type == "project_number":
            if claims.get("iss") != _CHAT_ISSUER:
                raise ValueError("invalid Google Chat token issuer")
        else:
            verified = claims.get("email_verified")
            if verified not in {True, "true", "True", 1}:
                raise ValueError("Google Chat token email is not verified")
            if claims.get("email") != _CHAT_ISSUER:
                raise ValueError("invalid Google Chat token email")
        return claims

    def send_message(self, space_id: str, text: str,
                     thread_name: str = "") -> Dict[str, Any]:
        if not space_id.startswith("spaces/"):
            raise ValueError("Google Chat space_id must start with spaces/")
        if not str(text or "").strip():
            return {}
        session = self._get_connection()
        url = f"{_CHAT_API}/{quote(space_id, safe='/')}/messages"
        body: Dict[str, Any] = {"text": str(text)}
        params: Optional[Dict[str, str]] = None
        if thread_name:
            body["thread"] = {"name": thread_name}
            params = {"messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"}
        response = session.post(url, json=body, params=params, timeout=self._timeout)
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}

    def download_attachment(self, resource_name: str) -> bytes:
        if not resource_name:
            raise ValueError("attachment resource_name is required")
        session = self._get_connection()
        url = f"{_CHAT_API}/media/{quote(resource_name, safe='/')}"
        response = session.get(url, params={"alt": "media"}, timeout=self._timeout)
        response.raise_for_status()
        return bytes(response.content)


ServiceFactory.register(GoogleChatService)
