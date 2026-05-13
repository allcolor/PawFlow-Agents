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

    TYPE = "packageRuntime"
    VERSION = "1.0.0"
    NAME = "PFP Package Runtime Service"
    DESCRIPTION = "Runtime proxy for installed PawFlow Package service providers"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._runtime_context: Dict[str, Any] = {}

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

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "") -> None:
        self._runtime_context = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "scope": scope or ("conversation" if conversation_id else "user"),
        }

    def generate(self, **kwargs) -> Dict[str, Any]:
        return self._invoke_media_operation("generate", kwargs)

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


ServiceFactory.register(PackageRuntimeService)
