"""Data preview — capture and inspect FlowFiles flowing through connections."""
import logging

import time
import threading
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field


@dataclass
class DataSample:
    """A captured sample of data flowing through a connection."""
    connection_id: str          # "source_id -> target_id"
    timestamp: float
    content_preview: str        # First 2000 chars
    content_size: int
    content_type: str           # Detected: json, xml, csv, text, binary
    attributes: Dict[str, str]
    sample_index: int


class DataPreviewManager:
    """Captures FlowFile samples at connections for inspection.

    Attaches to a ContinuousFlowExecutor and captures data
    flowing through selected connections.

    Usage:
        preview = DataPreviewManager()
        preview.enable_connection("task1", "task2")
        preview.attach(executor)
        # ... data flows through ...
        samples = preview.get_samples("task1", "task2")
        preview.detach()
    """

    def __init__(self, max_samples_per_connection: int = 10):
        self._enabled_connections: Set[str] = set()
        self._samples: Dict[str, List[DataSample]] = {}
        self._max_samples = max_samples_per_connection
        self._lock = threading.Lock()
        self._attached_executor = None
        self._capture_all = False

    @staticmethod
    def _conn_key(source_id: str, target_id: str) -> str:
        return f"{source_id} -> {target_id}"

    def enable_connection(self, source_id: str, target_id: str):
        """Enable data capture on a connection."""
        key = self._conn_key(source_id, target_id)
        self._enabled_connections.add(key)

    def disable_connection(self, source_id: str, target_id: str):
        """Disable data capture on a connection."""
        key = self._conn_key(source_id, target_id)
        self._enabled_connections.discard(key)

    def enable_all(self):
        """Capture data on all connections."""
        self._capture_all = True

    def disable_all(self):
        """Stop capturing on all connections."""
        self._capture_all = False
        self._enabled_connections.clear()

    def is_enabled(self, source_id: str, target_id: str) -> bool:
        if self._capture_all:
            return True
        return self._conn_key(source_id, target_id) in self._enabled_connections

    def capture(self, source_id: str, target_id: str, flowfile):
        """Capture a FlowFile sample at a connection point."""
        key = self._conn_key(source_id, target_id)
        if not self._capture_all and key not in self._enabled_connections:
            return

        try:
            # Extract content preview
            content = ""
            content_size = 0
            if hasattr(flowfile, 'get_content'):
                raw = flowfile.get_content()
                if isinstance(raw, bytes):
                    content = raw[:2000].decode('utf-8', errors='replace')
                    content_size = len(raw)
                else:
                    content = str(raw)[:2000]
                    content_size = len(str(raw))

            # Detect content type
            content_type = self._detect_type(content)

            # Extract attributes
            attrs = {}
            if hasattr(flowfile, 'get_attributes'):
                attrs = flowfile.get_attributes()
            elif hasattr(flowfile, 'attributes'):
                attrs = dict(flowfile.attributes)

            with self._lock:
                if key not in self._samples:
                    self._samples[key] = []

                sample = DataSample(
                    connection_id=key,
                    timestamp=time.time(),
                    content_preview=content,
                    content_size=content_size,
                    content_type=content_type,
                    attributes=attrs,
                    sample_index=len(self._samples[key]),
                )

                self._samples[key].append(sample)
                if len(self._samples[key]) > self._max_samples:
                    self._samples[key] = self._samples[key][-self._max_samples:]
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _detect_type(content: str) -> str:
        """Detect content type from preview."""
        stripped = content.strip()
        if not stripped:
            return "empty"
        if stripped.startswith('{') or stripped.startswith('['):
            return "json"
        if stripped.startswith('<?xml') or stripped.startswith('<'):
            return "xml"
        if ',' in stripped.split('\n')[0] and len(stripped.split('\n')) > 1:
            return "csv"
        # Check if mostly printable
        non_printable = sum(1 for c in stripped[:100] if not c.isprintable() and c not in '\n\r\t')
        if non_printable > 10:
            return "binary"
        return "text"

    def get_samples(self, source_id: str = None, target_id: str = None,
                    limit: int = 10) -> List[Dict]:
        """Get captured samples."""
        with self._lock:
            if source_id and target_id:
                key = self._conn_key(source_id, target_id)
                samples = self._samples.get(key, [])[-limit:]
            else:
                # All samples, sorted by timestamp
                all_samples = []
                for samples_list in self._samples.values():
                    all_samples.extend(samples_list)
                all_samples.sort(key=lambda s: s.timestamp, reverse=True)
                samples = all_samples[:limit]

            return [
                {
                    "connection": s.connection_id,
                    "timestamp": s.timestamp,
                    "content_preview": s.content_preview,
                    "content_size": s.content_size,
                    "content_type": s.content_type,
                    "attributes": s.attributes,
                    "index": s.sample_index,
                }
                for s in samples
            ]

    def get_connections_with_data(self) -> List[Dict]:
        """Get list of connections that have captured data."""
        with self._lock:
            return [
                {"connection": key, "sample_count": len(samples),
                 "latest": samples[-1].timestamp if samples else 0}
                for key, samples in self._samples.items()
                if samples
            ]

    def clear(self, source_id: str = None, target_id: str = None):
        """Clear captured samples."""
        with self._lock:
            if source_id and target_id:
                key = self._conn_key(source_id, target_id)
                self._samples.pop(key, None)
            else:
                self._samples.clear()

    def attach(self, executor):
        """Attach to a ContinuousFlowExecutor."""
        self._attached_executor = executor
        executor._data_preview = self

    def detach(self):
        """Detach from executor."""
        if self._attached_executor:
            self._attached_executor._data_preview = None
            self._attached_executor = None
