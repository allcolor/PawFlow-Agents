"""Streaming content management for FlowFiles.

Provides ContentReference - a transparent abstraction over in-memory
and file-backed content. Small content stays in RAM, large content
automatically spills to disk.

SpillTracker provides global tracking of all disk-spilled content
with stats, orphan cleanup, and memory pressure monitoring.
"""

import atexit
import io
import os
import hashlib
import logging
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Optional, BinaryIO, Dict, Set

logger = logging.getLogger(__name__)

# Default threshold: content larger than this spills to disk
SPILL_THRESHOLD = 1 * 1024 * 1024  # 1 MB

# Temp directory for spilled content
_spill_dir: Optional[Path] = None
_spill_lock = threading.Lock()


def _get_spill_dir() -> Path:
    """Get or create the temp directory for spilled content."""
    global _spill_dir
    if _spill_dir is None:
        with _spill_lock:
            if _spill_dir is None:
                _spill_dir = Path(tempfile.mkdtemp(prefix="pawflow_spill_"))
    return _spill_dir


def set_spill_directory(path: str):
    """Configure the spill directory (call before any FlowFile usage)."""
    global _spill_dir
    _spill_dir = Path(path)
    _spill_dir.mkdir(parents=True, exist_ok=True)


def set_spill_threshold(size_bytes: int):
    """Configure the spill threshold in bytes."""
    global SPILL_THRESHOLD
    SPILL_THRESHOLD = size_bytes


class SpillTracker:
    """Global tracker for all disk-spilled ContentReferences.

    Tracks:
    - All active spill files and their sizes
    - Total bytes on disk
    - Reference counts via weak references to ContentReference objects

    Provides:
    - cleanup_orphans(): remove spill files with no live ContentReference
    - cleanup_all(): remove all spill files (shutdown)
    - get_stats(): monitoring info
    """

    def __init__(self):
        self._lock = threading.Lock()
        # file_path -> (size, weakref to ContentReference)
        self._tracked: Dict[str, tuple] = {}
        self._total_spilled_bytes = 0
        self._total_spill_count = 0
        self._total_cleaned = 0

    def register(self, file_path: Path, size: int, ref: 'ContentReference'):
        """Register a new spill file with its owning ContentReference."""
        key = str(file_path)
        weak = weakref.ref(ref)
        with self._lock:
            self._tracked[key] = (size, weak)
            self._total_spilled_bytes += size
            self._total_spill_count += 1

    def unregister(self, file_path: Path):
        """Unregister a spill file (called on release)."""
        key = str(file_path)
        with self._lock:
            entry = self._tracked.pop(key, None)
            if entry:
                self._total_spilled_bytes -= entry[0]

    def cleanup_orphans(self) -> int:
        """Remove spill files whose ContentReference has been garbage collected.

        Returns the number of orphan files cleaned up.
        """
        orphans = []
        with self._lock:
            for key, (size, weak) in list(self._tracked.items()):
                if weak() is None:
                    orphans.append((key, size))

            for key, size in orphans:
                self._tracked.pop(key, None)
                self._total_spilled_bytes -= size

        cleaned = 0
        for key, _ in orphans:
            try:
                p = Path(key)
                if p.exists():
                    p.unlink()
                    cleaned += 1
            except OSError:
                pass

        with self._lock:
            self._total_cleaned += cleaned

        if cleaned:
            logger.debug(f"SpillTracker: cleaned {cleaned} orphan files")
        return cleaned

    def cleanup_all(self):
        """Remove ALL tracked spill files. Call on shutdown."""
        with self._lock:
            keys = list(self._tracked.keys())
            self._tracked.clear()
            self._total_spilled_bytes = 0

        cleaned = 0
        for key in keys:
            try:
                p = Path(key)
                if p.exists():
                    p.unlink()
                    cleaned += 1
            except OSError:
                pass

        # Also clean any untracked files in spill dir
        try:
            spill_dir = _get_spill_dir()
            for f in spill_dir.iterdir():
                if f.is_file() and f.name.startswith("spill_"):
                    try:
                        f.unlink()
                        cleaned += 1
                    except OSError:
                        pass
        except Exception:
            pass

        with self._lock:
            self._total_cleaned += cleaned
        logger.info(f"SpillTracker: cleanup_all removed {cleaned} files")

    def get_stats(self) -> dict:
        """Get tracking statistics for monitoring."""
        with self._lock:
            active = len(self._tracked)
            live = sum(1 for _, (_, w) in self._tracked.items() if w() is not None)
            return {
                "active_spill_files": active,
                "live_references": live,
                "orphaned": active - live,
                "total_bytes_on_disk": self._total_spilled_bytes,
                "total_spill_count": self._total_spill_count,
                "total_cleaned": self._total_cleaned,
            }

    @property
    def total_bytes_on_disk(self) -> int:
        with self._lock:
            return self._total_spilled_bytes


