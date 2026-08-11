from pathlib import Path

from core.handlers.copy import CopyHandler, _COPY_CHUNK_SIZE


class _Relay:
    def __init__(self, service_id):
        self._service_id = service_id
        self.calls = []

    def read_file(self, *_args, **_kwargs):
        raise AssertionError("copy must never buffer a complete relay file")

    def write_file(self, *_args, **_kwargs):
        raise AssertionError("copy must never buffer a complete relay file")

    def copy_file(self, source, dest, local=False):
        self.calls.append(("direct", source, dest, local))
        return {"size": 41}

    def copy_file_to_local(self, source, target, local=False):
        self.calls.append(("download", source, target, local))
        Path(target).write_bytes(b"x" * (_COPY_CHUNK_SIZE + 7))
        return {"written": _COPY_CHUNK_SIZE + 7}

    def write_file_stream(self, dest, chunks, expected_size, local=False):
        materialized = list(chunks)
        self.calls.append((
            "upload", dest, materialized, expected_size, local))
        return expected_size


def _handler(tmp_path, services):
    handler = CopyHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    handler.set_available_services([
        {"id": service_id} for service_id in services
    ], default_service_id=next(iter(services)))
    handler._find_service = lambda name="": services.get(
        name or handler._default_service_id)
    handler.set_workdir(str(tmp_path))
    return handler


def test_same_relay_copy_stays_on_that_relay(tmp_path):
    relay = _Relay("relay-a")
    handler = _handler(tmp_path, {"relay-a": relay})

    result = handler.execute({
        "source_service": "relay-a",
        "dest_service": "relay-a",
        "source_path": "large.bin",
        "dest_path": "copy.bin",
        "local": True,
    })

    assert "41 bytes" in result
    assert relay.calls == [
        ("direct", "large.bin", "copy.bin", True)]


def test_cross_relay_copy_uses_bounded_streams(tmp_path):
    source = _Relay("source")
    dest = _Relay("dest")
    handler = _handler(tmp_path, {"source": source, "dest": dest})

    result = handler.execute({
        "source_service": "source",
        "dest_service": "dest",
        "source_path": "large.bin",
        "dest_path": "copy.bin",
    })

    assert f"{_COPY_CHUNK_SIZE + 7:,} bytes" in result
    upload = dest.calls[0]
    assert upload[0] == "upload"
    assert b"".join(upload[2]) == b"x" * (_COPY_CHUNK_SIZE + 7)
    assert max(map(len, upload[2])) <= _COPY_CHUNK_SIZE


def test_copy_source_contains_no_whole_file_buffering():
    source = Path("core/handlers/copy.py").read_text(encoding="utf-8")
    assert "read_file(" not in source
    assert "write_file(" not in source
    assert "read_bytes(" not in source
    assert 'b"".join' not in source
