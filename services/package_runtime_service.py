"""Service proxy for installed PawFlow Package runtime providers."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

from core import Service, ServiceError, ServiceFactory


class PackageRuntimeService(Service):
    """Proxy service for installed PFP service providers.

    Installed package services should behave like normal PawFlow services. The
    proxy owns lifecycle state and delegates operations to the package runtime.
    """

    TYPE = ""
    VERSION = "1.0.0"
    NAME = "PFP Service Provider"
    DESCRIPTION = "Internal runtime base for PawFlow Package service providers"
    CATEGORY = "other"
    PARAMETERS: Dict[str, Any] = {}
    OPERATIONS: Dict[str, Any] = {}
    PACKAGE_RUNTIME: Dict[str, Any] = {}
    INSTALLED_FROM: Dict[str, Any] = {}
    PROVIDER_METADATA: Dict[str, Any] = {}

    @classmethod
    def materialize_config(cls, config: Dict[str, Any], *, user_id: str = "",
                           conversation_id: str = "", scope: str = "user") -> Dict[str, Any]:
        """Persist the provider descriptor required to recreate this proxy."""
        merged = dict(config or {})
        merged.setdefault("package_runtime", dict(cls.PACKAGE_RUNTIME))
        merged.setdefault("runtime_installed_from", dict(cls.INSTALLED_FROM))
        merged.setdefault("operations", dict(cls.OPERATIONS))
        merged.setdefault("_package_service_provider", dict(cls.PROVIDER_METADATA))
        if (user_id or conversation_id or "package_runtime_context" not in merged):
            merged["package_runtime_context"] = {
                "user_id": user_id,
                "conversation_id": conversation_id if scope in {"conv", "conversation"} else "",
                "scope": "conversation" if scope in {"conv", "conversation"} else "user",
            }
        return merged

    def __init__(self, config: Dict[str, Any]):
        merged = self.materialize_config(config or {})
        super().__init__(merged)
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._runtime_context: Dict[str, Any] = {}

    def get_parameter_schema(self) -> Dict[str, Any]:
        return dict(self.PARAMETERS)

    def validate(self) -> List[str]:
        errors = super().validate()
        runtime = self.config.get("package_runtime")
        if not isinstance(runtime, dict) or not runtime.get("package") or not runtime.get("object_id"):
            errors.append("package_runtime.package and package_runtime.object_id are required")
        installed_from = self.config.get("runtime_installed_from")
        if not isinstance(installed_from, dict):
            errors.append("runtime_installed_from must be an object")
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
        operations = self.config.get("operations") or self.OPERATIONS or {}
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

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        operations = self.get_operations()
        if name not in operations:
            raise AttributeError(name)

        def _operation(**kwargs):
            return self._invoke_media_operation(name, kwargs)

        return _operation

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = "") -> None:
        self._runtime_context = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "scope": scope or ("conversation" if conversation_id else "user"),
            "agent_name": agent_name,
        }

    def set_callback_base_url(self, base_url: str) -> None:
        self._runtime_context["callback_base_url"] = str(base_url or "").rstrip("/")

    def generate(self, **kwargs) -> Dict[str, Any]:
        return self._invoke_media_operation("generate", kwargs)

    def invoke(self, operation: str, arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        operation = str(operation or "").strip()
        if not operation:
            raise ServiceError("PFP service operation is required")
        operations = self.get_operations()
        if not operations:
            raise ServiceError("PFP service provider declares no operations")
        if operation not in operations:
            raise ServiceError(
                f"PFP service operation '{operation}' is not declared. Supported: {sorted(operations.keys())}.")
        if not self.is_connected():
            self.connect()
        from core import pfp_runtime
        try:
            return pfp_runtime.invoke_service(
                self.config.get("package_runtime") or {},
                self.config.get("runtime_installed_from") or {},
                operation,
                arguments or {},
                self._merged_runtime_context(),
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise ServiceError(f"PFP service operation failed: {exc}") from exc

    def _invoke_media_operation(self, operation: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="pawflow-pfp-artifacts-") as tmp:
            previous_context = dict(self._runtime_context)
            self._runtime_context = {**previous_context, "output_dir": tmp}
            try:
                result = self.invoke(operation, arguments)
            finally:
                self._runtime_context = previous_context
            return self._normalize_media_result(result, Path(tmp))

    def _merged_runtime_context(self) -> Dict[str, Any]:
        context = dict(self.config.get("package_runtime_context") or {})
        context.update({k: v for k, v in self._runtime_context.items() if v})
        internal = {
            "package_runtime", "runtime_installed_from", "installed_from",
            "operations", "package_capabilities", "package_runtime_context",
        }
        context["service_config"] = {
            key: value for key, value in self.config.items()
            if key not in internal and not str(key).startswith("_")
        }
        return context

    def _normalize_media_result(self, result: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
        artifact = result.get("artifact") if isinstance(result, dict) else None
        if not isinstance(artifact, dict):
            return result
        source = self._artifact_path(output_dir, artifact)
        content_type = str(artifact.get("content_type") or result.get("content_type") or "application/octet-stream")
        copied = tempfile.NamedTemporaryFile(prefix="pawflow-pfp-media-", delete=False)
        copied_path = Path(copied.name)
        copied.close()
        size = source.stat().st_size
        sha256 = _sha256_file(source)
        shutil.move(str(source), str(copied_path))
        artifact_info = dict(artifact)
        artifact_info["size"] = size
        artifact_info["sha256"] = sha256
        filename = str(artifact.get("filename") or source.name)
        media_key = self._media_path_key(artifact_info)
        normalized = {k: v for k, v in result.items() if k != "artifact"}
        normalized.update({
            media_key: str(copied_path),
            "content_type": content_type,
            "filename": filename,
            "artifact": artifact_info,
            "_delete_media_path": True,
        })
        return normalized

    def _artifact_path(self, output_dir: Path, artifact: Dict[str, Any]) -> Path:
        rel = str(artifact.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            raise ServiceError("PFP media artifact.path is required")
        parsed = PurePosixPath(rel)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise ServiceError("PFP media artifact.path must be relative to output_dir")
        source = (output_dir / rel).resolve()
        try:
            source.relative_to(output_dir.resolve())
        except ValueError as exc:
            raise ServiceError("PFP media artifact escapes output_dir") from exc
        if not source.is_file():
            raise ServiceError(f"PFP media artifact is missing: {rel}")
        return source

    def _media_path_key(self, artifact: Dict[str, Any]) -> str:
        kind = str(artifact.get("kind") or "").lower()
        provides = set(self.status().get("provides") or [])
        if kind == "video" or "media.video_generation" in provides:
            return "video_path"
        if kind == "audio" or "media.audio_generation" in provides:
            return "audio_path"
        return "image_path"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def register_package_service_proxy(metadata: Dict[str, Any]) -> type:
    """Register one named PFP service type in ``ServiceFactory``."""
    service_type = str(metadata.get("service_type") or "").strip()
    if not service_type:
        raise ValueError("service_type is required")
    runtime = dict(metadata.get("package_runtime") or {})
    installed_from = dict(metadata.get("installed_from") or {})
    if not runtime.get("package") or not runtime.get("object_id"):
        raise ValueError("package_runtime.package and package_runtime.object_id are required")
    parameters = metadata.get("parameters") or {}
    if (isinstance(parameters, dict)
            and parameters.get("type") == "object"
            and isinstance(parameters.get("properties"), dict)):
        parameters = parameters["properties"]
    if not isinstance(parameters, dict):
        raise ValueError("service provider parameters must be an object")
    operations = metadata.get("operations") or {}
    if isinstance(operations, list):
        operations = {str(name): {} for name in operations if str(name or "")}
    if not isinstance(operations, dict) or not operations:
        raise ValueError("service provider operations must be a non-empty object")

    existing = ServiceFactory._services.get(service_type)
    if existing is not None:
        existing_runtime = dict(getattr(existing, "PACKAGE_RUNTIME", {}) or {})
        same_provider = (
            issubclass(existing, PackageRuntimeService)
            and existing_runtime.get("package") == runtime.get("package")
            and existing_runtime.get("object_id") == runtime.get("object_id")
        )
        if not same_provider:
            raise ValueError(f"Service type '{service_type}' is already registered")

    provider_metadata = {
        "package": str(runtime.get("package") or ""),
        "object_id": str(runtime.get("object_id") or ""),
        "service_type": service_type,
        "version": str(metadata.get("version") or runtime.get("version") or "1.0.0"),
        "name": str(metadata.get("name") or service_type),
        "description": str(metadata.get("description") or ""),
        "category": str(metadata.get("category") or "other"),
        "parameters": dict(parameters),
        "rules": list(metadata.get("rules") or []),
        "actions": list(metadata.get("actions") or []),
        "operations": dict(operations),
        "package_runtime": runtime,
        "installed_from": installed_from,
    }

    class PackageServiceProxy(PackageRuntimeService):
        TYPE = service_type
        VERSION = provider_metadata["version"]
        NAME = provider_metadata["name"]
        DESCRIPTION = provider_metadata["description"]
        CATEGORY = provider_metadata["category"]
        PARAMETERS = dict(parameters)
        OPERATIONS = dict(operations)
        PACKAGE_RUNTIME = runtime
        INSTALLED_FROM = installed_from
        PROVIDER_METADATA = provider_metadata

        def get_parameter_rules(self) -> List[Dict[str, Any]]:
            return list(self.PROVIDER_METADATA.get("rules") or [])

        def get_service_actions(self) -> List[Dict[str, Any]]:
            return list(self.PROVIDER_METADATA.get("actions") or [])

    PackageServiceProxy.__name__ = _class_name_for(service_type)
    ServiceFactory.register(PackageServiceProxy)
    return PackageServiceProxy


def unregister_package_service_proxy(service_type: str,
                                     package_runtime: Dict[str, Any]) -> bool:
    """Remove a PFP type only when it still belongs to the expected provider."""
    current = ServiceFactory._services.get(str(service_type or ""))
    if current is None or not issubclass(current, PackageRuntimeService):
        return False
    runtime = dict(getattr(current, "PACKAGE_RUNTIME", {}) or {})
    expected = dict(package_runtime or {})
    if (runtime.get("package") != expected.get("package")
            or runtime.get("object_id") != expected.get("object_id")):
        return False
    ServiceFactory._services.pop(service_type, None)
    return True


def _class_name_for(service_type: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in service_type).strip("_")
    parts = [part for part in clean.split("_") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "Package"
    return f"{name}PackageServiceProxy"
