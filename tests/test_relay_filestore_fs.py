"""Tests for RelayFileStoreFs — virtualized FileStore FUSE handler.

Layout under test:
    /                           → list of conv_ids
    /<conv>                     → list of file_ids in that conv
    /<conv>/<fid>               → dir holding [filename]
    /<conv>/<fid>/<filename>    → file content

Coverage:
- Path parsing across the four levels, deep paths refused
- Method allowlist (unknown / write methods refused ENOSYS or EROFS)
- Per-user scope (cross-user isolation)
- Per-conv scope (a file_id from conv A is not reachable through conv B)
- File open/read/release on real disk via FileStore.get_disk_path
- statfs always succeeds with f_files = total visible count
"""

import base64
import errno
import os
import stat as _stat
import tempfile
import unittest
from pathlib import Path

from core.file_store import FileStore
from services.relay_filestore_fs import RelayFileStoreFs


class _FsCase(unittest.TestCase):
    """Each test gets a fresh FileStore singleton backed by tmpdir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        FileStore._instance = None
        self.store = FileStore(base_dir=str(Path(self._tmp.name) / "files"))
        FileStore._instance = self.store

        # alice has files in TWO convs to exercise the conv-first layout.
        self.alice_id_1 = self.store.store(
            "hello.txt", b"hello from alice", "text/plain",
            user_id="alice", conversation_id="convA")
        self.alice_id_2 = self.store.store(
            "data.bin", b"\x00\x01\x02", "application/octet-stream",
            user_id="alice", conversation_id="convA")
        self.alice_id_other_conv = self.store.store(
            "notes.md", b"# alice in convOther", "text/markdown",
            user_id="alice", conversation_id="convOther")
        self.bob_id = self.store.store(
            "secret.txt", b"bob private", "text/plain",
            user_id="bob", conversation_id="convB")

        self.fs = RelayFileStoreFs("alice")

    def tearDown(self):
        self.fs.close()
        FileStore._instance = None
        self._tmp.cleanup()


class TestConstruction(unittest.TestCase):

    def test_requires_user_id(self):
        with self.assertRaises(ValueError):
            RelayFileStoreFs("")


class TestPathParsing(unittest.TestCase):

    def test_root(self):
        self.assertEqual(RelayFileStoreFs._split_path("/"), ("", "", ""))
        self.assertEqual(RelayFileStoreFs._split_path(""), ("", "", ""))

    def test_conv_level(self):
        self.assertEqual(RelayFileStoreFs._split_path("/convA"),
                         ("convA", "", ""))
        self.assertEqual(RelayFileStoreFs._split_path("convA"),
                         ("convA", "", ""))

    def test_file_id_level(self):
        self.assertEqual(RelayFileStoreFs._split_path("/convA/abc123"),
                         ("convA", "abc123", ""))

    def test_file_leaf(self):
        self.assertEqual(
            RelayFileStoreFs._split_path("/convA/abc/foo.txt"),
            ("convA", "abc", "foo.txt"))

    def test_deep_path_refused(self):
        with self.assertRaises(FileNotFoundError):
            RelayFileStoreFs._split_path("/a/b/c/d")


class TestGetattr(_FsCase):

    def test_root_is_dir(self):
        r = self.fs.handle("ffs.getattr", {"path": "/"})
        self.assertIn("data", r)
        self.assertTrue(_stat.S_ISDIR(r["data"]["st_mode"]))

    def test_known_conv_is_dir(self):
        r = self.fs.handle("ffs.getattr", {"path": "/convA"})
        self.assertIn("data", r)
        self.assertTrue(_stat.S_ISDIR(r["data"]["st_mode"]))

    def test_unknown_conv_enoent(self):
        r = self.fs.handle("ffs.getattr", {"path": "/no_such_conv"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_known_file_id_is_dir(self):
        r = self.fs.handle("ffs.getattr",
                           {"path": f"/convA/{self.alice_id_1}"})
        self.assertIn("data", r)
        self.assertTrue(_stat.S_ISDIR(r["data"]["st_mode"]))

    def test_known_file_is_regular(self):
        r = self.fs.handle("ffs.getattr",
                           {"path": f"/convA/{self.alice_id_1}/hello.txt"})
        self.assertIn("data", r)
        self.assertTrue(_stat.S_ISREG(r["data"]["st_mode"]))
        self.assertEqual(r["data"]["st_size"], len(b"hello from alice"))

    def test_unknown_file_id_enoent(self):
        r = self.fs.handle("ffs.getattr",
                           {"path": "/convA/zzzzzzzzzzzz"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_wrong_filename_enoent(self):
        r = self.fs.handle("ffs.getattr",
                           {"path": f"/convA/{self.alice_id_1}/wrong.txt"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_other_users_conv_invisible(self):
        r = self.fs.handle("ffs.getattr", {"path": "/convB"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_other_users_file_invisible(self):
        # Even guessing the file_id, the path is unreachable: no convB
        # entry in alice's listing, so getattr stops at the conv level.
        r = self.fs.handle("ffs.getattr",
                           {"path": f"/convB/{self.bob_id}/secret.txt"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_file_id_from_wrong_conv_enoent(self):
        # alice_id_1 lives under convA; surfacing it through convOther
        # must NOT leak the file just because the user owns both convs.
        r = self.fs.handle("ffs.getattr",
                           {"path": f"/convOther/{self.alice_id_1}"})
        self.assertEqual(r.get("error"), "ENOENT")


class TestReaddir(_FsCase):

    def test_root_lists_user_convs(self):
        r = self.fs.handle("ffs.readdir", {"path": "/"})
        self.assertIn("data", r)
        entries = set(r["data"]["entries"])
        self.assertEqual(entries, {"convA", "convOther"})
        self.assertNotIn("convB", entries)  # bob's conv

    def test_conv_lists_only_its_files(self):
        r = self.fs.handle("ffs.readdir", {"path": "/convA"})
        self.assertIn("data", r)
        entries = set(r["data"]["entries"])
        self.assertEqual(entries, {self.alice_id_1, self.alice_id_2})
        self.assertNotIn(self.alice_id_other_conv, entries)

    def test_other_conv_lists_its_own(self):
        r = self.fs.handle("ffs.readdir", {"path": "/convOther"})
        self.assertIn("data", r)
        self.assertEqual(r["data"]["entries"], [self.alice_id_other_conv])

    def test_unknown_conv_enoent(self):
        r = self.fs.handle("ffs.readdir", {"path": "/no_such_conv"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_file_id_lists_one_filename(self):
        r = self.fs.handle("ffs.readdir",
                           {"path": f"/convA/{self.alice_id_1}"})
        self.assertEqual(r["data"]["entries"], ["hello.txt"])

    def test_readdir_on_file_path_enotdir(self):
        r = self.fs.handle("ffs.readdir",
                           {"path": f"/convA/{self.alice_id_1}/hello.txt"})
        self.assertEqual(r.get("error"), "ENOTDIR")

    def test_readdir_unknown_file_id_enoent(self):
        r = self.fs.handle("ffs.readdir",
                           {"path": "/convA/deadbeefcafe"})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_readdir_file_id_from_wrong_conv_enoent(self):
        r = self.fs.handle("ffs.readdir",
                           {"path": f"/convOther/{self.alice_id_1}"})
        self.assertEqual(r.get("error"), "ENOENT")


class TestReadCycle(_FsCase):

    def test_open_read_release(self):
        path = f"/convA/{self.alice_id_1}/hello.txt"
        r = self.fs.handle("ffs.open", {"path": path,
                                          "flags": os.O_RDONLY})
        self.assertIn("data", r)
        fh = r["data"]["fh"]
        r2 = self.fs.handle("ffs.read",
                            {"fh": fh, "offset": 0, "size": 1024})
        self.assertIn("data", r2)
        chunk = base64.b64decode(r2["data"]["data_b64"])
        self.assertEqual(chunk, b"hello from alice")
        r3 = self.fs.handle("ffs.release", {"fh": fh})
        self.assertIn("data", r3)

    def test_open_write_flags_refused(self):
        path = f"/convA/{self.alice_id_1}/hello.txt"
        r = self.fs.handle("ffs.open",
                           {"path": path, "flags": os.O_WRONLY})
        self.assertEqual(r.get("error"), "EROFS")

    def test_open_conv_dir_path_enoent(self):
        # /<conv> is a directory in our virtual layout, not a file
        r = self.fs.handle("ffs.open", {"path": "/convA",
                                          "flags": os.O_RDONLY})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_open_file_id_dir_path_enoent(self):
        # /<conv>/<file_id> is a directory, not a file
        r = self.fs.handle("ffs.open",
                           {"path": f"/convA/{self.alice_id_1}",
                            "flags": os.O_RDONLY})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_open_other_users_file_enoent(self):
        r = self.fs.handle("ffs.open",
                           {"path": f"/convB/{self.bob_id}/secret.txt",
                            "flags": os.O_RDONLY})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_open_file_id_from_wrong_conv_enoent(self):
        # alice_id_1 is in convA; trying to open via convOther leaks nothing
        r = self.fs.handle("ffs.open",
                           {"path": f"/convOther/{self.alice_id_1}/hello.txt",
                            "flags": os.O_RDONLY})
        self.assertEqual(r.get("error"), "ENOENT")

    def test_close_releases_fd(self):
        path = f"/convA/{self.alice_id_1}/hello.txt"
        r = self.fs.handle("ffs.open", {"path": path,
                                          "flags": os.O_RDONLY})
        self.assertIn("data", r)
        self.fs.close()
        # After close, fh is unknown
        r2 = self.fs.handle("ffs.read",
                            {"fh": r["data"]["fh"], "offset": 0, "size": 4})
        self.assertEqual(r2.get("error"), "EBADF")


class TestStatfs(_FsCase):

    def test_statfs_always_succeeds(self):
        r = self.fs.handle("ffs.statfs", {})
        self.assertIn("data", r)
        # f_files = total alice files across convs (2 in convA + 1 in convOther)
        self.assertEqual(r["data"]["f_files"], 3)


class TestMethodAllowlist(_FsCase):

    def test_unknown_method_enosys(self):
        r = self.fs.handle("ffs.bogus", {})
        self.assertEqual(r.get("error"), "ENOSYS")

    def test_wrong_prefix_enosys(self):
        # sfs.* is the cc-sessions protocol — not handled here
        r = self.fs.handle("sfs.getattr", {"path": "/"})
        self.assertEqual(r.get("error"), "ENOSYS")

    def test_write_methods_erofs(self):
        for meth in ("ffs.create", "ffs.write", "ffs.truncate",
                     "ffs.unlink", "ffs.mkdir", "ffs.rmdir",
                     "ffs.rename", "ffs.chmod", "ffs.utimens"):
            r = self.fs.handle(meth, {})
            self.assertEqual(r.get("error"), "EROFS",
                              f"{meth} should return EROFS, got {r}")


if __name__ == "__main__":
    unittest.main()
