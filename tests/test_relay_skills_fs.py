"""Tests for RelaySkillsFs — virtualized skills-repository FUSE handler.

Layout under test (mirrors data/repository/skills/):
    /                          → ['global', 'users']
    /global/<skill>/...         → global skill directories
    /users/<uid>/<skill>/...    → this relay user's skill tree

Coverage:
- Root + scope directory listings
- File getattr / open / read / release on real repository files
- Per-user scope: another user's users/<uid> subtree is not reachable
- Unknown top-level scopes and path traversal are refused
- Write-side methods return EROFS; unknown methods ENOSYS
"""

import base64
import errno
import stat as _stat
import tempfile
import unittest
from pathlib import Path

import core.paths as _paths
from services.relay_skills_fs import RelaySkillsFs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _SkillsCase(unittest.TestCase):
    """Each test gets a fresh skills repository under a tmpdir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_repo = _paths.REPOSITORY_DIR
        _paths.REPOSITORY_DIR = Path(self._tmp.name) / "repository"
        skills = _paths.REPOSITORY_DIR / "skills"
        _write(skills / "global" / "pdf-tools" / "SKILL.md",
               "---\nname: pdf-tools\ndescription: d\n---\nbody\n")
        _write(skills / "global" / "pdf-tools" / "scripts" / "run.py",
               "print('hi')\n")
        _write(skills / "users" / "alice" / "my-skill" / "SKILL.md",
               "---\nname: my-skill\ndescription: d\n---\nalice body\n")
        _write(skills / "users" / "bob" / "secret-skill" / "SKILL.md",
               "---\nname: secret-skill\ndescription: d\n---\nbob body\n")
        self.fs = RelaySkillsFs("alice")

    def tearDown(self):
        self.fs.close()
        _paths.REPOSITORY_DIR = self._orig_repo
        self._tmp.cleanup()


class TestConstruction(unittest.TestCase):

    def test_requires_user_id(self):
        with self.assertRaises(ValueError):
            RelaySkillsFs("")


class TestReaddir(_SkillsCase):

    def test_root_lists_scopes(self):
        r = self.fs.handle("skfs.readdir", {"path": "/"})
        self.assertEqual(sorted(r["data"]["entries"]), ["global", "users"])

    def test_global_lists_skills(self):
        r = self.fs.handle("skfs.readdir", {"path": "/global"})
        self.assertEqual(r["data"]["entries"], ["pdf-tools"])

    def test_skill_dir_lists_assets(self):
        r = self.fs.handle("skfs.readdir", {"path": "/global/pdf-tools"})
        self.assertEqual(sorted(r["data"]["entries"]), ["SKILL.md", "scripts"])

    def test_users_lists_only_relay_user(self):
        r = self.fs.handle("skfs.readdir", {"path": "/users"})
        self.assertEqual(r["data"]["entries"], ["alice"])

    def test_own_user_subtree(self):
        r = self.fs.handle("skfs.readdir", {"path": "/users/alice"})
        self.assertEqual(r["data"]["entries"], ["my-skill"])


class TestGetattr(_SkillsCase):

    def test_root_is_dir(self):
        r = self.fs.handle("skfs.getattr", {"path": "/"})
        self.assertTrue(_stat.S_ISDIR(r["data"]["st_mode"]))

    def test_users_is_synthetic_dir(self):
        r = self.fs.handle("skfs.getattr", {"path": "/users"})
        self.assertTrue(_stat.S_ISDIR(r["data"]["st_mode"]))

    def test_skill_file_is_readonly_regular(self):
        r = self.fs.handle("skfs.getattr",
                           {"path": "/global/pdf-tools/SKILL.md"})
        self.assertTrue(_stat.S_ISREG(r["data"]["st_mode"]))
        self.assertEqual(r["data"]["st_mode"] & 0o222, 0)

    def test_missing_path(self):
        r = self.fs.handle("skfs.getattr", {"path": "/global/nope"})
        self.assertEqual(r["errno"], errno.ENOENT)


class TestScopeIsolation(_SkillsCase):

    def test_other_user_subtree_hidden(self):
        r = self.fs.handle("skfs.readdir", {"path": "/users/bob"})
        self.assertEqual(r["errno"], errno.ENOENT)

    def test_other_user_file_hidden(self):
        r = self.fs.handle("skfs.getattr",
                           {"path": "/users/bob/secret-skill/SKILL.md"})
        self.assertEqual(r["errno"], errno.ENOENT)

    def test_unknown_scope_refused(self):
        r = self.fs.handle("skfs.readdir", {"path": "/etc"})
        self.assertEqual(r["errno"], errno.ENOENT)

    def test_path_traversal_refused(self):
        r = self.fs.handle("skfs.getattr", {"path": "/global/../../secret"})
        self.assertEqual(r["errno"], errno.ENOENT)


class TestFileIO(_SkillsCase):

    def test_open_read_release(self):
        r = self.fs.handle("skfs.open",
                           {"path": "/global/pdf-tools/scripts/run.py"})
        fh = r["data"]["fh"]
        r2 = self.fs.handle("skfs.read",
                            {"fh": fh, "offset": 0, "size": 4096})
        self.assertEqual(
            base64.b64decode(r2["data"]["data_b64"]), b"print('hi')\n")
        r3 = self.fs.handle("skfs.release", {"fh": fh})
        self.assertIn("data", r3)

    def test_open_directory_refused(self):
        r = self.fs.handle("skfs.open", {"path": "/global/pdf-tools"})
        self.assertEqual(r["errno"], errno.ENOENT)

    def test_open_write_flag_refused(self):
        import os
        r = self.fs.handle("skfs.open", {
            "path": "/global/pdf-tools/SKILL.md", "flags": os.O_WRONLY})
        self.assertEqual(r["errno"], errno.EROFS)


class TestMethodAllowlist(_SkillsCase):

    def test_unknown_method(self):
        r = self.fs.handle("skfs.bogus", {})
        self.assertEqual(r["errno"], errno.ENOSYS)

    def test_write_methods_refused(self):
        for meth in ("skfs.create", "skfs.write", "skfs.truncate",
                     "skfs.unlink", "skfs.mkdir", "skfs.rmdir",
                     "skfs.rename", "skfs.chmod", "skfs.utimens"):
            r = self.fs.handle(meth, {"path": "/global/pdf-tools"})
            self.assertEqual(r["errno"], errno.EROFS, meth)

    def test_statfs(self):
        r = self.fs.handle("skfs.statfs", {})
        self.assertEqual(r["data"]["f_namemax"], 255)


if __name__ == "__main__":
    unittest.main()
