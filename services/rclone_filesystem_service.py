"""Rclone filesystem service definition for relay-side native mounts.

This service stores rclone backend configuration. It is not a server-side
FilesystemBackend; it becomes usable when linked to a conversation remote FS
binding and materialized inside linked relays under /remote/<service_id>.
"""

from __future__ import annotations

from typing import Any, Dict

from core import ServiceFactory
from core.base_service import BaseService


class RcloneFilesystemService(BaseService):
    """Rclone remote filesystem configuration."""

    TYPE = "rcloneFilesystem"
    VERSION = "1.0.0"
    NAME = "Rclone Filesystem"
    DESCRIPTION = "Mount an rclone-supported remote filesystem inside linked relays"
    ICON = "folder-symlink"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "rclone_type": {
                "type": "select", "required": True, "default": "sftp",
                "label": "Backend type",
                "options": ["sftp", "s3", "drive", "onedrive", "gcs", "azureblob", "webdav", "ftp"],
                "description": "Rclone backend. The form only shows fields used by the selected backend.",
            },
            "mode": {
                "type": "select", "required": False, "default": "readwrite",
                "label": "Mount mode",
                "options": ["read", "readwrite"],
                "description": "Default mount mode when linked to a conversation",
            },
            "host": {"type": "string", "required": False, "label": "Host", "description": "Server hostname for SFTP or FTP"},
            "port": {"type": "integer", "required": False, "label": "Port", "description": "Server port. Defaults are backend-specific when left empty."},
            "user": {"type": "string", "required": False, "label": "Username", "description": "Remote username"},
            "pass": {"type": "password", "required": False, "sensitive": True, "label": "Password", "description": "Rclone-obscured or backend password value"},
            "key_file": {"type": "string", "required": False, "label": "SSH key file", "description": "SSH key file path available inside the relay"},
            "provider": {"type": "string", "required": False, "label": "Provider", "description": "S3 provider, for example AWS, Cloudflare, Minio, or Other"},
            "access_key_id": {"type": "string", "required": False, "sensitive": True, "label": "Access key ID", "description": "Access key for S3-compatible backends"},
            "secret_access_key": {"type": "password", "required": False, "sensitive": True, "label": "Secret access key", "description": "Secret key for S3-compatible backends"},
            "endpoint": {"type": "string", "required": False, "label": "Endpoint", "description": "Custom endpoint URL for S3 or Azure-compatible deployments"},
            "region": {"type": "string", "required": False, "label": "Region", "description": "Backend region"},
            "url": {"type": "string", "required": False, "label": "URL", "description": "WebDAV endpoint URL"},
            "vendor": {"type": "string", "required": False, "default": "other", "label": "Vendor", "description": "WebDAV vendor, for example nextcloud, owncloud, sharepoint, or other"},
            "account": {"type": "string", "required": False, "label": "Account", "description": "Azure Blob storage account name"},
            "key": {"type": "password", "required": False, "sensitive": True, "label": "Account key", "description": "Azure Blob storage account key"},
            "sas_url": {"type": "password", "required": False, "sensitive": True, "label": "SAS URL", "description": "Azure Blob SAS URL alternative to account/key"},
            "service_account_file": {"type": "string", "required": False, "label": "Service account file", "description": "Google Cloud service account file path available inside the relay"},
            "project_number": {"type": "string", "required": False, "label": "Project number", "description": "Google Cloud project number for GCS"},
            "rclone_config": {
                "type": "textarea", "required": False, "sensitive": True,
                "label": "Raw rclone config",
                "description": "Advanced override. Paste the body of an rclone config for this remote; when set, it replaces the guided fields above.",
            },
        }

    def get_parameter_rules(self):
        backend_fields = [
            "host", "port", "user", "pass", "key_file", "provider",
            "access_key_id", "secret_access_key", "endpoint", "region",
            "url", "vendor", "account", "key", "sas_url",
            "service_account_file", "project_number",
        ]

        def rule(rclone_type: str, visible: list[str], required: list[str] | None = None,
                 defaults: Dict[str, Any] | None = None):
            required = required or []
            defaults = defaults or {}
            fields = {name: {"visible": name in visible} for name in backend_fields}
            for name in required:
                fields.setdefault(name, {})["required"] = True
            for name, value in defaults.items():
                fields.setdefault(name, {})["default"] = value
            return {"when": {"rclone_type": rclone_type}, "set": fields}

        return [
            rule("sftp", ["host", "port", "user", "pass", "key_file"], ["host", "user"], {"port": "22"}),
            rule("ftp", ["host", "port", "user", "pass"], ["host"], {"port": "21"}),
            rule("webdav", ["url", "vendor", "user", "pass"], ["url"], {"vendor": "other"}),
            rule("s3", ["provider", "access_key_id", "secret_access_key", "endpoint", "region"], ["provider"], {"provider": "AWS"}),
            rule("azureblob", ["account", "key", "sas_url", "endpoint"], ["account"]),
            rule("gcs", ["service_account_file", "project_number"], []),
            rule("drive", [], ["rclone_config"]),
            rule("onedrive", [], ["rclone_config"]),
        ]

    def _create_connection(self):
        return self

    def _close_connection(self):
        return None


ServiceFactory.register(RcloneFilesystemService)
