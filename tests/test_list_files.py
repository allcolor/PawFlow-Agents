"""Tests for listFiles (enhanced), listSFTP, and FileTrackingService."""

import json
import os
import time
import pytest

from core import FlowFile
from tasks.system.list_files import ListFilesTask
from services.file_tracking_service import FileTrackingService


# ---------------------------------------------------------------------------
# FileTrackingService tests
# ---------------------------------------------------------------------------

class TestFileTrackingService:

    def test_is_new_unseen_file(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        assert svc.is_new("/some/file.csv", mtime=1000, size=500)

    def test_mark_and_not_new(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        svc.mark_processed("/some/file.csv", mtime=1000, size=500)
        assert svc.is_new("/some/file.csv", mtime=1000, size=500) is False

    def test_changed_mtime_is_new(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        svc.mark_processed("/file.csv", mtime=1000, size=500)
        assert svc.is_new("/file.csv", mtime=2000, size=500) is True

    def test_changed_size_is_new(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        svc.mark_processed("/file.csv", mtime=1000, size=500)
        assert svc.is_new("/file.csv", mtime=1000, size=600) is True

    def test_md5_strategy(self, tmp_path):
        svc = FileTrackingService({
            "storage_path": str(tmp_path / "track.json"),
            "strategy": "md5",
        })
        svc.connect()
        content = b"hello world"
        svc.mark_processed("/file.txt", content=content)
        assert svc.is_new("/file.txt", content=content) is False
        assert svc.is_new("/file.txt", content=b"changed") is True

    def test_both_strategy(self, tmp_path):
        svc = FileTrackingService({
            "storage_path": str(tmp_path / "track.json"),
            "strategy": "both",
        })
        svc.connect()
        svc.mark_processed("/f.txt", mtime=1000, size=10, content=b"data")
        # Same everything → not new
        assert svc.is_new("/f.txt", mtime=1000, size=10, content=b"data") is False
        # Changed mtime → new
        assert svc.is_new("/f.txt", mtime=2000, size=10, content=b"data") is True
        # Changed content → new
        assert svc.is_new("/f.txt", mtime=1000, size=10, content=b"other") is True

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "track.json")
        svc1 = FileTrackingService({"storage_path": path})
        svc1.connect()
        svc1.mark_processed("/file.csv", mtime=1000, size=500)
        svc1.disconnect()

        svc2 = FileTrackingService({"storage_path": path})
        svc2.connect()
        assert svc2.is_new("/file.csv", mtime=1000, size=500) is False
        svc2.disconnect()

    def test_reset_all(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        svc.mark_processed("/a.csv", mtime=1)
        svc.mark_processed("/b.csv", mtime=2)
        assert svc.get_tracked_count() == 2
        svc.reset()
        assert svc.get_tracked_count() == 0
        assert svc.is_new("/a.csv", mtime=1) is True

    def test_reset_single(self, tmp_path):
        svc = FileTrackingService({"storage_path": str(tmp_path / "track.json")})
        svc.connect()
        svc.mark_processed("/a.csv", mtime=1)
        svc.mark_processed("/b.csv", mtime=2)
        svc.reset("/a.csv")
        assert svc.is_new("/a.csv", mtime=1) is True
        assert svc.is_new("/b.csv", mtime=2) is False

    def test_max_entries_prune(self, tmp_path):
        svc = FileTrackingService({
            "storage_path": str(tmp_path / "track.json"),
            "max_entries": 5,
        })
        svc.connect()
        for i in range(10):
            svc.mark_processed(f"/file{i}.csv", mtime=i)
        assert svc.get_tracked_count() <= 5


# ---------------------------------------------------------------------------
# ListFilesTask tests
# ---------------------------------------------------------------------------

class TestListFilesTask:

    def _create_files(self, tmp_path, files):
        """Helper: create files with given names and optional content."""
        for name in files:
            p = tmp_path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(files[name] if isinstance(files, dict) else b"content")
        return tmp_path

    def test_basic_listing(self, tmp_path):
        for name in ["a.txt", "b.csv", "c.json"]:
            (tmp_path / name).write_bytes(b"data")
        task = ListFilesTask({"directory": str(tmp_path)})
        results = task.execute(None)
        assert len(results) == 3
        names = {ff.get_attribute("filename") for ff in results}
        assert names == {"a.txt", "b.csv", "c.json"}

    def test_glob_pattern(self, tmp_path):
        for name in ["data.csv", "info.csv", "readme.txt"]:
            (tmp_path / name).write_bytes(b"x")
        task = ListFilesTask({"directory": str(tmp_path), "pattern": "*.csv"})
        results = task.execute(None)
        assert len(results) == 2

    def test_file_extensions_filter(self, tmp_path):
        for name in ["a.csv", "b.json", "c.xml", "d.txt"]:
            (tmp_path / name).write_bytes(b"x")
        task = ListFilesTask({
            "directory": str(tmp_path),
            "file_extensions": ".csv,.json",
        })
        results = task.execute(None)
        assert len(results) == 2
        names = {ff.get_attribute("filename") for ff in results}
        assert names == {"a.csv", "b.json"}

    def test_regex_filter(self, tmp_path):
        for name in ["data_2024.csv", "data_2025.csv", "readme.txt"]:
            (tmp_path / name).write_bytes(b"x")
        task = ListFilesTask({
            "directory": str(tmp_path),
            "regex_filter": r"data_\d{4}\.csv",
        })
        results = task.execute(None)
        assert len(results) == 2

    def test_min_size_filter(self, tmp_path):
        (tmp_path / "small.txt").write_bytes(b"x")
        (tmp_path / "big.txt").write_bytes(b"x" * 1000)
        task = ListFilesTask({
            "directory": str(tmp_path),
            "min_size": 500,
        })
        results = task.execute(None)
        assert len(results) == 1
        assert results[0].get_attribute("filename") == "big.txt"

    def test_max_size_filter(self, tmp_path):
        (tmp_path / "small.txt").write_bytes(b"x")
        (tmp_path / "big.txt").write_bytes(b"x" * 1000)
        task = ListFilesTask({
            "directory": str(tmp_path),
            "max_size": 500,
        })
        results = task.execute(None)
        assert len(results) == 1
        assert results[0].get_attribute("filename") == "small.txt"

    def test_recursive(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_bytes(b"x")
        task = ListFilesTask({
            "directory": str(tmp_path),
            "recursive": True,
        })
        results = task.execute(None)
        assert len(results) == 2

    def test_not_recursive(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_bytes(b"x")
        task = ListFilesTask({
            "directory": str(tmp_path),
            "recursive": False,
        })
        results = task.execute(None)
        assert len(results) == 1

    def test_tracking_service(self, tmp_path):
        tracker = FileTrackingService({
            "storage_path": str(tmp_path / "track.json"),
        })
        tracker.connect()

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.csv").write_bytes(b"hello")
        (data_dir / "b.csv").write_bytes(b"world")

        task = ListFilesTask({
            "directory": str(data_dir),
            "tracking_service_id": "tracker",
        })
        task.set_services({"tracker": tracker})

        # First run: both files are new
        results = task.execute(None)
        assert len(results) == 2

        # Second run: no new files
        results = task.execute(None)
        assert len(results) == 0

        # Modify a file → becomes new again
        time.sleep(0.1)
        (data_dir / "a.csv").write_bytes(b"updated")
        results = task.execute(None)
        assert len(results) == 1
        assert results[0].get_attribute("filename") == "a.csv"

        tracker.disconnect()

    def test_flowfile_attributes(self, tmp_path):
        (tmp_path / "test.csv").write_bytes(b"data")
        task = ListFilesTask({"directory": str(tmp_path)})
        results = task.execute(None)
        ff = results[0]
        assert ff.get_attribute("filename") == "test.csv"
        assert ff.get_attribute("file.extension") == ".csv"
        assert ff.get_attribute("fileSize") is not None
        assert ff.get_attribute("file.lastModified") is not None
        assert ff.get_attribute("absolute.path") is not None

    def test_has_pending_input_disabled(self):
        task = ListFilesTask({"directory": "/tmp", "polling_interval": 0})
        assert task.has_pending_input() is False

    def test_has_pending_input_enabled(self):
        task = ListFilesTask({"directory": "/tmp", "polling_interval": 0.1})
        assert task.has_pending_input() is True
        task._last_poll_time = time.time()
        assert task.has_pending_input() is False
        time.sleep(0.15)
        assert task.has_pending_input() is True

    def test_nonexistent_directory(self):
        task = ListFilesTask({"directory": "/nonexistent/path/xyz"})
        with pytest.raises(ValueError, match="does not exist"):
            task.execute(None)

    def test_empty_directory(self, tmp_path):
        task = ListFilesTask({"directory": str(tmp_path)})
        results = task.execute(None)
        assert results == []

    def test_age_filter(self, tmp_path):
        (tmp_path / "old.txt").write_bytes(b"x")
        # Make the file look old by setting mtime
        old_time = time.time() - 3600  # 1 hour ago
        os.utime(tmp_path / "old.txt", (old_time, old_time))

        (tmp_path / "new.txt").write_bytes(b"x")

        # Only files older than 60 seconds
        task = ListFilesTask({
            "directory": str(tmp_path),
            "min_age_seconds": 60,
        })
        results = task.execute(None)
        assert len(results) == 1
        assert results[0].get_attribute("filename") == "old.txt"

        # Only files newer than 60 seconds
        task2 = ListFilesTask({
            "directory": str(tmp_path),
            "max_age_seconds": 60,
        })
        results2 = task2.execute(None)
        assert len(results2) == 1
        assert results2[0].get_attribute("filename") == "new.txt"

    def test_combined_filters(self, tmp_path):
        """Multiple filters applied together."""
        (tmp_path / "data_2024.csv").write_bytes(b"x" * 100)
        (tmp_path / "data_2024.txt").write_bytes(b"x" * 100)
        (tmp_path / "data_2024.csv.bak").write_bytes(b"x" * 100)
        (tmp_path / "tiny.csv").write_bytes(b"x")

        task = ListFilesTask({
            "directory": str(tmp_path),
            "file_extensions": ".csv",
            "regex_filter": r"data_\d{4}",
            "min_size": 10,
        })
        results = task.execute(None)
        assert len(results) == 1
        assert results[0].get_attribute("filename") == "data_2024.csv"


# ---------------------------------------------------------------------------
# ListSFTPTask tests (mock-based since we can't connect to real SFTP)
# ---------------------------------------------------------------------------

class TestListSFTPTask:

    def test_task_creation(self):
        from tasks.io.list_sftp import ListSFTPTask
        task = ListSFTPTask({
            "hostname": "sftp.example.com",
            "username": "user",
            "password": "pass",
            "remote_directory": "/data",
            "pattern": "*.csv",
            "file_extensions": ".csv,.json",
            "min_size": 100,
            "polling_interval": 30,
        })
        assert task.hostname == "sftp.example.com"
        assert task.pattern == "*.csv"
        assert task._extensions == {".csv", ".json"}
        assert task.min_size == 100

    def test_has_pending_input(self):
        from tasks.io.list_sftp import ListSFTPTask
        task = ListSFTPTask({
            "hostname": "h", "username": "u", "remote_directory": "/",
            "polling_interval": 0.1,
        })
        assert task.has_pending_input() is True
        task._last_poll_time = time.time()
        assert task.has_pending_input() is False

    def test_passes_filters_extension(self):
        from tasks.io.list_sftp import ListSFTPTask
        task = ListSFTPTask({
            "hostname": "h", "username": "u", "remote_directory": "/",
            "file_extensions": ".csv",
        })

        class FakeEntry:
            def __init__(self, filename, size=100, mtime=None):
                self.filename = filename
                self.st_size = size
                self.st_mtime = mtime or time.time()

        assert task._passes_filters(FakeEntry("data.csv"), time.time()) is True
        assert task._passes_filters(FakeEntry("data.txt"), time.time()) is False

    def test_passes_filters_size(self):
        from tasks.io.list_sftp import ListSFTPTask
        task = ListSFTPTask({
            "hostname": "h", "username": "u", "remote_directory": "/",
            "min_size": 50, "max_size": 200,
        })

        class FakeEntry:
            def __init__(self, size):
                self.filename = "f.csv"
                self.st_size = size
                self.st_mtime = time.time()

        assert task._passes_filters(FakeEntry(100), time.time()) is True
        assert task._passes_filters(FakeEntry(10), time.time()) is False
        assert task._passes_filters(FakeEntry(300), time.time()) is False

    def test_passes_filters_regex(self):
        from tasks.io.list_sftp import ListSFTPTask
        task = ListSFTPTask({
            "hostname": "h", "username": "u", "remote_directory": "/",
            "regex_filter": r"^report_\d+\.csv$",
        })

        class FakeEntry:
            def __init__(self, filename):
                self.filename = filename
                self.st_size = 100
                self.st_mtime = time.time()

        assert task._passes_filters(FakeEntry("report_2024.csv"), time.time()) is True
        assert task._passes_filters(FakeEntry("data.csv"), time.time()) is False

    def test_requires_paramiko(self):
        """Without paramiko, should raise TaskError."""
        from tasks.io.list_sftp import ListSFTPTask, _get_paramiko
        if _get_paramiko() is not None:
            pytest.skip("paramiko is installed")
        task = ListSFTPTask({
            "hostname": "h", "username": "u", "remote_directory": "/",
        })
        from core import TaskError
        with pytest.raises(TaskError, match="paramiko"):
            task.execute(None)
