"""Tests for ProcessGroup and ContentRepository."""

import pytest
import json

from core.process_group import ProcessGroup
from core.content_repository import ContentRepository, ContentClaim


class TestProcessGroup:

    def test_create_group(self):
        g = ProcessGroup(name="My Group")
        assert g.name == "My Group"
        assert len(g.tasks) == 0

    def test_add_task(self):
        g = ProcessGroup()
        g.add_task("log_1", "log", {"message": "hello"})
        assert "log_1" in g.tasks
        assert g.tasks["log_1"]["type"] == "log"
        assert g.tasks["log_1"]["group_id"] == g.id

    def test_remove_task(self):
        g = ProcessGroup()
        g.add_task("a", "log")
        g.add_task("b", "log")
        g.add_relation("a", "b")
        g.remove_task("a")
        assert "a" not in g.tasks
        assert len(g.relations) == 0

    def test_add_relation(self):
        g = ProcessGroup()
        g.add_task("a", "log")
        g.add_task("b", "log")
        g.add_relation("a", "b", "success")
        assert len(g.relations) == 1
        assert g.relations[0]["from"] == "a"

    def test_input_output_ports(self):
        g = ProcessGroup()
        g.add_input_port("in_1")
        g.add_output_port("out_1")
        assert "in_1" in g.input_ports
        assert "out_1" in g.output_ports
        assert g.tasks["in_1"]["type"] == "inputPort"
        assert g.tasks["out_1"]["type"] == "outputPort"

    def test_duplicate_port(self):
        g = ProcessGroup()
        g.add_input_port("in_1")
        g.add_input_port("in_1")
        assert g.input_ports.count("in_1") == 1

    def test_variables(self):
        g = ProcessGroup()
        g.set_variable("key", "value")
        assert g.variables["key"] == "value"

    def test_nested_groups(self):
        parent = ProcessGroup(name="Parent")
        child = ProcessGroup(name="Child")
        child.add_task("inner_log", "log")
        parent.add_child_group(child)
        assert child.id in parent.child_groups

    def test_serialize_deserialize(self):
        g = ProcessGroup(group_id="test_id", name="Test Group")
        g.add_task("log_1", "log", {"message": "hi"})
        g.add_relation("log_1", "log_1")
        g.set_variable("x", "1")

        data = g.to_dict()
        g2 = ProcessGroup.from_dict(data)
        assert g2.id == "test_id"
        assert g2.name == "Test Group"
        assert "log_1" in g2.tasks
        assert g2.variables["x"] == "1"

    def test_nested_serialize(self):
        parent = ProcessGroup(name="P")
        child = ProcessGroup(name="C")
        child.add_task("t1", "log")
        parent.add_child_group(child)

        data = parent.to_dict()
        restored = ProcessGroup.from_dict(data)
        assert child.id in restored.child_groups
        assert "t1" in restored.child_groups[child.id].tasks

    def test_flatten(self):
        parent = ProcessGroup()
        parent.add_task("p1", "log")
        child = ProcessGroup()
        child.add_task("c1", "log")
        child.add_task("c2", "log")
        child.add_relation("c1", "c2")
        parent.add_child_group(child)
        parent.add_relation("p1", "c1")

        flat = parent.flatten()
        assert "p1" in flat["tasks"]
        assert "c1" in flat["tasks"]
        assert "c2" in flat["tasks"]
        assert len(flat["relations"]) == 2

    def test_repr(self):
        g = ProcessGroup(group_id="abc", name="Test")
        g.add_task("t", "log")
        assert "abc" in repr(g)
        assert "Test" in repr(g)


class TestContentRepository:

    def test_store_and_retrieve(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        claim = repo.store(b"hello world")
        assert claim.size == 11
        data = repo.retrieve(claim)
        assert data == b"hello world"

    def test_deduplication(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        claim1 = repo.store(b"same content")
        claim2 = repo.store(b"same content")
        assert claim1.id == claim2.id
        assert repo.size() == 1

    def test_release(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        claim = repo.store(b"temp data")
        repo.release(claim)
        assert repo.retrieve(claim) is None
        assert repo.size() == 0

    def test_ref_counting(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        claim = repo.store(b"shared")
        repo.increment_ref(claim)
        # ref count is 2, first release doesn't delete
        repo.release(claim)
        assert repo.retrieve(claim) == b"shared"
        # second release deletes
        repo.release(claim)
        assert repo.retrieve(claim) is None

    def test_total_size(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        repo.store(b"12345")
        repo.store(b"67890")
        assert repo.total_size_bytes() == 10

    def test_clear(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        repo.store(b"a")
        repo.store(b"b")
        repo.clear()
        assert repo.size() == 0
        assert repo.total_size_bytes() == 0

    def test_retrieve_missing(self, tmp_path):
        repo = ContentRepository(str(tmp_path / "content"))
        claim = ContentClaim("nonexistent", 0)
        assert repo.retrieve(claim) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
