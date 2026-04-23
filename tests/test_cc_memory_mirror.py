"""Tests for services.cc_memory_mirror and its integration with
services.relay_server_fs.

Covers:
- Path matching: which rel_paths are considered mirrorable
- Frontmatter parsing: valid, malformed, missing, trailing whitespace
- upsert/delete behaviour (insert, update-on-rewrite, delete-on-unlink)
- End-to-end via RelayServerFs: create+write+release lands an entry;
  overwrite updates in place; unlink removes it; rename moves the entry;
  path-based truncate re-mirrors; MEMORY.md index is NOT mirrored.
"""

import base64
import os
import tempfile
import unittest
from pathlib import Path

from core.memory_store import MemoryStore
from services import cc_memory_mirror
from services.relay_server_fs import RelayServerFs


USER = "alice@test"
CONV = "conv1"
AGENT = "claude"
MEMDIR_REL = f"{CONV}/{AGENT}/projects/-cc-conv1-claude/memory"


def _write_frontmatter(name: str, type_: str, body: str,
                       description: str = "") -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n"
        f"type: {type_}\n---\n{body}"
    ).encode("utf-8")


def _isolated_store() -> tempfile.TemporaryDirectory:
    """Fresh MemoryStore singleton writing to a temp dir."""
    tmp = tempfile.TemporaryDirectory()
    MemoryStore.reset()
    MemoryStore._instance = MemoryStore(store_dir=tmp.name)
    return tmp


class TestMatchMemoryPath(unittest.TestCase):

    def test_match_well_formed(self):
        m = cc_memory_mirror.match_memory_path(
            f"{MEMDIR_REL}/user_role.md"
        )
        assert m == (CONV, AGENT, "user_role")

    def test_leading_slash_accepted(self):
        m = cc_memory_mirror.match_memory_path(
            f"/{MEMDIR_REL}/feedback_tests.md"
        )
        assert m == (CONV, AGENT, "feedback_tests")

    def test_reject_index_file(self):
        assert cc_memory_mirror.match_memory_path(
            f"{MEMDIR_REL}/MEMORY.md"
        ) is None

    def test_reject_non_md(self):
        assert cc_memory_mirror.match_memory_path(
            f"{MEMDIR_REL}/user_role.txt"
        ) is None

    def test_reject_not_in_memory_dir(self):
        assert cc_memory_mirror.match_memory_path(
            f"{CONV}/{AGENT}/projects/foo/bar.md"
        ) is None

    def test_reject_too_shallow(self):
        assert cc_memory_mirror.match_memory_path("memory/a.md") is None

    def test_reject_empty(self):
        assert cc_memory_mirror.match_memory_path("") is None
        assert cc_memory_mirror.match_memory_path(None) is None  # type: ignore


class TestParseFrontmatter(unittest.TestCase):

    def test_valid(self):
        data = _write_frontmatter("user role", "user",
                                   "Alice is a senior Python dev.",
                                   description="Alice’s role")
        f = cc_memory_mirror._parse_frontmatter(data)
        assert f is not None
        assert f["name"] == "user role"
        assert f["type"] == "user"
        assert f["description"] == "Alice’s role"
        assert f["body"] == "Alice is a senior Python dev."

    def test_trailing_newlines_stripped(self):
        data = _write_frontmatter("n", "user", "body\n\n")
        f = cc_memory_mirror._parse_frontmatter(data)
        assert f["body"] == "body"

    def test_no_frontmatter(self):
        assert cc_memory_mirror._parse_frontmatter(b"just a body") is None

    def test_unterminated_frontmatter(self):
        assert cc_memory_mirror._parse_frontmatter(b"---\nname: x\nbody") is None

    def test_colonless_lines_ignored(self):
        data = b"---\nname: x\nnot a pair line\ntype: user\n---\nbody"
        f = cc_memory_mirror._parse_frontmatter(data)
        assert f["name"] == "x"
        assert f["type"] == "user"


