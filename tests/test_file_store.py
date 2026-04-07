"""Tests for core/file_store.py — FileStore class."""

import os
import threading

import pytest

from core.file_store import FileStore


@pytest.fixture()
def store(tmp_path):
    """Create a FileStore backed by a temporary directory."""
    return FileStore(base_dir=str(tmp_path / "files"))


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the FileStore singleton between tests."""
    FileStore._instance = None
    yield
    FileStore._instance = None


# -- store() -----------------------------------------------------------------

class TestStore:

    def test_store_returns_file_id(self, store):
        fid = store.store("hello.txt", b"hello world", "text/plain")
        assert isinstance(fid, str)
        assert len(fid) > 0

    def test_store_with_category(self, store):
        fid = store.store(
            "img.png", b"\x89PNG", "image/png", category="image",
        )
        # Verify category persists in internal entries
        entry = store._entries.get(fid)
        assert entry is not None
        assert entry["category"] == "image"

    def test_store_writes_file_to_disk(self, store, tmp_path):
        fid = store.store("data.bin", b"\x00\x01\x02", "application/octet-stream")
        entry = store._entries[fid]
        assert os.path.exists(entry["path"])
        with open(entry["path"], "rb") as f:
            assert f.read() == b"\x00\x01\x02"


# -- get() (retrieve) --------------------------------------------------------

class TestGet:

    def test_retrieve_stored_file(self, store):
        fid = store.store("readme.md", b"# Hello", "text/markdown")
        result = store.get(fid)
        assert result is not None
        filename, content, content_type = result
        assert filename == "readme.md"
        assert content == b"# Hello"
        assert content_type == "text/markdown"

    def test_retrieve_nonexistent_returns_none(self, store):
        assert store.get("does_not_exist") is None

    def test_retrieve_after_delete_returns_none(self, store):
        fid = store.store("tmp.txt", b"bye", "text/plain")
        store.delete(fid)
        assert store.get(fid) is None


# -- delete() ----------------------------------------------------------------

class TestDelete:

    def test_delete_removes_file(self, store):
        fid = store.store("gone.txt", b"poof", "text/plain")
        path = store._entries[fid]["path"]
        assert store.delete(fid) is True
        assert not os.path.exists(path)
        assert store.get(fid) is None

    def test_delete_nonexistent_returns_false(self, store):
        # Should not raise, just return False
        assert store.delete("nonexistent_id") is False

    def test_delete_twice_returns_false_second_time(self, store):
        fid = store.store("x.bin", b"\xff", "application/octet-stream")
        assert store.delete(fid) is True
        assert store.delete(fid) is False


# -- list_files() ------------------------------------------------------------

class TestListFiles:

    def test_list_empty(self, store):
        assert store.list_files() == []

    def test_list_returns_metadata(self, store):
        store.store("a.txt", b"aaa", "text/plain")
        store.store("b.json", b"{}", "application/json")
        files = store.list_files()
        assert len(files) == 2
        filenames = {f["filename"] for f in files}
        assert filenames == {"a.txt", "b.json"}
        # Each entry should have expected keys
        for f in files:
            assert "file_id" in f
            assert "filename" in f
            assert "content_type" in f
            assert "size" in f
            assert "created_at" in f

    def test_list_after_delete(self, store):
        fid = store.store("del.txt", b"x", "text/plain")
        store.store("keep.txt", b"y", "text/plain")
        store.delete(fid)
        files = store.list_files()
        assert len(files) == 1
        assert files[0]["filename"] == "keep.txt"


# -- Singleton pattern -------------------------------------------------------

class TestSingleton:

    def test_instance_returns_same_object(self):
        a = FileStore.instance()
        b = FileStore.instance()
        assert a is b

    def test_instance_is_thread_safe(self):
        """Multiple threads calling instance() all get the same object."""
        results = []

        def grab():
            results.append(FileStore.instance())

        threads = [threading.Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r is results[0] for r in results)


# -- File persistence across FileStore instances -----------------------------

class TestPersistence:

    def test_new_store_same_dir_can_retrieve(self, tmp_path):
        base = str(tmp_path / "persistent")
        store1 = FileStore(base_dir=base)
        fid = store1.store("persist.txt", b"survive restart", "text/plain")

        # Create a brand-new FileStore pointing at the same directory
        store2 = FileStore(base_dir=base)
        result = store2.get(fid)
        assert result is not None
        filename, content, content_type = result
        assert filename == "persist.txt"
        assert content == b"survive restart"
        assert content_type == "text/plain"

    def test_persistence_after_multiple_stores(self, tmp_path):
        base = str(tmp_path / "multi")
        store1 = FileStore(base_dir=base)
        ids = []
        for i in range(5):
            ids.append(store1.store(f"f{i}.txt", f"content{i}".encode(), "text/plain"))

        store2 = FileStore(base_dir=base)
        for i, fid in enumerate(ids):
            result = store2.get(fid)
            assert result is not None
            assert result[1] == f"content{i}".encode()


# -- Large file handling -----------------------------------------------------

class TestLargeFile:

    def test_store_and_retrieve_large_file(self, store):
        size = 2 * 1024 * 1024  # 2 MB
        data = os.urandom(size)
        fid = store.store("big.bin", data, "application/octet-stream")
        result = store.get(fid)
        assert result is not None
        filename, content, content_type = result
        assert filename == "big.bin"
        assert len(content) == size
        assert content == data

    def test_large_file_size_in_metadata(self, store):
        size = 1_500_000
        data = b"\x00" * size
        fid = store.store("zeros.bin", data, "application/octet-stream")
        files = store.list_files()
        match = [f for f in files if f["file_id"] == fid]
        assert len(match) == 1
        assert match[0]["size"] == size


# -- Extra edge cases --------------------------------------------------------

class TestEdgeCases:

    def test_store_empty_content(self, store):
        fid = store.store("empty.txt", b"", "text/plain")
        result = store.get(fid)
        assert result is not None
        assert result[1] == b""

    def test_filename_sanitization(self, store):
        """Paths in filename are stripped to basename."""
        fid = store.store("../../etc/passwd", b"nope", "text/plain")
        entry = store._entries[fid]
        assert entry["filename"] == "passwd"

    def test_exists_method(self, store):
        fid = store.store("check.txt", b"x", "text/plain")
        assert store.exists(fid) is True
        assert store.exists("nope") is False

    def test_count(self, store):
        assert store.count() == 0
        store.store("a.txt", b"a", "text/plain")
        store.store("b.txt", b"b", "text/plain")
        assert store.count() == 2

    def test_list_by_category(self, store):
        store.store("img1.png", b"x", "image/png", category="image")
        store.store("doc.pdf", b"x", "application/pdf", category="export")
        store.store("img2.jpg", b"x", "image/jpeg", category="image")
        images = store.list_by_category("image")
        assert len(images) == 2
        exports = store.list_by_category("export")
        assert len(exports) == 1

    def test_delete_by_category(self, store):
        store.store("a.txt", b"a", "text/plain", category="temp")
        store.store("b.txt", b"b", "text/plain", category="temp")
        store.store("c.txt", b"c", "text/plain", category="keep")
        deleted = store.delete_by(category="temp")
        assert deleted == 2
        assert store.count() == 1

    def test_find_by_name(self, store):
        fid = store.store("report.csv", b"data", "text/csv")
        assert store.find_by_name("report.csv") == fid
        # Partial match
        assert store.find_by_name("report") == fid
        # No match
        assert store.find_by_name("nonexistent.xyz") is None
