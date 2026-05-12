"""Service proxy for installed PawFlow Package runtime providers."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from core import Service, ServiceError, ServiceFactory


class PackageRuntimeService(Service):
    """Proxy service for installed PFP service providers.

    Installed package services should behave like normal PawFlow services. The
    proxy owns lifecycle state and delegates operations to the package runtime.
    """

    TYPE = "packageRuntime"
    VERSION = "1.0.0"
    NAME = "PFP Package Runtime Service"
    DESCRIPTION = "Runtime proxy for installed PawFlow Package service providers"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "package_runtime": {"type": "object", "required": True},
            "installed_from": {"type": "object", "required": True},
            "operations": {"type": "object", "required": False},
        }

    def validate(self) -> List[str]:
        errors = super().validate()
        runtime = self.config.get("package_runtime")
        if not isinstance(runtime, dict) or not runtime.get("package") or not runtime.get("object_id"):
            errors.append("package_runtime.package and package_runtime.object_id are required")
        installed_from = self.config.get("installed_from")
        if not isinstance(installed_from, dict):
            errors.append("installed_from must be an object")
        return errors

    def connect(self):
        try:
            self._validate_config()
            self._connected = True
            self._connected_at = time.time()
            self._last_error = ""
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            raise

    def disconnect(self):
        self._connected = False
        self._connected_at = 0.0

    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        runtime = self.config.get("package_runtime") or {}
        return {
            "connected": self._connected,
            "connected_at": self._connected_at,
            "last_error": self._last_error,
            "package": runtime.get("package", ""),
            "version": runtime.get("version", ""),
            "object_id": runtime.get("object_id", ""),
            "provides": runtime.get("provides", []),
            "operations": self.get_operations(),
        }

    def get_operations(self) -> Dict[str, Any]:
        operations = self.config.get("operations") or {}
        if isinstance(operations, dict):
            return operations
        if isinstance(operations, list):
            return {str(name): {} for name in operations if str(name or "")}
        return {}

    def get_model_info(self) -> Dict[str, Any]:
        runtime = self.config.get("package_runtime") or {}
        return {
            "provider": "pfp",
            "package": runtime.get("package", ""),
            "version": runtime.get("version", ""),
            "object_id": runtime.get("object_id", ""),
            "provides": runtime.get("provides", []),
            "operations": self.get_operations(),
        }

    def invoke(self, operation: str, arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        operation = str(operation or "").strip()
        if not operation:
            raise ServiceError("PFP service operation is required")
        operations = self.get_operations()
        if operations and operation not in operations:
            raise ServiceError(
                f"PFP service operation '{operation}' is not declared. Supported: {sorted(operations.keys())}.")
        if not self.is_connected():
            self.connect()
        from core import pfp_runtime
        try:
            return pfp_runtime.invoke_service(
                self.config.get("package_runtime") or {},
                self.config.get("installed_from") or {},
                operation,
                arguments or {},
                self.config.get("package_runtime_context") or {},
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise ServiceError(f"PFP service operation failed: {exc}") from exc


ServiceFactory.register(PackageRuntimeService)
