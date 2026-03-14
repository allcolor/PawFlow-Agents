"""ConfigValue — wrapper for parameter/secret values with spill-to-disk.

Small values (< SPILL_THRESHOLD) stay as strings in memory.
Large values (>= SPILL_THRESHOLD) use ContentReference for disk-backed storage.
"""

import io
from typing import Optional, BinaryIO

from core.stream import ContentReference, SPILL_THRESHOLD


class ConfigValue:
    """Wraps a parameter/secret value. Small = str, large = ContentReference."""

    __slots__ = ('_str_value', '_content_ref', '_size')

    def __init__(self, value: Optional[str] = None, *,
                 data: Optional[bytes] = None,
                 content_ref: Optional[ContentReference] = None):
        """Create a ConfigValue.

        Args:
            value: String value (for small values).
            data: Raw bytes (auto-spills if large).
            content_ref: Pre-existing ContentReference (for loading from sidecar).
        """
        self._str_value: Optional[str] = None
        self._content_ref: Optional[ContentReference] = None
        self._size: int = 0

        if content_ref is not None:
            self._content_ref = content_ref
            self._size = content_ref.size
        elif data is not None:
            self._size = len(data)
            if self._size >= SPILL_THRESHOLD:
                self._content_ref = ContentReference(data=data)
            else:
                self._str_value = data.decode('utf-8', errors='replace')
        elif value is not None:
            raw = value.encode('utf-8')
            self._size = len(raw)
            if self._size >= SPILL_THRESHOLD:
                self._content_ref = ContentReference(data=raw)
            else:
                self._str_value = value

    @property
    def is_large(self) -> bool:
        return self._content_ref is not None

    @property
    def size(self) -> int:
        return self._size

    def __str__(self) -> str:
        if self._str_value is not None:
            return self._str_value
        if self._content_ref is not None:
            mb = self._size / (1024 * 1024)
            return f"<large:{mb:.1f}MB>"
        return ""

    def as_str(self) -> str:
        """Full string value. For large values, loads all into memory."""
        if self._str_value is not None:
            return self._str_value
        if self._content_ref is not None:
            return self._content_ref.get_bytes().decode('utf-8', errors='replace')
        return ""

    def as_bytes(self) -> bytes:
        """Raw bytes of the value."""
        if self._str_value is not None:
            return self._str_value.encode('utf-8')
        if self._content_ref is not None:
            return self._content_ref.get_bytes()
        return b''

    def get_stream(self) -> BinaryIO:
        """Streaming access. Caller must close the stream."""
        if self._content_ref is not None:
            return self._content_ref.get_stream()
        return io.BytesIO(self.as_bytes())

    def preview(self, max_chars: int = 200) -> str:
        """Truncated preview for display."""
        if self._str_value is not None:
            if len(self._str_value) <= max_chars:
                return self._str_value
            return self._str_value[:max_chars] + "..."
        if self._content_ref is not None:
            # Read just enough for preview
            stream = self._content_ref.get_stream()
            try:
                chunk = stream.read(max_chars * 4)  # UTF-8 worst case
                text = chunk.decode('utf-8', errors='replace')[:max_chars]
                return text + "..."
            finally:
                stream.close()
        return ""

    def release(self):
        """Release resources (cleanup ContentReference if large)."""
        if self._content_ref is not None:
            self._content_ref.release()
            self._content_ref = None

    def __repr__(self) -> str:
        if self.is_large:
            mb = self._size / (1024 * 1024)
            return f"ConfigValue(<large:{mb:.1f}MB>)"
        return f"ConfigValue({self._str_value!r})"

    def __eq__(self, other):
        if isinstance(other, ConfigValue):
            return self.as_str() == other.as_str()
        if isinstance(other, str):
            return not self.is_large and self._str_value == other
        return NotImplemented

    def __hash__(self):
        if not self.is_large:
            return hash(self._str_value)
        return hash(self._size)
