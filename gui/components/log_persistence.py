"""Log persistence — save logs to disk for post-mortem analysis.

Supports:
- Manual save (download button already in log_viewer)
- Auto-persist on execution complete
- Retention policy (configurable days)
- Per-flow log files
"""

import os
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = "logs"
DEFAULT_RETENTION_DAYS = 30


class LogPersistence:
    """Persist log records to disk as JSON-lines files."""

    def __init__(self, log_dir: str = None, retention_days: int = DEFAULT_RETENTION_DAYS):
        self.log_dir = Path(log_dir or DEFAULT_LOG_DIR)
        self.retention_days = retention_days
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def save_records(self, records: List[Dict], flow_id: str = None,
                     execution_id: str = None) -> str:
        """Save log records to a .jsonl file.

        Args:
            records: List of log record dicts from LogCapture.
            flow_id: Optional flow identifier.
            execution_id: Optional execution identifier.

        Returns:
            Path to the saved log file.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        parts = ["pyfi2"]
        if flow_id:
            parts.append(flow_id)
        if execution_id:
            parts.append(execution_id)
        parts.append(timestamp)
        filename = "_".join(parts) + ".jsonl"

        filepath = self.log_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        logger.info("Saved %d log records to %s", len(records), filepath)
        return str(filepath)

    def load_records(self, filepath: str) -> List[Dict]:
        """Load log records from a .jsonl file."""
        records = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def list_log_files(self, flow_id: str = None) -> List[Dict]:
        """List available log files, newest first.

        Returns list of dicts with keys: path, filename, size, modified, flow_id.
        """
        files = []
        for p in self.log_dir.glob("pyfi2_*.jsonl"):
            stat = p.stat()
            # Extract flow_id from filename if present
            parts = p.stem.split("_")
            file_flow_id = None
            if len(parts) >= 3:
                # pyfi2_<flow_id>_<exec_id>_<timestamp> or pyfi2_<timestamp>
                # If part after "pyfi2" is not a timestamp, it's a flow_id
                candidate = parts[1]
                if not candidate.isdigit():
                    file_flow_id = candidate

            if flow_id and file_flow_id != flow_id:
                continue

            files.append({
                "path": str(p),
                "filename": p.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "flow_id": file_flow_id,
            })

        files.sort(key=lambda x: x["modified"], reverse=True)
        return files

    def cleanup_old_logs(self) -> int:
        """Remove log files older than retention_days.

        Returns number of files removed.
        """
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        removed = 0
        for p in self.log_dir.glob("pyfi2_*.jsonl"):
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink()
                removed += 1
                logger.info("Removed old log file: %s", p)
        return removed

    def get_log_dir(self) -> str:
        return str(self.log_dir)


def auto_persist_logs(flow_id: str, execution_id: str = None):
    """Auto-persist logs for a flow after execution. Called by executor hooks."""
    from gui.components.log_viewer import LogCapture

    capture = LogCapture.get_for_flow(flow_id)
    records = capture.get_records(limit=10000)
    if records:
        persistence = LogPersistence()
        persistence.save_records(records, flow_id=flow_id, execution_id=execution_id)
