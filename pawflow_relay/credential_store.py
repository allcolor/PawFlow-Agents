"""OS credential-store boundary for PawFlow Relay server secrets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

SERVICE_NAME = "org.allcolor.pawflow.relay"
SECRET_FIELDS = ("gateway_key", "gateway_cookie", "session_token")
_backend_override = None


class CredentialStoreUnavailable(RuntimeError):
    pass


class CredentialBackend(Protocol):
    priority: float

    def get_password(self, service: str, username: str) -> str | None:
        ...

    def set_password(self, service: str, username: str, password: str) -> None:
        ...

    def delete_password(self, service: str, username: str) -> None:
        ...


def _backend() -> CredentialBackend:
    if _backend_override is not None:
        return _backend_override
    try:
        import keyring
        backend = keyring.get_keyring()
    except (ImportError, RuntimeError) as exc:
        raise CredentialStoreUnavailable(
            "No OS credential-store backend is available for PawFlow Relay"
        ) from exc
    if float(getattr(backend, "priority", 0)) <= 0:
        raise CredentialStoreUnavailable(
            "No secure OS credential-store backend is available for PawFlow Relay"
        )
    return backend


def available() -> bool:
    try:
        _backend()
        return True
    except CredentialStoreUnavailable:
        return False


def _account(scope: Path, server_name: str, field: str) -> str:
    scope_hash = hashlib.sha256(str(scope.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{scope_hash}:{server_name}:{field}"


def get(scope: Path, server_name: str, field: str) -> str:
    if field not in SECRET_FIELDS:
        raise ValueError(f"unsupported relay credential field: {field}")
    return _backend().get_password(
        SERVICE_NAME, _account(scope, server_name, field)
    ) or ""


def set(scope: Path, server_name: str, field: str, value: str) -> None:
    if field not in SECRET_FIELDS:
        raise ValueError(f"unsupported relay credential field: {field}")
    if not value:
        delete(scope, server_name, field)
        return
    _backend().set_password(
        SERVICE_NAME, _account(scope, server_name, field), value
    )


def delete(scope: Path, server_name: str, field: str | None = None) -> None:
    fields = (field,) if field else SECRET_FIELDS
    backend = _backend()
    for item in fields:
        if item not in SECRET_FIELDS:
            raise ValueError(f"unsupported relay credential field: {item}")
        account = _account(scope, server_name, item)
        try:
            backend.delete_password(SERVICE_NAME, account)
        except Exception as exc:
            if exc.__class__.__name__ not in {
                "PasswordDeleteError", "KeyringError"
            }:
                raise


def load(scope: Path, server_name: str) -> dict[str, str]:
    return {
        field: get(scope, server_name, field)
        for field in SECRET_FIELDS
    }
