"""Provider-neutral service used to fetch external secrets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from core import ServiceFactory
from core.base_service import BaseService
from core.secret_provider import (
    ProviderValue,
    SecretProviderAdapter,
    SecretProviderFactory,
    SecretResolveContext,
)


class SecretProviderService(BaseService):
    """Configured, read-only connection to an external secret provider."""

    TYPE = "secretProvider"
    VERSION = "1.0.0"
    NAME = "External Secret Provider"
    DESCRIPTION = "Read-only provider used by local secret aliases"
    CATEGORY = "security"
    TAGS: ClassVar[list[str]] = ["secrets", "credentials", "security"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "provider": {
                "type": "string", "required": True,
                "description": (
                    "Registered adapter name, for example aws_secrets_manager, "
                    "keeper, vault, azure_key_vault or gcp_secret_manager."),
            },
            "provider_config": {
                "type": "textarea", "required": False, "default": "{}",
                "sensitive": True,
                "description": "Provider authentication and connection settings as JSON.",
            },
            "cache_ttl_seconds": {
                "type": "integer", "required": False, "default": 300,
                "description": "In-memory materialized-value cache lifetime; 0 disables caching.",
            },
            "timeout_seconds": {
                "type": "integer", "required": False, "default": 15,
                "description": "Maximum provider request duration.",
            },
        }

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        if value in (None, ""):
            return {}
        if not isinstance(value, str):
            # ServiceRegistry encrypts sensitive scalar strings. Accepting a
            # mapping here could persist provider credentials as plaintext.
            raise TypeError("provider_config must be a JSON string")
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider_config must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise TypeError("provider_config must be a JSON object")
        return parsed

    @property
    def cache_ttl_seconds(self) -> float:
        return max(0.0, float(self.config.get("cache_ttl_seconds", 300)))

    @property
    def provider_name(self) -> str:
        return str(self.config.get("provider") or "").strip().lower()

    def _create_connection(self) -> SecretProviderAdapter:
        if not self.provider_name:
            raise ValueError("provider is required")
        import core.secret_provider_adapters  # noqa: F401
        provider_config = dict(self._mapping(
            self.config.get("provider_config", {})))
        provider_config.setdefault(
            "timeout_seconds", float(self.config.get("timeout_seconds", 15)))
        return SecretProviderFactory.create(self.provider_name, provider_config)

    def _close_connection(self):
        if self._connection is not None:
            self._connection.close()

    def fetch(self, locator: Mapping[str, Any],
              context: SecretResolveContext) -> ProviderValue:
        if not isinstance(locator, Mapping):
            raise TypeError("external secret locator must be an object")
        adapter = self._get_connection()
        return adapter.fetch(dict(locator), context)


ServiceFactory.register(SecretProviderService)
