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
                "type": "select", "required": False, "default": "sftp",
                "options": ["sftp", "s3", "drive", "onedrive", "gcs", "azureblob", "webdav", "ftp"],
                "description": "Rclone backend type",
            },
            "rclone_config": {
                "type": "textarea", "required": False, "sensitive": True,
                "description": "Raw rclone config body for this remote. If set, it is used instead of key/value fields.",
            },
            "host": {"type": "string", "required": False, "description": "Remote host for SFTP/FTP/WebDAV"},
            "user": {"type": "string", "required": False, "description": "Remote username"},
            "pass": {"type": "password", "required": False, "sensitive": True, "description": "Rclone-obscured or backend password value"},
            "key_file": {"type": "string", "required": False, "description": "SSH key file path available inside the relay"},
            "provider": {"type": "string", "required": False, "description": "Backend provider, e.g. AWS for s3"},
            "access_key_id": {"type": "string", "required": False, "sensitive": True, "description": "Access key for S3-compatible backends"},
            "secret_access_key": {"type": "password", "required": False, "sensitive": True, "description": "Secret key for S3-compatible backends"},
            "endpoint": {"type": "string", "required": False, "description": "Custom endpoint URL"},
            "region": {"type": "string", "required": False, "description": "Backend region"},
            "mode": {
                "type": "select", "required": False, "default": "readwrite",
                "options": ["read", "readwrite"],
                "description": "Default mount mode when linked to a conversation",
            },
        }

    def _create_connection(self):
        return self

    def _close_connection(self):
        return None


ServiceFactory.register(RcloneFilesystemService)
