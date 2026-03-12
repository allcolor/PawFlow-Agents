"""Worker Protocol - Streaming serialization for FlowFile transfer.

Binary protocol for transferring FlowFiles between coordinator and workers.
Uses streaming to avoid loading entire content into memory.

Single FlowFile format:
- 4 bytes: header size (uint32 big-endian)
- N bytes: header JSON = {"attributes": {...}, "task_id": "...", "content_size": int, ...}
- M bytes: raw content (streamed in 64KB chunks)

Multi-result format:
- 4 bytes: result header size
- N bytes: result header JSON = {"assignment_id": "...", "status": "...", "count": int}
- For each flowfile: 4 bytes header + header JSON (with content_size!) + content
"""

import struct
import json
import io
from typing import List, Tuple, Optional, BinaryIO, Dict, Any

from core import FlowFile

CHUNK_SIZE = 65536  # 64KB


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    """Read exactly n bytes from stream, raising on short read."""
    data = stream.read(n)
    if len(data) < n:
        raise ValueError(f"Expected {n} bytes, got {len(data)}")
    return data


def _write_header(stream: BinaryIO, header: dict):
    """Write a length-prefixed JSON header."""
    header_bytes = json.dumps(header, separators=(',', ':')).encode('utf-8')
    stream.write(struct.pack('>I', len(header_bytes)))
    stream.write(header_bytes)


def _read_header(stream: BinaryIO) -> dict:
    """Read a length-prefixed JSON header."""
    size = struct.unpack('>I', _read_exact(stream, 4))[0]
    return json.loads(_read_exact(stream, size).decode('utf-8'))


def _stream_content(source: BinaryIO, dest: BinaryIO, size: int):
    """Copy exactly `size` bytes from source to dest in chunks."""
    remaining = size
    while remaining > 0:
        chunk = source.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            break
        dest.write(chunk)
        remaining -= len(chunk)


class FlowFileSerializer:
    """Streaming serializer for FlowFile transfer."""

    @staticmethod
    def serialize_to_stream(flowfile: FlowFile, stream: BinaryIO,
                            task_id: str = "", task_type: str = "",
                            config: Optional[dict] = None) -> None:
        """Serialize a FlowFile + task metadata to a binary stream."""
        header = {
            "attributes": flowfile.get_attributes(),
            "task_id": task_id,
            "task_type": task_type,
            "config": config or {},
            "content_size": flowfile.size(),
        }
        _write_header(stream, header)

        # Stream content in chunks
        content_stream = flowfile.get_content_stream()
        try:
            _stream_content(content_stream, stream, flowfile.size())
        finally:
            content_stream.close()

    @staticmethod
    def deserialize_from_stream(stream: BinaryIO) -> Tuple[FlowFile, dict]:
        """Deserialize a FlowFile + metadata from a binary stream.

        Returns (FlowFile, metadata_dict).
        Content is streamed — auto-spills to disk if large.
        """
        header = _read_header(stream)
        content_size = header.get("content_size", 0)

        metadata = {
            "task_id": header.get("task_id", ""),
            "task_type": header.get("task_type", ""),
            "config": header.get("config", {}),
            "content_size": content_size,
        }

        ff = FlowFile(attributes=header.get("attributes", {}))

        # Wrap the source stream in a size-limited reader so
        # set_content_from_stream reads exactly content_size bytes
        limited = _LimitedReader(stream, content_size)
        ff.set_content_from_stream(limited, size_hint=content_size)

        return ff, metadata

    @staticmethod
    def serialize_result_to_stream(flowfiles: List[FlowFile], stream: BinaryIO,
                                   assignment_id: str,
                                   error: Optional[str] = None) -> None:
        """Serialize task execution results (multiple FlowFiles)."""
        result_header = {
            "assignment_id": assignment_id,
            "status": "failed" if error else "completed",
            "error": error,
            "count": len(flowfiles),
        }
        _write_header(stream, result_header)

        for ff in flowfiles:
            FlowFileSerializer._serialize_ff(ff, stream)

    @staticmethod
    def deserialize_result_from_stream(stream: BinaryIO) -> Tuple[List[FlowFile], dict]:
        """Deserialize task results from a binary stream.

        Returns (list_of_flowfiles, result_metadata).
        """
        result_header = _read_header(stream)
        metadata = {
            "assignment_id": result_header.get("assignment_id", ""),
            "status": result_header.get("status", "unknown"),
            "error": result_header.get("error"),
            "count": result_header.get("count", 0),
        }

        flowfiles = []
        for _ in range(metadata["count"]):
            ff = FlowFileSerializer._deserialize_ff(stream)
            flowfiles.append(ff)

        return flowfiles, metadata

    # -- Internal helpers --

    @staticmethod
    def _serialize_ff(flowfile: FlowFile, stream: BinaryIO):
        """Serialize a single FlowFile (header with content_size + content)."""
        header = {
            "attributes": flowfile.get_attributes(),
            "content_size": flowfile.size(),
        }
        _write_header(stream, header)

        content_stream = flowfile.get_content_stream()
        try:
            _stream_content(content_stream, stream, flowfile.size())
        finally:
            content_stream.close()

    @staticmethod
    def _deserialize_ff(stream: BinaryIO) -> FlowFile:
        """Deserialize a single FlowFile from stream."""
        header = _read_header(stream)
        content_size = header.get("content_size", 0)

        ff = FlowFile(attributes=header.get("attributes", {}))
        limited = _LimitedReader(stream, content_size)
        ff.set_content_from_stream(limited, size_hint=content_size)
        return ff


class _LimitedReader:
    """Wraps a stream to read at most `limit` bytes.

    Prevents set_content_from_stream from reading past the current
    FlowFile's content boundary in a multi-flowfile stream.
    """

    def __init__(self, stream: BinaryIO, limit: int):
        self._stream = stream
        self._remaining = limit

    def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b''
        if n < 0:
            n = self._remaining
        to_read = min(n, self._remaining)
        data = self._stream.read(to_read)
        self._remaining -= len(data)
        return data


# -- Convenience functions --

def serialize_flowfile(flowfile: FlowFile, task_id: str = "",
                       task_type: str = "", config: dict = None) -> bytes:
    """Serialize a FlowFile to bytes."""
    buf = io.BytesIO()
    FlowFileSerializer.serialize_to_stream(flowfile, buf, task_id, task_type, config)
    return buf.getvalue()


def deserialize_flowfile(data: bytes) -> Tuple[FlowFile, dict]:
    """Deserialize a FlowFile from bytes."""
    return FlowFileSerializer.deserialize_from_stream(io.BytesIO(data))


def serialize_results(flowfiles: List[FlowFile], assignment_id: str,
                      error: str = None) -> bytes:
    """Serialize task results to bytes."""
    buf = io.BytesIO()
    FlowFileSerializer.serialize_result_to_stream(flowfiles, buf, assignment_id, error)
    return buf.getvalue()


def deserialize_results(data: bytes) -> Tuple[List[FlowFile], dict]:
    """Deserialize task results from bytes."""
    return FlowFileSerializer.deserialize_result_from_stream(io.BytesIO(data))
