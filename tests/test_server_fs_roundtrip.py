"""End-to-end test: ServerFsClient (relay-side) ↔ RelayServerFs (server-side).

No real WebSocket: the test wires the client's `send_callable` to a
thread that decodes the envelope, calls the server handler, and feeds
the response back via `dispatch_response`. This exercises the wire
format both ways without docker, pyfuse3, or a network.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from pawflow_relay.server_fs_client import ServerFsClient
from services.relay_server_fs import RelayServerFs


class _LoopbackBridge:
    """In-process bridge that loops a relay request straight to the server
    handler and pipes the response back via the client's dispatcher."""

    def __init__(self, server_handler: RelayServerFs, client: ServerFsClient):
        self._srv = server_handler
        self._cli = client

    def send(self, payload: bytes) -> None:
        envelope = json.loads(payload.decode("utf-8"))
        # Server-side handle (sync — the real path runs this on an
        # executor; here we just call inline since FUSE doesn't exist).
        reply = self._srv.handle(envelope["method"], envelope.get("args", {}))
        # Server's `_handle_relay_request` builds the envelope this way:
        response = {
            "type": "relay_response",
            "request_id": envelope["request_id"],
            **reply,
        }
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
        self.cli = ServerFsClient(send_callable=lambda _: None)
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
        import base64
        opened = self.cli.request("sfs.open",
                                   {"path": "convA/claude/hello.txt",
                                    "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        chunk = self.cli.request("sfs.read",
                                  {"fh": fh, "offset": 0, "size": 1024})
        self.assertEqual(
            base64.b64decode(chunk["data"]["data_b64"]).decode(),
            "hello via roundtrip")
        rel = self.cli.request("sfs.release", {"fh": fh})
        self.assertEqual(rel.get("data"), {})

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
