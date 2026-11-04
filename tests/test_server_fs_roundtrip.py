"""End-to-end test: ServerFsClient (relay-side) ↔ RelayServerFs (server-side).

No real WebSocket: the test wires the client's `send_callable` to a
thread that decodes the frames, calls the server handler, and feeds
the response back via `dispatch_response`. Both directions go through the
real wire format (``binary_channel``): JSON text frames plus binary
attachment frames for read and write data, never base64.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from pawflow_relay.binary_channel import (
    CHANNEL_ATTACHMENT, AttachmentAssembler, decode, pack_message)
from pawflow_relay.server_fs_client import ServerFsClient
from services.relay_server_fs import RelayServerFs


class _LoopbackBridge:
    """In-process bridge that loops a relay request straight to the server
    handler and pipes the response back via the client's dispatcher."""

    def __init__(self, server_handler: RelayServerFs, client: ServerFsClient):
        self._srv = server_handler
        self._cli = client
        self._server_side = AttachmentAssembler()
        self.binary_frames = 0

    def send(self, payload: bytes, opcode: int = 0x01) -> None:
        if opcode == 0x02:
            channel, _op, stream_id, data = decode(payload)
            assert channel == CHANNEL_ATTACHMENT
            self._server_side.add(stream_id, data)
            self.binary_frames += 1
            return
        envelope = self._server_side.resolve(
            json.loads(payload.decode("utf-8")))
        # Server-side handle (sync — the real path runs this on an
        # executor; here we just call inline since FUSE doesn't exist).
        reply = self._srv.handle(envelope["method"], envelope.get("args", {}))
        # Server's `_handle_relay_request` builds the envelope this way:
        response = {
            "type": "relay_response",
            "request_id": envelope["request_id"],
            **reply,
        }
        # ...and serializes it with its bytes as attachments.
        client_side = AttachmentAssembler()
        for frame, frame_opcode in pack_message(response):
            if frame_opcode == 0x02:
                _c, _o, stream_id, data = decode(frame)
                client_side.add(stream_id, data)
                self.binary_frames += 1
            else:
                response = client_side.resolve(json.loads(frame))
        # Dispatch on a separate thread to mimic the WS receive loop
        # waking the waiter from a different thread than the sender.
        t = threading.Thread(target=self._cli.dispatch_response,
                             args=(response,), daemon=True)
        t.start()


class TestRoundtrip(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        slot = self.root / "alice" / "convA" / "claude"
        slot.mkdir(parents=True)
        (slot / "hello.txt").write_text("hello via roundtrip")
        (slot / "sub").mkdir()
        self.srv = RelayServerFs("alice", root_dir=self.root)
        # Build client first with a placeholder send; rebind once bridge exists.
        self.cli = ServerFsClient(send_callable=lambda *_: None)
        self.bridge = _LoopbackBridge(self.srv, self.cli)
        self.cli._send = self.bridge.send  # rebind

    def tearDown(self):
        self.srv.close()
        self._tmp.cleanup()

    def test_getattr_roundtrip(self):
        r = self.cli.request("sfs.getattr",
                              {"path": "convA/claude/hello.txt"})
        self.assertIn("data", r)
        self.assertEqual(r["data"]["st_size"], len("hello via roundtrip"))

    def test_readdir_roundtrip(self):
        r = self.cli.request("sfs.readdir", {"path": "convA/claude"})
        self.assertEqual(r["data"]["entries"], ["hello.txt", "sub"])

    def test_open_read_release_roundtrip(self):
        import os
        opened = self.cli.request("sfs.open",
                                   {"path": "convA/claude/hello.txt",
                                    "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        chunk = self.cli.request("sfs.read",
                                  {"fh": fh, "offset": 0, "size": 1024})
        self.assertEqual(chunk["data"]["data"], b"hello via roundtrip")
        self.assertEqual(self.bridge.binary_frames, 1)
        rel = self.cli.request("sfs.release", {"fh": fh})
        self.assertEqual(rel.get("data"), {})

    def test_write_then_read_binary_roundtrip(self):
        import os
        payload = bytes(range(256)) * 64
        created = self.cli.request("sfs.create",
                                    {"path": "convA/claude/blob.bin"})
        fh = created["data"]["fh"]
        written = self.cli.request("sfs.write",
                                    {"fh": fh, "offset": 0, "data": payload})
        self.assertEqual(written["data"]["bytes_written"], len(payload))
        self.cli.request("sfs.release", {"fh": fh})
        opened = self.cli.request("sfs.open",
                                   {"path": "convA/claude/blob.bin",
                                    "flags": os.O_RDONLY})
        chunk = self.cli.request("sfs.read", {
            "fh": opened["data"]["fh"], "offset": 0, "size": len(payload)})
        self.assertEqual(chunk["data"]["data"], payload)
        self.cli.request("sfs.release", {"fh": opened["data"]["fh"]})

    def test_error_roundtrip_propagates_errno(self):
        r = self.cli.request("sfs.getattr", {"path": "convA/missing.txt"})
        self.assertEqual(r.get("error"), "ENOENT")
        self.assertEqual(r.get("errno"), 2)

    def test_dotdot_escape_roundtrip(self):
        # Server must refuse — the wire must carry the EACCES back
        r = self.cli.request("sfs.getattr", {"path": "../bob/secret"})
        self.assertEqual(r.get("error"), "EACCES")


if __name__ == "__main__":
    unittest.main()