class TestMirrorUpsertDelete(unittest.TestCase):

    def setUp(self):
        self._store_tmp = _isolated_store()

    def tearDown(self):
        self._store_tmp.cleanup()
        MemoryStore.reset()

    def test_insert_then_update(self):
        rel = f"{MEMDIR_REL}/user_role.md"
        cc_memory_mirror.mirror_write(
            USER, rel,
            _write_frontmatter("user role", "user", "Alice likes Python")
        )
        entries = MemoryStore.instance().list_all(USER)
        assert len(entries) == 1
        assert "Alice likes Python" in entries[0].text
        assert entries[0].category == "facts"
        assert f"cc-mem:{CONV}:{AGENT}:user_role" in entries[0].tags

        # Rewrite the file with a different body — same slug → same entry
        cc_memory_mirror.mirror_write(
            USER, rel,
            _write_frontmatter("user role", "user", "Alice also likes Rust")
        )
        entries = MemoryStore.instance().list_all(USER)
        assert len(entries) == 1
        assert "Rust" in entries[0].text
        assert "Python" not in entries[0].text

    def test_type_to_category_mapping(self):
        for t, expected in [("user", "facts"), ("feedback", "advice"),
                             ("project", "facts"), ("reference", "facts")]:
            rel = f"{MEMDIR_REL}/{t}_x.md"
            cc_memory_mirror.mirror_write(
                USER, rel, _write_frontmatter("n", t, f"body for {t}")
            )
        by_type = {}
        for e in MemoryStore.instance().list_all(USER):
            for tag in e.tags:
                if tag.startswith("cc-type:"):
                    by_type[tag.split(":", 1)[1]] = e
        assert by_type["user"].category == "facts"
        assert by_type["feedback"].category == "advice"
        assert by_type["project"].category == "facts"
        assert by_type["reference"].category == "facts"

    def test_unlink_removes_entry(self):
        rel = f"{MEMDIR_REL}/project_x.md"
        cc_memory_mirror.mirror_write(
            USER, rel, _write_frontmatter("n", "project", "body")
        )
        assert len(MemoryStore.instance().list_all(USER)) == 1
        cc_memory_mirror.mirror_unlink(USER, rel)
        assert len(MemoryStore.instance().list_all(USER)) == 0

    def test_ignore_non_memory_paths(self):
        cc_memory_mirror.mirror_write(USER, f"{CONV}/{AGENT}/spill/foo.log",
                                       b"not a memory")
        assert MemoryStore.instance().list_all(USER) == []

    def test_skip_index_file(self):
        cc_memory_mirror.mirror_write(
            USER, f"{MEMDIR_REL}/MEMORY.md",
            b"---\nname: idx\ntype: user\n---\nirrelevant"
        )
        assert MemoryStore.instance().list_all(USER) == []

    def test_skip_missing_frontmatter(self):
        cc_memory_mirror.mirror_write(
            USER, f"{MEMDIR_REL}/bad.md", b"no frontmatter at all"
        )
        assert MemoryStore.instance().list_all(USER) == []