# Global singleton
_spill_tracker = SpillTracker()


def get_spill_tracker() -> SpillTracker:
    """Get the global SpillTracker instance."""
    return _spill_tracker


@atexit.register
def _cleanup_on_exit():
    """Clean up all spill files on interpreter shutdown."""
    try:
        _spill_tracker.cleanup_all()
    except Exception:
        pass


class ContentReference:
    """Reference to content that may be in memory or on disk.

    - Small content (< SPILL_THRESHOLD): kept as bytes in RAM
    - Large content: written to a temp file on disk, loaded on demand

    Thread-safe via internal lock. Supports reference counting for
    zero-copy cloning of FlowFiles.
    """

    __slots__ = ('_data', '_file_path', '_size', '_ref_count', '_lock', '__weakref__')

    def __init__(self, data: Optional[bytes] = None,
                 file_path: Optional[Path] = None,
                 size: int = 0):
        self._lock = threading.Lock()
        self._data: Optional[bytes] = None
        self._file_path: Optional[Path] = None
        self._size = 0
        self._ref_count = 1

        if data is not None:
            self._size = len(data)
            if self._size > SPILL_THRESHOLD:
                self._spill(data)
            else:
                self._data = data
        elif file_path is not None:
            self._file_path = file_path
            self._size = size if size > 0 else file_path.stat().st_size

    def _spill(self, data: bytes):
        """Write data to a temp file and register with SpillTracker."""
        spill_dir = _get_spill_dir()
        h = hashlib.sha256(data).hexdigest()[:16]
        self._file_path = spill_dir / f"spill_{h}_{id(self)}"
        self._file_path.write_bytes(data)
        self._data = None
        _spill_tracker.register(self._file_path, len(data), self)

    @property
    def size(self) -> int:
        return self._size

    @property
    def is_on_disk(self) -> bool:
        return self._file_path is not None

    def get_bytes(self) -> bytes:
        """Get full content as bytes. Loads from disk if spilled."""
        with self._lock:
            if self._data is not None:
                return self._data
            if self._file_path is not None and self._file_path.exists():
                return self._file_path.read_bytes()
            return b''

    def get_stream(self) -> BinaryIO:
        """Get a readable stream over the content.

        Returns an io.BytesIO for in-memory content, or an open file
        handle for disk-backed content. Caller must close the stream.
        """
        with self._lock:
            if self._data is not None:
                return io.BytesIO(self._data)
            if self._file_path is not None and self._file_path.exists():
                return open(self._file_path, 'rb')
            return io.BytesIO(b'')

    def increment_ref(self):
        """Increment reference count (for FlowFile cloning)."""
        with self._lock:
            self._ref_count += 1

    def release(self):
        """Decrement reference count. Deletes temp file when zero."""
        with self._lock:
            self._ref_count -= 1
            if self._ref_count <= 0:
                if self._file_path is not None:
                    _spill_tracker.unregister(self._file_path)
                    if self._file_path.exists():
                        try:
                            self._file_path.unlink()
                        except OSError:
                            pass
                self._data = None
                self._file_path = None

    @property
    def ref_count(self) -> int:
        return self._ref_count

    def clone_data(self) -> 'ContentReference':
        """Create a deep copy (independent data, new ref count).

        Use this when the clone will modify content.
        For read-only sharing, use increment_ref() instead.
        """
        data = self.get_bytes()
        return ContentReference(data=data)

    @classmethod
    def from_stream(cls, stream: BinaryIO, size_hint: int = 0) -> 'ContentReference':
        """Create a ContentReference by reading from a stream.

        If size_hint exceeds SPILL_THRESHOLD, streams directly to disk
        without buffering entire content in memory.
        """
        if size_hint > SPILL_THRESHOLD:
            # Stream directly to disk
            spill_dir = _get_spill_dir()
            temp_path = spill_dir / f"spill_stream_{id(stream)}"
            total = 0
            with open(temp_path, 'wb') as f:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            ref = cls.__new__(cls)
            ref._lock = threading.Lock()
            ref._data = None
            ref._file_path = temp_path
            ref._size = total
            ref._ref_count = 1
            _spill_tracker.register(temp_path, total, ref)
            return ref
        else:
            # Read into memory, check if it exceeds threshold
            data = stream.read()
            return cls(data=data)

    def __del__(self):
        """Cleanup temp file if ref count is zero."""
        try:
            if self._ref_count <= 0 and self._file_path and self._file_path.exists():
                self._file_path.unlink()
        except Exception:
            pass

    def __repr__(self):
        loc = "disk" if self.is_on_disk else "memory"
        return f"ContentReference(size={self._size}, {loc}, refs={self._ref_count})"
