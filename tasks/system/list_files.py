# List Files Task

"""
ListFiles — list files in a directory with advanced filtering and tracking.

For each matching file, creates one FlowFile with file metadata as attributes.
Supports file tracking to avoid reprocessing already-seen files.

Can run as:
- Regular task: triggered by incoming FlowFile, lists directory, outputs N FlowFiles
- Self-triggering: polls directory at configurable interval (for continuous mode)
"""

import fnmatch
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class ListFilesTask(BaseTask):
    """List files in a directory, creating one FlowFile per matching file."""

    TYPE = "listFiles"
    VERSION = "2.0.0"
    NAME = "List Files"
    DESCRIPTION = "List files in a directory with filtering and tracking"
    ICON = "list"
    TAGS = ["io", "filesystem", "source", "listing"]

    PARAMETERS = {
        "directory": {
            "type": "string",
            "required": True,
            "description": "Directory to list",
        },
        "pattern": {
            "type": "string",
            "required": False,
            "default": "*",
            "description": "Glob pattern (e.g. *.csv, data_*.json)",
        },
        "regex_filter": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Regex filter on filename (applied after glob pattern)",
        },
        "file_extensions": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Comma-separated extensions (e.g. .csv,.json,.xml)",
        },
        "recursive": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Search subdirectories recursively",
        },
        "include_dirs": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Include directories in the listing",
        },
        "min_size": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": "Minimum file size in bytes (0 = no minimum)",
        },
        "max_size": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": "Maximum file size in bytes (0 = no maximum)",
        },
        "min_age_seconds": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": "Minimum file age in seconds (0 = no minimum)",
        },
        "max_age_seconds": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": "Maximum file age in seconds (0 = no maximum)",
        },
        "tracking_service_id": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "FileTrackingService ID (skip already-processed files)",
        },
        "polling_interval": {
            "type": "float",
            "required": False,
            "default": 0,
            "description": "Polling interval in seconds for self-triggering mode (0 = disabled)",
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.directory = self.config.get('directory', '')
        self.pattern = self.config.get('pattern', '*')
        self.regex_filter = self.config.get('regex_filter', '')
        self.file_extensions = self.config.get('file_extensions', '')
        self.recursive = self.config.get('recursive', False)
        self.include_dirs = self.config.get('include_dirs', False)
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
        """Self-triggering: return True when polling interval has elapsed."""
        if self.polling_interval <= 0:
            return False
        return (time.time() - self._last_poll_time) >= self.polling_interval

    @property
    def is_persistent_source(self) -> bool:
        return self.polling_interval > 0

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """List files, creating one FlowFile per matching file."""
        self._last_poll_time = time.time()

        if not self.directory:
            raise ValueError("The 'directory' parameter is required")

        directory = Path(self.directory)
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {self.directory}")

        # Get tracking service if configured
        tracker = None
        if self.tracking_service_id:
            tracker = self.get_service(self.tracking_service_id)
            if tracker:
                tracker.ensure_connected()

        flowfiles = []
        now = time.time()

        if self.recursive:
            candidates = directory.rglob(self.pattern)
        else:
            candidates = directory.glob(self.pattern)

        for file_path in candidates:
            if not file_path.is_file() and not (self.include_dirs and file_path.is_dir()):
                continue

            try:
                stat = file_path.stat()
            except OSError:
                continue

            # --- Filters ---
            if not self._passes_filters(file_path, stat, now):
                continue

            # --- Tracking ---
            if tracker:
                if not tracker.is_new(str(file_path), mtime=stat.st_mtime, size=stat.st_size):
                    continue

            # --- Create FlowFile ---
            new_ff = self.create_flowfile(content=b'', attributes={
                'filename': file_path.name,
                'path': str(file_path.parent),
                'absolute.path': str(file_path.resolve()),
                'fileSize': str(stat.st_size),
                'file.lastModified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'file.lastModifiedTimestamp': str(stat.st_mtime),
                'file.extension': file_path.suffix.lower(),
                'file.isDirectory': str(file_path.is_dir()).lower(),
            })
            flowfiles.append(new_ff)

            # Mark as processed
            if tracker:
                tracker.mark_processed(str(file_path), mtime=stat.st_mtime, size=stat.st_size)

        return flowfiles

    def _passes_filters(self, file_path: Path, stat: os.stat_result, now: float) -> bool:
        """Check if a file passes all configured filters."""
        # Extension filter
        if self._extensions and file_path.suffix.lower() not in self._extensions:
            return False

        # Regex filter
        if self._compiled_regex and not self._compiled_regex.search(file_path.name):
            return False

        # Size filters (for files only)
        if file_path.is_file():
            if self.min_size > 0 and stat.st_size < self.min_size:
                return False
            if self.max_size > 0 and stat.st_size > self.max_size:
                return False

        # Age filters
        file_age = now - stat.st_mtime
        if self.min_age > 0 and file_age < self.min_age:
            return False
        if self.max_age > 0 and file_age > self.max_age:
            return False

        return True

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {k: v for k, v in self.PARAMETERS.items()}


# Register
TaskFactory.register(ListFilesTask)
