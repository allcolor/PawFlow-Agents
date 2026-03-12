"""Content Repository - external content storage for FlowFiles.

Instead of keeping all content in memory, large content can be stored
in a content repository (filesystem-backed) with reference counting.
"""

import hashlib
import os
import threading
import uuid
from pathlib import Path
from typing import Optional


class ContentClaim:
    """A reference to content stored in the repository."""

    def __init__(self, claim_id: str, size: int = 0):
        self.id = claim_id
        self.size = size

    def __repr__(self):
        return f"ContentClaim(id={self.id[:8]}..., size={self.size})"


class ContentRepository:
    """Filesystem-backed content store with reference counting.

    Content is stored as files keyed by SHA-256 hash for deduplication.
    """

    def __init__(self, base_dir: str = "./content_repo"):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ref_counts: dict[str, int] = {}

    def store(self, content: bytes) -> ContentClaim:
        """Store content and return a claim."""
        content_hash = hashlib.sha256(content).hexdigest()
        claim_id = content_hash

        with self._lock:
            filepath = self._base_dir / claim_id
            if not filepath.exists():
                filepath.write_bytes(content)
            self._ref_counts[claim_id] = self._ref_counts.get(claim_id, 0) + 1

        return ContentClaim(claim_id, len(content))

    def retrieve(self, claim: ContentClaim) -> Optional[bytes]:
        """Retrieve content by claim."""
        filepath = self._base_dir / claim.id
        if filepath.exists():
            return filepath.read_bytes()
        return None

    def release(self, claim: ContentClaim):
        """Release a content claim. Deletes file when ref count reaches 0."""
        with self._lock:
            if claim.id in self._ref_counts:
                self._ref_counts[claim.id] -= 1
                if self._ref_counts[claim.id] <= 0:
                    del self._ref_counts[claim.id]
                    filepath = self._base_dir / claim.id
                    if filepath.exists():
                        filepath.unlink()

    def increment_ref(self, claim: ContentClaim):
        """Increment reference count (e.g., when cloning a FlowFile)."""
        with self._lock:
            self._ref_counts[claim.id] = self._ref_counts.get(claim.id, 0) + 1

    def size(self) -> int:
        """Number of stored content items."""
        with self._lock:
            return len(self._ref_counts)

    def total_size_bytes(self) -> int:
        """Total size of stored content in bytes."""
        total = 0
        for f in self._base_dir.iterdir():
            if f.is_file():
                total += f.stat().st_size
        return total

    def clear(self):
        """Remove all stored content."""
        with self._lock:
            for f in self._base_dir.iterdir():
                if f.is_file():
                    f.unlink()
            self._ref_counts.clear()


# Singleton
_content_repo: Optional[ContentRepository] = None


def get_content_repository(base_dir: str = "./content_repo") -> ContentRepository:
    """Get or create the singleton ContentRepository."""
    global _content_repo
    if _content_repo is None:
        _content_repo = ContentRepository(base_dir)
    return _content_repo
