"""Tests for RelayServerFs — server-side handler for relay-initiated FS ops.

Coverage:
- Path sandboxing (refuses .. traversal, absolute paths, symlink escape)
- R/O ops: getattr, readdir, open+read+release, statfs
- Method allowlist (unknown / write methods refused with ENOSYS or EROFS)
- Per-instance fd lifecycle (closed on .close())
- Cross-user isolation (one instance can't read another's slot)
"""

import base64
import errno
import os
import stat as _stat
import tempfile
import unittest
from pathlib import Path

from services.relay_server_fs import RelayServerFs


class _FsCase(unittest.TestCase):
    """Base case that gives each test a private CLAUDE_SESSIONS_DIR."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Pre-populate user 'alice' with a small tree
        self.alice_root = self.root / "alice"
        (self.alice_root / "convA" / "claude").mkdir(parents=True)
        (self.alice_root / "convA" / "claude" / "hello.txt").write_text(
            "hello from alice")
        (self.alice_root / "convA" / "claude" / "sub").mkdir()
        (self.alice_root / "convA" / "claude" / "sub" / "deep.txt").write_text(
            "deep content")
        # And user 'bob' with his own slot
        self.bob_root = self.root / "bob"
        (self.bob_root / "convB").mkdir(parents=True)
        (self.bob_root / "convB" / "secret.txt").write_text("bob's secret")

        self.fs = RelayServerFs("alice", root_dir=self.root)

    def tearDown(self):
        self.fs.close()
        self._tmp.cleanup()


class TestConstruction(_FsCase):

    def test_requires_user_id(self):
        with self.assertRaises(ValueError):
            RelayServerFs("", root_dir=self.root)

    def test_creates_user_slot_lazily(self):
        fs = RelayServerFs("newuser", root_dir=self.root)
        self.assertTrue((self.root / "newuser").is_dir())
        fs.close()


class TestSandbox(_FsCase):

    def test_resolves_relative_path(self):
        r = self.fs.handle("sfs.getattr", {"path": "convA/claude/hello.txt"})
        self.assertIn("data", r)
        self.assertEqual(r["data"]["st_size"], len("hello from alice"))

    def test_leading_slash_treated_as_relative(self):
        # /convA/... is read relative to the slot root, NOT as system absolute
        r = self.fs.handle("sfs.getattr", {"path": "/convA/claude/hello.txt"})
        self.assertIn("data", r)

    def test_dotdot_escape_refused(self):
        # "../bob/..." attempts to break out of alice's slot
        r = self.fs.handle("sfs.getattr",
                           {"path": "../bob/convB/secret.txt"})
        self.assertEqual(r.get("error"), "EACCES")

    def test_cannot_read_other_user_slot(self):
        # Even if alice's relay forges a path that resolves to bob's content
        r = self.fs.open if False else None  # noqa: pyflakes — placeholder
        out = self.fs.handle("sfs.open",
                             {"path": "../bob/convB/secret.txt", "flags": os.O_RDONLY})
        self.assertEqual(out.get("error"), "EACCES")

    def test_symlink_escaping_slot_refused(self):
        link = self.alice_root / "convA" / "escape"
        os.symlink(self.bob_root / "convB" / "secret.txt", link)
        r = self.fs.handle("sfs.getattr", {"path": "convA/escape"})
        self.assertEqual(r.get("error"), "EACCES")

    def test_symlink_inside_slot_followed(self):
        link = self.alice_root / "convA" / "alias"
        os.symlink(self.alice_root / "convA" / "claude" / "hello.txt", link)
        r = self.fs.handle("sfs.getattr", {"path": "convA/alias"})
        self.assertIn("data", r)
        self.assertEqual(r["data"]["st_size"], len("hello from alice"))


class TestReadOps(_FsCase):

    def test_readdir(self):
        r = self.fs.handle("sfs.readdir", {"path": "convA/claude"})
        self.assertIn("data", r)
        self.assertEqual(r["data"]["entries"], ["hello.txt", "sub"])

    def test_readdir_on_file_returns_enotdir(self):
        r = self.fs.handle("sfs.readdir", {"path": "convA/claude/hello.txt"})
        self.assertEqual(r.get("error"), "ENOTDIR")

    def test_open_read_release_round_trip(self):
        opened = self.fs.handle("sfs.open",
                                {"path": "convA/claude/hello.txt",
                                 "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        chunk = self.fs.handle("sfs.read",
                                {"fh": fh, "offset": 0, "size": 100})
        self.assertEqual(
            base64.b64decode(chunk["data"]["data_b64"]).decode(),
            "hello from alice")
        rel = self.fs.handle("sfs.release", {"fh": fh})
        self.assertEqual(rel.get("data"), {})

    def test_read_with_offset(self):
        opened = self.fs.handle("sfs.open",
                                {"path": "convA/claude/hello.txt",
                                 "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        chunk = self.fs.handle("sfs.read",
                                {"fh": fh, "offset": 6, "size": 100})
        self.assertEqual(
            base64.b64decode(chunk["data"]["data_b64"]).decode(),
            "from alice")
        self.fs.handle("sfs.release", {"fh": fh})

    def test_read_chunk_size_capped(self):
        opened = self.fs.handle("sfs.open",
                                {"path": "convA/claude/hello.txt",
                                 "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        too_big = self.fs.handle("sfs.read",
                                  {"fh": fh, "offset": 0,
                                   "size": RelayServerFs.MAX_READ_CHUNK + 1})
        self.assertEqual(too_big.get("error"), "EINVAL")
        self.fs.handle("sfs.release", {"fh": fh})

    def test_read_with_unknown_fh_returns_ebadf(self):
        r = self.fs.handle("sfs.read",
                           {"fh": 99999, "offset": 0, "size": 10})
        self.assertEqual(r.get("error"), "EBADF")

    def test_release_unknown_fh_returns_ebadf(self):
        r = self.fs.handle("sfs.release", {"fh": 99999})
        self.assertEqual(r.get("error"), "EBADF")

    def test_open_directory_returns_eisdir(self):
        r = self.fs.handle("sfs.open",
                           {"path": "convA/claude", "flags": os.O_RDONLY})
        self.assertEqual(r.get("error"), "EISDIR")

    def test_getattr_missing_returns_enoent(self):
        r = self.fs.handle("sfs.getattr", {"path": "convA/nope.txt"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_statfs_returns_filesystem_stats(self):
        r = self.fs.handle("sfs.statfs", {"path": "convA"})
        self.assertIn("data", r)
        self.assertGreater(r["data"]["f_bsize"], 0)


class TestMethodAllowlist(_FsCase):

    def test_unknown_method_returns_enosys(self):
        r = self.fs.handle("sfs.never_existed", {})
        self.assertEqual(r.get("error"), "ENOSYS")

    def test_method_outside_allowlist_returns_enosys(self):
        # Even if a Python method exists with the right name, it's
        # rejected unless explicitly allowlisted. Defense against
        # reflection-style attacks via forged method strings.
        r = self.fs.handle("sfs.__init__", {})
        self.assertEqual(r.get("error"), "ENOSYS")

    def test_open_with_o_creat_refused(self):
        # O_CREAT must use sfs.create explicitly so the mode is required.
        r = self.fs.handle("sfs.open",
                           {"path": "convA/claude/new.txt",
                            "flags": os.O_WRONLY | os.O_CREAT})
        self.assertEqual(r.get("error"), "EINVAL")

    def test_open_with_write_flags_works(self):
        # Phase 2: O_WRONLY on an existing file is allowed
        r = self.fs.handle("sfs.open",
                           {"path": "convA/claude/hello.txt",
                            "flags": os.O_WRONLY})
        self.assertIn("data", r)
        self.fs.handle("sfs.release", {"fh": r["data"]["fh"]})


class TestWriteOps(_FsCase):

    def test_create_then_write_then_read_back(self):
        import base64
        c = self.fs.handle("sfs.create",
                            {"path": "convA/claude/new.txt", "mode": 0o644})
        self.assertIn("data", c)
        fh = c["data"]["fh"]
        payload = b"hello new file"
        w = self.fs.handle("sfs.write", {
            "fh": fh, "offset": 0,
            "data_b64": base64.b64encode(payload).decode("ascii"),
        })
        self.assertEqual(w["data"]["bytes_written"], len(payload))
        self.fs.handle("sfs.release", {"fh": fh})
        o = self.fs.handle("sfs.open",
                            {"path": "convA/claude/new.txt",
                             "flags": os.O_RDONLY})
        rfh = o["data"]["fh"]
        rd = self.fs.handle("sfs.read",
                             {"fh": rfh, "offset": 0, "size": 1024})
        self.assertEqual(
            base64.b64decode(rd["data"]["data_b64"]), payload)
        self.fs.handle("sfs.release", {"fh": rfh})

    def test_write_chunk_size_capped(self):
        import base64
        c = self.fs.handle("sfs.create",
                            {"path": "convA/claude/big.bin"})
        fh = c["data"]["fh"]
        too_big = b"X" * (RelayServerFs.MAX_WRITE_CHUNK + 1)
        w = self.fs.handle("sfs.write", {
            "fh": fh, "offset": 0,
            "data_b64": base64.b64encode(too_big).decode("ascii"),
        })
        self.assertEqual(w.get("error"), "EINVAL")
        self.fs.handle("sfs.release", {"fh": fh})

    def test_truncate_path_based(self):
        r = self.fs.handle("sfs.truncate",
                            {"path": "convA/claude/hello.txt", "length": 5})
        self.assertEqual(r.get("data"), {})
        st = self.fs.handle("sfs.getattr",
                             {"path": "convA/claude/hello.txt"})
        self.assertEqual(st["data"]["st_size"], 5)

    def test_truncate_fh_based(self):
        o = self.fs.handle("sfs.open",
                            {"path": "convA/claude/hello.txt",
                             "flags": os.O_RDWR})
        fh = o["data"]["fh"]
        r = self.fs.handle("sfs.truncate", {"fh": fh, "length": 3})
        self.assertEqual(r.get("data"), {})
        self.fs.handle("sfs.release", {"fh": fh})

    def test_unlink_removes_file(self):
        r = self.fs.handle("sfs.unlink",
                            {"path": "convA/claude/hello.txt"})
        self.assertEqual(r.get("data"), {})
        st = self.fs.handle("sfs.getattr",
                             {"path": "convA/claude/hello.txt"})
        self.assertEqual(st.get("error"), "ENOENT")

    def test_mkdir_then_rmdir(self):
        r = self.fs.handle("sfs.mkdir",
                            {"path": "convA/claude/newdir", "mode": 0o755})
        self.assertEqual(r.get("data"), {})
        ls = self.fs.handle("sfs.readdir", {"path": "convA/claude"})
        self.assertIn("newdir", ls["data"]["entries"])
        rm = self.fs.handle("sfs.rmdir", {"path": "convA/claude/newdir"})
        self.assertEqual(rm.get("data"), {})

    def test_rename_within_slot(self):
        r = self.fs.handle("sfs.rename", {
            "old": "convA/claude/hello.txt",
            "new": "convA/claude/renamed.txt",
        })
        self.assertEqual(r.get("data"), {})
        ls = self.fs.handle("sfs.readdir", {"path": "convA/claude"})
        self.assertIn("renamed.txt", ls["data"]["entries"])
        self.assertNotIn("hello.txt", ls["data"]["entries"])

    def test_rename_refused_when_target_escapes(self):
        r = self.fs.handle("sfs.rename", {
            "old": "convA/claude/hello.txt",
            "new": "../bob/stolen.txt",
        })
        self.assertEqual(r.get("error"), "EACCES")

    def test_chmod_masks_setuid(self):
        # Setuid bit (04000) must be stripped — only 0o777 perms allowed
        r = self.fs.handle("sfs.chmod", {
            "path": "convA/claude/hello.txt",
            "mode": 0o4755,
        })
        self.assertEqual(r.get("data"), {})
        st = self.fs.handle("sfs.getattr",
                             {"path": "convA/claude/hello.txt"})
        import stat as _s
        perms = _s.S_IMODE(st["data"]["st_mode"])
        self.assertEqual(perms, 0o755)

    def test_utimens_sets_mtime(self):
        r = self.fs.handle("sfs.utimens", {
            "path": "convA/claude/hello.txt",
            "atime": 1700000000.0,
            "mtime": 1700000001.0,
        })
        self.assertEqual(r.get("data"), {})
        st = self.fs.handle("sfs.getattr",
                             {"path": "convA/claude/hello.txt"})
        self.assertEqual(int(st["data"]["st_mtime"]), 1700000001)

    def test_create_outside_slot_refused(self):
        c = self.fs.handle("sfs.create",
                            {"path": "../bob/intruder.txt"})
        self.assertEqual(c.get("error"), "EACCES")

    def test_unlink_outside_slot_refused(self):
        u = self.fs.handle("sfs.unlink",
                            {"path": "../bob/convB/secret.txt"})
        self.assertEqual(u.get("error"), "EACCES")


class TestLifecycle(_FsCase):

    def test_close_releases_open_fds(self):
        opened = self.fs.handle("sfs.open",
                                {"path": "convA/claude/hello.txt",
                                 "flags": os.O_RDONLY})
        fh = opened["data"]["fh"]
        # Close the handler — the underlying real fd must be released
        self.fs.close()
        # Subsequent ops on this handler don't crash; the fh table is empty
        r = self.fs.handle("sfs.read", {"fh": fh, "offset": 0, "size": 1})
        self.assertEqual(r.get("error"), "EBADF")


class TestCrossUserIsolation(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "alice" / "convA").mkdir(parents=True)
        (root / "alice" / "convA" / "a.txt").write_text("alice data")
        (root / "bob" / "convB").mkdir(parents=True)
        (root / "bob" / "convB" / "b.txt").write_text("bob data")
        self.alice = RelayServerFs("alice", root_dir=root)
        self.bob = RelayServerFs("bob", root_dir=root)

    def tearDown(self):
        self.alice.close()
        self.bob.close()
        self._tmp.cleanup()

    def test_each_handler_only_sees_its_own_slot(self):
        # Alice's handler sees alice's content
        self.assertIn("data",
                      self.alice.handle("sfs.getattr", {"path": "convA/a.txt"}))
        # Bob's handler sees bob's content
        self.assertIn("data",
                      self.bob.handle("sfs.getattr", {"path": "convB/b.txt"}))
        # Alice can't reach bob's content even by guessing the path
        r = self.alice.handle("sfs.getattr", {"path": "convB/b.txt"})
        self.assertEqual(r.get("error"), "ENOENT")
        r = self.alice.handle("sfs.getattr", {"path": "../bob/convB/b.txt"})
        self.assertEqual(r.get("error"), "EACCES")


if __name__ == "__main__":
    unittest.main()
