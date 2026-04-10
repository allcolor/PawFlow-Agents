"""Tests for storage backends (Git, SQLite, Filesystem)."""

import json
import pytest
from pathlib import Path


# ============================================================================
# Git Storage Tests
# ============================================================================

class TestGitStorage:

    @pytest.fixture
    def git_storage(self, tmp_path):
        from core.storage_backends.git_storage import GitStorage
        return GitStorage({
            'repository': str(tmp_path),
            'flows_dir': 'flows',
            'auto_commit': True,
        })

    def test_save_and_load_flow(self, git_storage):
        config = {"name": "Test Flow", "version": "1.0.0", "tasks": {}}
        assert git_storage.save_flow("test-flow", config)

        loaded = git_storage.load_flow("test-flow")
        assert loaded is not None
        assert loaded["name"] == "Test Flow"

    def test_list_flows(self, git_storage):
        git_storage.save_flow("flow-a", {"name": "A"})
        git_storage.save_flow("flow-b", {"name": "B"})

        flows = git_storage.list_flows()
        assert "flow-a" in flows
        assert "flow-b" in flows
        assert len(flows) == 2

    def test_delete_flow(self, git_storage):
        git_storage.save_flow("to-delete", {"name": "Delete Me"})
        assert git_storage.load_flow("to-delete") is not None

        assert git_storage.delete_flow("to-delete")
        assert git_storage.load_flow("to-delete") is None

    def test_delete_nonexistent(self, git_storage):
        assert not git_storage.delete_flow("nonexistent")

    def test_load_nonexistent(self, git_storage):
        assert git_storage.load_flow("nonexistent") is None

    def test_overwrite_flow(self, git_storage):
        git_storage.save_flow("my-flow", {"name": "V1"})
        git_storage.save_flow("my-flow", {"name": "V2"})

        loaded = git_storage.load_flow("my-flow")
        assert loaded["name"] == "V2"

    def test_save_task(self, git_storage):
        assert git_storage.save_task("log", {"id": "log1", "message": "hi"})

    def test_save_service(self, git_storage):
        assert git_storage.load_service("dbPool", {"id": "db1", "host": "localhost"})

    def test_flow_history(self, git_storage):
        git_storage.save_flow("versioned", {"name": "V1"})
        git_storage.save_flow("versioned", {"name": "V2"})
        git_storage.save_flow("versioned", {"name": "V3"})

        history = git_storage.get_flow_history("versioned")
        assert len(history) >= 2  # At least 2 commits (could be 3)
        assert all("commit" in h for h in history)

    def test_flow_at_commit(self, git_storage):
        git_storage.save_flow("time-travel", {"name": "Original"})
        git_storage.save_flow("time-travel", {"name": "Modified"})

        history = git_storage.get_flow_history("time-travel")
        assert len(history) >= 2

        # Get the older version
        old_commit = history[-1]["commit"]
        old_version = git_storage.get_flow_at_commit("time-travel", old_commit)
        assert old_version is not None
        assert old_version["name"] == "Original"

    def test_no_auto_commit(self, tmp_path):
        from core.storage_backends.git_storage import GitStorage
        storage = GitStorage({
            'repository': str(tmp_path),
            'auto_commit': False,
        })
        storage.save_flow("no-commit", {"name": "Test"})

        # Flow saved on disk but no auto commit
        loaded = storage.load_flow("no-commit")
        assert loaded is not None


# ============================================================================
# SQLite Storage Tests
# ============================================================================

class TestSqliteStorage:

    @pytest.fixture
    def sqlite_storage(self, tmp_path):
        from core.storage_backends.sqlite_storage import SqliteStorage
        return SqliteStorage({'database': str(tmp_path / 'test.db')})

    def test_save_and_load_flow(self, sqlite_storage):
        config = {"name": "SQLite Flow", "tasks": {}}
        assert sqlite_storage.save_flow("sql-flow", config)

        loaded = sqlite_storage.load_flow("sql-flow")
        assert loaded is not None
        assert loaded["name"] == "SQLite Flow"

    def test_list_flows(self, sqlite_storage):
        sqlite_storage.save_flow("a", {"name": "A"})
        sqlite_storage.save_flow("b", {"name": "B"})
        flows = sqlite_storage.list_flows()
        assert "a" in flows
        assert "b" in flows

    def test_delete_flow(self, sqlite_storage):
        sqlite_storage.save_flow("del", {"name": "Delete"})
        assert sqlite_storage.delete_flow("del")
        assert sqlite_storage.load_flow("del") is None

    def test_load_nonexistent(self, sqlite_storage):
        assert sqlite_storage.load_flow("nope") is None

    def test_overwrite(self, sqlite_storage):
        sqlite_storage.save_flow("ow", {"name": "V1"})
        sqlite_storage.save_flow("ow", {"name": "V2"})
        loaded = sqlite_storage.load_flow("ow")
        assert loaded["name"] == "V2"

    def test_save_task(self, sqlite_storage):
        assert sqlite_storage.save_task("log", {"message": "hi"})

    def test_save_service(self, sqlite_storage):
        assert sqlite_storage.load_service("cache", {"ttl": 60})


