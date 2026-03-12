"""listSFTP — list files on an SFTP server with filtering and tracking.

For each matching remote file, creates one FlowFile with metadata.
Supports file tracking to avoid reprocessing already-seen files.

Uses paramiko if available.

Config:
    hostname: str              — SFTP server hostname
    port: int                  — SFTP port (default 22)
    username: str              — SFTP username
    password: str              — SFTP password (or use private_key_path)
    private_key_path: str      — path to SSH private key
    remote_directory: str      — remote directory to list
    pattern: str               — glob-like pattern (default "*")
    regex_filter: str          — regex filter on filename
    file_extensions: str       — comma-separated extensions
    recursive: bool            — recurse into subdirectories (default False)
    min_size: int              — minimum file size in bytes
    max_size: int              — maximum file size in bytes
    min_age_seconds: int       — minimum file age in seconds
    max_age_seconds: int       — maximum file age in seconds
    tracking_service_id: str   — FileTrackingService ID
    polling_interval: float    — polling interval for self-triggering (seconds, 0=disabled)
"""

import fnmatch
import logging
import re
import stat as stat_module
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskError, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        return None


class ListSFTPTask(BaseTask):
    """List files on an SFTP server, creating one FlowFile per matching file."""

    TYPE = "listSFTP"
    VERSION = "1.0.0"
    NAME = "List SFTP"
    DESCRIPTION = "List files on an SFTP server with filtering and tracking"
    ICON = "folder"
    TAGS = ["io", "sftp", "source", "listing"]

    PARAMETERS = {
        "hostname": {"type": "string", "required": True, "description": "SFTP hostname"},
        "port": {"type": "integer", "required": False, "default": 22},
        "username": {"type": "string", "required": True},
        "password": {"type": "secret", "required": False},
        "private_key_path": {"type": "string", "required": False},
        "remote_directory": {
            "type": "string", "required": True,
            "description": "Remote directory to list",
        },
        "pattern": {
            "type": "string", "required": False, "default": "*",
            "description": "Glob pattern for filename matching",
        },
        "regex_filter": {
            "type": "string", "required": False, "default": "",
            "description": "Regex filter on filename",
        },
        "file_extensions": {
            "type": "string", "required": False, "default": "",
            "description": "Comma-separated extensions (e.g. .csv,.json)",
        },
        "recursive": {"type": "boolean", "required": False, "default": False},
        "min_size": {"type": "integer", "required": False, "default": 0},
        "max_size": {"type": "integer", "required": False, "default": 0},
        "min_age_seconds": {"type": "integer", "required": False, "default": 0},
        "max_age_seconds": {"type": "integer", "required": False, "default": 0},
        "tracking_service_id": {
            "type": "string", "required": False, "default": "",
            "description": "FileTrackingService ID to skip already-processed files",
        },
        "polling_interval": {
            "type": "float", "required": False, "default": 0,
            "description": "Polling interval in seconds for self-triggering (0=disabled)",
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.hostname = self.config.get('hostname', '')
        self.port = int(self.config.get('port', 22))
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.private_key_path = self.config.get('private_key_path', '')
        self.remote_directory = self.config.get('remote_directory', '/')
        self.pattern = self.config.get('pattern', '*')
        self.regex_filter = self.config.get('regex_filter', '')
        self.file_extensions = self.config.get('file_extensions', '')
        self.recursive = self.config.get('recursive', False)
        self.min_size = int(self.config.get('min_size', 0))
        self.max_size = int(self.config.get('max_size', 0))
        self.min_age = int(self.config.get('min_age_seconds', 0))
        self.max_age = int(self.config.get('max_age_seconds', 0))
        self.tracking_service_id = self.config.get('tracking_service_id', '')
        self.polling_interval = float(self.config.get('polling_interval', 0))

        self._compiled_regex = re.compile(self.regex_filter) if self.regex_filter else None
        self._extensions = set()
        if self.file_extensions:
            self._extensions = {
                ext.strip().lower() if ext.strip().startswith('.') else f'.{ext.strip().lower()}'
                for ext in self.file_extensions.split(',') if ext.strip()
            }

        self._last_poll_time = 0.0

    def has_pending_input(self) -> bool:
        if self.polling_interval <= 0:
            return False
        return (time.time() - self._last_poll_time) >= self.polling_interval

    @property
    def is_persistent_source(self) -> bool:
        return self.polling_interval > 0

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """List SFTP directory, creating one FlowFile per matching file."""
        self._last_poll_time = time.time()

        paramiko = _get_paramiko()
        if paramiko is None:
            raise TaskError("listSFTP: paramiko is required. Install with: pip install paramiko")

        # Get tracking service
        tracker = None
        if self.tracking_service_id:
            tracker = self.get_service(self.tracking_service_id)
            if tracker:
                tracker.ensure_connected()

        # Connect SFTP
        try:
            transport = paramiko.Transport((self.hostname, self.port))
            if self.private_key_path:
                key = paramiko.RSAKey.from_private_key_file(self.private_key_path)
                transport.connect(username=self.username, pkey=key)
            else:
                transport.connect(username=self.username, password=self.password)
            sftp = paramiko.SFTPClient.from_transport(transport)
        except Exception as e:
            raise TaskError(f"listSFTP: connection failed: {e}")

        try:
            flowfiles = self._list_directory(sftp, self.remote_directory, tracker)
        finally:
            sftp.close()
            transport.close()

        return flowfiles

    def _list_directory(self, sftp, directory: str, tracker) -> List[FlowFile]:
        """List a single directory (and recurse if configured)."""
        flowfiles = []
        now = time.time()

        try:
            entries = sftp.listdir_attr(directory)
        except Exception as e:
            logger.warning(f"listSFTP: cannot list {directory}: {e}")
            return flowfiles

        for entry in entries:
            full_path = f"{directory.rstrip('/')}/{entry.filename}"
            is_dir = stat_module.S_ISDIR(entry.st_mode) if entry.st_mode else False

            # Recurse
            if is_dir and self.recursive:
                flowfiles.extend(self._list_directory(sftp, full_path, tracker))
                continue

            if is_dir:
                continue  # Skip directories

            # --- Pattern match ---
            if not fnmatch.fnmatch(entry.filename, self.pattern):
                continue

            # --- Filters ---
            if not self._passes_filters(entry, now):
                continue

            # --- Tracking ---
            if tracker:
                mtime = entry.st_mtime or 0
                if not tracker.is_new(full_path, mtime=mtime, size=entry.st_size or 0):
                    continue

            # --- Create FlowFile ---
            mtime = entry.st_mtime or 0
            ext = ''
            if '.' in entry.filename:
                ext = '.' + entry.filename.rsplit('.', 1)[1].lower()

            ff = self.create_flowfile(content=b'', attributes={
                'filename': entry.filename,
                'path': directory,
                'absolute.path': full_path,
                'fileSize': str(entry.st_size or 0),
                'file.lastModified': datetime.fromtimestamp(mtime).isoformat() if mtime else '',
                'file.lastModifiedTimestamp': str(mtime),
                'file.extension': ext,
                'sftp.host': self.hostname,
                'sftp.port': str(self.port),
                'sftp.remote.path': full_path,
            })
            flowfiles.append(ff)

            # Mark as processed
            if tracker:
                tracker.mark_processed(full_path, mtime=mtime, size=entry.st_size or 0)

        return flowfiles

    def _passes_filters(self, entry, now: float) -> bool:
        """Check if a file entry passes all filters."""
        filename = entry.filename
        size = entry.st_size or 0
        mtime = entry.st_mtime or 0

        # Extension filter
        if self._extensions:
            ext = ''
            if '.' in filename:
                ext = '.' + filename.rsplit('.', 1)[1].lower()
            if ext not in self._extensions:
                return False

        # Regex filter
        if self._compiled_regex and not self._compiled_regex.search(filename):
            return False

        # Size filters
        if self.min_size > 0 and size < self.min_size:
            return False
        if self.max_size > 0 and size > self.max_size:
            return False

        # Age filters
        if mtime > 0:
            age = now - mtime
            if self.min_age > 0 and age < self.min_age:
                return False
            if self.max_age > 0 and age > self.max_age:
                return False

        return True

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {k: v for k, v in self.PARAMETERS.items()}


# Register
TaskFactory.register(ListSFTPTask)