class TestRelayServerFsIntegration(unittest.TestCase):
    """Drive the full FUSE protocol dispatch path and verify the mirror
    lands entries without any direct call to cc_memory_mirror."""

    def setUp(self):
        self._fs_tmp = tempfile.TemporaryDirectory()
        self._store_tmp = _isolated_store()
        self.fs = RelayServerFs(user_id=USER, root_dir=Path(self._fs_tmp.name))
        # Make sure the memory dir exists inside the slot
        (Path(self._fs_tmp.name) / USER / MEMDIR_REL).mkdir(parents=True)

    def tearDown(self):
        self.fs.close()
        self._fs_tmp.cleanup()
        self._store_tmp.cleanup()
        MemoryStore.reset()

    def _create_write_release(self, rel_path: str, data: bytes) -> None:
        r = self.fs.handle("sfs.create", {"path": rel_path})
        assert "error" not in r, r
        fh = r["data"]["fh"]
        r = self.fs.handle("sfs.write", {
            "fh": fh, "offset": 0,
            "data_b64": base64.b64encode(data).decode("ascii"),
        })
        assert "error" not in r, r
        r = self.fs.handle("sfs.release", {"fh": fh})
        assert "error" not in r, r

    def test_write_lands_in_store(self):
        rel = f"{MEMDIR_REL}/user_role.md"
        self._create_write_release(
            rel, _write_frontmatter("user", "user", "Alice senior python dev")
        )
        entries = MemoryStore.instance().list_all(USER)
        assert len(entries) == 1
        assert "Alice senior python dev" in entries[0].text

    def test_overwrite_updates_same_entry(self):
        rel = f"{MEMDIR_REL}/user_role.md"
        self._create_write_release(
            rel, _write_frontmatter("u", "user", "v1")
        )
        self._create_write_release(
            rel, _write_frontmatter("u", "user", "v2 reworded")
        )
        entries = MemoryStore.instance().list_all(USER)
        assert len(entries) == 1
        assert "v2 reworded" in entries[0].text

    def test_unlink_removes(self):
        rel = f"{MEMDIR_REL}/project_x.md"
        self._create_write_release(
            rel, _write_frontmatter("p", "project", "plan details")
        )
        assert len(MemoryStore.instance().list_all(USER)) == 1
        r = self.fs.handle("sfs.unlink", {"path": rel})
        assert "error" not in r, r
        assert MemoryStore.instance().list_all(USER) == []

    def test_rename_moves_entry(self):
        old = f"{MEMDIR_REL}/user_a.md"
        new = f"{MEMDIR_REL}/user_b.md"
        self._create_write_release(
            old, _write_frontmatter("u", "user", "same body")
        )
        r = self.fs.handle("sfs.rename", {"old": old, "new": new})
        assert "error" not in r, r
        entries = MemoryStore.instance().list_all(USER)
        assert len(entries) == 1
        # The old slug tag is gone; the new one is present.
        tag_a = f"cc-mem:{CONV}:{AGENT}:user_a"
        tag_b = f"cc-mem:{CONV}:{AGENT}:user_b"
        assert tag_a not in entries[0].tags
        assert tag_b in entries[0].tags

    def test_memory_index_file_is_ignored(self):
        # CC will still write MEMORY.md on disk, but we must not mirror it.
        rel = f"{MEMDIR_REL}/MEMORY.md"
        self._create_write_release(rel,
            b"# Index\n- [user](user_role.md) - a memory\n")
        assert MemoryStore.instance().list_all(USER) == []
        # And the file itself landed on disk.
        disk = Path(self._fs_tmp.name) / USER / rel
        assert disk.exists()

    def test_release_without_write_does_not_mirror(self):
        # Open+release with no writes must not spawn a mirror call.
        rel = f"{MEMDIR_REL}/user_role.md"
        self._create_write_release(
            rel, _write_frontmatter("u", "user", "body")
        )
        assert len(MemoryStore.instance().list_all(USER)) == 1
        # Now reopen the same file read-only and release it.
        r = self.fs.handle("sfs.open", {"path": rel, "flags": os.O_RDONLY})
        assert "error" not in r, r
        fh = r["data"]["fh"]
        self.fs.handle("sfs.read", {"fh": fh, "offset": 0, "size": 4096})
        self.fs.handle("sfs.release", {"fh": fh})
        # Still just one entry.
        assert len(MemoryStore.instance().list_all(USER)) == 1

    def test_path_based_truncate_re_mirrors(self):
        rel = f"{MEMDIR_REL}/user_role.md"
        self._create_write_release(
            rel, _write_frontmatter("u", "user", "full body content")
        )
        assert len(MemoryStore.instance().list_all(USER)) == 1
        # Truncate to 0 — file is now empty, mirror should skip (no
        # frontmatter) but must not crash, and the previous entry stays.
        r = self.fs.handle("sfs.truncate", {"path": rel, "length": 0})
        assert "error" not in r, r
        # We don't auto-remove on empty truncate; only explicit unlink
        # removes entries. So the old entry is still present.
        assert len(MemoryStore.instance().list_all(USER)) == 1


if __name__ == "__main__":
    unittest.main()