# ============================================================================
# Filesystem Storage Tests
# ============================================================================

class TestFilesystemStorage:

    @pytest.fixture
    def fs_storage(self, tmp_path):
        from core.storage_backends.filesystem_storage import FilesystemStorage
        return FilesystemStorage({
            'flows_path': str(tmp_path / 'flows'),
            'tasks_path': str(tmp_path / 'tasks'),
            'services_path': str(tmp_path / 'services'),
        })

    def test_save_and_load_flow(self, fs_storage):
        config = {"name": "FS Flow"}
        assert fs_storage.save_flow("fs-flow", config)

        loaded = fs_storage.load_flow("fs-flow")
        assert loaded is not None
        assert loaded["name"] == "FS Flow"

    def test_list_flows(self, fs_storage):
        fs_storage.save_flow("x", {"name": "X"})
        fs_storage.save_flow("y", {"name": "Y"})
        flows = fs_storage.list_flows()
        assert "x" in flows
        assert "y" in flows

    def test_delete_flow(self, fs_storage):
        fs_storage.save_flow("rem", {"name": "Remove"})
        assert fs_storage.delete_flow("rem")
        assert fs_storage.load_flow("rem") is None

    def test_delete_nonexistent(self, fs_storage):
        assert not fs_storage.delete_flow("ghost")

    def test_save_task(self, fs_storage):
        assert fs_storage.save_task("log", {"id": "t1", "msg": "hello"})

    def test_save_service(self, fs_storage):
        assert fs_storage.load_service("db", {"id": "s1", "host": "localhost"})


# ============================================================================
# StorageManager Integration Tests
# ============================================================================

class TestStorageManager:

    @pytest.fixture
    def storage_manager(self, tmp_path):
        from core.storage import StorageManager
        from core.storage_backends.filesystem_storage import FilesystemStorage
        fs = FilesystemStorage({'flows_path': str(tmp_path / 'flows')})
        return StorageManager(storage=fs)

    def test_save_and_load(self, storage_manager):
        config = {"name": "Manager Flow", "version": "1.0.0"}
        assert storage_manager.save_flow("mgr-flow", config)
        loaded = storage_manager.load_flow("mgr-flow")
        assert loaded is not None
        assert loaded["name"] == "Manager Flow"

    def test_list_and_delete(self, storage_manager):
        storage_manager.save_flow("a", {"name": "A"})
        storage_manager.save_flow("b", {"name": "B"})
        assert len(storage_manager.list_flows()) == 2

        storage_manager.delete_flow("a")
        assert len(storage_manager.list_flows()) == 1

    def test_versioning(self, storage_manager):
        storage_manager.save_version("flow1", {"name": "V1"}, "1.0.0")
        storage_manager.save_version("flow1", {"name": "V2"}, "2.0.0")

        versions = storage_manager.get_versions("flow1")
        assert len(versions) == 2
        assert "1.0.0" in versions

        v1 = storage_manager.get_version("flow1", "1.0.0")
        assert v1 is not None

    def test_search(self, storage_manager):
        storage_manager.save_flow("alpha", {"name": "Alpha Flow", "description": "Test"})
        storage_manager.save_flow("beta", {"name": "Beta Flow", "description": "Production"})

        results = storage_manager.search_flows("Alpha")
        assert len(results) == 1

    def test_flow_stats(self, storage_manager):
        storage_manager.save_flow("stats-flow", {
            "name": "Stats",
            "version": "1.0.0",
            "tasks": {"a": {}, "b": {}},
            "relations": [{"from": "a", "to": "b"}],
        })
        stats = storage_manager.get_flow_stats("stats-flow")
        assert stats["name"] == "Stats"
        assert stats["tasks_count"] == 2
        assert stats["relations_count"] == 1

    def test_switch_backend(self, tmp_path, storage_manager):
        from core.storage_backends.sqlite_storage import SqliteStorage
        sqlite = SqliteStorage({'database': str(tmp_path / 'switch.db')})
        storage_manager.set_storage(sqlite)
        assert storage_manager.get_storage_type() == "SqliteStorage"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
