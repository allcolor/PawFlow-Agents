"""Provider-neutral contracts and registry for external secret backends."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar


class SecretProviderError(RuntimeError):
    """Base error raised while materializing an external secret."""


class SecretProviderNotFoundError(SecretProviderError):
    """Raised when a configured provider adapter is unavailable."""


@dataclass(frozen=True)
class ProviderValue:
    """Opaque value returned by a provider, with optional cache metadata."""

    value: bytes
    version: str = ""
    content_type: str = "text/plain"
    ttl_seconds: float | None = None

    @classmethod
    def from_value(cls, value: Any, **metadata: Any) -> ProviderValue:
        if isinstance(value, bytes):
            raw = value
        else:
            raw = str(value).encode("utf-8")
        return cls(value=raw, **metadata)


@dataclass(frozen=True)
class SecretResolveContext:
    """Identity and scope supplied to an adapter for auditing."""

    secret_name: str
    source_scope: str
    source_scope_id: str
    owner_user_id: str = ""
    conversation_id: str = ""
    agent_name: str = ""


class SecretProviderAdapter(ABC):
    """Read-only adapter implemented by each external secret provider."""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})

    @abstractmethod
    def fetch(self, locator: Mapping[str, Any],
              context: SecretResolveContext) -> ProviderValue:
        """Fetch one secret. Implementations must not mutate the provider."""

    def close(self) -> None:
        """Release provider resources, if any."""


class SecretProviderFactory:
    """Thread-safe registry mapping provider names to adapter classes."""

    _types: ClassVar[dict[str, type[SecretProviderAdapter]]] = {}
    _lock = threading.RLock()

    @classmethod
    def register(cls, provider: str,
                 adapter_type: type[SecretProviderAdapter]) -> None:
        name = str(provider or "").strip().lower()
        if not name:
            raise ValueError("provider is required")
        if not issubclass(adapter_type, SecretProviderAdapter):
            raise TypeError("adapter_type must extend SecretProviderAdapter")
        with cls._lock:
            cls._types[name] = adapter_type

    @classmethod
    def create(cls, provider: str,
               config: Mapping[str, Any] | None = None) -> SecretProviderAdapter:
        name = str(provider or "").strip().lower()
        if not name:
            raise ValueError("provider is required")
        with cls._lock:
            adapter_type = cls._types.get(name)
        if adapter_type is None:
            raise SecretProviderNotFoundError(
                f"Secret provider adapter '{name}' is not installed")
        return adapter_type(config)

    @classmethod
    def list_providers(cls) -> list[str]:
        with cls._lock:
            return sorted(cls._types)


class MemorySecretProvider(SecretProviderAdapter):
    """Deterministic adapter intended for tests and local development only."""

    def fetch(self, locator: Mapping[str, Any],
              context: SecretResolveContext) -> ProviderValue:
        key = str(locator.get("key") or "").strip()
        if not key:
            raise ValueError("memory secret locator requires key")
        values = self.config.get("values")
        if not isinstance(values, Mapping) or key not in values:
            raise SecretProviderError(f"External secret '{key}' was not found")
        return ProviderValue.from_value(values[key], version="memory")


SecretProviderFactory.register("memory", MemorySecretProvider)
