"""Validated contracts shared by the universal installer CLI and GUI."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetKind = Literal["local", "ssh"]
InstallSource = Literal["published", "source"]
ReachabilityMode = Literal["local", "tailscale", "existing_https", "public_manual"]
HostKeyPolicy = Literal["strict", "accept-new"]

RELAY_CAPABILITIES = frozenset({
    "filesystem.read",
    "filesystem.write",
    "shell.exec",
    "container.exec",
    "host.local",
    "automation",
    "desktop.control",
    "service.tunnels",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str | None, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _absolute_path(value: str) -> str:
    expanded = str(Path(value).expanduser())
    if not (Path(expanded).is_absolute() or PureWindowsPath(expanded).is_absolute()):
        raise ValueError("shared paths must be absolute")
    return value


def _https_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain a query or fragment")
    return value.rstrip("/")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TargetConfig(StrictModel):
    kind: TargetKind
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = None
    identity_file: str | None = None
    host_key_policy: HostKeyPolicy | None = None

    @model_validator(mode="after")
    def validate_target(self) -> TargetConfig:
        if self.kind == "ssh":
            self.host = _required(self.host, "SSH host")
            self.user = _required(self.user, "SSH user")
            if self.port is None:
                raise ValueError("SSH port is required")
            if self.host_key_policy is None:
                raise ValueError("SSH host-key policy is required")
            if any(ch.isspace() for ch in self.host):
                raise ValueError("SSH host must not contain whitespace")
        elif any(value is not None for value in (
            self.host, self.port, self.user, self.identity_file, self.host_key_policy
        )):
            raise ValueError("local targets must not contain SSH fields")
        return self


class ServerInstallConfig(StrictModel):
    pawflow_home: str
    port: int = Field(ge=1, le=65535)
    version: str | None
    source: InstallSource
    native: bool
    keep_old_images: bool
    skip_apparmor: bool

    @field_validator("pawflow_home")
    @classmethod
    def validate_home(cls, value: str) -> str:
        return _required(value, "PawFlow home")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", text):
            raise ValueError("version contains unsupported characters")
        return text.removeprefix("v")


class ReachabilityConfig(StrictModel):
    mode: ReachabilityMode
    hostname: str | None = None
    certificate_sha256: str | None = None

    @model_validator(mode="after")
    def validate_reachability(self) -> ReachabilityConfig:
        if self.certificate_sha256 is not None:
            fingerprint = self.certificate_sha256.replace(":", "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError("certificate SHA-256 must contain 64 hex digits")
            self.certificate_sha256 = fingerprint
        if self.mode in {"existing_https", "public_manual"}:
            self.hostname = _https_url(
                _required(self.hostname, "existing HTTPS URL"), "existing HTTPS URL"
            )
        elif self.mode == "tailscale" and self.hostname:
            if "/" in self.hostname or any(ch.isspace() for ch in self.hostname):
                raise ValueError("Tailscale hostname must be a DNS name or IP address")
        elif self.mode == "local" and self.hostname is not None:
            raise ValueError("local reachability does not accept a hostname")
        return self


class RelayDesktopConfig(StrictModel):
    install: bool
    server_url: str | None = None
    server_name: str | None = None
    workspace_name: str | None = None
    capabilities: list[str]
    paths: list[str]
    autostart: bool
    artifact_path: str | None = None
    artifact_sha256: str | None = None

    @model_validator(mode="after")
    def validate_relay(self) -> RelayDesktopConfig:
        unknown = sorted(set(self.capabilities) - RELAY_CAPABILITIES)
        if unknown:
            raise ValueError(f"unknown Relay Desktop capabilities: {', '.join(unknown)}")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Relay Desktop capabilities must be unique")
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("Relay Desktop paths must be unique")
        self.paths = [_absolute_path(path) for path in self.paths]
        if self.install:
            self.server_url = _https_url(
                _required(self.server_url, "Relay Desktop server URL"),
                "Relay Desktop server URL",
            )
            self.server_name = _required(self.server_name, "Relay Desktop server name")
            self.workspace_name = _required(
                self.workspace_name, "Relay Desktop workspace name"
            )
            if not self.paths:
                raise ValueError("at least one explicit Relay Desktop path is required")
            if "filesystem.read" not in self.capabilities:
                raise ValueError("filesystem.read is required when sharing a path")
            if self.artifact_sha256 is not None and not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.artifact_sha256
            ):
                raise ValueError("Relay Desktop artifact SHA-256 must contain 64 hex digits")
            if bool(self.artifact_path) != bool(self.artifact_sha256):
                raise ValueError(
                    "Relay Desktop artifact path and SHA-256 must be provided together")
        elif any((
            self.server_url,
            self.server_name,
            self.workspace_name,
            self.capabilities,
            self.paths,
            self.autostart,
            self.artifact_path,
            self.artifact_sha256,
        )):
            raise ValueError("disabled Relay Desktop configuration must not contain options")
        return self


class InstallRequest(StrictModel):
    version: Literal[1]
    target: TargetConfig
    install: ServerInstallConfig
    reachability: ReachabilityConfig
    relay_desktop: RelayDesktopConfig
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=utc_now)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        return str(uuid.UUID(value))

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_cross_fields(self) -> InstallRequest:
        if self.target.kind == "local" and self.reachability.mode not in {
            "local", "tailscale", "existing_https", "public_manual"
        }:
            raise ValueError("unsupported local reachability mode")
        if self.target.kind == "ssh" and self.reachability.mode == "local":
            raise ValueError("remote installation cannot use local reachability")
        if self.install.native and self.install.source != "source":
            raise ValueError("native server installation requires source mode")
        return self

    def semantic_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude={"request_id", "created_at"})

    def digest(self) -> str:
        encoded = json.dumps(
            self.semantic_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
